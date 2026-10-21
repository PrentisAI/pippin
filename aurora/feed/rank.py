"""How well an item fits each project, and why, in one sentence.

A project is described by its objective, the titles of the papers in its literature and what its
runs found.  Items are first scored by the words they share with each project (cheap, and the
whole answer when no model is available); the most promising are then ranked by a model, a
batch at a time, which writes the "Why for <project>" sentence.  Items the person marked "Not
interested" are given to the model as examples of what they do not want, and lower the score of
similar items.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections import Counter
from typing import Any

from aurora.literature.records import fold

logger = logging.getLogger(__name__)

_STOP = set("""a an the of in on for to and or with by from at as is are be been was were it its this that these those
we our you your they their he she his her i me my can could should would will may might do does did not no
than then there here into onto over under via using use used based new study paper results result show shows
approach method methods model models data analysis how what when where which who why also more most such
between across about after before during each other some any all both only one two three first using within
towards toward without per vs""".split())


def terms(text: str) -> list[str]:
    out = []
    for w in fold(text).split():
        if len(w) < 3 or w in _STOP or w.isdigit():
            continue
        for suf in ("ings", "ing", "ies", "es", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)] + ("y" if suf == "ies" else "")
                break
        out.append(w)
    return out


def item_text(it: dict[str, Any]) -> str:
    return f"{it.get('title', '')}. {it.get('summary', '')}"


class Lexical:
    """Word overlap between items and project descriptions, weighted by rarity."""

    def __init__(self, profiles: list[dict[str, Any]], items: list[dict[str, Any]],
                 dislikes: list[dict[str, Any]] | None = None) -> None:
        self.profiles = profiles
        docs = [set(terms(p["text"])) for p in profiles] + [set(terms(item_text(i))) for i in items]
        df: Counter[str] = Counter()
        for d in docs:
            df.update(d)
        n = max(len(docs), 1)
        self.idf = {t: math.log(1 + n / c) for t, c in df.items()}
        self.proj = [Counter(terms(p["text"])) for p in profiles]
        self.dislike = Counter(t for d in dislikes or [] for t in set(terms(item_text(d))))

    def score(self, it: dict[str, Any]) -> list[tuple[float, list[str]]]:
        """(score, shared words) for each project, in the order of the profiles."""
        words = set(terms(item_text(it)))
        out = []
        for pc in self.proj:
            shared = [w for w in words if w in pc]
            s = sum(self.idf.get(w, 0) * (1 + math.log(pc[w])) for w in shared)
            s /= math.sqrt(len(words) + 8)
            if self.dislike:
                s -= 0.15 * sum(self.idf.get(w, 0) for w in words if self.dislike[w] >= 2) / math.sqrt(len(words) + 8)
            shared.sort(key=lambda w: -self.idf.get(w, 0))
            out.append((s, shared[:4]))
        return out

    @staticmethod
    def rel(score: float) -> int:
        return 5 if score > 3.2 else 4 if score > 2.2 else 3 if score > 1.4 else 2 if score > 0.7 else 1


def lexical_ranks(lex: Lexical, it: dict[str, Any], boost: float = 0.0) -> list[tuple[str, int, str]]:
    """Ranks without a model: the projects the item shares the rarest words with."""
    out = []
    for p, (s, shared) in zip(lex.profiles, lex.score(it), strict=True):
        rel = Lexical.rel(s + boost)
        if rel >= 2 and shared:
            out.append((p["campaign_id"], rel, f"Shares {', '.join(shared)} with {p['name']}’s objective and papers."))
    out.sort(key=lambda x: -x[1])
    return out[:2]


_RANK_PROMPT = """You pick what a researcher should read today. Their projects:

{projects}

Things the researcher marked "not interested" lately (rank similar things low):
{dislikes}

For each item below, say which project(s) it fits (at most two) and how well, 1..5:
5 = directly about the project's question, data or method, or contradicts its findings: must read;
4 = clearly useful to the project; 3 = related, worth a look; 2 = loosely related; 1 = not related.
Write one sentence per fit ("why"), addressed to the researcher, saying concretely what it means
for that project (its data, a planned step, a finding it supports or contradicts). No hype, no
"this paper"; under 35 words. Items that fit no project get an empty list.

Items:
{items}

Answer with JSON only: {{"items": [{{"id": "...", "fits": [{{"project": "<project id>", "rel": 1-5, "why": "..."}}]}}]}}"""


def _projects_block(profiles: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"[{p['campaign_id']}] {p['name']}\n{p['text'][:1500]}" for p in profiles)


def parse_json(text: str) -> Any:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


async def model_ranks(items: list[dict[str, Any]], profiles: list[dict[str, Any]], access: Any,
                      dislikes: list[dict[str, Any]] | None = None) -> dict[str, list[tuple[str, int, str]]]:
    """item_id -> [(campaign_id, rel, why)] from one model call; {} if it failed."""
    from aurora.brainstorm.agents import _client
    known = {p["campaign_id"] for p in profiles}
    lines = []
    for it in items:
        lines.append(f"- id {it['item_id']} [{it['kind']}, {it.get('source', '')}] {it['title']}\n  "
                     f"{(it.get('summary') or '')[:500]}")
    prompt = _RANK_PROMPT.format(projects=_projects_block(profiles),
                                 dislikes="\n".join(f"- {d['title']}" for d in (dislikes or [])[:12]) or "(none)",
                                 items="\n".join(lines))
    try:
        r = await _client(access).chat.completions.create(
            model=access.model, messages=[{"role": "user", "content": prompt}], max_tokens=4000, temperature=0.2)
        data = parse_json(r.choices[0].message.content or "")
    except Exception:
        logger.warning("feed ranking: the model call failed", exc_info=True)
        return {}
    out: dict[str, list[tuple[str, int, str]]] = {}
    for row in (data or {}).get("items") or []:
        fits = []
        for f in row.get("fits") or []:
            cid = str(f.get("project", "")).strip("[] ")
            if cid in known:
                try:
                    fits.append((cid, int(f.get("rel", 1)), str(f.get("why", "")).strip()))
                except (TypeError, ValueError):
                    continue
        out[str(row.get("id", ""))] = sorted(fits, key=lambda x: -x[1])[:2]
    return out


_TOPICS_PROMPT = """A research project's objective:

{objective}

Give 2 or 3 short search phrases (2-4 words each) that would find new papers relevant to it, and the
arXiv categories (like cs.LG, stat.AP, physics.soc-ph) where such papers appear (at most 2).
Answer with JSON only: {{"keywords": ["..."], "categories": ["..."]}}"""

_CAT = re.compile(r"^[a-z\-]+(\.[A-Za-z\-]+)?$")


async def objective_topics(objective: str, access: Any) -> dict[str, list[str]]:
    """Search phrases and arXiv categories for a project's objective."""
    if access is not None and getattr(access, "api_key", ""):
        from aurora.brainstorm.agents import _client
        try:
            r = await _client(access).chat.completions.create(
                model=access.model, messages=[{"role": "user", "content": _TOPICS_PROMPT.format(objective=objective[:2000])}],
                max_tokens=600, temperature=0.2)
            data = parse_json(r.choices[0].message.content or "") or {}
            kws = [str(k).strip() for k in data.get("keywords") or [] if str(k).strip()][:3]
            cats = [str(c).strip() for c in data.get("categories") or [] if _CAT.match(str(c).strip())][:2]
            if kws:
                return {"keywords": kws, "categories": cats}
        except Exception:
            logger.info("feed: topics from an objective failed", exc_info=True)
    words = [w for w, _ in Counter(terms(objective)).most_common(6)]
    return {"keywords": [" ".join(words[i:i + 2]) for i in range(0, min(len(words), 4), 2)], "categories": []}


def topics_key(objective: str) -> str:
    return hashlib.sha1(objective.encode()).hexdigest()[:12]


_DISCUSS_PROMPT = """People are discussing the paper “{title}”. What they wrote, from {n} places:

{texts}

Give the main points of the discussion (at most 3), each one sentence saying what was raised, how
often, and whether the authors answered. Then one sentence of summary. Use only what is written above.
Answer with JSON only: {{"summary": "...", "points": ["...", "..."]}}"""


async def summarize_discussion(title: str, texts: list[str], n_places: int, access: Any) -> tuple[str, list[str]]:
    """(summary, points) of what people say about a paper."""
    if access is not None and getattr(access, "api_key", ""):
        from aurora.brainstorm.agents import _client
        try:
            r = await _client(access).chat.completions.create(
                model=access.model, max_tokens=1200, temperature=0.2, messages=[{"role": "user", "content": _DISCUSS_PROMPT.format(
                    title=title, n=n_places, texts="\n".join(f"- {t[:400]}" for t in texts[:40]))}])
            data = parse_json(r.choices[0].message.content or "") or {}
            points = [str(p).strip() for p in data.get("points") or [] if str(p).strip()][:3]
            if points:
                return str(data.get("summary") or "Main points, with links to each place:").strip(), points
        except Exception:
            logger.info("feed: summarizing a discussion failed", exc_info=True)
    return "Main points, with links to each place:", [" ".join(t.split())[:200] for t in texts[:3]]

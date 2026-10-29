"""Saving something into the notebook: its claims taken out, each merged into the idea unit that says the same thing
or made a unit of its own, contradictions linked, and what it bears on among the person's research questions.

1. Read it (``fetch.get``) and keep a snapshot.
2. Take out what it claims: one to five claims, each with the source's own words, which must be found in the text.
3. Embed the claims and find the units that may say the same thing (across languages).
4. A model decides, for each claim: the same as a unit, the opposite of one, a refinement of one, or new.
5. Merge (one more source for the unit), or make a new unit; link contradictions.
6. Score what each unit bears on among the person's research questions.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
from typing import Any

from aurora.memory import fetch
from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)

KINDS = ("finding", "method", "opinion", "tool", "background")
CANDIDATES = 5          # units a claim is compared with
SIMILAR = 0.45          # cosine above which a unit may say the same thing
BEARS = 0.3             # relevance from which a unit counts as bearing on a question (across languages it runs lower)
_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Embeddings (an OpenRouter model; mock-* models embed words locally, for tests and the demo)


def _mock_vec(text: str, dim: int = 256) -> list[float]:
    v = [0.0] * dim
    words = re.findall(r"[a-z0-9]+|[一-鿿]", text.lower())
    for w in words + [a + b for a, b in zip(words, words[1:], strict=False)]:
        v[int(hashlib.md5(w.encode()).hexdigest()[:8], 16) % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def embed(access: Any, model: str, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if model.startswith("mock-"):
        return [_mock_vec(t) for t in texts]
    from aurora.review.llm import _client
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        resp = await _client(access).embeddings.create(model=model, input=[t[:8000] for t in texts[i:i + 64]])
        out.extend(list(d.embedding) for d in resp.data)
    return out


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def unit_vectors(access: Any, model: str, units: list[dict[str, Any]]) -> dict[str, list[float]]:
    """The units' vectors, embedding any that lack one."""
    have = mem_store.vectors([f"u:{u['unit_id']}" for u in units], model)
    missing = [u for u in units if f"u:{u['unit_id']}" not in have]
    if missing:
        vecs = await embed(access, model, [f"{u['claim']}\n{u['gist']}" for u in missing])
        for u, v in zip(missing, vecs, strict=False):
            mem_store.put_vector(f"u:{u['unit_id']}", model, v)
            have[f"u:{u['unit_id']}"] = v
    return {k[2:]: v for k, v in have.items()}


async def question_vectors(access: Any, model: str, questions: list[dict[str, Any]]) -> dict[str, list[float]]:
    have = mem_store.vectors([f"q:{q['question_id']}" for q in questions], model)
    missing = [q for q in questions if f"q:{q['question_id']}" not in have]
    if missing:
        for q, v in zip(missing, await embed(access, model, [q["text"] for q in missing]), strict=False):
            mem_store.put_vector(f"q:{q['question_id']}", model, v)
            have[f"q:{q['question_id']}"] = v
    return {k[2:]: v for k, v in have.items()}


def relevance(cos: float) -> float:
    """A cosine as a relevance between 0 and 1 (embeddings of related texts sit well above 0)."""
    return max(0.0, min(1.0, (cos - 0.2) / 0.45))


# ---------------------------------------------------------------------------
# The pipeline


def start(access: Any, models: dict[str, str], owner: str, **what: Any) -> dict[str, Any]:
    """Save something (a link, a page's text, a selection, a note) and work on it in the background."""
    url = str(what.get("url") or "").strip()
    src = mem_store.add_source(owner, platform=fetch.platform_of(url), url=url, title=str(what.get("title") or "")[:300],
                               via=str(what.get("via") or "manual"), selection=str(what.get("selection") or "")[:4000])
    task = asyncio.create_task(process(access, models, src["source_id"], text=str(what.get("text") or ""),
                                       html=str(what.get("html") or "")))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return src


async def process(access: Any, models: dict[str, str], source_id: str, *, text: str = "", html: str = "") -> dict[str, Any]:
    src = mem_store.source(source_id)
    if src is None:
        return {}
    owner = src["owner"]
    try:
        got = await fetch.get(owner, url=src["url"], text=text, html=html, title=src["title"])
        same = mem_store.find_source(owner, digest=fetch.digest(got.text))
        if same is not None and same["source_id"] != source_id:
            result = {**same["result"], "duplicate_of": same["source_id"]}
            mem_store.update_source(source_id, state="done", result=result, done_at=time.time(), title=got.title,
                                    digest=fetch.digest(got.text))
            return result
        mem_store.save_snapshot(source_id, got.text, got.html)
        mem_store.update_source(source_id, platform=got.platform if got.platform != "text" else src["platform"],
                                title=got.title or src["title"], author=got.author, published=got.published,
                                paper_id=got.paper_id, digest=fetch.digest(got.text))
        claims = await extract(access.with_model(models["memory"]), got, src["selection"])
        result = await settle(access, models, owner, source_id, claims)
        mem_store.update_source(source_id, state="done", result=result, done_at=time.time(),
                                lang=claims[0].get("lang", "") if claims else "")
        if got.paper_id:            # a new paper that does what you set out to do is worth an interruption
            try:
                from aurora.memory import alerts
                if await alerts.check_overlap(access, models, owner, source_id):
                    result["overlap"] = True
                    mem_store.update_source(source_id, result=result)
            except Exception:
                logger.info("checking %s for overlap failed", source_id, exc_info=True)
        return result
    except Exception as exc:
        logger.info("saving %s into your notebook failed", source_id, exc_info=True)
        mem_store.update_source(source_id, state="failed", error=str(exc)[:500], done_at=time.time())
        return {"error": str(exc)}


async def extract(access: Any, got: fetch.Got, selection: str = "") -> list[dict[str, Any]]:
    """What the source claims, each with its own words (kept only if found in the text)."""
    from aurora.review import llm
    text = got.text if len(got.text) <= 40_000 else got.text[:30_000] + "\n…\n" + got.text[-8_000:]
    marked = f"\nThe researcher highlighted this when saving it (it matters most to them):\n{selection}\n" if selection else ""
    out = await llm.ask(access, "memory_claims", f"""A researcher saved this ({got.platform}) into their research \
memory. Take out what it CLAIMS that a researcher could rely on, argue with, or use: findings, results, methods that \
work or fail, informed opinions, tools. One to five claims, the most important first; fewer when it says less. Leave \
out greetings, promotion, and general background everyone knows.

For each claim:
- "claim": one sentence, specific and checkable (conditions, numbers, names), in the source's language;
- "gist": two or three sentences on what supports it in the source;
- "kind": finding | method | opinion | tool | background;
- "quote": the source's own words that state it, copied EXACTLY (10 to 60 words), so it can be found again;
- "stance": asserts (the source says it), qualifies (it says it holds only under conditions), or disputes (it \
reports the claim and argues against it; then "claim" is what it argues FOR).
Also "lang": the source's language (zh, en, ...).
{marked}
Title: {got.title}
Text:
{text}

JSON: {{"lang": "en", "claims": [{{"claim": "...", "gist": "...", "kind": "finding", "quote": "...", "stance": "asserts"}}]}}""",
                        want="claims", max_tokens=4000)
    claims = []
    for c in out.get("claims") or []:
        if not isinstance(c, dict) or not str(c.get("claim", "")).strip():
            continue
        quote = str(c.get("quote", "")).strip()
        if not fetch.find_quote(got.text, quote):
            continue            # words the source does not have are not evidence
        claims.append({"claim": llm.unlabel(c["claim"])[:600], "gist": llm.unlabel(c.get("gist", ""))[:2000],
                       "kind": c.get("kind") if c.get("kind") in KINDS else "finding", "quote": quote,
                       "stance": c.get("stance") if c.get("stance") in ("asserts", "qualifies", "disputes") else "asserts",
                       "lang": str(out.get("lang") or "")[:8]})
    return claims[:5]


async def settle(access: Any, models: dict[str, str], owner: str, source_id: str,
                 claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge each claim into the unit that says the same thing, or make it one; link contradictions."""
    if not claims:
        return {"units": [], "merged": [], "new": [], "conflicts": [], "questions": []}
    emb = models["embedding"]
    cvecs = await embed(access, emb, [f"{c['claim']}\n{c['gist']}" for c in claims])
    units = mem_store.units(owner)
    uvecs = await unit_vectors(access, emb, units)
    by_id = {u["unit_id"]: u for u in units}
    cands = []
    for v in cvecs:
        scored = sorted(((cosine(v, uv), uid) for uid, uv in uvecs.items()), reverse=True)
        cands.append([uid for s, uid in scored[:CANDIDATES] if s >= SIMILAR])
    verdicts = await align(access.with_model(models["memory"]), claims, cands, by_id) if any(cands) else [{}] * len(claims)
    merged, new, conflicts, touched = [], [], [], []
    for c, v, verdict in zip(claims, cvecs, verdicts, strict=False):
        same = verdict.get("same")
        if same and same in by_id:
            unit = by_id[same]
            mem_store.add_evidence(unit["unit_id"], source_id, c["quote"], c["stance"])
            merged.append(unit["unit_id"])
        else:
            unit = mem_store.add_unit(owner, c["claim"], c["gist"], c["kind"])
            mem_store.add_evidence(unit["unit_id"], source_id, c["quote"], c["stance"])
            mem_store.put_vector(f"u:{unit['unit_id']}", emb, v)
            by_id[unit["unit_id"]] = unit
            new.append(unit["unit_id"])
        for other in verdict.get("contradicts") or []:
            if other in by_id and other != unit["unit_id"]:
                mem_store.link(unit["unit_id"], other, "contradicts", verdict.get("why", ""))
                conflicts.append([unit["unit_id"], other])
        for other in verdict.get("refines") or []:
            if other in by_id and other != unit["unit_id"]:
                mem_store.link(unit["unit_id"], other, "refines")
        touched.append(unit["unit_id"])
    questions = await bear(access, models, owner, touched)
    return {"units": touched, "merged": merged, "new": new, "conflicts": conflicts, "questions": questions}


async def align(access: Any, claims: list[dict[str, Any]], cands: list[list[str]],
                by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """For each new claim: which candidate unit says the same thing, which say the opposite, which it refines."""
    from aurora.review import llm
    apart = kept_apart([uid for ids in cands for uid in ids])
    parts = []
    for i, (c, ids) in enumerate(zip(claims, cands, strict=False)):
        lines = "\n".join(f"  [{uid}] {by_id[uid]['claim']}"
                          + "".join(f"\n      (the researcher said this is NOT the same claim as: {x})" for x in apart.get(uid, []))
                          for uid in ids) or "  (none)"
        parts.append(f"New claim {i + 1}: {c['claim']}\n  (support: {c['gist']})\nCandidates:\n{lines}")
    out = await llm.ask(access, "memory_align", f"""A researcher's memory keeps one unit per claim, with every source \
that makes it. For each new claim, compare it with its candidate units (they may be in another language):
- "same": the unit that makes the SAME claim, or null. Same means: about the same thing and reaching the same \
conclusion. Sources that report it from different settings (another GPU, dataset, model size, numbers that agree in \
direction) still make the same claim; the unit then simply has more evidence. Only when a condition changes the \
conclusion (it holds here but not there) is it a different claim.
- "contradicts": units that claim the opposite about the same thing (both cannot hold under the same conditions).
- "refines": units it qualifies: it says the claim holds only under narrower conditions, or adds a mechanism.
- "why": one sentence, when it contradicts something.
The researcher's own corrections are noted under a unit: a claim like one they kept apart is not the same claim.

{chr(10).join(parts)}

JSON: {{"verdicts": [{{"n": 1, "same": "u_... or null", "contradicts": [], "refines": [], "why": ""}}]}}""",
                        want="verdicts", max_tokens=3000)
    got: dict[int, dict[str, Any]] = {}
    for v in out.get("verdicts") or []:
        try:
            n = int(v.get("n"))
        except (TypeError, ValueError, AttributeError):
            continue
        allowed = set(cands[n - 1]) if 1 <= n <= len(cands) else set()
        same = v.get("same") if v.get("same") in allowed else None
        got[n] = {"same": same, "contradicts": [x for x in v.get("contradicts") or [] if x in allowed and x != same],
                  "refines": [x for x in v.get("refines") or [] if x in allowed and x != same],
                  "why": llm.unlabel(v.get("why", ""))}
    return [got.get(i + 1, {}) for i in range(len(claims))]


def kept_apart(unit_ids: list[str]) -> dict[str, list[str]]:
    """The researcher's corrections, for aligning: per unit, the claims they split off it ("not the same") and the
    units they split it from. Used as counterexamples, so the same mistake is not made again."""
    out: dict[str, list[str]] = {}
    for m in mem_store.marks(list(dict.fromkeys(unit_ids))):
        d = m.get("data") or {}
        if m["kind"] == "split":
            other = mem_store.unit(d.get("into", ""))
            quote = d.get("quote") or (other or {}).get("claim", "")
            if quote:
                out.setdefault(m["unit_id"], []).append(f"“{quote[:240]}”")
        elif m["kind"] == "split_from":
            other = mem_store.unit(d.get("unit_id", ""))
            if other:
                out.setdefault(m["unit_id"], []).append(f"“{other['claim'][:240]}”")
    return out


async def bear(access: Any, models: dict[str, str], owner: str, unit_ids: list[str]) -> list[str]:
    """What the units bear on among the person's research questions; the questions they bear on clearly."""
    from aurora.memory import questions as qs
    questions = qs.current(owner)
    if not questions or not unit_ids:
        return []
    emb = models["embedding"]
    qv = await question_vectors(access, emb, questions)
    uv = await unit_vectors(access, emb, [u for uid in unit_ids if (u := mem_store.unit(uid))])
    hit = []
    for uid, v in uv.items():
        for q in questions:
            if q["question_id"] in qv:
                r = relevance(cosine(v, qv[q["question_id"]]))
                mem_store.set_bears(uid, q["question_id"], r)
                if r >= BEARS and q["question_id"] not in hit:
                    hit.append(q["question_id"])
    return hit


def summary(result: dict[str, Any], owner: str) -> str:
    """What saving it did, in a sentence (the toast)."""
    from aurora.memory import questions as qs
    if result.get("error"):
        return f"Could not save it: {result['error']}"
    if result.get("duplicate_of"):
        return "Already in your notebook."
    if not result.get("units"):
        return "Saved; it makes no claim worth keeping on its own, so it stays as a source."
    names = {q["question_id"]: q["label"] for q in qs.current(owner)}
    bits = []
    for uid in result.get("merged") or []:
        u = mem_store.unit(uid)
        if u:
            n = mem_store.source_counts([uid]).get(uid, 1)
            bits.append(f"merged into “{u['claim'][:80]}”, now {n} sources")
    if result.get("new"):
        bits.append(f"{len(result['new'])} new idea unit{'s' if len(result['new']) > 1 else ''}")
    if result.get("conflicts"):
        bits.append(f"{len(result['conflicts'])} conflict{'s' if len(result['conflicts']) > 1 else ''} with what you saved before")
    qn = [names[q] for q in result.get("questions") or [] if q in names]
    if qn:
        bits.append("bears on " + ", ".join(qn))
    text = "; ".join(bits)
    return text[:1].upper() + text[1:] + "."

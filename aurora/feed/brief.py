"""The briefing: what came in since the last one, chosen and written up by a model, easy to read.

New papers from arXiv (the person's categories), HF Daily Papers and the X pages the browser extension reads
are one pool; a paper seen in several places is one item with its signals (its category, HF upvotes, who posted
about it on X).  What the person works on and cares about (their topics and keywords, their projects, what they
saved in the notebook and the feed, what they marked "not interested") is the yardstick.

A cheap score trims the pool to a few dozen; a model then picks what is worth reading today, as many or as few as
deserve it (none is an answer), groups them under headings of its own (a project, what is trending, what people
are discussing) and writes for each a plain sentence on what it does and one on why it is worth this person's
time.  Every item keeps its links (arXiv, HF, the posts on X, the item in the console) for diving in, and is
numbered, so "read 2" in a chat app works.  Without a model, the most talked-about items are listed as they are.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from typing import Any

from aurora.feed import rank as rk
from aurora.feed.store import feed_store

logger = logging.getLogger(__name__)

POOL = 45                 # items the model is shown
MODEL = "anthropic/claude-sonnet-5.5"   # writes the briefing (digest.model): a few cents a briefing
SHOWN_SUMMARY = 420       # characters of each abstract it reads

_PROMPT = """You write a short research briefing for one researcher. Pick, from the new items below, what is worth
their time today, and write it up so it reads in a minute.

What they work on and care about:
{interests}

Things they marked "not interested" lately (leave similar things out):
{dislikes}

Each item shows where it was seen: "arXiv <category>", "HF <n> upvotes" (Hugging Face Daily Papers, the community's
picks), "X:" with who posted about it and what they said.

Choose as many or as few as deserve it (none is a fine answer; more than ten is too many). Include:
- what bears on their work (their projects, topics, what they saved);
- what is clearly important or widely discussed in their field even if it is not about their projects (many HF
  upvotes, several people on X), when a researcher in their field would regret missing it.
Leave out the merely popular outside their field, near-duplicates and incremental work.

Group the picks under 1 to 4 short headings of your own (for example a project's name, "Trending", "What people
are discussing"). For each pick write:
- "gist": what it does and finds, in plain words, one sentence (under 35 words), no hype, no "this paper";
- "why": why it is worth this person's time, concretely (what it means for their project, or that it is widely
  discussed and by whom), one sentence (under 30 words).
Start with "headline": one sentence on what today's briefing comes down to.
Write in {language}. Keep paper titles, names and technical terms as they are.

Items:
{items}

Answer with JSON only:
{{"headline": "...", "sections": [{{"title": "...", "items": [{{"id": "...", "gist": "...", "why": "..."}}]}}]}}"""

WORDS = {
    "en": {"title": "Pippin briefing · {day}, {half}", "half": ("morning", "afternoon", "evening"),
           "read": "Reply “read 2” (or 「读 2」) to keep one and read it with Pippin.", "upvotes": "HF {n} upvotes",
           "x": "on X: {who}", "more": "and {n} more", "open": "In Pippin", "language": "English",
           "fallback": "What came in, most talked about first (no model was available to choose)."},
    "zh": {"title": "Pippin 简报 · {day} {half}", "half": ("上午", "下午", "晚上"),
           "read": "回复「读 2」可以把第 2 篇存下来，并让 Pippin 陪你读。", "upvotes": "HF {n} 赞",
           "x": "X 上：{who}", "more": "等 {n} 人", "open": "在 Pippin 里看", "language": "Simplified Chinese",
           "fallback": "今天新进来的内容，按讨论热度排列（没有可用的模型来挑选）。"},
}


def _day(lang: str) -> str:
    now = datetime.now()
    return f"{now.month}月{now.day}日" if lang == "zh" else now.strftime("%a %d %b")


def _half(lang: str) -> str:
    h = datetime.now().hour
    return WORDS[lang]["half"][0 if h < 12 else 1 if h < 18 else 2]


def interests(user: Any, cfg: dict[str, Any]) -> str:
    """What the person works on and cares about, as the model reads it."""
    from aurora.feed.service import project_profiles
    lines = []
    if cfg.get("topics"):
        lines.append("arXiv categories they follow: " + ", ".join(cfg["topics"]))
    if cfg.get("keywords"):
        lines.append("Their keywords: " + ", ".join(cfg["keywords"]))
    for p in project_profiles(user)[:8]:
        lines.append(f"Project “{p['name']}”: {(p['objective'] or '')[:400]}"
                     + (f" (papers in it: {'; '.join(p['papers'][-5:])[:400]})" if p.get("papers") else ""))
    try:
        from aurora.memory.store import mem_store
        saved = list(dict.fromkeys(s["title"] for s in mem_store.sources(user.user_id, 30) if s.get("title")))[:15]
        if saved:
            lines.append("Lately saved in their notebook: " + "; ".join(saved)[:900])
    except Exception:
        pass
    kept = [feed_store.item(i) for i in list(feed_store.saves(user.user_id))[:10]]
    kept_titles = list(dict.fromkeys(k["title"] for k in kept if k))
    if kept_titles:
        lines.append("Lately kept from the feed: " + "; ".join(kept_titles)[:700])
    return "\n".join(lines) or "(nothing known yet: judge by what a researcher following these sources would want)"


def signals_line(it: dict[str, Any], lang: str) -> str:
    w = WORDS[lang]
    sig = it.get("signals") or {}
    parts = []
    if sig.get("arxiv"):
        parts.append(sig["arxiv"] if str(sig["arxiv"]).startswith("arXiv") else f"arXiv {sig['arxiv']}")
    if sig.get("hf"):
        parts.append(w["upvotes"].format(n=sig["hf"]))
    who = list(dict.fromkeys(m.get("who") or "" for m in sig.get("x") or [] if m.get("who")))
    if who:
        names = "、".join(who[:3]) if lang == "zh" else ", ".join(who[:3])
        parts.append(w["x"].format(who=names + (f" {w['more'].format(n=len(who) - 3)}" if len(who) > 3 else "")))
    if it.get("kind") == "post" and not parts:
        parts.append(it.get("source") or "X")
    return " · ".join(parts)


def links(it: dict[str, Any], base: str) -> list[str]:
    """Where to dive in: the paper, its HF page, the first posts about it, the item in the console."""
    out = []
    if it.get("url"):
        out.append(it["url"])
    aid = it["ukey"][6:] if it.get("ukey", "").startswith("arxiv:") else ""
    sig = it.get("signals") or {}
    if aid and sig.get("hf"):
        out.append(f"https://huggingface.co/papers/{aid}")
    out += [m["url"] for m in (sig.get("x") or [])[:2] if m.get("url") and m["url"] not in out]
    if base:
        out.append(f"{base}/?feed={it['item_id']}")
    return out


def score(it: dict[str, Any], lex: rk.Lexical | None) -> float:
    sig = it.get("signals") or {}
    s = 0.0
    if lex is not None:
        s += max((x for x, _ in lex.score(it)), default=0.0)
    s += 0.6 * math.log1p(int(sig.get("hf") or 0))
    s += 1.2 * len({m.get("who") for m in sig.get("x") or []})
    if it.get("kind") == "post" and not sig:
        s -= 0.5                     # a post that names no paper needs to be about their work
    return s


def pool(user: Any, cfg: dict[str, Any], since: float) -> list[dict[str, Any]]:
    """The new items, trimmed to the ones worth showing the model."""
    rows = [r for r in feed_store.news(user.user_id, since) if r["kind"] in ("paper", "post")]
    if len(rows) <= POOL:
        return rows
    text = interests(user, cfg)
    dislikes = feed_store.items(user.user_id, time.time() - 60 * 86400, hidden=True)[:30]
    lex = rk.Lexical([{"campaign_id": "me", "name": "me", "text": text}], rows, dislikes)
    ranked = sorted(rows, key=lambda r: -score(r, lex))
    keep = ranked[:POOL - 10]
    keep += [r for r in sorted(rows, key=lambda r: -int((r.get("signals") or {}).get("hf") or 0))[:10] if r not in keep]
    return keep[:POOL]


def _item_line(it: dict[str, Any], lang: str) -> str:
    sig = signals_line(it, "en")
    said = "; ".join(f"{m.get('who')}: {m.get('text', '')[:160]}" for m in (it.get("signals") or {}).get("x", [])[:3])
    return (f"- id {it['item_id']} [{it['kind']}] {it['title']}\n  seen: {sig or it.get('source', '')}\n"
            f"  {(it.get('summary') or '')[:SHOWN_SUMMARY]}" + (f"\n  said on X: {said}" if said else ""))


async def choose(user: Any, cfg: dict[str, Any], items: list[dict[str, Any]], lang: str) -> dict[str, Any] | None:
    """The model's briefing over *items*: {headline, sections: [{title, items: [{id, gist, why}]}]}; None without
    a model or when it failed."""
    from aurora.feed.service import _access_for
    access = _access_for(user)
    if not getattr(access, "api_key", ""):
        return None
    import dataclasses

    from aurora.brainstorm.agents import _client
    access = dataclasses.replace(access, model=(cfg.get("digest") or {}).get("model") or MODEL)
    dislikes = feed_store.items(user.user_id, time.time() - 60 * 86400, hidden=True)[:12]
    prompt = _PROMPT.format(interests=interests(user, cfg), language=WORDS[lang]["language"],
                            dislikes="\n".join(f"- {d['title']}" for d in dislikes) or "(none)",
                            items="\n".join(_item_line(it, lang) for it in items))
    try:
        r = await _client(access).chat.completions.create(
            model=access.model, messages=[{"role": "user", "content": prompt}], max_tokens=4000, temperature=0.3)
        data = rk.parse_json(r.choices[0].message.content or "")
    except Exception:
        logger.warning("the briefing: the model call failed", exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    known = {it["item_id"]: it for it in items}
    sections = []
    for sec in data.get("sections") or []:
        picks = []
        for p in sec.get("items") or []:
            it = known.get(str(p.get("id")))
            if it is None:
                continue
            if not _sound(str(p.get("gist") or "")):     # a garbled sentence: the abstract's start instead
                p = {**p, "gist": _first_sentence(it.get("summary") or "")}
            if not _sound(str(p.get("why") or "")):
                p = {**p, "why": ""}
            picks.append(p)
        if picks:
            sections.append({"title": str(sec.get("title") or "").strip(), "items": picks})
    return {"headline": str(data.get("headline") or "").strip(), "sections": sections}


_JUNK = ("占位", "placeholder", "lorem", "TODO", "N/A", "不要把", "translate")


def _sound(text: str) -> bool:
    """A sentence a reader can use: long enough, and not the model talking to itself."""
    t = text.strip()
    return len(t) >= 12 and not any(j.lower() in t.lower() for j in _JUNK)


def _first_sentence(text: str, n: int = 220) -> str:
    import re
    m = re.match(r"(.{20,}?[.!?。！？])(\s|$)", text.strip())
    return (m.group(1) if m else text.strip())[:n]


def _fallback(items: list[dict[str, Any]], lang: str) -> dict[str, Any]:
    top = sorted(items, key=lambda r: -score(r, None))[:6]
    return {"headline": WORDS[lang]["fallback"],
            "sections": [{"title": "", "items": [{"id": it["item_id"], "gist": _first_sentence(it.get("summary") or ""), "why": ""}
                                                 for it in top]}] if top else []}


def render(brief: dict[str, Any], items: dict[str, dict[str, Any]], lang: str, base: str,
           chat: bool) -> tuple[str, list[str]]:
    """The briefing as text (readable in a chat app), and the item IDs in their numbers."""
    w = WORDS[lang]
    lines = [brief["headline"], ""] if brief.get("headline") else []
    numbered: list[str] = []
    for sec in brief["sections"]:
        if sec.get("title"):
            lines.append(f"【{sec['title']}】" if lang == "zh" else f"— {sec['title']} —")
        for p in sec["items"]:
            it = items.get(str(p["id"]))
            if it is None or it["item_id"] in numbered:
                continue
            numbered.append(it["item_id"])
            lines.append(f"{len(numbered)}. {it['title']}")
            body = " ".join(x for x in (str(p.get("gist") or "").strip(), str(p.get("why") or "").strip()) if x)
            if body:
                lines.append(f"   {body}")
            sig = signals_line(it, lang)
            if sig:
                lines.append(f"   {sig}")
            for u in links(it, base):
                lines.append(f"   {u}")
        lines.append("")
    if chat and numbered:
        lines.append(w["read"])
    return "\n".join(lines).strip(), numbered


async def compose(user: Any, cfg: dict[str, Any], since: float, *, chat: bool, base: str) -> tuple[str, str, list[str]] | None:
    """(title, text, numbered item IDs) of the briefing since *since*; None when nothing is worth sending."""
    lang = (cfg.get("digest") or {}).get("lang") or "en"
    lang = lang if lang in WORDS else "en"
    items = pool(user, cfg, since)
    if not items:
        return None
    brief = await choose(user, cfg, items, lang)
    if brief is None:
        brief = _fallback(items, lang)
    if not brief["sections"]:
        return None
    text, numbered = render(brief, {it["item_id"]: it for it in items}, lang, base, chat)
    if not numbered:
        return None
    title = WORDS[lang]["title"].format(day=_day(lang), half=_half(lang))
    return title, text, numbered

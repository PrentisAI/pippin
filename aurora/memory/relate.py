"""A page the person is looking at, related to their work: which research questions a paper bears on and how, and
which of their saved claims it touches. Looking is not saving: nothing goes into the memory until they save it.

Also the daily digest: what came in (saved by hand or from what they follow), by research question.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aurora.memory import fetch, ingest
from aurora.memory import questions as qs
from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)
_cache: dict[tuple[str, str, str], dict[str, Any]] = {}


async def _paper(url: str) -> dict[str, Any] | None:
    from aurora.literature.check import Reference, check
    from aurora.literature.library import library
    from aurora.literature.records import clean_arxiv, clean_doi
    arxiv = clean_arxiv(url) if fetch.platform_of(url) == "arxiv" else ""
    doi = clean_doi(url)
    if not (arxiv or doi):
        return None
    try:
        result = await check(Reference(arxiv_id=arxiv, doi=doi))
    except Exception:
        logger.info("looking up %s failed", url, exc_info=True)
        return None
    if not result.verified or result.paper is None:
        return None
    stored = library.upsert_paper(result.paper)
    p = stored["paper"]
    return {"paper_id": stored["paper_id"], "key": stored["key"], "title": p.title, "byline": p.byline(),
            "year": p.year, "abstract": p.abstract or "", "doi": p.doi or "", "arxiv": p.arxiv_id or arxiv}


async def relate(access: Any, models: dict[str, str], owner: str, *, url: str, title: str = "",
                 text: str = "") -> dict[str, Any]:
    key = (owner, url or title, mem_store.version(owner))
    if key in _cache and time.time() - _cache[key]["at"] < 3600:
        return _cache[key]["out"]
    paper = await _paper(url) if url else None
    body = f"{paper['title']}\n\n{paper['abstract']}" if paper else f"{title}\n\n{text[:3000]}"
    questions = qs.current(owner)
    emb = models["embedding"]
    vec = (await ingest.embed(access, emb, [body]))[0]
    qv = await ingest.question_vectors(access, emb, questions)
    qrel = sorted(((ingest.relevance(ingest.cosine(vec, qv[q["question_id"]])), q) for q in questions
                   if q["question_id"] in qv), key=lambda x: -x[0])
    units = [u for u in mem_store.units(owner) if not u["sunk"]]
    uv = await ingest.unit_vectors(access, emb, units)
    near = sorted(((ingest.relevance(ingest.cosine(vec, uv[u["unit_id"]])), u) for u in units if u["unit_id"] in uv),
                  key=lambda x: -x[0])[:6]
    near = [(r, u) for r, u in near if r >= 0.35]
    bearing = [(r, q) for r, q in qrel if r >= 0.3][:3]
    judged: dict[str, Any] = {}
    if bearing or near:
        try:
            judged = await _judge(access.with_model(models["memory"]), owner, body, bearing, near)
        except Exception:
            logger.info("relating a page failed; giving the embedding ranking only", exc_info=True)
    jq = {x.get("id"): x for x in judged.get("questions") or [] if isinstance(x, dict)}
    ju = {x.get("id"): x for x in judged.get("units") or [] if isinstance(x, dict)}
    saved = mem_store.find_source(owner, url=url) if url else None
    if saved is None and paper:
        saved = next((s for s in mem_store.sources(owner, 500) if s["paper_id"] == paper["paper_id"] and s["state"] == "done"), None)
    out = {"paper": {k: v for k, v in (paper or {}).items() if k != "abstract"} or None, "saved": saved is not None,
           "questions": [{"question_id": q["question_id"], "label": q["label"], "text": q["text"], "relevance": round(r, 3),
                          "how": jq.get(q["question_id"], {}).get("how", ""),
                          "stance": jq.get(q["question_id"], {}).get("stance", "background")}
                         for r, q in bearing if jq.get(q["question_id"], {}).get("bears", True)],
           "units": [{"unit_id": u["unit_id"], "claim": u["claim"], "relevance": round(r, 3),
                      "relation": ju.get(u["unit_id"], {}).get("relation", "")}
                     for r, u in near if ju.get(u["unit_id"], {}).get("related", True)],
           "zotero": zotero_state(owner, paper["paper_id"]) if paper else None}
    out["quiet"] = not out["questions"] and not out["units"]
    _cache[key] = {"at": time.time(), "out": out}
    if len(_cache) > 300:
        _cache.pop(next(iter(_cache)))
    return out


async def _judge(access: Any, owner: str, body: str, bearing: list, near: list) -> dict[str, Any]:
    from aurora.memory.surface import language
    from aurora.review import llm
    qlines = "\n".join(f"[{q['question_id']}] {q['text']}" for _, q in bearing) or "(none)"
    ulines = "\n".join(f"[{u['unit_id']}] {u['claim']}" for _, u in near) or "(none)"
    out = await llm.ask(access, "memory_relate", f"""A researcher has this page open:
{body[:4000]}

Their research questions that may bear on it:
{qlines}

Claims they saved before that may bear on it:
{ulines}

For each question: does the page really bear on it ("bears"), how, in one concrete sentence in {language(owner)} \
("how"), and its stance toward the question (supports, challenges, adds, background). For each claim: is it really \
related ("related"), and the relation in one sentence in {language(owner)} ("relation": agrees, contradicts, \
qualifies, the same result elsewhere...). Be strict: say false when the link is only topical.

JSON: {{"questions": [{{"id": "q_...", "bears": true, "how": "...", "stance": "adds"}}], \
"units": [{{"id": "u_...", "related": true, "relation": "..."}}]}}""", max_tokens=2000)
    for x in (out.get("questions") or []) + (out.get("units") or []):
        if isinstance(x, dict):
            for k in ("how", "relation"):
                if k in x:
                    x[k] = llm.unlabel(x[k])
    return out


def zotero_state(owner: str, paper_id: str) -> dict[str, Any]:
    from aurora.auth import writes
    from aurora.literature.zotero import zotero_store
    link = zotero_store.link(owner)
    item = zotero_store.item_of_paper(owner, paper_id) if link else None
    return {"connected": link is not None, "in_zotero": item is not None, "level": writes.level(owner, "zotero.save")}


def bibtex_of(paper_id: str) -> str:
    from aurora.literature.bibtex import entry
    from aurora.literature.library import library
    e = library.paper(paper_id)
    return entry(e["key"], e["paper"]) if e else ""


# ---------------------------------------------------------------------------
# The daily digest


def digest(owner: str, hours: int = 24) -> dict[str, Any]:
    """What came in, by research question: merged into what they had, new claims, new conflicts."""
    since = time.time() - hours * 3600
    sources = [s for s in mem_store.sources(owner, 500) if s["created_at"] >= since and s["state"] == "done"]
    questions = qs.current(owner)
    claim = {u["unit_id"]: u for u in mem_store.units(owner)}
    counts = mem_store.source_counts(list(claim))
    groups = {q["question_id"]: {"question_id": q["question_id"], "label": q["label"], "text": q["text"],
                                 "merged": [], "new": [], "conflicts": []} for q in questions}
    other = {"question_id": "", "label": "", "text": "Not tied to a question", "merged": [], "new": [], "conflicts": []}
    for s in sources:
        r = s["result"]
        for uid in (r.get("merged") or []) + (r.get("new") or []):
            u = claim.get(uid)
            if u is None:
                continue
            bears = [b["question_id"] for b in mem_store.bears(unit_id=uid) if b["relevance"] >= ingest.BEARS]
            row = {"unit_id": uid, "claim": u["claim"], "sources": counts.get(uid, 1), "from": s["title"] or s["url"],
                   "platform": s["platform"], "via": s["via"]}
            kind = "merged" if uid in (r.get("merged") or []) else "new"
            for g in [groups[b] for b in bears if b in groups] or [other]:
                if not any(x["unit_id"] == uid for x in g[kind]):
                    g[kind].append(row)
        for a, b in r.get("conflicts") or []:
            if a in claim and b in claim:
                bears = [x["question_id"] for x in mem_store.bears(unit_id=a) if x["relevance"] >= ingest.BEARS]
                for g in [groups[q] for q in bears if q in groups] or [other]:
                    if not any({c["a_id"], c["b_id"]} == {a, b} for c in g["conflicts"]):
                        g["conflicts"].append({"a": claim[a]["claim"], "b": claim[b]["claim"], "a_id": a, "b_id": b})
    out = [g for g in [*groups.values(), other] if g["merged"] or g["new"] or g["conflicts"]]
    return {"hours": hours, "sources": len(sources), "from_feed": sum(s["via"] == "feed" for s in sources),
            "groups": out}

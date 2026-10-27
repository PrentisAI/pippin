"""The sentences of a paper you are reading that what you saved before argues with.

The units of your memory nearest to the paper (by meaning; not those you saved from this very paper) are given to
a model with the paper's text. It names the sentences of the paper they challenge or qualify, each copied exactly,
and which units do. A sentence is kept only when it is found again on its page, so the reader can mark it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aurora.memory import ingest
from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)
_cache: dict[tuple[str, str, str], dict[str, Any]] = {}


async def find(access: Any, models: dict[str, str], owner: str, paper_id: str, campaign_id: str = "") -> dict[str, Any]:
    from aurora.literature.annotations import find_page
    from aurora.literature.library import library
    from aurora.literature.reading import fetch_open_pdf, find_pdf, page_texts
    from aurora.review import llm
    key = (owner, paper_id, mem_store.version(owner))
    if key in _cache and time.time() - _cache[key]["at"] < 3600:
        return _cache[key]["out"]
    e = library.paper(paper_id)
    if e is None:
        raise ValueError("no such paper")
    path = find_pdf(paper_id, owner, campaign_id) or await fetch_open_pdf(paper_id)
    if path is None:
        return {"paper_id": paper_id, "items": [], "note": "There is no PDF of this paper to read."}
    pages = page_texts(path)
    own = {s["source_id"] for s in mem_store.sources(owner, 2000) if s["paper_id"] == paper_id}
    units = [u for u in mem_store.units(owner, include_sunk=False)
             if not any(ev["source_id"] in own for ev in mem_store.evidence(u["unit_id"]))]
    if not units:
        return {"paper_id": paper_id, "items": []}
    emb = models["embedding"]
    vec = (await ingest.embed(access, emb, [f"{e['paper'].title}\n{(e['paper'].abstract or '')[:2000]}"]))[0]
    uv = await ingest.unit_vectors(access, emb, units)
    near = [u for u in sorted(units, key=lambda u: -ingest.cosine(vec, uv.get(u["unit_id"], [0.0])))[:15]
            if ingest.relevance(ingest.cosine(vec, uv.get(u["unit_id"], [0.0]))) >= 0.3]
    if not near:
        out = {"paper_id": paper_id, "items": []}
        _cache[key] = {"at": time.time(), "out": out}
        return out
    text = ""
    for i, p in enumerate(pages[:14]):
        chunk = f"--- page {i + 1} ---\n{p}"
        if len(text) + len(chunk) > 40_000:
            break
        text += chunk + "\n"
    ulines = "\n".join(f"[{u['unit_id']}] {u['claim']} — {u['gist'][:250]}" for u in near)
    res = await llm.ask(access.with_model(models["memory"]), "memory_dissent", f"""A researcher is reading this paper. \
Claims they saved before, from other sources:
{ulines}

The paper:
{text}

Find the sentences of the paper that these claims challenge (they say the opposite, or that it does not hold in a \
setting the paper's claim covers) or qualify (they say it holds only under conditions the paper does not state). \
Only real disagreements about the same thing, at most eight. For each: the page, the paper's sentence copied EXACTLY \
(10 to 40 words), the ids of the claims, "challenges" or "qualifies", and one sentence why.

JSON: {{"items": [{{"page": 3, "quote": "...", "units": ["u_..."], "kind": "challenges", "why": "..."}}]}}""",
                        want="items", max_tokens=6000)
    by = {u["unit_id"]: u for u in near}
    items = []
    for x in res.get("items") or []:
        quote = " ".join(str(x.get("quote") or "").split())
        page = find_page(pages, quote)
        ids = [i for i in x.get("units") or [] if i in by]
        if page < 0 or not ids:
            continue
        items.append({"page": page, "quote": quote, "kind": "qualifies" if x.get("kind") == "qualifies" else "challenges",
                      "why": llm.unlabel(x.get("why", "")),
                      "units": [{"unit_id": i, "claim": by[i]["claim"],
                                 "sources": len({ev["source_id"] for ev in mem_store.evidence(i)})} for i in ids]})
    out = {"paper_id": paper_id, "items": sorted(items, key=lambda it: it["page"])}
    _cache[key] = {"at": time.time(), "out": out}
    return out

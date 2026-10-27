"""A citation for a sentence you are writing, from your own library: papers you saved into your memory or keep in
your Library, each with the passage that backs the sentence.

Candidates are found by meaning (embeddings of the units you saved from papers, and of your Library's papers),
then a model keeps those whose words really support the sentence and quotes them. A quote must be found again in
what it came from (the evidence you saved, or the paper's abstract). When nothing in your library supports it, it
says so, rather than reaching for a paper that only shares the topic.
"""

from __future__ import annotations

import logging
from typing import Any

from aurora.memory import ingest
from aurora.memory.fetch import find_quote
from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)


async def paper_vectors(access: Any, model: str, papers: list[dict[str, Any]]) -> dict[str, list[float]]:
    have = mem_store.vectors([f"p:{p['paper_id']}" for p in papers], model)
    missing = [p for p in papers if f"p:{p['paper_id']}" not in have]
    if missing:
        vecs = await ingest.embed(access, model, [f"{p['paper'].title}\n{(p['paper'].abstract or '')[:2000]}" for p in missing])
        for p, v in zip(missing, vecs, strict=False):
            mem_store.put_vector(f"p:{p['paper_id']}", model, v)
            have[f"p:{p['paper_id']}"] = v
    return {k[2:]: v for k, v in have.items()}


async def suggest(access: Any, models: dict[str, str], owner: str, sentence: str, context: str = "") -> dict[str, Any]:
    from aurora.literature.bibtex import entry
    from aurora.literature.library import library
    from aurora.review import llm
    emb = models["embedding"]
    vec = (await ingest.embed(access, emb, [sentence]))[0]
    # what you saved from papers: units with their words
    cands: dict[str, dict[str, Any]] = {}
    units = [u for u in mem_store.units(owner)]
    uv = await ingest.unit_vectors(access, emb, units)
    for u in sorted(units, key=lambda u: -ingest.cosine(vec, uv.get(u["unit_id"], [0.0])))[:15]:
        for e in mem_store.evidence(u["unit_id"]):
            if e["paper_id"]:
                c = cands.setdefault(e["paper_id"], {"paper_id": e["paper_id"], "texts": [], "from": "your memory"})
                c["texts"].append(e["quote"])
    # your Library's papers, by their abstracts
    items = [library.paper(i["paper_id"]) for i in library.items("", owner=owner)]
    items = [p for p in items if p and p["paper"].abstract]
    pv = await paper_vectors(access, emb, items)
    for p in sorted(items, key=lambda p: -ingest.cosine(vec, pv.get(p["paper_id"], [0.0])))[:10]:
        c = cands.setdefault(p["paper_id"], {"paper_id": p["paper_id"], "texts": [], "from": "your Library"})
        c["texts"].append(p["paper"].abstract)
    if not cands:
        return {"sentence": sentence, "candidates": [], "note": "Your memory and Library hold no papers yet."}
    rows = []
    for pid, c in list(cands.items())[:20]:
        e = library.paper(pid)
        if e:
            rows.append((pid, e, c))
    listing = "\n\n".join(f"[{pid}] {e['paper'].title} ({e['paper'].year or '?'})\n" + "\n".join(f"  > {t[:600]}" for t in c["texts"][:3])
                          for pid, e, c in rows)
    out = await llm.ask(access.with_model(models["memory"]), "memory_cite", f"""A researcher is writing this sentence \
and wants a citation for it:
"{sentence}"
{('Around it: ' + context[:1500]) if context else ''}

Papers from their own library, with passages:
{listing}

Which papers support the sentence (they state or show what the sentence claims, not merely the same topic)? For \
each, the passage that backs it, copied EXACTLY from the passages given, and how it supports it in a few words. At \
most four, the strongest first. None is a fine answer.

JSON: {{"cites": [{{"id": "...", "quote": "...", "how": "..."}}]}}""", want="cites", max_tokens=5000)
    by = {pid: (e, c) for pid, e, c in rows}
    found = []
    for x in out.get("cites") or []:
        pid = str(x.get("id") or "")
        if pid not in by:
            continue
        e, c = by[pid]
        quote = str(x.get("quote") or "")
        if not any(find_quote(t, quote) for t in c["texts"]):
            continue                # a passage the paper does not have is not support
        found.append({"paper_id": pid, "key": e["key"], "title": e["paper"].title, "byline": e["paper"].byline(),
                      "quote": quote, "how": llm.unlabel(x.get("how", "")), "from": c["from"],
                      "bibtex": entry(e["key"], e["paper"]), "doi": e["paper"].doi or ""})
    return {"sentence": sentence, "candidates": found,
            "note": "" if found else "Nothing in your memory or Library supports this sentence. Search for it, or soften it."}


_hints: dict[tuple[str, str, str], dict[str, Any]] = {}
HINT = 0.6          # relevance from which a paper may back the sentence (worth a quiet hint)


async def hint(access: Any, models: dict[str, str], owner: str, sentence: str) -> dict[str, Any]:
    """Whether the sentence you stopped at may be backed by papers you have (by meaning only: no model is asked), so
    the editor can offer a citation quietly. The real suggestion, with the passages, is ``suggest``."""
    from aurora.literature.library import library
    key = (owner, " ".join(sentence.split())[:600], mem_store.version(owner))
    if key in _hints:
        return _hints[key]
    emb = models["embedding"]
    vec = (await ingest.embed(access, emb, [sentence]))[0]
    found: dict[str, float] = {}
    units = mem_store.units(owner)
    uv = await ingest.unit_vectors(access, emb, units)
    for u in units:
        r = ingest.relevance(ingest.cosine(vec, uv.get(u["unit_id"], [0.0])))
        if r >= HINT:
            for e in mem_store.evidence(u["unit_id"]):
                if e["paper_id"]:
                    found[e["paper_id"]] = max(found.get(e["paper_id"], 0.0), r)
    items = [p for i in library.items("", owner=owner) if (p := library.paper(i["paper_id"])) and p["paper"].abstract]
    pv = await paper_vectors(access, emb, items)
    for p in items:
        r = ingest.relevance(ingest.cosine(vec, pv.get(p["paper_id"], [0.0])))
        if r >= HINT:
            found[p["paper_id"]] = max(found.get(p["paper_id"], 0.0), r)
    best = sorted(found, key=lambda k: -found[k])[:3]
    out = {"papers": len(found), "top": [e["paper"].title for pid in best if (e := library.paper(pid))]}
    _hints[key] = out
    if len(_hints) > 500:
        _hints.pop(next(iter(_hints)))
    return out

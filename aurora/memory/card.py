"""A paper read in the background, for Zotero: its methods, findings and limitations, waiting for you the next time
you open it, together with what your notebook makes of it (its claims, conflicts with what you saved, the questions
it bears on).

The Zotero plugin sends each paper you add (its metadata, and its PDF when Zotero has it). The paper is saved into
your notebook like anything else, and a card is written from its text. Each line of the card says what it rests on:
the paper itself, or the reader's inference.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from aurora.memory import ingest
from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mem_cards (
    owner      TEXT NOT NULL,
    paper_id   TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed
    card       TEXT NOT NULL DEFAULT '{}',
    source_id  TEXT NOT NULL DEFAULT '',
    error      TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (owner, paper_id)
);
"""


def _db() -> Any:
    mem_store._conn.executescript(_SCHEMA)
    return mem_store._conn


def get(owner: str, paper_id: str) -> dict[str, Any] | None:
    with mem_store._lock:
        r = _db().execute("SELECT * FROM mem_cards WHERE owner=? AND paper_id=?", (owner, paper_id)).fetchone()
    return {**dict(r), "card": json.loads(r["card"])} if r else None


def _put(owner: str, paper_id: str, **fields: Any) -> None:
    cols = {k: (json.dumps(v) if k == "card" else v) for k, v in fields.items()}
    with mem_store._lock:
        _db().execute("INSERT OR IGNORE INTO mem_cards (owner, paper_id, updated_at) VALUES (?,?,?)", (owner, paper_id, time.time()))
        if cols:
            _db().execute(f"UPDATE mem_cards SET {', '.join(f'{k}=?' for k in cols)}, updated_at=? WHERE owner=? AND paper_id=?",
                          (*cols.values(), time.time(), owner, paper_id))
        mem_store._conn.commit()


async def resolve(*, doi: str = "", arxiv: str = "", title: str = "") -> dict[str, Any] | None:
    """The paper an item is, verified in the indexes."""
    from aurora.literature.check import Reference, check
    from aurora.literature.library import library
    try:
        r = await check(Reference(doi=doi, arxiv_id=arxiv, title=title))
    except Exception:
        logger.info("looking up a Zotero item failed", exc_info=True)
        return None
    if not r.verified or r.paper is None:
        return None
    return library.upsert_paper(r.paper)


def start(access: Any, models: dict[str, str], owner: str, entry: dict[str, Any], pdf_text: str = "") -> dict[str, Any]:
    have = get(owner, entry["paper_id"])
    if have and have["state"] in ("pending", "done"):
        return have
    _put(owner, entry["paper_id"], state="pending", error="")
    task = asyncio.create_task(_read(access, models, owner, entry, pdf_text))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return get(owner, entry["paper_id"])  # type: ignore[return-value]


async def _read(access: Any, models: dict[str, str], owner: str, entry: dict[str, Any], pdf_text: str) -> None:
    from aurora.review import llm
    p = entry["paper"]
    try:
        url = f"https://doi.org/{p.doi}" if p.doi else f"https://arxiv.org/abs/{p.arxiv_id}" if p.arxiv_id else ""
        src = mem_store.find_source(owner, url=url) if url else None
        if src is None:
            src = mem_store.add_source(owner, platform="paper", url=url, title=p.title, via="zotero")
            await ingest.process(access, models, src["source_id"], text=pdf_text)
        text = mem_store.snapshot(src["source_id"]) or f"{p.title}\n\n{p.abstract or ''}"
        body = text if len(text) < 40_000 else text[:30_000] + "\n…\n" + text[-8_000:]
        out = await llm.ask(access.with_model(models["memory"]), "memory_card", f"""Read this paper for a researcher \
who just added it to their library, so they need not read it now:

{p.title}
{body}

Give its methods (what was done, concretely: data, model, setup), its findings (results with numbers), and its \
limitations (as the authors state them, and ones you infer: say which). Two to five points each, one sentence each, \
each labelled "paper" (the paper states it) or "inf" (your inference). Also "one_line": what the paper is, in one \
sentence.

JSON: {{"one_line": "...", "methods": [{{"text": "...", "label": "paper"}}], "findings": [...], "limitations": [...]}}""",
                            max_tokens=6000)
        card = {"one_line": llm.unlabel(out.get("one_line", ""))}
        for k in ("methods", "findings", "limitations"):
            card[k] = llm.paragraphs(out.get(k) or [])[:6]
        card["read_from"] = "pdf" if len(text) > 3000 else "abstract"
        _put(owner, entry["paper_id"], state="done", card=card, source_id=src["source_id"])
    except Exception as exc:
        logger.info("reading %s for Zotero failed", entry["paper_id"], exc_info=True)
        _put(owner, entry["paper_id"], state="failed", error=str(exc)[:300])


def view(owner: str, entry: dict[str, Any]) -> dict[str, Any]:
    """The card and what the notebook makes of the paper: its claims, conflicts, the questions it bears on."""
    from aurora.memory import questions as qs
    c = get(owner, entry["paper_id"]) or {"state": "none", "card": {}, "source_id": ""}
    units, conflicts, bears = [], [], {}
    src = mem_store.source(c["source_id"]) if c.get("source_id") else None
    labels = {q["question_id"]: q["label"] for q in qs.current(owner)}
    for uid in (src or {}).get("result", {}).get("units") or []:
        u = mem_store.unit(uid)
        if u is None:
            continue
        n = len({e["source_id"] for e in mem_store.evidence(uid)})
        units.append({"unit_id": uid, "claim": u["claim"], "sources": n})
        for lk in mem_store.links([uid]):
            if lk["rel"] == "contradicts":
                other = mem_store.unit(lk["b"] if lk["a"] == uid else lk["a"])
                if other:
                    conflicts.append({"unit_id": uid, "claim": u["claim"], "other": other["claim"]})
        for b in mem_store.bears(unit_id=uid):
            if b["relevance"] >= ingest.BEARS and b["question_id"] in labels:
                bears[labels[b["question_id"]]] = max(bears.get(labels[b["question_id"]], 0), b["relevance"])
    return {"paper_id": entry["paper_id"], "key": entry["key"], "title": entry["paper"].title, "state": c["state"],
            "error": c.get("error", ""), "card": c["card"], "units": units, "conflicts": conflicts,
            "questions": sorted(bears, key=lambda k: -bears[k])}

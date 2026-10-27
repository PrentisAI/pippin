"""The shared memory, for every entry point (the console, the browser extension, the desktop app, plugins).

Each person's memory is their own. An entry point saves things into it (``/capture``), says what the person is
doing now (``/context``), and shows what surfaces for that (``/surface``); the rest is the memory's units, sources,
questions, marks, the questions it asks the person, and the weekly report.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.auth.guard import current_user
from aurora.memory import ingest, surface
from aurora.memory import questions as qs
from aurora.memory.store import mem_store

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _uid() -> str:
    return current_user().user_id


def _access() -> tuple[Any, dict[str, str]]:
    from aurora.api.routes.brainstorm import _access as bs_access
    from aurora.api.routes.review import _cfg
    from aurora.review import llm
    a = bs_access()
    if not a.api_key:
        raise HTTPException(400, f"No API key is set: {a.missing}")
    m = _cfg().models
    return (llm.Access(api_key=a.api_key, base_url=a.base_url, model=m.memory or a.model, missing=a.missing),
            {"memory": m.memory or a.model, "embedding": m.embedding})


def _unit(unit_id: str) -> dict[str, Any]:
    u = mem_store.unit(unit_id)
    if u is None or u["owner"] != _uid():
        raise HTTPException(404, "No such idea unit")
    return u


# ---------------------------------------------------------------------------
# Saving, and what saving did


class Capture(BaseModel):
    url: str = ""
    text: str = ""           # the page's text as the entry point sees it (behind a login, too)
    html: str = ""
    title: str = ""
    selection: str = ""      # what the person highlighted
    via: str = "manual"      # manual | extension | orb | feed | obsidian | voice


@router.post("/capture")
async def capture(body: Capture) -> dict[str, Any]:
    """Save a link, a page, a selection or a note; it is read and merged in the background."""
    if not (body.url.strip() or body.text.strip() or body.html.strip()):
        raise HTTPException(400, "Give a link, or the text to save")
    access, models = _access()
    return ingest.start(access, models, _uid(), **body.model_dump())


def _source_view(s: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in s.items() if k != "digest"}
    if s["state"] in ("done", "failed"):
        out["summary"] = ingest.summary({**s["result"], **({"error": s["error"]} if s["state"] == "failed" else {})},
                                        s["owner"])
    return out


@router.get("/sources")
async def sources(limit: int = 50) -> list[dict[str, Any]]:
    return [_source_view(s) for s in mem_store.sources(_uid(), max(1, min(500, limit)))]


@router.get("/sources/{source_id}")
async def source(source_id: str) -> dict[str, Any]:
    s = mem_store.source(source_id)
    if s is None or s["owner"] != _uid():
        raise HTTPException(404, "No such source")
    return _source_view(s)


@router.get("/sources/{source_id}/snapshot")
async def snapshot(source_id: str) -> dict[str, Any]:
    s = mem_store.source(source_id)
    if s is None or s["owner"] != _uid():
        raise HTTPException(404, "No such source")
    return {"source_id": source_id, "title": s["title"], "url": s["url"], "text": mem_store.snapshot(source_id)}


# ---------------------------------------------------------------------------
# What the person is doing, and what surfaces for it


class Context(BaseModel):
    app: str = ""
    title: str = ""
    url: str = ""
    paper_id: str = ""
    page: str = ""
    selection: str = ""
    text: str = ""
    via: str = ""


@router.post("/context")
async def put_context(body: Context) -> dict[str, Any]:
    """An entry point says what the person is doing now (kept in memory for a few hours, never stored)."""
    return surface.report_context(_uid(), body.model_dump())


@router.get("/contexts")
async def contexts() -> dict[str, Any]:
    """The contexts to choose from: what is open now (if an entry point said), and the research questions."""
    uid = _uid()
    cur = surface.current_context(uid)
    return {"current": {"label": cur["title"] or cur["url"], "app": cur["app"], "at": cur["at"]} if cur else None,
            "questions": [{"question_id": q["question_id"], "label": q["label"], "text": q["text"], "origin": q["origin"]}
                          for q in qs.current(uid)]}


@router.get("/surface")
async def get_surface(ctx: str = "current") -> dict[str, Any]:
    """The three layers for a context: what surfaces, what is absorbed, what sinks."""
    uid = _uid()
    try:
        c = surface.resolve(uid, ctx)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    access, models = _access()
    layers = await surface.rank(access, models, uid, c)
    return {"context": {k: c[k] for k in ("key", "kind", "label", "chip", "sub")}, **layers}


@router.get("/search")
async def search(q: str) -> list[dict[str, Any]]:
    """Units by meaning (and by their words), sunk ones too."""
    uid = _uid()
    q = q.strip()
    if not q:
        return []
    access, models = _access()
    units = mem_store.units(uid)
    vec = (await ingest.embed(access, models["embedding"], [q]))[0]
    uvecs = await ingest.unit_vectors(access, models["embedding"], units)
    scored = []
    for u in units:
        s = ingest.cosine(vec, uvecs.get(u["unit_id"], [])) if u["unit_id"] in uvecs else 0.0
        if q.lower() in (u["claim"] + u["gist"]).lower():
            s += 0.5
        scored.append((s, u))
    scored.sort(key=lambda x: -x[0])
    counts = mem_store.source_counts([u["unit_id"] for _, u in scored[:20]])
    return [{"unit_id": u["unit_id"], "claim": u["claim"], "sunk": bool(u["sunk"]), "score": round(s, 3),
             "sources": counts.get(u["unit_id"], 0)} for s, u in scored[:20] if s > 0.2]


# ---------------------------------------------------------------------------
# A unit up close, and what the person does to it


@router.get("/units/{unit_id}")
async def unit(unit_id: str) -> dict[str, Any]:
    u = _unit(unit_id)
    labels = {q["question_id"]: q for q in qs.current(u["owner"])}
    links = mem_store.links([unit_id])
    others = {x: mem_store.unit(x) for lk in links for x in (lk["a"], lk["b"]) if x != unit_id}
    marks = mem_store.marks([unit_id])
    moved = False
    for m in marks:
        moved = True if m["kind"] == "moved" else False if m["kind"] == "unmoved" else moved
    return {**u, "sunk": bool(u["sunk"]), "moved": moved,
            "evidence": [{k: e[k] for k in ("evidence_id", "source_id", "quote", "stance", "platform", "url", "title",
                                            "author", "lang", "paper_id", "published", "via", "created_at")}
                         for e in mem_store.evidence(unit_id)],
            "links": [{"rel": lk["rel"], "note": lk["note"], "unit_id": o, "claim": others[o]["claim"],
                       "direction": "out" if lk["a"] == unit_id else "in"}
                      for lk in links if (o := lk["b"] if lk["a"] == unit_id else lk["a"]) and others.get(o)],
            "questions": [{"question_id": b["question_id"], "label": labels[b["question_id"]]["label"],
                           "text": labels[b["question_id"]]["text"], "relevance": b["relevance"]}
                          for b in mem_store.bears(unit_id=unit_id) if b["question_id"] in labels and b["relevance"] >= 0.3],
            "marks": marks,
            "answers": [a for a in mem_store.asks(u["owner"]) if unit_id in a["basis"] and a["answer"]]}


class UnitChange(BaseModel):
    claim: str | None = None
    gist: str | None = None


@router.patch("/units/{unit_id}")
async def change_unit(unit_id: str, body: UnitChange) -> dict[str, Any]:
    u = _unit(unit_id)
    fields = {k: v.strip() for k, v in body.model_dump().items() if v is not None and v.strip()}
    if fields:
        mem_store.update_unit(unit_id, **fields)
        mem_store.mark(unit_id, u["owner"], "edited", {"before": {k: u[k] for k in fields}})
    return await unit(unit_id)


class Mark(BaseModel):
    kind: str            # sunk | raised | moved | unmoved


@router.post("/units/{unit_id}/mark")
async def mark(unit_id: str, body: Mark) -> dict[str, Any]:
    """Let it sink or raise it again; say it changed your view (or take that back)."""
    u = _unit(unit_id)
    if body.kind not in ("sunk", "raised", "moved", "unmoved"):
        raise HTTPException(400, "kind: sunk | raised | moved | unmoved")
    if body.kind in ("sunk", "raised"):
        mem_store.update_unit(unit_id, sunk=int(body.kind == "sunk"))
    mem_store.mark(unit_id, u["owner"], body.kind)
    return await unit(unit_id)


class MergeBody(BaseModel):
    into: str


@router.post("/units/{unit_id}/merge")
async def merge(unit_id: str, body: MergeBody) -> dict[str, Any]:
    """Two units make the same claim (the memory missed it): one unit, all the sources."""
    u, target = _unit(unit_id), _unit(body.into)
    if u["unit_id"] == target["unit_id"]:
        raise HTTPException(400, "It is the same unit")
    mem_store.merge_units(u["unit_id"], target["unit_id"])
    mem_store.mark(target["unit_id"], target["owner"], "merged", {"from": u["unit_id"], "claim": u["claim"]})
    return await unit(target["unit_id"])


@router.post("/evidence/{evidence_id}/split")
async def split(evidence_id: str) -> dict[str, Any]:
    """A source was merged into the wrong unit: it becomes a unit of its own (and the memory remembers)."""
    e = mem_store.evidence_item(evidence_id)
    u = _unit(e["unit_id"]) if e else None
    if e is None or u is None:
        raise HTTPException(404, "No such evidence")
    if len(mem_store.evidence(u["unit_id"])) < 2:
        raise HTTPException(400, "It is the unit's only source")
    s = mem_store.source(e["source_id"]) or {}
    new = mem_store.add_unit(u["owner"], e["quote"][:300], f"Split from “{u['claim'][:120]}”. From {s.get('title', '')}.",
                             u["kind"])
    mem_store.move_evidence(evidence_id, new["unit_id"])
    mem_store.mark(u["unit_id"], u["owner"], "split", {"evidence_id": evidence_id, "into": new["unit_id"]})
    return await unit(new["unit_id"])


# ---------------------------------------------------------------------------
# Asking instead of summarising


class AskBody(BaseModel):
    ctx: str = "current"


@router.post("/ask")
async def ask(body: AskBody) -> dict[str, Any]:
    uid = _uid()
    try:
        c = surface.resolve(uid, body.ctx)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    access, models = _access()
    a = await surface.ask(access, models, uid, c)
    if a is None:
        raise HTTPException(404, "Nothing in your memory bears on this yet")
    return a


class Answer(BaseModel):
    text: str


@router.post("/asks/{ask_id}/answer")
async def answer(ask_id: str, body: Answer) -> dict[str, Any]:
    try:
        return surface.answer(_uid(), ask_id, body.text)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


# ---------------------------------------------------------------------------
# Questions, the week, settings


class QuestionBody(BaseModel):
    text: str


@router.get("/questions")
async def questions() -> list[dict[str, Any]]:
    return qs.current(_uid())


@router.post("/questions")
async def add_question(body: QuestionBody) -> dict[str, Any]:
    if not body.text.strip():
        raise HTTPException(400, "The question is empty")
    q = mem_store.add_question(_uid(), body.text)
    return next(x for x in qs.current(_uid()) if x["question_id"] == q["question_id"])


@router.delete("/questions/{question_id}")
async def archive_question(question_id: str) -> dict[str, Any]:
    q = mem_store.question(question_id)
    if q is None or q["owner"] != _uid():
        raise HTTPException(404, "No such question")
    mem_store.update_question(question_id, archived=1)
    return {"ok": True}


@router.get("/questions/{question_id}/synthesis")
async def question_synthesis(question_id: str) -> dict[str, Any]:
    access, models = _access()
    try:
        return await surface.synthesis(access, models, _uid(), question_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get("/report")
async def report(days: int = 7) -> dict[str, Any]:
    return surface.report(_uid(), max(1, min(60, days)))


class Settings(BaseModel):
    language: str        # auto | zh | en


@router.get("/settings")
async def settings() -> dict[str, Any]:
    from aurora.auth.store import auth_store
    return {"language": auth_store.get_pref(_uid(), "memory_language", "auto")}


@router.put("/settings")
async def put_settings(body: Settings) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    if body.language not in ("auto", "zh", "en"):
        raise HTTPException(400, "language: auto | zh | en")
    auth_store.set_pref(_uid(), "memory_language", body.language)
    return {"language": body.language}

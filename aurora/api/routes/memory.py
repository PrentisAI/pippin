"""The shared memory, for every entry point (the console, the browser extension, the desktop app, plugins).

Each person's memory is their own. An entry point saves things into it (``/capture``), says what the person is
doing now (``/context``), and shows what surfaces for that (``/surface``); the rest is the memory's units, sources,
questions, marks, the questions it asks the person, and the weekly report.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aurora.auth.guard import current_user
from aurora.memory import follow, ingest, relate, surface
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


@router.post("/capture/file")
async def capture_file(request: Request, name: str = "file", via: str = "manual") -> dict[str, Any]:
    """Save a file dropped on an entry point: a PDF (recognised as a paper when it is one) or a text note."""
    data = await request.body()
    if len(data) > 80 * 1024 * 1024:
        raise HTTPException(400, "The file is larger than 80 MB")
    access, models = _access()
    if data.startswith(b"%PDF"):
        from aurora.literature.pdfs import _text, identify
        ref = identify(data, name)
        text, _ = _text(data, pages=80)
        url = f"https://arxiv.org/abs/{ref.arxiv_id}" if ref.arxiv_id else f"https://doi.org/{ref.doi}" if ref.doi else ""
        return ingest.start(access, models, _uid(), url=url, text=text, title=ref.title or name, via=via)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "Drop a PDF or a text file") from None
    return ingest.start(access, models, _uid(), text=text, title=name, via=via)


def _source_view(s: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in s.items() if k != "digest"}
    if s["state"] in ("done", "failed"):
        out["summary"] = ingest.summary({**s["result"], **({"error": s["error"]} if s["state"] == "failed" else {})},
                                        s["owner"])
    return out


@router.get("/sources")
async def sources(limit: int = 50) -> list[dict[str, Any]]:
    # forgotten ones, and the old versions of edited notes, are not listed
    return [_source_view(s) for s in mem_store.sources(_uid(), max(1, min(500, limit))) if s["state"] != "retired"]


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
    """An entry point says what the person is doing now (kept in memory for a few hours, never stored). In a PDF
    reader other than AURORA's, the paper is told from the file name when it can be."""
    ctx = body.model_dump()
    if ctx["app"] in surface.PDF_READERS and not ctx["paper_id"] and ctx["title"]:
        try:
            ctx = await surface.infer_paper(_uid(), ctx)
        except Exception:
            pass                      # the file name only, then
    return surface.report_context(_uid(), ctx)


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
    mem_store.mark(u["unit_id"], u["owner"], "split", {"evidence_id": evidence_id, "into": new["unit_id"],
                                                      "quote": e["quote"][:300]})
    mem_store.mark(new["unit_id"], u["owner"], "split_from", {"unit_id": u["unit_id"]})
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


# ---------------------------------------------------------------------------
# A page you are looking at, and saving a paper to Zotero


@router.get("/relate")
async def relate_page(url: str = "", title: str = "") -> dict[str, Any]:
    """How the page you have open relates to your questions and to what you saved (looking is not saving)."""
    if not (url.strip() or title.strip()):
        raise HTTPException(400, "Give the page's address or title")
    access, models = _access()
    return await relate.relate(access, models, _uid(), url=url.strip(), title=title.strip())


class ZoteroSave(BaseModel):
    paper_id: str


@router.post("/zotero")
async def save_to_zotero(body: ZoteroSave) -> dict[str, Any]:
    """Save a paper to your Zotero, if you allowed it; otherwise its BibTeX and DOI, to import yourself."""
    from aurora.auth import writes
    from aurora.literature.library import library
    uid = _uid()
    e = library.paper(body.paper_id)
    if e is None:
        raise HTTPException(404, "No such paper")
    copy = {"bibtex": relate.bibtex_of(e["paper_id"]), "doi": e["paper"].doi or ""}
    if not writes.allowed(uid, "zotero.save"):
        return {"saved": False, "reason": "Writing to Zotero is off. Turn on “Save papers and their PDFs to Zotero” "
                                           "(account button → What AURORA may write), or import it yourself:", **copy}
    from aurora.literature import zotero
    from aurora.literature.reading import fetch_open_pdf
    try:
        pdf = await fetch_open_pdf(e["paper_id"])
        await zotero.add_paper(uid, current_user().name, e["paper_id"], "", pdf)
    except zotero.ZoteroError as exc:
        return {"saved": False, "reason": str(exc), **copy}
    return {"saved": True, **copy}


# ---------------------------------------------------------------------------
# Following people, topics and threads; the daily digest


class FollowBody(BaseModel):
    url: str
    label: str = ""
    every_min: int = 60


@router.get("/follows")
async def follows() -> list[dict[str, Any]]:
    return [{k: v for k, v in f.items() if k != "seen"} | {"seen": len(f["seen"])} for f in mem_store.follows(_uid())]


@router.post("/follows")
async def add_follow(body: FollowBody) -> dict[str, Any]:
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Give the address of the page to follow (a person, a topic, a subreddit, a feed)")
    platform, via = follow.kind_of(url)
    f = mem_store.add_follow(_uid(), platform, url, body.label.strip() or url, via, body.every_min)
    return {k: v for k, v in f.items() if k != "seen"} | {"seen": 0}


class FollowChange(BaseModel):
    paused: bool | None = None
    every_min: int | None = None
    label: str | None = None


def _follow(follow_id: str) -> dict[str, Any]:
    f = mem_store.follow(follow_id)
    if f is None or f["owner"] != _uid():
        raise HTTPException(404, "No such follow")
    return f


@router.patch("/follows/{follow_id}")
async def change_follow(follow_id: str, body: FollowChange) -> dict[str, Any]:
    _follow(follow_id)
    fields = {k: (int(v) if k == "paused" else max(15, v) if k == "every_min" else v)
              for k, v in body.model_dump().items() if v is not None}
    mem_store.update_follow(follow_id, **fields)
    f = mem_store.follow(follow_id)
    return {k: v for k, v in f.items() if k != "seen"} | {"seen": len(f["seen"])}  # type: ignore[union-attr]


@router.delete("/follows/{follow_id}")
async def delete_follow(follow_id: str) -> dict[str, Any]:
    _follow(follow_id)
    mem_store.delete_follow(follow_id)
    return {"ok": True}


@router.get("/follows/due")
async def follows_due(via: str = "extension") -> list[dict[str, Any]]:
    """The follows an entry point should read now (the extension reads those behind a login)."""
    return [{"follow_id": f["follow_id"], "platform": f["platform"], "url": f["url"], "label": f["label"],
             "seen": f["seen"][-200:]} for f in follow.due(_uid(), via)]


class FollowItems(BaseModel):
    items: list[dict[str, Any]] = []
    error: str = ""


@router.post("/follows/{follow_id}/items")
async def follow_items(follow_id: str, body: FollowItems) -> dict[str, Any]:
    """What a followed page showed: the posts not seen before are saved into the memory."""
    f = _follow(follow_id)
    access, models = _access()
    return {"taken": follow.take(access, models, f, body.items[:100], error=body.error)}


@router.get("/digest")
async def digest(hours: int = 24) -> dict[str, Any]:
    return relate.digest(_uid(), max(1, min(168, hours)))


# ---------------------------------------------------------------------------
# Interruptions


@router.get("/alerts")
async def alerts(unseen: bool = False, hours: int = 72) -> list[dict[str, Any]]:
    """What was worth interrupting for (``tier: now``) or waits for the digest; entry points poll for the unseen."""
    import time as _time
    return mem_store.alerts(_uid(), since=_time.time() - max(1, min(24 * 30, hours)) * 3600, unseen=unseen)


@router.post("/alerts/{alert_id}/seen")
async def alert_seen(alert_id: str) -> dict[str, Any]:
    import time as _time
    a = mem_store.alert(alert_id)
    if a is None or a["owner"] != _uid():
        raise HTTPException(404, "No such alert")
    mem_store.update_alert(alert_id, seen_at=a["seen_at"] or _time.time())
    return mem_store.alert(alert_id)  # type: ignore[return-value]


class Verdict(BaseModel):
    worth: bool


@router.post("/alerts/{alert_id}/verdict")
async def alert_verdict(alert_id: str, body: Verdict) -> dict[str, Any]:
    """Was it worth interrupting you? Not worth it: alerts of its kind wait for the digest from now on."""
    from aurora.memory import alerts as al
    try:
        return al.verdict(_uid(), alert_id, body.worth)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.post("/alerts/check")
async def check_now() -> dict[str, Any]:
    """Check the papers you rely on for retractions now (otherwise once a day)."""
    from aurora.memory import alerts as al
    raised = await al.check_retractions(_uid(), force=True)
    return {"checked": len(al.relied_on(_uid())), "retracted": raised}


@router.delete("/sources/{source_id}")
async def forget_source(source_id: str) -> dict[str, Any]:
    """Take a saved thing out of your memory (units left without any source go too)."""
    s = mem_store.source(source_id)
    if s is None or s["owner"] != _uid():
        raise HTTPException(404, "No such source")
    return {"gone_units": mem_store.retire_source(source_id)}


# ---------------------------------------------------------------------------
# Writing: a citation for a sentence, and the references before you submit


class CiteBody(BaseModel):
    sentence: str
    context: str = ""


@router.post("/cite")
async def cite(body: CiteBody) -> dict[str, Any]:
    """Papers from your own library that back a sentence you are writing, each with the passage that does."""
    from aurora.memory import cite as ct
    if len(body.sentence.strip()) < 8:
        raise HTTPException(400, "Give the sentence that needs a citation")
    access, models = _access()
    return await ct.suggest(access, models, _uid(), body.sentence.strip()[:2000], body.context)


@router.post("/cite/hint")
async def cite_hint(body: CiteBody) -> dict[str, Any]:
    """Whether papers you have may back the sentence you stopped at (by meaning only; no model call)."""
    from aurora.memory import cite
    if len(body.sentence.strip()) < 40 or "\\cite" in body.sentence:
        return {"papers": 0, "top": []}
    access, models = _access()
    return await cite.hint(access, models, _uid(), body.sentence)


class AuditBody(BaseModel):
    tex: str = ""
    bib: str = ""
    title: str = ""
    pdf: str = ""            # or the manuscript as a PDF, base64


@router.post("/audit")
async def start_audit(body: AuditBody) -> dict[str, Any]:
    """Check a manuscript's references: exist, retracted, misquoted, key work missing (in the background)."""
    import base64

    from aurora.memory import audit as au
    pdf = b""
    if body.pdf:
        try:
            pdf = base64.b64decode(body.pdf)
        except ValueError:
            raise HTTPException(400, "The PDF could not be read") from None
        if not pdf.startswith(b"%PDF") or len(pdf) > 80 * 1024 * 1024:
            raise HTTPException(400, "Give a PDF (at most 80 MB)")
    elif "\\" not in body.tex and not body.bib.strip():
        raise HTTPException(400, "Give the manuscript's LaTeX (and its .bib), or its PDF")
    access, models = _access()
    return au.start(access, models, _uid(), body.tex, body.bib, body.title, pdf=pdf)


@router.get("/audits")
async def audits() -> list[dict[str, Any]]:
    from aurora.memory import audit as au
    return au.listing(_uid())


@router.get("/audits/{audit_id}")
async def get_audit(audit_id: str) -> dict[str, Any]:
    from aurora.memory import audit as au
    a = au.get(audit_id)
    if a is None or a["owner"] != _uid():
        raise HTTPException(404, "No such check")
    return a


@router.get("/dissent")
async def dissent(paper: str, space: str = "") -> dict[str, Any]:
    """The sentences of a paper that what you saved before challenges or qualifies (for the reader's margin)."""
    from aurora.literature.library import library
    from aurora.memory import dissent as ds
    e = library.paper(paper)
    if e is None:
        raise HTTPException(404, "No such paper")
    cid = "" if not space or space.startswith("user:") or space == "global" else space
    access, models = _access()
    return await ds.find(access, models, _uid(), e["paper_id"], cid)


# ---------------------------------------------------------------------------
# The Zotero plugin: a paper read in the background


class ZoteroItem(BaseModel):
    doi: str = ""
    arxiv: str = ""
    title: str = ""
    pdf: str = ""            # the PDF, base64, when Zotero has it


@router.post("/zotero-read")
async def zotero_read(body: ZoteroItem) -> dict[str, Any]:
    """A paper added to Zotero: read it in the background (into the memory, and a card of it)."""
    import base64

    from aurora.memory import card
    entry = await card.resolve(doi=body.doi, arxiv=body.arxiv, title=body.title)
    if entry is None:
        raise HTTPException(404, "The paper was not found in the scholarly indexes")
    text = ""
    if body.pdf:
        try:
            data = base64.b64decode(body.pdf)
            if data.startswith(b"%PDF") and len(data) < 60 * 1024 * 1024:
                from aurora.literature.pdfs import _text
                from aurora.literature.reading import cached_pdf
                text, _ = _text(data, pages=80)
                # kept, so the margin ("your saves argue with this page") can read it too
                target = cached_pdf(entry["paper_id"])
                if not target.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
        except Exception:
            text = ""
    access, models = _access()
    card.start(access, models, _uid(), entry, text)
    return card.view(_uid(), entry)


@router.get("/zotero-card")
async def zotero_card(doi: str = "", arxiv: str = "", title: str = "") -> dict[str, Any]:
    """What the plugin shows beside an item: its card (methods, findings, limitations) and what your memory makes of it."""
    from aurora.memory import card
    entry = await card.resolve(doi=doi, arxiv=arxiv, title=title)
    if entry is None:
        raise HTTPException(404, "The paper was not found in the scholarly indexes")
    return card.view(_uid(), entry)


# ---------------------------------------------------------------------------
# Your own notes (an Obsidian vault, or any folder of Markdown files)


class VaultSettings(BaseModel):
    path: str = ""
    folders: list[str] = []
    exclude: list[str] = []
    on: bool = False


@router.get("/vault")
async def vault_settings() -> dict[str, Any]:
    from aurora.memory import vault
    uid = _uid()
    return {**vault.settings(uid), "notes": len([n for n in vault.notes(uid) if n["source_id"]])}


@router.put("/vault")
async def put_vault(body: VaultSettings) -> dict[str, Any]:
    """Which vault folder the console reads (on the machine it runs on), and which of its subfolders."""
    from aurora.memory import vault
    try:
        return vault.set_settings(_uid(), body.path.strip(), body.folders, body.exclude, body.on)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/vault/scan")
async def scan_vault() -> dict[str, Any]:
    """Read the vault's new and changed notes now (otherwise every few minutes)."""
    from aurora.memory import vault
    access, models = _access()
    return vault.scan(access, models, _uid())


@router.get("/notes")
async def list_notes() -> list[dict[str, Any]]:
    from aurora.memory import vault
    return vault.notes(_uid())


class NoteBody(BaseModel):
    vault: str
    path: str
    text: str
    mtime: float = 0.0


@router.post("/notes")
async def put_note(body: NoteBody) -> dict[str, Any]:
    """A note sent by the Obsidian plugin as you write it: read into your memory (its old version retired)."""
    from aurora.memory import vault
    if not body.vault.strip() or not body.path.strip():
        raise HTTPException(400, "Give the vault and the note's path")
    access, models = _access()
    return vault.upsert(access, models, _uid(), body.vault.strip(), body.path.strip(), body.text[:vault.MAX_FILE], body.mtime)


@router.delete("/notes")
async def delete_note(vault_name: str, path: str) -> dict[str, Any]:
    """A note deleted (or excluded) in the vault: what only it said goes from your memory."""
    from aurora.memory import vault
    return {"removed": vault.remove(_uid(), vault_name, path)}


@router.get("/units/{unit_id}/markdown")
async def unit_markdown(unit_id: str) -> dict[str, Any]:
    """An idea unit as Markdown (claim, sources with their words, conflicts), to paste into a note."""
    from aurora.memory import vault
    _unit(unit_id)
    return {"markdown": vault.markdown(unit_id)}


# ---------------------------------------------------------------------------
# Speaking: hold a key, say it, let go


class Said(BaseModel):
    text: str = ""            # the words, when the entry point recognised them itself (the desktop app does)
    audio: str = ""           # or the recording, base64, recognised here (AURORA_WHISPER)
    audio_type: str = "wav"
    screenshot: str = ""      # the window spoken about, as a data: URL ("this figure")


@router.post("/voice")
async def voice_said(body: Said) -> dict[str, Any]:
    """What was said, understood with what you are doing now and done: what to say back, what to show, how to undo."""
    import base64

    from aurora.memory import voice
    text = body.text.strip()
    if not text and body.audio:
        try:
            text = await voice.transcribe(base64.b64decode(body.audio), "." + body.audio_type.strip(".")[:5])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    if not text:
        raise HTTPException(400, "Say something (send the words or the recording)")
    shot = body.screenshot if body.screenshot.startswith("data:image/") and len(body.screenshot) < 8_000_000 else ""
    access, models = _access()
    return await voice.hear(access, models, _uid(), text, screenshot=shot)


class Thought(BaseModel):
    text: str


@router.post("/thought")
async def keep_thought(body: Thought) -> dict[str, Any]:
    """A thought typed with the quick-note key: kept as your own note about what is in front (and as a note on its
    Zotero item when you allowed that)."""
    from aurora.memory import voice
    if not body.text.strip():
        raise HTTPException(400, "Write the thought")
    access, models = _access()
    return await voice.thought(access, models, _uid(), body.text[:4000])


@router.get("/voice/turns")
async def voice_turns(limit: int = 5) -> list[dict[str, Any]]:
    """What was said and answered lately (the panel shows the full answer of what was spoken short)."""
    from aurora.memory import voice
    return voice.turns(_uid(), max(1, min(20, limit)))


@router.post("/voice/undo/{undo_id}")
async def voice_undo(undo_id: str) -> dict[str, Any]:
    from aurora.memory import voice
    try:
        return {"undone": await voice.undo(_uid(), undo_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/voice/digest")
async def voice_digest() -> dict[str, Any]:
    """Today's digest to listen to, numbered so you can act on "the second one" afterwards."""
    from aurora.memory import voice
    return voice.spoken_digest(_uid())


class SpeakBody(BaseModel):
    text: str


@router.post("/voice/speak")
async def voice_speak(body: SpeakBody) -> Any:
    """A long text in the cloud voice (when one is set up); otherwise the desktop app uses the system voice."""
    from fastapi.responses import Response

    from aurora.memory import voice
    try:
        return Response(await voice.cloud_speech(body.text), media_type="audio/mpeg")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


# ---------------------------------------------------------------------------
# A claim into a brainstorm


class ToBrainstorm(BaseModel):
    space: str = ""           # empty: your own space
    card_id: str = ""         # an idea it bears on (optional)
    rel: str = "supports"     # supports | challenges


@router.post("/units/{unit_id}/to-brainstorm")
async def unit_to_brainstorm(unit_id: str, body: ToBrainstorm) -> dict[str, Any]:
    """Bring a claim from your memory onto a brainstorm board, as material (private to you), filed under an idea if
    you name one."""
    from aurora.brainstorm.agents import memory_card
    from aurora.brainstorm.store import bs_store
    u = _unit(unit_id)
    uid = _uid()
    from aurora.api.routes.brainstorm import _may_write, _space
    space, campaign_id = _space(body.space or f"user:{uid}")
    _may_write(campaign_id)
    card = memory_card(space, uid, u)
    if body.card_id:
        idea = bs_store.card(body.card_id)
        if idea is None or idea["space"] != space:
            raise HTTPException(404, "No such idea in that space")
        bs_store.link(card["card_id"], idea["card_id"], "challenges" if body.rel == "challenges" else "supports")
    return card


# ---------------------------------------------------------------------------
# Acting on the day's digest: what to read closely, the reading list, how two things differ


@router.get("/digest/worth")
async def digest_worth() -> list[dict[str, Any]]:
    """One or two papers of the day worth reading closely: the feed's best fits for your projects, and papers you
    saved today that bear most on your questions."""
    from aurora.literature.library import library
    from aurora.memory import card
    from aurora.memory import voice as vc
    uid = _uid()
    out: list[dict[str, Any]] = []
    for p in vc._feed_papers(uid):
        url = p["url"]
        e = await card.resolve(doi=url.split("doi.org/")[-1] if "doi.org/" in url else "",
                               arxiv=url.rsplit("/", 1)[-1] if "arxiv.org/" in url else "", title=p["title"])
        if e:
            out.append({"paper_id": e["paper_id"], "title": e["paper"].title, "byline": e["paper"].byline(),
                        "why": p["why"], "from": "the feed"})
    import time as _t
    since = _t.time() - 86400
    saved = [s for s in mem_store.sources(uid, 200) if s["paper_id"] and s["created_at"] >= since and s["state"] == "done"]
    saved.sort(key=lambda s: -len(s["result"].get("questions") or []))
    for s in saved:
        e = library.paper(s["paper_id"])
        if e and not any(o["paper_id"] == e["paper_id"] for o in out):
            q = len(s["result"].get("questions") or [])
            out.append({"paper_id": e["paper_id"], "title": e["paper"].title, "byline": e["paper"].byline(),
                        "why": f"You saved it today; it bears on {q} of your questions." if q else "You saved it today.",
                        "from": "your memory"})
    for o in out:
        o["to_read"] = library.has_item("", o["paper_id"], owner=uid)
    return out[:2]


class PaperRef(BaseModel):
    paper_id: str


@router.post("/to-read")
async def to_read(body: PaperRef) -> dict[str, Any]:
    """Put a paper on your reading list (your Library: AURORA's own, nothing is written elsewhere)."""
    from aurora.literature.library import library
    if library.paper(body.paper_id) is None:
        raise HTTPException(404, "No such paper")
    added = library.add_item("", body.paper_id, source="to_read", added_by="you", owner=_uid())
    return {"added": added}


class CompareRef(BaseModel):
    unit_id: str = ""
    source_id: str = ""
    paper_id: str = ""


class CompareBody(BaseModel):
    a: CompareRef
    b: CompareRef


@router.post("/compare")
async def compare_two(body: CompareBody) -> dict[str, Any]:
    """How two things you have differ (claims, saved sources or papers), each difference with the words it rests on."""
    import re as _re

    from aurora.memory import voice as vc
    uid = _uid()
    ts = [vc.target_of(uid, **x.model_dump()) for x in (body.a, body.b)]
    if not all(ts):
        raise HTTPException(404, "Name two claims, saved things or papers of yours")
    access, models = _access()
    zh = surface.language(uid).startswith("Chinese") or any(_re.search(r"[\u4e00-\u9fff]", t["title"]) for t in ts if t)
    short, full = await vc.compare(access, models, ts, zh)  # type: ignore[arg-type]
    return {"short": short, "text": full, "a": ts[0]["title"], "b": ts[1]["title"]}  # type: ignore[index]

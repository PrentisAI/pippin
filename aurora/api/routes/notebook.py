"""``/api/notebook``: a page for every run (written from the run), clips from papers, and your notes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.auth.guard import current_user
from aurora.notebook import entry as nb_entry
from aurora.notebook.store import nb_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/notebook", tags=["notebook"])
_tasks: set[asyncio.Task] = set()


def _me() -> str:
    return current_user().user_id


@router.get("")
async def overview() -> dict[str, Any]:
    """The notebook's contents: each project with its runs, how many clips, your notes."""
    from aurora.api.routes.campaigns import get_campaign, list_campaigns
    projects = []
    for c in await list_campaigns():
        try:
            full = await get_campaign(c["campaign_id"])
        except HTTPException:
            continue
        runs = sorted(full.get("experiments", []), key=lambda e: e.get("started_at") or 0)
        projects.append({"campaign_id": c["campaign_id"], "name": c["name"], "example": bool(c.get("example")),
                         "runs": [{"run_id": e["run_id"], "name": e.get("task_name") or e["run_id"], "status": e.get("status"),
                                   "started_at": e.get("started_at")} for e in runs]})
    me = _me()
    return {"projects": projects, "clips": len(nb_store.clips(me)), "notes": nb_store.notes(me)}


# ---------------------------------------------------------------- a run's page

def _judge_later(campaign_id: str, run_id: str, e: dict[str, Any], key: str) -> None:
    from aurora.api.routes.memory import _access
    try:
        access, models = _access()
    except HTTPException:
        return                              # no model key: the page goes without the comparison
    nb_store.set_match(campaign_id, run_id, key, "judging")

    async def go() -> None:
        try:
            nb_store.set_match(campaign_id, run_id, key, "done", await nb_entry.judge(access, models, e))
        except Exception as exc:
            logger.info("comparing what %s expected with what it found failed", run_id, exc_info=True)
            nb_store.set_match(campaign_id, run_id, key, "failed", {"error": str(exc)[:300]})
    t = asyncio.create_task(go())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


@router.get("/runs/{campaign_id}/{exp_id}")
async def run_page(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """The run's page: its idea, hypothesis, why it was worth trying, expected against actual, and your notes."""
    from aurora.api.routes.run_results import _run
    e = nb_entry.entry(_run(campaign_id, exp_id))
    key = nb_entry.match_key(e)
    m = nb_store.match(campaign_id, exp_id)
    if nb_entry.judgeable(e) and (m is None or (m["key"] != key and m["state"] != "judging")):
        _judge_later(campaign_id, exp_id, e, key)
        m = nb_store.match(campaign_id, exp_id)
    note = nb_store.run_note(_me(), campaign_id, exp_id)
    return {**e, "campaign_id": campaign_id,
            "match": {"state": m["state"], "stale": m["key"] != key, **m["result"]} if m else None,
            "note": {"html": note["html"], "updated_at": note["updated_at"]} if note else None,
            "clips": nb_store.clips(_me(), campaign_id=campaign_id, run_id=exp_id)}


class Body(BaseModel):
    html: str = ""
    text: str = ""


@router.put("/runs/{campaign_id}/{exp_id}/note")
async def save_run_note(campaign_id: str, exp_id: str, body: Body) -> dict[str, Any]:
    """Your notes on the run (only you see them)."""
    n = nb_store.save_run_note(_me(), campaign_id, exp_id, body.html[:400_000], body.text[:200_000])
    return {"html": n["html"], "updated_at": n["updated_at"]}


# ---------------------------------------------------------------- clips

class NewClip(BaseModel):
    paper_id: str
    quote: str
    paper_title: str = ""
    space: str = ""
    page_index: int = 0
    rects: list[list[float]] = []
    words: str = ""
    campaign_id: str = ""
    run_id: str = ""


def _for(campaign_id: str) -> None:
    """A clip may be for a project only if you can see it."""
    if campaign_id:
        from aurora.auth.guard import require
        require(campaign_id, "view")


@router.get("/clips")
async def clips(paper: str = "", campaign_id: str = "", run_id: str = "") -> list[dict[str, Any]]:
    return nb_store.clips(_me(), paper_id=paper, campaign_id=campaign_id, run_id=run_id)


@router.post("/clips")
async def add_clip(body: NewClip) -> dict[str, Any]:
    """A passage clipped from a paper, as it is written, with where it is; what it says also goes into the notebook's
    claims, so a paper that disagrees with it later says so."""
    if not body.quote.strip():
        raise HTTPException(400, "Select the words to clip")
    _for(body.campaign_id)
    c = nb_store.add_clip(_me(), paper_id=body.paper_id, quote=body.quote.strip()[:6000], paper_title=body.paper_title[:300],
                          space=body.space, page_index=body.page_index, rects=body.rects[:200], words=body.words.strip()[:6000],
                          campaign_id=body.campaign_id, run_id=body.run_id)
    try:
        from aurora.api.routes.memory import _access
        from aurora.memory import ingest
        access, models = _access()
        ingest.start(access, models, _me(), text=c["quote"] + (f"\n\nMy note: {c['words']}" if c["words"] else ""),
                     title=c["paper_title"] or c["paper_id"], via="clip", selection=c["quote"])
    except HTTPException:
        pass                                 # no model key: kept as a clip all the same
    return c


class ClipChange(BaseModel):
    words: str | None = None
    campaign_id: str | None = None
    run_id: str | None = None


def _mine_clip(clip_id: str) -> dict[str, Any]:
    c = nb_store.clip(clip_id)
    if c is None or c["owner"] != _me():
        raise HTTPException(404, "No such clip")
    return c


@router.patch("/clips/{clip_id}")
async def change_clip(clip_id: str, body: ClipChange) -> dict[str, Any]:
    _mine_clip(clip_id)
    if body.campaign_id:
        _for(body.campaign_id)
    return nb_store.change_clip(clip_id, words=body.words, campaign_id=body.campaign_id, run_id=body.run_id)  # type: ignore[return-value]


@router.delete("/clips/{clip_id}", status_code=204)
async def delete_clip(clip_id: str) -> None:
    _mine_clip(clip_id)
    nb_store.delete_clip(clip_id)


# ---------------------------------------------------------------- notes

class Note(BaseModel):
    title: str | None = None
    html: str | None = None
    text: str | None = None
    campaign_id: str | None = None


def _mine_note(note_id: str) -> dict[str, Any]:
    n = nb_store.note(note_id)
    if n is None or n["owner"] != _me() or n["run_id"]:
        raise HTTPException(404, "No such note")
    return n


@router.post("/notes")
async def new_note(body: Note) -> dict[str, Any]:
    if body.campaign_id:
        _for(body.campaign_id)
    return nb_store.add_note(_me(), title=(body.title or "")[:300], html=(body.html or "")[:400_000],
                             text=(body.text or "")[:200_000], campaign_id=body.campaign_id or "")


@router.get("/notes/{note_id}")
async def get_note(note_id: str) -> dict[str, Any]:
    return _mine_note(note_id)


@router.put("/notes/{note_id}")
async def save_note(note_id: str, body: Note) -> dict[str, Any]:
    _mine_note(note_id)
    if body.campaign_id:
        _for(body.campaign_id)
    return nb_store.save_note(note_id, title=None if body.title is None else body.title[:300],  # type: ignore[return-value]
                              html=None if body.html is None else body.html[:400_000],
                              text=None if body.text is None else body.text[:200_000], campaign_id=body.campaign_id)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: str) -> None:
    _mine_note(note_id)
    nb_store.delete_note(note_id)

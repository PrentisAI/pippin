"""The paper reader: a paper's PDF, the notes you and the agents make on it, and their way to Zotero.

* ``GET  /api/reader/papers/{paper}``: the paper, whether a PDF is there, the notes you see on it, which
  agents are reading along, and where the notes go in Zotero (``?space=`` the project or ``global``;
  ``card_id`` / ``chat_id``: the idea it is read for);
* ``GET  /api/reader/papers/{paper}/pdf``: the PDF (an open copy is fetched the first time);
* notes: create, change (keep an agent's suggestion, share with the project, place it on the page),
  delete (with Undo for a few seconds), reply (``@Skeptic`` and the agent answers);
* ``POST /api/reader/papers/{paper}/read-along``: the agents read the paper for the idea and mark passages;
* ``POST /api/reader/papers/{paper}/companion``: Pippin reads the paper against your work and writes notes worth
  thinking about, with a figure to play with where trying beats reading (see :mod:`aurora.literature.companion`);
* ``GET  /api/reader/notes/{note}/figure``: that figure, as a page for a sandboxed frame;
* ``POST /api/reader/papers/{paper}/zotero``: put the paper and its PDF into your Zotero.

Notes are private to the person who made them; a note shared with a project is seen by its members.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from aurora.auth.guard import current_user, require, rights, visible
from aurora.brainstorm import agents as ag
from aurora.brainstorm.store import bs_store
from aurora.literature import reading
from aurora.literature.annotations import AGENT_COLORS, annotations
from aurora.literature.library import library

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reader", tags=["reader"])

AGENT_ORDER = ("Librarian", "Skeptic", "Methods")
_tasks: set[asyncio.Task] = set()


def _uid() -> str:
    return current_user().user_id


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _space(space: str) -> tuple[str, str]:
    """(space key, project ID or ''), checking the person may see it."""
    if not space or space == "global" or space == f"user:{_uid()}":
        return f"user:{_uid()}", ""
    if space.startswith("user:"):
        raise HTTPException(404, "No such space")
    require(space, "view")
    return space, space


def _paper(ident: str) -> dict[str, Any]:
    entry = library.paper(ident)
    if entry is None:
        raise HTTPException(404, "No such paper")
    return entry


def _projects() -> list[str]:
    from aurora.api.routes.campaigns import store
    return visible(current_user(), [row["campaign_id"] for row in store.list_campaigns()])


def _pdf(paper_id: str, campaign_id: str) -> Path | None:
    return reading.find_pdf(paper_id, _uid(), campaign_id, _projects())


async def _texts(entry: dict[str, Any], campaign_id: str) -> list[str] | None:
    """What the agents read of it: a paper's PDF page by page (its open copy fetched the first time), or a page's
    snapshot (taken now if it has none); None when there is nothing to read."""
    if entry["paper"].is_page:
        from aurora.literature import pages
        if not pages.texts(entry["paper_id"]):
            await pages.fill(entry["paper_id"])
        return pages.texts(entry["paper_id"]) or None
    pdf = _pdf(entry["paper_id"], campaign_id) or await reading.fetch_open_pdf(entry["paper_id"])
    return reading.page_texts(pdf) if pdf else None


def _name(user_id: str) -> str:
    if user_id in AGENT_COLORS:
        return user_id
    if user_id == _uid() or user_id == "local":
        return "You"
    from aurora.auth.store import auth_store
    row = auth_store.get_user(user_id)
    return row["name"] if row else "Someone"


def _idea(space: str, card_id: str = "", chat_id: str = "") -> dict[str, Any] | None:
    """The idea the paper is read for: the one named, else the conversation's focus or its latest idea."""
    uid = _uid()

    def sees(c: dict[str, Any] | None) -> bool:
        return c is not None and c["space"] == space and (c["owner"] == uid or c["vis"] == "project")

    if card_id:
        c = bs_store.card(card_id)
        return c if sees(c) and c["type"] == "idea" else None
    if chat_id:
        ch = bs_store.chat(chat_id)
        if ch is None or ch["space"] != space or not (ch["owner"] == uid or ch["vis"] == "project"):
            return None
        if ch["focus"] and sees(c := bs_store.card(ch["focus"])):
            return c
        ideas = [c for c in bs_store.cards(space) if c["chat_id"] == chat_id and c["type"] == "idea" and sees(c)]
        return max(ideas, key=lambda c: c["created_at"]) if ideas else None
    return None


# what the agents read for while a conversation has no idea written down yet (a Mentor's conversation
# settles one only at its end): the researcher's own words in it
CONVERSATION = "chat:"


def _conversation(space: str, chat_id: str) -> dict[str, Any] | None:
    """The conversation as what the paper is read for, when it has no idea yet: what the researcher said."""
    uid = _uid()
    ch = bs_store.chat(chat_id) if chat_id else None
    if ch is None or ch["space"] != space or not (ch["owner"] == uid or ch["vis"] == "project"):
        return None
    said = [re.sub(r"@\w+\s*", "", m["text"]).strip() for m in bs_store.messages(chat_id, viewer=uid)
            if m["who"] == uid and m["kind"] == "text"]
    said = [t for t in said if t]
    if not said:
        return None
    # the first thing said with substance (usually the topic), then as much of the latest as fits
    at = next((i for i, t in enumerate(said[:3]) if len(t) >= 40), 0)
    first, rest, kept, room = said[at][:300], said[at + 1:], [], 1200
    for t in reversed(rest):
        if len(t) > room:
            break
        kept.insert(0, t)
        room -= len(t) + 3
    words = " / ".join([first, *(["…"] if len(kept) < len(rest) else []), *kept])
    text = "Not written down as an idea yet. What the researcher is working out in a brainstorm, in their own words: " + words
    return {"card_id": CONVERSATION + chat_id, "text": text, "space": space, "chat_id": chat_id, "kind": "conversation"}


def _read_for(space: str, card_id: str = "", chat_id: str = "") -> dict[str, Any] | None:
    """What the agents read the paper for: the idea, else (no idea named) the conversation."""
    return _idea(space, card_id, chat_id) or (None if card_id else _conversation(space, chat_id))


def _ideas_for(idea: dict[str, Any]) -> list[dict[str, Any]]:
    """The idea, and up to two more from its conversation."""
    if idea.get("kind") == "conversation":
        return [idea]
    uid = _uid()
    more = [c for c in bs_store.cards(idea["space"]) if c["type"] == "idea" and c["card_id"] != idea["card_id"]
            and idea["chat_id"] and c["chat_id"] == idea["chat_id"] and (c["owner"] == uid or c["vis"] == "project")]
    more.sort(key=lambda c: -c["created_at"])
    return [idea, *more[:2]]


def _note_view(a: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    mine = a["owner"] == _uid()
    card = bs_store.card(a["card_id"]) if a["card_id"] else None
    if not mine:
        status, why = "shared", f"Shared with the project by {_name(a['owner'])}"
    elif a["state"] == "suggested":
        status, why = "suggested", "A suggestion: keep it to make it yours (and send it to Zotero)"
    elif a["state"] == "draft":
        status, why = "draft", "Not saved yet"
    elif target["state"] != "ok":
        status = "local"
        why = {"off": "Zotero is not connected", "read_only": "Your Zotero key can only read",
               "not_in_zotero": "The paper is not in your Zotero",
               "no_pdf": "Your Zotero has the paper but not its PDF"}.get(target["state"], "")
    elif not a["rects"]:
        status, why = "local", "Not placed on the page yet"
    elif a["sync_error"]:
        status, why = "error", a["sync_error"]
    elif a["dirty"] or not a["zotero_key"]:
        status, why = "saving", "Going to Zotero"
    else:
        status, why = "synced", "In Zotero"
    return {
        "ann_id": a["ann_id"], "paper_id": a["paper_id"], "author": a["author"], "mine": mine,
        "owner_name": _name(a["owner"]), "author_name": a["author"] or _name(a["owner"]),
        "state": a["state"], "text": a["text"], "comment": a["comment"], "color": a["color"],
        "page_index": a["page_index"], "page_label": a["page_label"], "rects": a["rects"], "sort_index": a["sort_index"],
        "shared": a["shared"], "space": a["space"], "card_id": a["card_id"], "rel": a["rel"],
        "inline": bool(a.get("inline")), "kind": a.get("kind") or "", "figure": bool(a.get("figure")),
        "idea_text": card["text"] if card else "", "source": a["source"], "status": status, "why": why,
        "created_at": a["created_at"], "updated_at": a["updated_at"],
        "replies": [{**r, "who_name": _name(r["who"]), "agent": r["who"] in AGENT_COLORS} for r in a.get("replies", [])],
    }


def _view(entry: dict[str, Any], space: str, campaign_id: str, idea: dict[str, Any] | None) -> dict[str, Any]:
    from aurora.api.routes.literature import paper_view
    from aurora.auth.store import auth_store
    from aurora.literature.zotero import notes_target, zotero_store
    uid, pid = _uid(), entry["paper_id"]
    target = notes_target(uid, pid)
    notes = [_note_view(a, target) for a in annotations.for_paper(pid, uid, space)]
    reads = annotations.reads(uid, pid, space, idea["card_id"]) if idea else {}
    companion = annotations.reads(uid, pid, space, COMPANION).get("Pippin")
    page = entry["paper"].is_page
    pdf = None if page else _pdf(pid, campaign_id)
    link = zotero_store.link(uid)
    return {
        "paper": paper_view(entry), "space": space, "campaign_id": campaign_id,
        "pdf": {"available": pdf is not None, "openable": not page and bool(reading.open_url(entry["paper"]))},
        "page": {"url": entry["paper"].url} if page else None,
        "notes": notes,
        "idea": {"card_id": idea["card_id"], "text": idea["text"], "kind": idea.get("kind", "idea")} if idea else None,
        "reads": {a: {"state": r["state"], "detail": r["detail"]} for a, r in reads.items()},
        "companion": {"state": companion["state"], "detail": companion["detail"]} if companion else None,
        "agents_mark": bool(auth_store.get_pref(uid, "reader_agents_mark", True)),
        "can_share": bool(campaign_id) and "comment" in rights(current_user(), campaign_id),
        "zotero": {**target, "connected": link is not None,
                   "counts": {"synced": sum(n["status"] == "synced" for n in notes),
                              "saving": sum(n["status"] == "saving" for n in notes),
                              "suggested": sum(n["status"] == "suggested" for n in notes),
                              "local": sum(n["status"] in ("local", "error") for n in notes)}},
    }


@router.get("/papers/{ident}")
async def get_paper(ident: str, space: str = "", card_id: str = "", chat_id: str = "") -> dict[str, Any]:
    entry = _paper(ident)
    key, cid = _space(space)
    return _view(entry, key, cid, _read_for(key, card_id, chat_id))


@router.post("/papers/{ident}/prefetch")
async def prefetch_pdf(ident: str, space: str = "") -> dict[str, Any]:
    """Start fetching the paper's open PDF while the person looks at the paper, so it is there (or
    on its way) when they open it."""
    entry = _paper(ident)
    _, cid = _space(space)
    if _pdf(entry["paper_id"], cid) is None and not reading.cached_pdf(entry["paper_id"]).is_file():
        reading.start_fetch(entry["paper_id"])
    return await pdf_progress(ident, space)


@router.get("/papers/{ident}/pdf/progress")
async def pdf_progress(ident: str, space: str = "") -> dict[str, Any]:
    """How far the paper's PDF is: ready, being fetched (bytes so far, of how many), or none."""
    entry = _paper(ident)
    _, cid = _space(space)
    if _pdf(entry["paper_id"], cid) is not None:
        return {"state": "ready", "loaded": 0, "total": 0}
    return reading.fetch_progress(entry["paper_id"])


@router.get("/papers/{ident}/pdf")
async def get_pdf(ident: str, space: str = "") -> FileResponse:
    entry = _paper(ident)
    _, cid = _space(space)
    path = _pdf(entry["paper_id"], cid) or await reading.fetch_open_pdf(entry["paper_id"])
    if path is None:
        raise HTTPException(404, "There is no PDF of this paper here, and no open copy could be fetched")
    return FileResponse(path, media_type="application/pdf", filename=f"{entry['key']}.pdf",
                        content_disposition_type="inline")


@router.get("/papers/{ident}/page")
async def get_page(ident: str) -> dict[str, Any]:
    """A page kept in the Library, as it was saved: its readable HTML and its text (taken now if it has none)."""
    from aurora.literature import pages
    entry = _paper(ident)
    if not entry["paper"].is_page:
        raise HTTPException(400, "This is a paper: open its PDF")
    snap = pages.snapshot(entry["paper_id"])
    if snap is None and await pages.fill(entry["paper_id"]):
        snap = pages.snapshot(entry["paper_id"])
    if snap is None:
        raise HTTPException(404, "The page could not be fetched; open it on the web, or save it again from the "
                                 "browser extension, which reads it as you see it")
    return {**snap, "url": entry["paper"].url}


# ---------------------------------------------------------------------------
# Agents: answering on a passage, reading along


def _access(campaign_id: str) -> ag.ModelAccess:
    from aurora.api.routes.brainstorm import _access as bs_access
    access = bs_access()
    if campaign_id:
        from aurora.auth.providers import model_problem
        if problem := model_problem(access.model, campaign_id):
            return ag.ModelAccess(api_key="", base_url=access.base_url, model=access.model,
                                  missing=f"{problem} An admin can choose another model for the brainstorm agents.")
    return access


def _ask(ann_id: str, agent: str, campaign_id: str) -> None:
    """The agent answers in the note's conversation (a pending reply until it has)."""
    a = annotations.get(ann_id)
    if a is None:
        return
    reply = annotations.add_reply(ann_id, agent, pending=True)
    access = _access(campaign_id)
    owner_name = "The researcher"

    async def go() -> None:
        try:
            note = annotations.get(ann_id)
            entry = library.paper(note["paper_id"]) if note else None
            if note is None or entry is None:
                annotations.finish_reply(reply["reply_id"], "The note is gone.")
                return
            pages = await _texts(entry, campaign_id) or []
            card = bs_store.card(note["card_id"]) if note["card_id"] else None
            thread = [(owner_name if not note["author"] else note["author"], note["comment"])] if note["comment"] else []
            thread += [(r["who"] if r["who"] in AGENT_COLORS else owner_name, r["text"])
                       for r in annotations.replies(ann_id) if not r["pending"] and r["text"]]
            if not thread:
                thread = [(owner_name, "What does this passage mean for the idea?")]
            from aurora.brainstorm.reading import talk
            text = await talk(agent, key=entry["key"], title=entry["paper"].title, passage=note["text"],
                              page_index=note["page_index"],
                              page_text=pages[note["page_index"]] if 0 <= note["page_index"] < len(pages) else "",
                              idea=card["text"] if card else "", thread=thread, access=access)
        except Exception as exc:
            logger.warning("answering on note %s failed", ann_id, exc_info=True)
            text = f"I could not answer: {str(exc)[:200]}"
        annotations.finish_reply(reply["reply_id"], text)
        note = annotations.get(ann_id)
        if note and note["state"] == "kept" and note["zotero_key"]:
            annotations.update(ann_id)                   # the thread is part of the Zotero comment
            _to_zotero(note["owner"])

    _spawn(go())


def _to_zotero(owner: str) -> None:
    from aurora.literature.zotero import notes_soon
    notes_soon(owner)


class ReadAlong(BaseModel):
    space: str = ""
    card_id: str = ""
    chat_id: str = ""


@router.post("/papers/{ident}/read-along")
async def read_along(ident: str, body: ReadAlong) -> dict[str, Any]:
    """The agents read the paper for the idea and mark passages (once per paper and idea)."""
    from aurora.auth.store import auth_store
    entry = _paper(ident)
    key, cid = _space(body.space)
    idea = _read_for(key, body.card_id, body.chat_id)
    uid, pid = _uid(), entry["paper_id"]
    if idea is None:
        return {"started": False, "why": "Open the paper from a brainstorm, with an idea or a conversation, for the agents to read along"}
    if not auth_store.get_pref(uid, "reader_agents_mark", True):
        return {"started": False, "why": "The agents mark passages only when you ask"}
    if annotations.reads(uid, pid, key, idea["card_id"]):
        return {"started": False, "why": "They have read it for this idea"}
    pages = await _texts(entry, cid)
    if pages is None:
        return {"started": False, "why": "There is nothing to read: no PDF, or the page could not be fetched"}
    access = _access(cid)
    if not access.api_key:
        return {"started": False, "why": f"No API key is set: {access.missing}"}
    ideas = [{"card_id": c["card_id"], "text": c["text"]} for c in _ideas_for(idea)]
    for agent in AGENT_ORDER:
        annotations.set_read(uid, pid, key, idea["card_id"], agent, "reading")

    async def go() -> None:
        from aurora.brainstorm.reading import read_along as agent_reads
        for agent in AGENT_ORDER:
            if not any(p.strip() for p in pages):
                annotations.set_read(uid, pid, key, idea["card_id"], agent, "failed", "The PDF has no text to read")
                continue
            already = [a["text"] for a in annotations.for_paper(pid, uid, key)]
            try:
                marks = await agent_reads(agent, key=entry["key"], title=entry["paper"].title, pages=pages, ideas=ideas,
                                          access=access, already=already)
            except Exception as exc:
                logger.warning("%s reading %s failed", agent, pid, exc_info=True)
                annotations.set_read(uid, pid, key, idea["card_id"], agent, "failed", str(exc)[:200])
                continue
            for m in marks:
                annotations.add(uid, pid, author=agent, state="suggested", text=m["quote"], comment=m["comment"],
                                page_index=m["page_index"], page_label=str(m["page_index"] + 1), space=key,
                                card_id="" if m["card_id"].startswith(CONVERSATION) else m["card_id"], rel=m["rel"],
                                chat_id=idea["chat_id"])
            annotations.set_read(uid, pid, key, idea["card_id"], agent, "done",
                                 f"{len(marks)} passage{'s' if len(marks) != 1 else ''}")

    _spawn(go())
    return {"started": True}


# Pippin reads every paper once per space, against what the researcher is working on (not for one idea)
COMPANION = "pippin"


class Companion(BaseModel):
    space: str = ""
    card_id: str = ""
    chat_id: str = ""


@router.post("/papers/{ident}/companion")
async def companion_read(ident: str, body: Companion) -> dict[str, Any]:
    """Pippin reads the paper with you and writes notes in its margin (once per paper and space)."""
    from aurora.auth.store import auth_store
    from aurora.literature import companion
    entry = _paper(ident)
    key, cid = _space(body.space)
    uid, pid = _uid(), entry["paper_id"]
    if not auth_store.get_pref(uid, "reader_agents_mark", True):
        return {"started": False, "why": "The agents mark passages only when you ask"}
    if annotations.reads(uid, pid, key, COMPANION):
        return {"started": False, "why": "Pippin has read it here"}
    pages = await _texts(entry, cid)
    if pages is None:
        return {"started": False, "why": "There is nothing to read: no PDF, or the page could not be fetched"}
    access = _access(cid)
    if not access.api_key:
        return {"started": False, "why": f"No API key is set: {access.missing}"}
    idea = _read_for(key, body.card_id, body.chat_id)
    annotations.set_read(uid, pid, key, COMPANION, "Pippin", "reading")

    async def go() -> None:
        try:
            if not any(p.strip() for p in pages):
                annotations.set_read(uid, pid, key, COMPANION, "Pippin", "failed", "There is no text to read")
                return
            context = companion.context_of(uid, cid, idea["text"] if idea else "")
            already = [a["text"] for a in annotations.for_paper(pid, uid, key)]
            notes = await companion.read(key=entry["key"], title=entry["paper"].title, pages=pages, context=context,
                                         access=access, already=already)
            for n in notes:
                a = annotations.add(uid, pid, author="Pippin", state="suggested", text=n["quote"], comment=n["note"],
                                    page_index=n["page_index"], page_label=str(n["page_index"] + 1), space=key,
                                    rel=n["rel"], kind=n["kind"], chat_id=idea["chat_id"] if idea else "")
                if n["figure_brief"]:
                    try:
                        html = await companion.figure(key=entry["key"], title=entry["paper"].title, quote=n["quote"],
                                                      page_index=n["page_index"], page_text=pages[n["page_index"]],
                                                      brief=n["figure_brief"], access=access)
                    except Exception:
                        logger.info("Pippin's figure for %s failed", pid, exc_info=True)
                        html = ""
                    if html:
                        annotations.update(a["ann_id"], touch=False, figure=html, inline=True)
            annotations.set_read(uid, pid, key, COMPANION, "Pippin", "done",
                                 f"{len(notes)} note{'s' if len(notes) != 1 else ''}")
        except Exception as exc:
            logger.warning("Pippin reading %s failed", pid, exc_info=True)
            annotations.set_read(uid, pid, key, COMPANION, "Pippin", "failed", str(exc)[:200])

    _spawn(go())
    return {"started": True}


@router.get("/notes/{ann_id}/figure")
async def note_figure(ann_id: str) -> HTMLResponse:
    """The figure Pippin made for a passage: a page shown in a sandboxed frame, allowed nothing but its own script."""
    a, _ = _seen(ann_id)
    if not a.get("figure"):
        raise HTTPException(404, "This note has no figure")
    from aurora.figures import TELL           # the frame's height follows the figure's
    return HTMLResponse(a["figure"] + TELL, headers={
        "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                   "img-src data: blob:; font-src data:; sandbox allow-scripts",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Notes


class NewNote(BaseModel):
    space: str = ""
    card_id: str = ""
    chat_id: str = ""
    text: str
    comment: str = ""
    page_index: int = 0
    page_label: str = ""
    rects: list[list[float]] = []
    sort_index: str = ""
    draft: bool = False
    ask: str = ""            # an agent to ask about the passage
    inline: bool = False     # open a gap in the page under the words, and write the note there


def _mentioned(text: str) -> list[str]:
    return ag.mentioned(text)


@router.post("/papers/{ident}/notes")
async def add_note(ident: str, body: NewNote) -> dict[str, Any]:
    entry = _paper(ident)
    key, cid = _space(body.space)
    if not body.text.strip():
        raise HTTPException(400, "Mark some words of the paper")
    if body.ask and body.ask not in AGENT_COLORS:
        raise HTTPException(400, f"ask is one of {list(AGENT_COLORS)}")
    idea = _idea(key, body.card_id, body.chat_id)
    comment = body.comment.strip()
    if body.ask and not comment:
        comment = f"@{body.ask} What does this mean for the idea?" if idea else f"@{body.ask} What do you make of this?"
    a = annotations.add(_uid(), entry["paper_id"], state="draft" if body.draft else "kept", text=" ".join(body.text.split()),
                        comment=comment, page_index=body.page_index, page_label=body.page_label, rects=body.rects,
                        sort_index=body.sort_index, space=key, card_id=idea["card_id"] if idea else "",
                        chat_id=body.chat_id, inline=body.inline)
    if not body.draft:
        for agent in dict.fromkeys([body.ask] if body.ask else _mentioned(comment)):
            _ask(a["ann_id"], agent, cid)
        _to_zotero(_uid())
    return _note_out(a["ann_id"], key, cid)


def _note_out(ann_id: str, space: str, campaign_id: str) -> dict[str, Any]:
    from aurora.literature.zotero import notes_target
    a = annotations.get(ann_id)
    if a is None:
        raise HTTPException(404, "No such note")
    a["replies"] = annotations.replies(ann_id)
    return _note_view(a, notes_target(a["owner"], a["paper_id"]))


def _own(ann_id: str) -> tuple[dict[str, Any], str]:
    """A note of yours (and its project, '' for your own space)."""
    a = annotations.get(ann_id)
    if a is None or a["owner"] != _uid():
        raise HTTPException(404, "No such note")
    cid = "" if a["space"].startswith("user:") or not a["space"] else a["space"]
    if cid and "view" not in rights(current_user(), cid):
        raise HTTPException(404, "No such note")
    return a, cid


def _seen(ann_id: str) -> tuple[dict[str, Any], str]:
    """A note you may see: yours, or one shared with a project you are in."""
    a = annotations.get(ann_id)
    if a is None or a["deleted_at"]:
        raise HTTPException(404, "No such note")
    cid = "" if a["space"].startswith("user:") or not a["space"] else a["space"]
    if a["owner"] != _uid() and not (a["shared"] and cid and "view" in rights(current_user(), cid)):
        raise HTTPException(404, "No such note")
    return a, cid


class Place(BaseModel):
    page_index: int
    page_label: str = ""
    rects: list[list[float]]
    sort_index: str = ""


class NoteChange(BaseModel):
    comment: str | None = None
    keep: bool | None = None
    shared: bool | None = None
    place: Place | None = None


@router.patch("/notes/{ann_id}")
async def change_note(ann_id: str, body: NoteChange) -> dict[str, Any]:
    a, cid = _own(ann_id)
    fields: dict[str, Any] = {}
    asks: list[str] = []
    if body.place is not None:
        fields.update(page_index=body.place.page_index, page_label=body.place.page_label or str(body.place.page_index + 1),
                      rects=body.place.rects, sort_index=body.place.sort_index)
    if body.comment is not None:
        new = body.comment.strip()
        if a["author"] and a["state"] == "suggested":
            raise HTTPException(400, "Keep the suggestion first, then change it")
        fields["comment"] = new
        asks = [x for x in _mentioned(new) if x not in _mentioned(a["comment"]) or a["state"] == "draft"]
        if a["state"] == "draft":
            fields["state"] = "kept"
    if body.keep:
        fields["state"] = "kept"
    if body.shared is not None:
        if body.shared and (not cid or "comment" not in rights(current_user(), cid)):
            raise HTTPException(403, "You can share notes only in a project where you may comment")
        fields["shared"] = body.shared
    touch = a["state"] == "kept" or fields.get("state") == "kept"
    annotations.update(ann_id, touch=touch, **fields)
    for agent in asks:
        _ask(ann_id, agent, cid)
    if touch:
        _to_zotero(a["owner"])
    return _note_out(ann_id, a["space"], cid)


@router.delete("/notes/{ann_id}")
async def delete_note(ann_id: str) -> dict[str, Any]:
    """Deleted here; Zotero is told after a few seconds (Undo until then)."""
    a, _ = _own(ann_id)
    annotations.remove(ann_id)
    _to_zotero(a["owner"])
    return {"deleted": True, "in_zotero": bool(a["zotero_key"])}


@router.post("/notes/{ann_id}/restore")
async def restore_note(ann_id: str) -> dict[str, Any]:
    a, cid = _own(ann_id)
    if a["deleted_at"] is None:
        return _note_out(ann_id, a["space"], cid)
    annotations.restore(ann_id)
    return _note_out(ann_id, a["space"], cid)


class Reply(BaseModel):
    text: str


@router.post("/notes/{ann_id}/replies")
async def reply(ann_id: str, body: Reply) -> dict[str, Any]:
    a, cid = _seen(ann_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Write something")
    if a["owner"] != _uid() and "comment" not in rights(current_user(), cid):
        raise HTTPException(403, "You may not comment in this project")
    annotations.add_reply(ann_id, _uid(), text)
    agents = _mentioned(text) or ([a["author"]] if a["author"] else [])
    for agent in agents:
        _ask(ann_id, agent, cid)
    if a["state"] == "kept" and a["zotero_key"]:
        annotations.update(ann_id)
        _to_zotero(a["owner"])
    return _note_out(ann_id, a["space"], cid)


# ---------------------------------------------------------------------------
# Settings and Zotero


class ReaderSettings(BaseModel):
    agents_mark: bool


@router.put("/settings")
async def settings(body: ReaderSettings) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    auth_store.set_pref(_uid(), "reader_agents_mark", body.agents_mark)
    return {"agents_mark": body.agents_mark}


class ToZotero(BaseModel):
    space: str = ""


@router.post("/papers/{ident}/zotero")
async def add_to_zotero(ident: str, body: ToZotero) -> dict[str, Any]:
    """Put the paper and its PDF into your Zotero (the project's collection), so the notes can follow."""
    from aurora.literature.zotero import ZoteroError, add_paper
    entry = _paper(ident)
    _, cid = _space(body.space)
    pid = entry["paper_id"]
    pdf = _pdf(pid, cid) or await reading.fetch_open_pdf(pid)
    who = "You" if current_user().is_local else current_user().name
    try:
        return await add_paper(_uid(), who, pid, cid, pdf)
    except ZoteroError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sync")
async def sync_now() -> dict[str, Any]:
    """Bring the notes to Zotero (and Zotero's back) now."""
    from aurora.literature.zotero import ZoteroError, sync_notes, zotero_store
    if zotero_store.link(_uid()) is None:
        raise HTTPException(400, "Zotero is not connected")
    try:
        await sync_notes(_uid())
    except ZoteroError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}

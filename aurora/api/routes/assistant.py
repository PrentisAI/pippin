"""Pippin at your side: ``/api/assistant``, the conversation of the desktop orb and the side panel."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.auth.guard import current_user

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class Page(BaseModel):
    url: str
    title: str = ""
    text: str = ""               # as the browser shows it (signed-in pages too)
    selection: str = ""


class Said(BaseModel):
    text: str = ""
    audio: str = ""              # base64; recognised here when the text is not given
    audio_type: str = "wav"
    screenshot: str = ""         # the window in front when they spoke ("this figure")
    via: str = "typed"           # typed | voice
    context: str = ""            # what they are looking at in the console (⌘K): the project, the run, a selection
    page: Page | None = None     # the page open in the browser, asked about from the extension's side panel


def _access() -> tuple[Any, Any, dict[str, str]]:
    from aurora.api.routes.brainstorm import _access as bs_access
    from aurora.api.routes.campaigns import base_config
    from aurora.api.routes.memory import _access as mem_access
    from aurora.config import AuroraConfig
    a = bs_access()
    if not a.api_key:
        raise HTTPException(400, f"No API key is set: {a.missing}")
    cfg = AuroraConfig.model_validate({**base_config(), "objective": "assistant"})
    mem, models = mem_access()
    return a.__class__(api_key=a.api_key, base_url=a.base_url, model=cfg.models.frontdesk or a.model,
                       missing=a.missing), mem, models


@router.post("")
async def say(body: Said) -> dict[str, Any]:
    """One turn of the conversation: typed, or spoken (the recording, or the words the orb recognised)."""
    from aurora.memory import assistant, voice
    text = body.text.strip()
    if not text and body.audio:
        try:
            text = await voice.transcribe(base64.b64decode(body.audio), "." + body.audio_type.strip(".")[:5])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    if not text:
        raise HTTPException(400, "Say or type something")
    access, mem, models = _access()
    shot = body.screenshot if body.screenshot.startswith("data:image/") and len(body.screenshot) < 8_000_000 else ""
    return await assistant.ask(current_user().user_id, text[:4000], access=access, mem_access=mem, models=models,
                               screenshot=shot, via="voice" if body.via == "voice" else "typed", context=body.context[:2000],
                               page=body.page.model_dump() if body.page else None)


class Answer(BaseModel):
    confirm: bool


@router.post("/actions/{action_id}")
async def answer_action(action_id: str, body: Answer) -> dict[str, Any]:
    """Do what Pippin proposed (they pressed its button), or drop it. Their own rights in the project apply."""
    from aurora.memory import assistant
    try:
        return await assistant.act(current_user().user_id, action_id, body.confirm)
    except KeyError:
        raise HTTPException(404, "No such proposal") from None


@router.get("/history")
async def history(limit: int = 60) -> list[dict[str, Any]]:
    from aurora.memory import assistant
    return assistant.history(current_user().user_id, min(max(limit, 1), 200))


@router.delete("/history", status_code=204)
async def new_conversation() -> None:
    from aurora.memory import assistant
    assistant.clear(current_user().user_id)


async def _keep_page(uid: str, page: Page) -> dict[str, Any]:
    """The page in their Library (a paper's page: the paper), read as it was asked about."""
    from aurora.literature import pages
    from aurora.literature.library import library
    from aurora.memory import onpage
    p = await onpage.load(uid, url=page.url, title=page.title, text=page.text, selection=page.selection)
    if p["paper_id"]:
        library.add_item("", p["paper_id"], source="person", added_by=uid, owner=uid)
        entry = library.paper(p["paper_id"])
    else:
        try:
            entry = pages.save_page(uid, url=p["url"], title=page.title, text=page.text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    return {"paper_id": entry["paper_id"], "key": entry["key"], "title": entry["paper"].title,
            "is_page": entry["paper"].is_page}


@router.post("/page/open")
async def open_page(body: Page) -> dict[str, Any]:
    """Open the page in Pippin: it is kept in their Library and read there, at the passage they selected."""
    return await _keep_page(current_user().user_id, body)


class Keep(BaseModel):
    turn_id: str
    page: Page


@router.post("/page/keep")
async def keep_answer(body: Keep) -> dict[str, Any]:
    """Keep an answer about the page as a note on it: the passage (their selection, else the one it drew on), their
    question as the note, Pippin's answer as its reply. The page goes into their Library."""
    from aurora.literature.annotations import annotations
    from aurora.memory import assistant, onpage
    uid = current_user().user_id
    turns = assistant.history(uid, 200)
    i = next((k for k, t in enumerate(turns) if t["turn_id"] == body.turn_id and t["role"] == "assistant"), -1)
    if i < 0 or not turns[i].get("page"):
        raise HTTPException(404, "No such answer about a page")
    if turns[i].get("kept"):
        return turns[i]["kept"]
    question = next((t["text"] for t in reversed(turns[:i]) if t["role"] == "user"), "")
    entry = await _keep_page(uid, body.page)
    quote, at = onpage.anchor(uid)
    if not quote:
        raise HTTPException(400, "Select the words the answer is about, then keep it")
    a = annotations.add(uid, entry["paper_id"], state="kept", text=quote, comment=question, space=f"user:{uid}",
                        page_index=max(at, 0), page_label="" if entry["is_page"] else str(max(at, 0) + 1))
    annotations.add_reply(a["ann_id"], "Pippin", turns[i]["text"])
    kept = {**entry, "ann_id": a["ann_id"], "quote": quote}
    assistant.mark(uid, body.turn_id, kept=kept)
    if not entry["is_page"]:
        from aurora.api.routes.reader import _to_zotero
        _to_zotero(uid)
    return kept

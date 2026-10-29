"""Pippin at your side: ``/api/assistant``, the conversation of the desktop orb and the side panel."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.auth.guard import current_user

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class Said(BaseModel):
    text: str = ""
    audio: str = ""              # base64; recognised here when the text is not given
    audio_type: str = "wav"
    screenshot: str = ""         # the window in front when they spoke ("this figure")
    via: str = "typed"           # typed | voice
    context: str = ""            # what they are looking at in the console (⌘K): the project, the run, a selection


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
                               screenshot=shot, via="voice" if body.via == "voice" else "typed", context=body.context[:2000])


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

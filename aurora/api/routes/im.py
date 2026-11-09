"""Pippin in a chat app: ``/im/feishu``, ``/im/wecom``, ``/im/slack`` and ``/im/whatsapp`` take the apps' message
callbacks (checked by their token or signature, not by a sign-in); ``/api/im`` is the console's side (the team's apps, linking your account)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from aurora import im
from aurora.auth.guard import current_user

public_router = APIRouter(tags=["im"])
router = APIRouter(prefix="/api/im", tags=["im"])


@public_router.post("/im/feishu")
async def feishu(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Not JSON") from None
    try:
        return await im.feishu_event(body)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None


@public_router.get("/im/wecom")
async def wecom_check(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> PlainTextResponse:
    try:
        return PlainTextResponse(im.wecom_verify(msg_signature, timestamp, nonce, echostr))
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403, str(exc)) from None


@public_router.post("/im/wecom")
async def wecom(request: Request, msg_signature: str, timestamp: str, nonce: str) -> PlainTextResponse:
    try:
        await im.wecom_event(msg_signature, timestamp, nonce, await request.body())
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403, str(exc)) from None
    return PlainTextResponse("success")


@public_router.post("/im/slack")
async def slack(request: Request) -> dict[str, Any]:
    try:
        return await im.slack_event(request.headers.get("X-Slack-Request-Timestamp", ""),
                                    request.headers.get("X-Slack-Signature", ""), await request.body())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None


@public_router.get("/im/whatsapp")
async def whatsapp_check(request: Request) -> PlainTextResponse:
    q = request.query_params
    try:
        return PlainTextResponse(im.whatsapp_check(q.get("hub.mode", ""), q.get("hub.verify_token", ""),
                                                   q.get("hub.challenge", "")))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None


@public_router.post("/im/whatsapp")
async def whatsapp(request: Request) -> dict[str, Any]:
    try:
        await im.whatsapp_event(request.headers.get("X-Hub-Signature-256", ""), await request.body())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    return {}


def _callbacks() -> dict[str, str]:
    from aurora.api.routes.campaigns import console_url
    base = console_url()
    return {app: f"{base}/im/{app}" if base else f"/im/{app}" for app in im.APPS}


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    """The team's chat apps, and the callback address to give each."""
    return {"apps": {app: im.view(app) for app in im.APPS}, "callbacks": _callbacks()}


@router.put("/settings/{app}")
async def put_settings(app: str, body: dict[str, Any]) -> dict[str, Any]:
    if app not in im.APPS:
        raise HTTPException(404, "feishu, wecom, slack or whatsapp")
    return im.save_settings(app, body)


@router.get("/me")
async def me() -> dict[str, Any]:
    """Your linked chat accounts, and the apps you can link."""
    uid = current_user().user_id
    return {"links": im.links(uid), "ready": [app for app in im.APPS if im.ready(app)]}


@router.post("/code")
async def code() -> dict[str, Any]:
    """A code to send to the bot (绑定 <code>), valid for a quarter of an hour."""
    return {"code": im.new_code(current_user().user_id), "expires_in": im.CODE_TTL}


@router.delete("/links/{app}", status_code=204)
async def unlink(app: str) -> None:
    im.unlink(current_user().user_id, app)

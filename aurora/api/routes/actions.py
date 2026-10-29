"""``/a/<token>``: answering a decision from a link in a notification.

GET shows the decision and asks to confirm; only the POST of that form acts.  The link stands for
the person it was sent to, and they still need the right to answer the run's decisions.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from aurora.human import actions

router = APIRouter(tags=["actions"])

_STYLE = """
:root { --bg: #f7f6f2; --fg: #24221e; --dim: #6b665d; --line: #e2ded4; --ok: #2f7a4a; --stop: #a33a2b; color-scheme: light }
@media (prefers-color-scheme: dark) { :root { --bg: #17181a; --fg: #e7e4dc; --dim: #9a958b; --line: #2d2e31; --ok: #6cc18a; --stop: #e0806f; color-scheme: dark } }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.55 system-ui, -apple-system, sans-serif; }
main { max-width: 520px; margin: 0 auto; padding: 40px 18px; display: flex; flex-direction: column; gap: 14px; }
h1 { font-size: 20px; margin: 0; line-height: 1.3; }
.dim { color: var(--dim); font-size: 13px; }
.detail { white-space: pre-wrap; border-left: 3px solid var(--line); padding: 4px 12px; font-size: 13.5px; max-height: 40vh; overflow: auto; }
button { font: inherit; font-weight: 600; padding: 10px 18px; border-radius: 9px; border: none; color: #fff; cursor: pointer; }
.continue { background: var(--ok); } .stop { background: var(--stop); }
a { color: inherit; }
"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, "
        f"initial-scale=1'><meta name='robots' content='noindex'><title>{html.escape(title)}</title><style>{_STYLE}</style>"
        f"</head><body><main>{body}</main></body></html>", status_code=status,
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
                 "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})


def _lookup(token: str) -> tuple[dict, object, object]:
    from aurora.api.routes.campaigns import _ensure_campaign
    data = actions.read(token)
    run = _ensure_campaign(data["c"]).experiments.get(data["r"])
    if run is None:
        raise ValueError("the experiment no longer exists")
    gate = next((g for g in (run.engine.human_gate.pending if run.engine is not None else []) if g.gate_id == data["g"]), None)
    return data, run, gate


def _console(data: dict) -> str:
    from aurora.api.routes.campaigns import console_url
    base = console_url()
    return f"{base}/campaigns/{data['c']}/experiments/{data['r']}" if base else ""


@router.get("/a/{token}")
async def confirm_page(token: str) -> HTMLResponse:
    try:
        data, run, gate = _lookup(token)
    except ValueError as exc:
        return _page("Link not working", f"<h1>{html.escape(str(exc).capitalize())}</h1>", 410)
    link = _console(data)
    open_it = f"<p class='dim'><a href='{html.escape(link)}'>Open the experiment in Pippin</a></p>" if link else ""
    if gate is None:
        return _page("Already decided", f"<h1>This decision was already made</h1><p class='dim'>{html.escape(run.task_name)}</p>{open_it}")
    label = actions.LABELS[data["a"]]
    return _page(label, (
        f"<p class='dim'>{html.escape(run.task_name)} · decision needed</p><h1>{html.escape(gate.title)}</h1>"
        f"<div class='detail'>{html.escape(gate.detail[:4000])}</div>"
        f"<form method='post'><button class='{data['a']}' type='submit'>{html.escape(label)}</button></form>"
        "<p class='dim'>To answer otherwise (with an instruction, say), open the experiment.</p>" + open_it))


@router.post("/a/{token}")
async def act(token: str, request: Request) -> HTMLResponse:
    from aurora.api.routes.campaigns import may_decide
    from aurora.auth.guard import CURRENT
    from aurora.auth.permissions import LOCAL_USER
    from aurora.auth.store import auth_store
    try:
        data, run, gate = _lookup(token)
    except ValueError as exc:
        return _page("Link not working", f"<h1>{html.escape(str(exc).capitalize())}</h1>", 410)
    if gate is None:
        return _page("Already decided", "<h1>This decision was already made</h1>")
    if data["u"] in ("", "local"):
        user = LOCAL_USER
    else:
        row = auth_store.get_user(data["u"])
        if row is None:
            return _page("Not allowed", "<h1>This person no longer has an account</h1>", 403)
        user = auth_store.as_user(row)
    token_ctx = CURRENT.set(user)
    try:
        allowed, who = may_decide(data["c"], run)
        if not allowed:
            return _page("Not allowed", "<h1>In this project another person answers this run’s decisions</h1>", 403)
        ok = run.engine.human_gate.resolve(gate.gate_id, data["a"], "", by=who or user.name)
    finally:
        CURRENT.reset(token_ctx)
    if not ok:
        return _page("Already decided", "<h1>This decision was already made</h1>")
    auth_store.set_pref(user.user_id, "onboarding_decided", True)
    auth_store.log(user.user_id if not user.is_local else None, f"answered a decision from a notification: {data['a']}",
                   data["c"], run.task_name)
    done = "Approved: the experiment continues." if data["a"] == "continue" else "Stopped: the experiment ends after it saves its work."
    link = _console(data)
    return _page("Done", f"<h1>{done}</h1><p class='dim'>{html.escape(run.task_name)}</p>"
                         + (f"<p><a href='{html.escape(link)}'>Open the experiment in Pippin</a></p>" if link else ""))

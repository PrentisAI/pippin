"""Read-only links to a project's report, for people outside the team.

``/api/campaigns/{id}/link`` makes, extends or revokes the project's link (the right to share is
needed, the team's rule must allow links, and a confidential project has none).  ``/r/{token}``
is the page anyone with the link sees: the latest report of the project and its figures, nothing
else, until the link expires.
"""

from __future__ import annotations

import html
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from aurora.auth.guard import current_user, rights
from aurora.auth.store import auth_store

router = APIRouter(prefix="/api/campaigns", tags=["links"])
public_router = APIRouter(tags=["links"])

DAYS = (7, 30, 90)
_FIGURES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf"}


def _base() -> str:
    from aurora.api.routes.campaigns import console_url
    return console_url()


def _why_not(campaign_id: str) -> str:
    """Why this person cannot make a link here, or ''."""
    me = current_user()
    access = auth_store.project_access(campaign_id) or {}
    if access.get("confidential"):
        return "Not available: the project is confidential."
    if "share" not in rights(me, campaign_id):
        return "Only people who may share the project can make a link."
    if not me.is_admin and not auth_store.rules().get("external_links", True):
        return "The team’s rules do not allow links outside the team."
    return ""


def _view(campaign_id: str) -> dict[str, Any]:
    link = auth_store.active_link(campaign_id)
    out: dict[str, Any] = {"link": None, "allowed": not _why_not(campaign_id), "reason": _why_not(campaign_id),
                           "has_report": _latest_report(campaign_id) is not None}
    if link:
        days = round((link["expires_at"] - link["created_at"]) / 86400)
        out["link"] = {"path": f"/r/{link['token']}", "url": f"{_base()}/r/{link['token']}" if _base() else "",
                       "days": days, "expires_at": link["expires_at"], "created_at": link["created_at"]}
    return out


@router.get("/{campaign_id}/link")
async def get_link(campaign_id: str) -> dict[str, Any]:
    return _view(campaign_id)


class LinkBody(BaseModel):
    days: int = 30


@router.post("/{campaign_id}/link")
async def make_link(campaign_id: str, body: LinkBody) -> dict[str, Any]:
    """Anyone with the link can read the report: make the link, or change how long it works."""
    reason = _why_not(campaign_id)
    if reason:
        raise HTTPException(403, reason)
    if body.days not in DAYS:
        raise HTTPException(400, f"A link works for {', '.join(map(str, DAYS))} days")
    me = current_user()
    existed = auth_store.active_link(campaign_id) is not None
    auth_store.set_link(campaign_id, me.user_id, body.days)
    auth_store.log(me.user_id, ("changed the read-only link" if existed else "made a read-only link to the report"),
                   campaign_id, f"{body.days} days")
    return _view(campaign_id)


@router.delete("/{campaign_id}/link")
async def revoke_link(campaign_id: str) -> dict[str, Any]:
    if "share" not in rights(current_user(), campaign_id):
        raise HTTPException(403, "Only people who may share the project can stop its link")
    if auth_store.revoke_links(campaign_id):
        auth_store.log(current_user().user_id, "stopped the read-only link", campaign_id)
    return _view(campaign_id)


# ---------------------------------------------------------------------------- the public page

def _latest_report(campaign_id: str) -> tuple[Any, Path] | None:
    from aurora.api.routes.campaigns import _ensure_campaign, _report_path
    try:
        camp = _ensure_campaign(campaign_id)
    except HTTPException:
        return None
    for run in sorted(camp.experiments.values(), key=lambda r: -r.started_at):
        path = _report_path(run)
        if path is not None:
            return run, path
    return None


def _valid(token: str) -> dict[str, Any]:
    link = auth_store.link(token)
    if link is None or link["revoked_at"]:
        raise HTTPException(404, "This link does not work any more")
    if link["expires_at"] < time.time():
        raise HTTPException(410, "This link has expired")
    if (auth_store.project_access(link["campaign_id"]) or {}).get("confidential"):
        raise HTTPException(404, "This link does not work any more")
    return link


_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; frame-ancestors 'none'"
_STYLE = """
:root { --bg: #fbfaf7; --fg: #23211d; --dim: #6c675e; --line: #e4e0d8; --accent: #2f5d8a; color-scheme: light }
@media (prefers-color-scheme: dark) { :root { --bg: #17181a; --fg: #e6e3dc; --dim: #9a958c; --line: #2c2d30; --accent: #8fb5dc; color-scheme: dark } }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.65 Georgia, 'Times New Roman', serif; }
main { max-width: 760px; margin: 0 auto; padding: 32px 18px 64px; }
header { font: 13px/1.5 system-ui, sans-serif; color: var(--dim); border-bottom: 1px solid var(--line); padding-bottom: 14px; margin-bottom: 28px; }
header b { color: var(--fg); font-weight: 600; }
h1, h2, h3 { font-family: system-ui, sans-serif; line-height: 1.25; text-wrap: balance; }
img { max-width: 100%; height: auto; }
pre { overflow-x: auto; background: color-mix(in srgb, var(--line) 45%, transparent); padding: 12px; border-radius: 6px; font-size: 13px; }
code { font-size: .9em; }
table { border-collapse: collapse; display: block; overflow-x: auto; font: 14px/1.4 system-ui, sans-serif; }
th, td { border: 1px solid var(--line); padding: 6px 10px; text-align: left; }
a { color: var(--accent); }
footer { margin-top: 48px; font: 12px/1.5 system-ui, sans-serif; color: var(--dim); }
"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
           f"content=\"width=device-width, initial-scale=1\"><meta name=\"robots\" content=\"noindex\">"
           f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head><body><main>{body}</main></body></html>")
    return HTMLResponse(doc, status_code=status, headers={"Content-Security-Policy": _CSP,
                                                          "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})


def render_report(text: str, token: str, run_id: str, report_dir: Path, root: Path) -> str:
    """The report's Markdown as HTML, figures served through the link.  HTML in the report cannot run
    anything: the page's Content-Security-Policy allows no scripts, frames or outside resources."""
    import markdown
    text = re.sub(r"(?is)<(script|iframe|object|embed|form)\b.*?(</\1>|$)", "", text)
    out = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])

    def fix(m: re.Match[str]) -> str:
        src = html.unescape(m.group(2))
        if re.match(r"^(https?:|data:|/)", src):
            return m.group(0) if src.startswith("data:") else m.group(1) + '""'
        target = (report_dir / src).resolve()
        if not target.is_relative_to(root):
            return m.group(1) + '""'
        rel = target.relative_to(root).as_posix()
        return f'{m.group(1)}"/r/{token}/files/{quote(run_id)}/{quote(rel)}"'
    return re.sub(r'(<img[^>]*?\ssrc=)"([^"]*)"', fix, out)


@public_router.get("/r/{token}")
async def read_report(token: str) -> HTMLResponse:
    from aurora.api.routes.campaigns import _ensure_campaign
    try:
        link = _valid(token)
    except HTTPException as exc:
        return _page("Link not working", f"<h1>{html.escape(str(exc.detail))}</h1><p>Ask the person who sent it for a new one.</p>",
                     exc.status_code)
    camp = _ensure_campaign(link["campaign_id"])
    found = _latest_report(link["campaign_id"])
    until = time.strftime("%d %b %Y", time.localtime(link["expires_at"]))
    head = (f"<header><b>{html.escape(camp.name)}</b> · a report shared read-only through AURORA · "
            f"the link works until {until}</header>")
    if found is None:
        return _page(camp.name, head + "<p>The project has no report yet. Come back later: the link shows the latest one.</p>")
    run, path = found
    root = run.campaign_dir.resolve()
    body = render_report(path.read_text(errors="replace")[:400_000], token, run.run_id, path.parent.resolve(), root)
    when = time.strftime("%d %b %Y", time.localtime(path.stat().st_mtime))
    return _page(camp.name, head + body + f"<footer>From the run “{html.escape(run.task_name or run.run_id)}”, "
                 f"written {when}. Read only: nothing else of the project is shared.</footer>")


@public_router.get("/r/{token}/files/{run_id}/{path:path}")
async def report_figure(token: str, run_id: str, path: str) -> FileResponse:
    from aurora.api.routes.campaigns import _ensure_campaign
    link = _valid(token)
    run = _ensure_campaign(link["campaign_id"]).experiments.get(run_id)
    found = _latest_report(link["campaign_id"])
    if run is None or found is None or found[0].run_id != run_id:
        raise HTTPException(404, "Not found")
    root = run.campaign_dir.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.suffix.lower() not in _FIGURES:
        raise HTTPException(404, "Not found")
    return FileResponse(target, headers={"Content-Security-Policy": _CSP, "Cache-Control": "no-store"})

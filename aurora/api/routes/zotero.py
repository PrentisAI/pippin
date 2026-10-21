"""Connecting your Zotero: the key, which collection goes with which project, and syncing.

Everything here is the signed-in person's own: their key, their collections, their mappings.
Mapping a collection to a project needs edit rights in that project.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.auth import secrets
from aurora.auth.guard import current_user, rights, visible
from aurora.literature import zotero as z
from aurora.literature.zotero import ZoteroClient, ZoteroError, zotero_store

router = APIRouter(prefix="/api/zotero", tags=["zotero"])


def _me() -> tuple[str, str]:
    user = current_user()
    return user.user_id, ("You" if user.is_local else user.name)


def _projects() -> list[dict[str, str]]:
    """Projects the person may map a collection to (they can edit them)."""
    from aurora.api.routes.campaigns import _ensure_campaign, store
    me = current_user()
    out = []
    for cid in visible(me, [row["campaign_id"] for row in store.list_campaigns()]):
        if "edit" in rights(me, cid):
            try:
                out.append({"campaign_id": cid, "name": _ensure_campaign(cid).name})
            except Exception:
                continue
    return out


def _status(user_id: str) -> dict[str, Any]:
    link = zotero_store.link(user_id)
    if link is None:
        return {"connected": False}
    names = {p["campaign_id"]: p["name"] for p in _projects()}
    access = link["access"]
    return {
        "connected": True, "method": link["method"], "username": link["username"],
        "can_write": link["method"] == "web" and bool((access.get("user") or {}).get("write")),
        "files": bool((access.get("user") or {}).get("files", link["method"] == "local")),
        "options": link["options"], "last_sync": link["last_sync"], "last_error": link["last_error"],
        "last_report": link["last_report"], "syncing": z.syncing(user_id),
        "key_hint": secrets.key_status(f"user:{user_id}", "zotero")["hint"] if link["method"] == "web" else "",
        "mappings": [{**m, "project": names.get(m["campaign_id"], "") if m["campaign_id"] else "Your Library"}
                     for m in zotero_store.maps(user_id)],
    }


@router.get("")
async def get_zotero() -> dict[str, Any]:
    user_id, _ = _me()
    return _status(user_id)


class Connect(BaseModel):
    method: str             # web | local
    api_key: str = ""


@router.post("/connect")
async def connect(body: Connect) -> dict[str, Any]:
    """Check the key (or that Zotero answers on this computer) and remember the connection."""
    user_id, _ = _me()
    if body.method not in ("web", "local"):
        raise HTTPException(400, "method must be web or local")
    key = body.api_key.strip()
    if body.method == "web" and len(key) < 10:
        raise HTTPException(400, "Paste the private key from zotero.org → Settings → Security → Create new private key")
    client = ZoteroClient(body.method, key, client=z.http_client)
    try:
        info = await client.whoami()
    except ZoteroError as exc:
        raise HTTPException(400, str(exc)) from exc
    access = info.get("access") or {}
    if body.method == "web" and not (access.get("user") or {}).get("library") and not access.get("groups"):
        raise HTTPException(400, "The key has no library access: create it with “Allow library access” ticked")
    if body.method == "web":
        secrets.put_key(f"user:{user_id}", "zotero", key)
    zotero_store.save_link(user_id, body.method, str(info.get("userID", "0")), info.get("username", ""), access)
    return _status(user_id)


def _path(c: dict[str, Any] | None, by_key: dict[str, dict[str, Any]], lib_name: str) -> str:
    """"My Library / Bike share / Citi Bike": a collection with its parents."""
    parts, seen = [], set()
    while c is not None and c["key"] not in seen:
        seen.add(c["key"])
        parts.append((c.get("data") or {}).get("name", ""))
        parent = (c.get("data") or {}).get("parentCollection")
        c = by_key.get(parent) if parent else None
    return " / ".join([lib_name, *reversed(parts)])


@router.get("/collections")
async def list_collections() -> dict[str, Any]:
    """Every collection the connection can see (your library and your groups), with suggested projects."""
    user_id, _ = _me()
    try:
        client = z.client_for(user_id)
        libraries = [("user", "My Library")] + [(f"group:{g['id']}", g["name"]) for g in await client.groups()]
        rows: list[dict[str, Any]] = []
        for lib, lib_name in libraries:
            cols = await client.collections(lib)
            by_key = {c["key"]: c for c in cols}
            rows += [{"library": lib, "collection_key": c["key"], "name": _path(c, by_key, lib_name),
                      "items": (c.get("meta") or {}).get("numItems", 0)} for c in cols]
    except ZoteroError as exc:
        raise HTTPException(400, str(exc)) from exc
    projects = _projects()
    current = {(m["library"], m["collection_key"]): m["campaign_id"] for m in zotero_store.maps(user_id)}
    suggested = z.suggest(rows, projects) if not current else {}
    for r in rows:
        r["campaign_id"] = current.get((r["library"], r["collection_key"]), suggested.get(r["collection_key"]))
    rows.sort(key=lambda r: r["name"].lower())
    return {"collections": rows, "projects": projects, "suggested": bool(suggested)}


class Mapping(BaseModel):
    library: str
    collection_key: str
    name: str = ""
    campaign_id: str            # '' : your own Library


class Settings(BaseModel):
    mappings: list[Mapping] | None = None
    options: dict[str, bool] | None = None


@router.put("")
async def save_settings(body: Settings) -> dict[str, Any]:
    """Which collection goes with which project, and what flows each way; then sync."""
    user_id, who = _me()
    if zotero_store.link(user_id) is None:
        raise HTTPException(400, "Connect Zotero first")
    if body.mappings is not None:
        allowed = {p["campaign_id"] for p in _projects()}
        for m in body.mappings:
            if m.campaign_id and m.campaign_id not in allowed:
                raise HTTPException(403, f"You cannot add papers to the project of “{m.name}”")
        zotero_store.set_maps(user_id, [m.model_dump() for m in body.mappings])
    if body.options is not None:
        zotero_store.set_options(user_id, body.options)
    z.start_sync(user_id, who)
    return _status(user_id)


@router.post("/sync")
async def sync_now(campaign_id: str = "", soon: bool = False) -> dict[str, Any]:
    """Sync now (all your collections, or one project's). ``soon``: skip if it synced a minute ago."""
    user_id, who = _me()
    if zotero_store.link(user_id) is None:
        return {"connected": False}
    z.start_sync(user_id, who, campaign_id or None, min_gap=60 if soon else 0)
    return _status(user_id)


@router.delete("")
async def disconnect() -> dict[str, Any]:
    """Forget the key and the mappings; papers already brought in stay, Zotero is not touched."""
    user_id, _ = _me()
    zotero_store.forget(user_id)
    from aurora.auth.store import auth_store
    auth_store.delete_secret(f"user:{user_id}", "zotero")
    return {"connected": False}


class Push(BaseModel):
    paper_ids: list[str] = []      # empty: every verified paper the collection lacks


@router.post("/push/{campaign_id}")
async def push(campaign_id: str, body: Push) -> dict[str, Any]:
    """Send a project's papers (``library``: your Library's) to your collection for it now."""
    user_id, who = _me()
    if campaign_id == "library":
        campaign_id = ""
    if campaign_id and "edit" not in rights(current_user(), campaign_id):
        raise HTTPException(404, "No such project")
    link = zotero_store.link(user_id)
    maps = [m for m in zotero_store.maps(user_id) if m["campaign_id"] == campaign_id]
    if link is None or not maps:
        raise HTTPException(400, "No Zotero collection goes with this project yet: choose one in the Zotero window")
    if link["method"] != "web":
        raise HTTPException(400, "Zotero on this computer is read-only: connect your Zotero account to send papers")
    report = z.SyncReport()
    try:
        client = z.client_for(user_id)
        for m in maps:
            await z.push(user_id, m, client, report, only=set(body.paper_ids) or None)
    except ZoteroError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"pushed": report.pushed, "problems": report.problems, "zotero": _status(user_id)}

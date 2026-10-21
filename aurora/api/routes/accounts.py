"""Signing in, the signed-in user, the team, and sharing a project.

* ``/api/auth``: sign in and out, look at and accept an invitation (no sign-in needed);
* ``/api/me``: your profile and your API keys;
* ``/api/team``: people, invitations, groups, rules, the team's key and the activity log;
* ``/api/campaigns/{id}/access``: who is in a project, with which rights, and its sharing settings.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from aurora.auth import secrets as keys
from aurora.auth.guard import COOKIE, accounts_on, current_user, rights, user_of
from aurora.auth.passwords import check_new_password, verify_password
from aurora.auth.permissions import PERM_LABELS, PERMS, ROLES, TEAM_ACCESS, TEAM_ROLES, member_rights
from aurora.auth.store import SESSION_DAYS, auth_store

auth_router = APIRouter(prefix="/api/auth", tags=["accounts"])
me_router = APIRouter(prefix="/api/me", tags=["accounts"])
team_router = APIRouter(prefix="/api/team", tags=["accounts"])
access_router = APIRouter(prefix="/api/campaigns", tags=["accounts"])


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")


def _invite_link(request: Request, token: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/invite/{token}"


def _person(u: dict[str, Any], admin_view: bool = False) -> dict[str, Any]:
    out = {"user_id": u["user_id"], "email": u["email"], "name": u["name"], "team_role": u["team_role"],
           "org": u.get("org") or ""}
    if admin_view:
        out["last_active"] = u.get("last_active")
        out["key"] = keys.key_for(auth_store.as_user(u))[1]     # own | team | none: what their calls use
    return out


# ---------------------------------------------------------------------------
# /api/auth
# ---------------------------------------------------------------------------

class Login(BaseModel):
    email: str
    password: str


@auth_router.get("/status")
async def auth_status(request: Request) -> dict[str, Any]:
    """Whether the console has accounts, and who is signed in."""
    user = user_of(request)
    return {"accounts": accounts_on(), "user": user.public() if user else None,
            "team": auth_store.team_name() if accounts_on() else None}


@auth_router.post("/login")
async def login(body: Login, request: Request, response: Response) -> dict[str, Any]:
    if not accounts_on():
        raise HTTPException(400, "This console has no accounts; nothing to sign in to")
    row = auth_store.check_login(body.email, body.password)
    if row is None:
        await asyncio.sleep(0.5)              # slows down guessing
        raise HTTPException(401, "Wrong email or password")
    _set_cookie(response, request, auth_store.new_session(row["user_id"]))
    auth_store.log(row["user_id"], "signed in")
    return {"user": auth_store.as_user(row).public()}


@auth_router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(COOKIE)
    if token:
        auth_store.end_session(token)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


def _invite_view(inv: dict[str, Any]) -> dict[str, Any]:
    project = None
    if inv.get("campaign_id"):
        from aurora.api.routes.campaigns import store
        row = store.get_campaign(inv["campaign_id"])
        project = row["name"] if row else None
    by = auth_store.get_user(inv["created_by"]) if inv.get("created_by") else None
    return {"email": inv["email"], "team_role": inv["team_role"], "team": auth_store.team_name(),
            "project": project, "project_role": inv.get("project_role"),
            "invited_by": by["name"] if by else None, "has_account": auth_store.user_by_email(inv["email"]) is not None}


@auth_router.get("/invites/{token}")
async def see_invite(token: str) -> dict[str, Any]:
    inv = auth_store.invite_by_token(token)
    if inv is None:
        raise HTTPException(404, "This invitation is not valid any more; ask for a new one")
    return _invite_view(inv)


class Accept(BaseModel):
    name: str = ""
    password: str = ""


@auth_router.post("/invites/{token}/accept")
async def accept_invite(token: str, body: Accept, request: Request, response: Response) -> dict[str, Any]:
    inv = auth_store.invite_by_token(token)
    if inv is None:
        raise HTTPException(404, "This invitation is not valid any more; ask for a new one")
    row = auth_store.user_by_email(inv["email"])
    if row is None or row["disabled"]:
        problem = check_new_password(body.password)
        if problem:
            raise HTTPException(400, problem)
        if row is None:
            org = inv["email"].split("@")[1] if inv["team_role"] == "guest" else ""
            row = auth_store.add_user(inv["email"], body.name, body.password, inv["team_role"], org=org)
        else:                                  # a removed account comes back
            auth_store.update_user(row["user_id"], disabled=0, password=body.password, name=body.name or row["name"],
                                   team_role=inv["team_role"])
            row = auth_store.get_user(row["user_id"])
    elif not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "You already have an account: enter its password to accept")
    if inv.get("campaign_id"):
        auth_store.set_member(inv["campaign_id"], f"user:{row['user_id']}", inv["project_role"] or "commenter")
    auth_store.mark_invite_used(token)
    auth_store.log(row["user_id"], "accepted an invitation", inv.get("campaign_id") or "team")
    _set_cookie(response, request, auth_store.new_session(row["user_id"]))
    return {"user": auth_store.as_user(row).public(), "campaign_id": inv.get("campaign_id")}


# ---------------------------------------------------------------------------
# /api/me
# ---------------------------------------------------------------------------

class MeUpdate(BaseModel):
    name: str | None = None
    password: str | None = None
    current_password: str = ""


@me_router.get("")
async def me() -> dict[str, Any]:
    user = current_user()
    return {"user": user.public(), "team": auth_store.team_name() if accounts_on() else None,
            "accounts": accounts_on()}


@me_router.put("")
async def update_me(body: MeUpdate) -> dict[str, Any]:
    user = current_user()
    if user.is_local:
        raise HTTPException(400, "This console has no accounts")
    if body.password is not None:
        row = auth_store.get_user(user.user_id)
        if not verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(401, "Your current password is wrong")
        problem = check_new_password(body.password)
        if problem:
            raise HTTPException(400, problem)
        auth_store.update_user(user.user_id, password=body.password)
    if body.name is not None and body.name.strip():
        auth_store.update_user(user.user_id, name=body.name.strip())
    return {"user": auth_store.as_user(auth_store.get_user(user.user_id)).public()}


class KeyBody(BaseModel):
    value: str


@me_router.get("/keys")
async def my_keys() -> dict[str, Any]:
    user = current_user()
    if user.is_local:
        return {"keys": [], "uses": "server", "note": "Without accounts the server's key (OPENROUTER_API_KEY) is used."}
    _, source = keys.key_for(user)
    return {"keys": [keys.key_status(f"user:{user.user_id}", p) for p in keys.PROVIDERS], "uses": source,
            "team_key_allowed": bool(auth_store.rules().get("team_key_for_members")) and not user.is_guest}


@me_router.put("/keys/{provider}")
async def set_my_key(provider: str, body: KeyBody) -> dict[str, Any]:
    user = current_user()
    if user.is_local:
        raise HTTPException(400, "Without accounts, set OPENROUTER_API_KEY for the server")
    try:
        hint = keys.put_key(f"user:{user.user_id}", provider, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    auth_store.log(user.user_id, "set their API key", provider)
    return {"provider": provider, "set": True, "hint": hint}


@me_router.delete("/keys/{provider}")
async def delete_my_key(provider: str) -> dict[str, Any]:
    user = current_user()
    auth_store.delete_secret(f"user:{user.user_id}", provider)
    auth_store.log(user.user_id, "removed their API key", provider)
    return {"provider": provider, "set": False}


# ---------------------------------------------------------------------------
# /api/team
# ---------------------------------------------------------------------------

class TeamUpdate(BaseModel):
    name: str


@team_router.get("")
async def team() -> dict[str, Any]:
    user = current_user()
    return {"name": auth_store.team_name(), "accounts": accounts_on(), "me": user.public(),
            "people": len(auth_store.list_users()), "roles": list(TEAM_ROLES)}


@team_router.put("")
async def rename_team(body: TeamUpdate) -> dict[str, Any]:
    auth_store.set_team_name(body.name)
    auth_store.log(current_user().user_id, "renamed the team", body.name)
    return {"name": auth_store.team_name()}


@team_router.get("/people")
async def people() -> list[dict[str, Any]]:
    admin = current_user().is_admin
    return [_person(u, admin) for u in auth_store.list_users()]


class PersonUpdate(BaseModel):
    team_role: str


def _not_last_admin(user_id: str) -> None:
    admins = auth_store.admins()
    if len(admins) == 1 and admins[0]["user_id"] == user_id:
        raise HTTPException(409, "The team needs at least one admin")


@team_router.put("/people/{user_id}")
async def set_person(user_id: str, body: PersonUpdate) -> dict[str, Any]:
    if body.team_role not in TEAM_ROLES:
        raise HTTPException(400, f"team_role must be one of {TEAM_ROLES}")
    row = auth_store.get_user(user_id)
    if row is None:
        raise HTTPException(404, "No such person")
    if row["team_role"] == "admin" and body.team_role != "admin":
        _not_last_admin(user_id)
    auth_store.update_user(user_id, team_role=body.team_role)
    auth_store.log(current_user().user_id, f"made {row['name']} {body.team_role}", user_id)
    return _person(auth_store.get_user(user_id), True)


@team_router.delete("/people/{user_id}")
async def remove_person(user_id: str) -> dict[str, Any]:
    row = auth_store.get_user(user_id)
    if row is None:
        raise HTTPException(404, "No such person")
    if row["team_role"] == "admin":
        _not_last_admin(user_id)
    auth_store.remove_user(user_id)
    auth_store.log(current_user().user_id, f"removed {row['name']} from the team", user_id)
    return {"ok": True, "note": "Their private brainstorms stay theirs; transfer the projects they own if needed."}


class InviteBody(BaseModel):
    email: str
    team_role: str = "member"
    campaign_id: str | None = None
    project_role: str | None = None


@team_router.get("/invites")
async def invites() -> list[dict[str, Any]]:
    return [{k: v for k, v in i.items() if k != "token_hash"} for i in auth_store.list_invites()]


@team_router.post("/invites")
async def invite(body: InviteBody, request: Request) -> dict[str, Any]:
    if body.team_role not in TEAM_ROLES:
        raise HTTPException(400, f"team_role must be one of {TEAM_ROLES}")
    if "@" not in body.email:
        raise HTTPException(400, "Give an email address")
    token, inv = auth_store.new_invite(body.email, body.team_role, current_user().user_id, body.campaign_id,
                                       body.project_role)
    auth_store.log(current_user().user_id, f"invited {body.email} as {body.team_role}")
    return {"invite_id": inv["invite_id"], "link": _invite_link(request, token), "expires_at": inv["expires_at"]}


@team_router.delete("/invites/{invite_id}")
async def revoke_invite(invite_id: str) -> dict[str, Any]:
    auth_store.revoke_invite(invite_id)
    return {"ok": True}


class GroupBody(BaseModel):
    name: str | None = None
    members: list[str] | None = None


@team_router.get("/groups")
async def groups() -> list[dict[str, Any]]:
    return auth_store.list_groups()


@team_router.post("/groups")
async def add_group(body: GroupBody) -> dict[str, Any]:
    if not (body.name or "").strip():
        raise HTTPException(400, "Give the group a name")
    g = auth_store.add_group(body.name)
    if body.members:
        auth_store.set_group_members(g["group_id"], body.members)
    auth_store.log(current_user().user_id, f"created the group {g['name']}")
    return next(x for x in auth_store.list_groups() if x["group_id"] == g["group_id"])


@team_router.put("/groups/{group_id}")
async def update_group(group_id: str, body: GroupBody) -> dict[str, Any]:
    if body.name:
        auth_store.rename_group(group_id, body.name)
    if body.members is not None:
        auth_store.set_group_members(group_id, body.members)
    found = next((x for x in auth_store.list_groups() if x["group_id"] == group_id), None)
    if found is None:
        raise HTTPException(404, "No such group")
    return found


@team_router.delete("/groups/{group_id}")
async def delete_group(group_id: str) -> dict[str, Any]:
    auth_store.remove_group(group_id)
    return {"ok": True}


@team_router.get("/rules")
async def rules() -> dict[str, Any]:
    return auth_store.rules()


@team_router.put("/rules")
async def set_rules(changes: dict[str, Any]) -> dict[str, Any]:
    try:
        result = auth_store.set_rules(changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    auth_store.log(current_user().user_id, "changed the team rules", ", ".join(sorted(changes)))
    return result


@team_router.get("/keys")
async def team_keys() -> dict[str, Any]:
    return {"team": [keys.key_status("team", p) for p in keys.PROVIDERS],
            "server_key": bool(os.environ.get("OPENROUTER_API_KEY")),
            "people": [{"user_id": u["user_id"], "name": u["name"], "own_key": keys.key_status(f"user:{u['user_id']}")["set"]}
                       for u in auth_store.list_users()]}


@team_router.put("/keys/{provider}")
async def set_team_key(provider: str, body: KeyBody) -> dict[str, Any]:
    try:
        hint = keys.put_key("team", provider, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    auth_store.log(current_user().user_id, "set the team key", provider)
    return {"provider": provider, "set": True, "hint": hint}


@team_router.delete("/keys/{provider}")
async def delete_team_key(provider: str) -> dict[str, Any]:
    auth_store.delete_secret("team", provider)
    auth_store.log(current_user().user_id, "removed the team key", provider)
    return {"provider": provider, "set": False}


@team_router.get("/activity")
async def activity(limit: int = 200) -> list[dict[str, Any]]:
    names = {u["user_id"]: u["name"] for u in auth_store.list_users()}
    # no user: done on the server with `aurora users`; a removed user: their name is gone
    return [{**e, "who": names.get(e["user_id"], "A former member") if e["user_id"] else "The server"}
            for e in auth_store.audit(min(limit, 1000))]


class Transfer(BaseModel):
    campaign_id: str
    to_user_id: str


@team_router.post("/transfer")
async def transfer(body: Transfer) -> dict[str, Any]:
    """Give a project to someone else (e.g. when its owner leaves). Private brainstorms are not moved."""
    to = auth_store.get_user(body.to_user_id)
    if to is None or to["disabled"]:
        raise HTTPException(404, "No such person")
    if auth_store.project_access(body.campaign_id) is None:
        raise HTTPException(404, "No such project")
    auth_store.update_project_access(body.campaign_id, owner_id=to["user_id"])
    auth_store.set_member(body.campaign_id, f"user:{to['user_id']}", "owner")
    auth_store.log(current_user().user_id, f"gave project {body.campaign_id} to {to['name']}", body.campaign_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/campaigns/{campaign_id}/access
# ---------------------------------------------------------------------------

def _principal_view(m: dict[str, Any], users: dict[str, dict[str, Any]], groups: dict[str, dict[str, Any]]) -> dict[str, Any]:
    kind, pid = m["principal"].split(":", 1)
    base = {"principal": m["principal"], "kind": kind, "role": m["role"],
            "perms": sorted(member_rights(m["role"], m.get("perms")), key=PERMS.index)}
    if kind == "user":
        u = users.get(pid) or {"name": "(removed)", "email": "", "team_role": "member", "org": ""}
        return {**base, "name": u["name"], "email": u["email"], "team_role": u["team_role"], "org": u.get("org") or ""}
    g = groups.get(pid) or {"name": "(removed group)", "members": []}
    return {**base, "name": g["name"], "members": len(g["members"])}


def _access_view(campaign_id: str) -> dict[str, Any]:
    access = auth_store.project_access(campaign_id) or {"owner_id": "local", "team_access": "none", "confidential": 0,
                                                        "deciders": "starter", "decider_user": None}
    users = {u["user_id"]: u for u in auth_store.list_users()}
    groups = {g["group_id"]: g for g in auth_store.list_groups()}
    me = current_user()
    return {
        "owner_id": access["owner_id"], "team_access": access["team_access"], "confidential": bool(access["confidential"]),
        "deciders": access["deciders"], "decider_user": access["decider_user"],
        "members": [_principal_view(m, users, groups) for m in auth_store.members(campaign_id)],
        "invites": [{"invite_id": i["invite_id"], "email": i["email"], "role": i["project_role"], "team_role": i["team_role"]}
                    for i in auth_store.list_invites(campaign_id)],
        "my_rights": sorted(rights(me, campaign_id), key=PERMS.index),
        "roles": {k: sorted(v, key=PERMS.index) for k, v in ROLES.items()},
        "perm_labels": PERM_LABELS, "team_access_options": list(TEAM_ACCESS), "team": auth_store.team_name(),
    }


@access_router.get("/{campaign_id}/access")
async def get_access(campaign_id: str) -> dict[str, Any]:
    return _access_view(campaign_id)


class AccessSettings(BaseModel):
    team_access: str | None = None
    confidential: bool | None = None
    deciders: str | None = None          # starter | owners | user
    decider_user: str | None = None


@access_router.put("/{campaign_id}/access")
async def set_access(campaign_id: str, body: AccessSettings) -> dict[str, Any]:
    me = current_user()
    changes: dict[str, Any] = {}
    if body.team_access is not None:
        if body.team_access not in TEAM_ACCESS:
            raise HTTPException(400, f"team_access must be one of {list(TEAM_ACCESS)}")
        changes["team_access"] = body.team_access
    if body.confidential is not None:
        if "manage" not in rights(me, campaign_id):
            raise HTTPException(403, "Only those who manage the project can change whether it is confidential")
        changes["confidential"] = int(body.confidential)
        if body.confidential:
            auth_store.revoke_links(campaign_id)     # no links outside the team
    if body.deciders is not None:
        if body.deciders not in ("starter", "owners", "user"):
            raise HTTPException(400, "deciders must be starter, owners or user")
        changes["deciders"] = body.deciders
        if body.deciders == "user":
            if not body.decider_user:
                raise HTTPException(400, "Name the person who decides")
            u = auth_store.get_user(body.decider_user)
            if u is None or "decide" not in rights(auth_store.as_user(u), campaign_id):
                raise HTTPException(400, "That person cannot answer decisions in this project")
            changes["decider_user"] = body.decider_user
    if auth_store.project_access(campaign_id) is None:
        auth_store.init_project(campaign_id, me.user_id)
    auth_store.update_project_access(campaign_id, **changes)
    auth_store.log(me.user_id, "changed the sharing settings", campaign_id, ", ".join(f"{k}={v}" for k, v in changes.items()))
    return _access_view(campaign_id)


class MemberBody(BaseModel):
    role: str
    perms: list[str] | None = None


def _owners(campaign_id: str) -> list[str]:
    return [m["principal"] for m in auth_store.members(campaign_id) if m["role"] == "owner"]


def _may_grant(campaign_id: str, wanted: frozenset[str]) -> None:
    """Nobody hands out rights they do not have themselves."""
    mine = rights(current_user(), campaign_id)
    extra = wanted - mine
    if extra:
        raise HTTPException(403, f"You cannot give rights you do not have: {', '.join(sorted(extra))}")


@access_router.put("/{campaign_id}/access/members/{principal}")
async def set_member(campaign_id: str, principal: str, body: MemberBody) -> dict[str, Any]:
    if body.role not in (*ROLES, "custom"):
        raise HTTPException(400, f"role must be one of {[*ROLES, 'custom']}")
    if not principal.startswith(("user:", "group:")):
        raise HTTPException(400, "Members are user:<id> or group:<id>")
    wanted = member_rights(body.role, body.perms)
    _may_grant(campaign_id, wanted)
    current = next((m for m in auth_store.members(campaign_id) if m["principal"] == principal), None)
    if current and current["role"] == "owner" and body.role != "owner" and _owners(campaign_id) == [principal]:
        raise HTTPException(409, "The project needs at least one owner")
    if principal.startswith("user:"):
        u = auth_store.get_user(principal[5:])
        if u is None:
            raise HTTPException(404, "No such person")
        if u["team_role"] == "guest" and body.role == "owner":
            raise HTTPException(400, "Guests cannot own projects")
    auth_store.set_member(campaign_id, principal, body.role, sorted(wanted) if body.role == "custom" else None)
    auth_store.log(current_user().user_id, f"set {principal} to {body.role}", campaign_id,
                   ", ".join(sorted(wanted)) if body.role == "custom" else "")
    return _access_view(campaign_id)


@access_router.delete("/{campaign_id}/access/members/{principal}")
async def remove_member(campaign_id: str, principal: str) -> dict[str, Any]:
    if _owners(campaign_id) == [principal]:
        raise HTTPException(409, "The project needs at least one owner")
    auth_store.remove_member(campaign_id, principal)
    auth_store.log(current_user().user_id, f"removed {principal}", campaign_id)
    return _access_view(campaign_id)


class ProjectInvite(BaseModel):
    email: str
    role: str = "commenter"
    perms: list[str] | None = None


def _team_role_for(email: str) -> str:
    """Members by email domain (the team's member domains, else the inviter's own); guests otherwise."""
    domain = email.split("@")[-1].lower()
    domains = [d.lower() for d in auth_store.rules().get("member_domains") or []]
    me = current_user()
    if not domains and me.email:
        domains = [me.email.split("@")[-1].lower()]
    return "member" if domain in domains else "guest"


@access_router.post("/{campaign_id}/access/invites")
async def invite_to_project(campaign_id: str, body: ProjectInvite, request: Request) -> dict[str, Any]:
    if "@" not in body.email:
        raise HTTPException(400, "Give an email address")
    if body.role not in (*ROLES, "custom"):
        raise HTTPException(400, f"role must be one of {[*ROLES, 'custom']}")
    wanted = member_rights(body.role, body.perms)
    _may_grant(campaign_id, wanted)
    me = current_user()
    existing = auth_store.user_by_email(body.email)
    if existing and not existing["disabled"]:
        if existing["team_role"] == "guest" and body.role == "owner":
            raise HTTPException(400, "Guests cannot own projects")
        auth_store.set_member(campaign_id, f"user:{existing['user_id']}", body.role,
                              sorted(wanted) if body.role == "custom" else None)
        auth_store.log(me.user_id, f"added {existing['name']} as {body.role}", campaign_id)
        return {"added": True, "access": _access_view(campaign_id)}
    team_role = _team_role_for(body.email)
    if team_role == "guest" and body.role == "owner":
        raise HTTPException(400, "Guests cannot own projects")
    token, inv = auth_store.new_invite(body.email, team_role, me.user_id, campaign_id, body.role)
    auth_store.log(me.user_id, f"invited {body.email} as {body.role} ({team_role})", campaign_id)
    return {"added": False, "invite_id": inv["invite_id"], "team_role": team_role,
            "link": _invite_link(request, token), "access": _access_view(campaign_id)}


@access_router.delete("/{campaign_id}/access/invites/{invite_id}")
async def cancel_project_invite(campaign_id: str, invite_id: str) -> dict[str, Any]:
    if not any(i["invite_id"] == invite_id for i in auth_store.list_invites(campaign_id)):
        raise HTTPException(404, "No such invitation")
    auth_store.revoke_invite(invite_id)
    return _access_view(campaign_id)

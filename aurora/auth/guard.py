"""Who is asking, and may they: authentication and the permission check of every API route.

Every router of the console carries :func:`guard` as a dependency.  It finds the user (the local
owner when there are no accounts) and checks the rule :data:`RULES` gives the route:

* a project right (``view``, ``run``, ...) in the project named by ``{campaign_id}``;
* ``member``: anyone in the team but guests;  ``admin``: team admins;  ``any``: anyone signed in.

A route without a rule is refused, so a new route cannot be forgotten silently (a test checks
that every route has one).  Without the right to see a project, its routes answer 404, so they
do not reveal that it exists.
"""

from __future__ import annotations

from contextvars import ContextVar

from fastapi import HTTPException, WebSocketException
from starlette.requests import HTTPConnection

from aurora.auth.permissions import LOCAL_USER, PERMS, User, project_rights
from aurora.auth.store import auth_store

COOKIE = "aurora_session"
CURRENT: ContextVar[User] = ContextVar("aurora_user", default=LOCAL_USER)

P = "/api/campaigns/{campaign_id}"
E = P + "/experiments/{exp_id}"
RULES: dict[tuple[str, str], str] = {
    # projects
    ("GET", "/api/campaigns/"): "any",
    ("POST", "/api/campaigns/"): "member",
    ("POST", "/api/campaigns/quick"): "member",
    ("GET", "/api/campaigns/others"): "member",
    ("GET", P): "view",
    ("PATCH", P): "manage",
    ("DELETE", P): "manage",
    ("GET", P + "/disk"): "view",
    ("POST", P + "/chat/send"): "view",          # each person's own conversation with the front desk
    ("GET", P + "/chat/history"): "view",
    ("GET", P + "/chat/status"): "view",
    ("POST", P + "/proposals/{proposal_id}/confirm"): "run",
    # experiments
    ("POST", P + "/experiments"): "run",
    ("POST", P + "/experiments/custom"): "run",
    ("GET", E): "view",
    ("PATCH", E): "edit",
    ("DELETE", E): "manage",
    ("GET", E + "/disk"): "view",
    ("POST", E + "/stop"): "run",
    ("POST", E + "/rerun"): "run",
    ("POST", E + "/resume"): "run",
    ("POST", E + "/decide"): "decide",
    ("PUT", E + "/autonomy"): "run",
    ("PUT", E + "/budget"): "run",
    ("PUT", E + "/resources"): "run",
    ("PUT", E + "/frontdesk"): "run",
    ("POST", E + "/chat/send"): "run",          # the front desk relays instructions to the agents
    ("GET", E + "/chat/history"): "view",
    ("GET", E + "/compute"): "view",
    ("GET", E + "/compute/gpus"): "view",
    ("POST", E + "/compute/{job}/cancel"): "run",
    ("WS", E + "/ws"): "view",
    ("GET", E + "/workspace/tree"): "view",
    # a run's report and results, taking them away, and comparing runs
    ("GET", E + "/report"): "view",
    ("GET", E + "/results"): "view",
    ("GET", E + "/export"): "view",
    ("GET", E + "/reproduce.sh"): "view",
    ("GET", E + "/overleaf"): "view",
    ("POST", E + "/overleaf"): "view",
    ("GET", P + "/compare"): "view",
    ("GET", E + "/forkpoints"): "view",
    ("POST", E + "/fork"): "run",
    ("POST", P + "/estimate"): "view",
    ("GET", E + "/workspace/file"): "view",
    ("GET", E + "/workspace/raw"): "view",
    ("PUT", E + "/workspace/file"): "edit",
    ("PUT", E + "/workspace/upload"): "edit",
    # project files
    ("GET", P + "/files"): "view",
    ("GET", P + "/files/preview"): "view",
    ("PUT", P + "/files"): "edit",
    ("POST", P + "/files/folder"): "edit",
    ("DELETE", P + "/files"): "edit",
    ("POST", P + "/files/uploaded"): "edit",
    # sharing a project
    ("GET", P + "/access"): "view",
    ("PUT", P + "/access"): "share",
    ("PUT", P + "/access/members/{principal}"): "share",
    ("DELETE", P + "/access/members/{principal}"): "share",
    ("POST", P + "/access/invites"): "share",
    ("DELETE", P + "/access/invites/{invite_id}"): "share",
    ("GET", P + "/link"): "view",
    ("POST", P + "/link"): "share",
    ("DELETE", P + "/link"): "share",
    # a project's literature
    ("GET", P + "/literature"): "view",
    ("GET", P + "/literature/searches/{search_id}"): "view",
    ("POST", P + "/literature/search"): "edit",
    ("POST", P + "/literature/papers"): "edit",
    ("POST", P + "/literature/papers/add"): "edit",
    ("PUT", P + "/literature/papers/{paper_id}"): "edit",
    ("DELETE", P + "/literature/papers/{paper_id}"): "edit",
    ("POST", P + "/literature/refs/{ref_id}"): "edit",
    ("POST", P + "/literature/refs/{ref_id}/recheck"): "edit",
    ("POST", P + "/literature/import"): "edit",
    ("GET", P + "/literature/export.bib"): "view",
    ("POST", P + "/literature/papers/many"): "edit",
    ("POST", P + "/literature/upload"): "edit",
    ("PUT", P + "/literature/rule"): "edit",
    ("PUT", P + "/literature/papers/{paper_id}/version"): "edit",
    ("POST", P + "/literature/resolve"): "view",
    ("GET", P + "/files/raw"): "view",
    # each person's library (what it shows of projects is filtered to what they may see)
    ("GET", "/api/library"): "any",
    ("POST", "/api/library/search"): "any",
    ("POST", "/api/library/papers"): "any",
    ("POST", "/api/library/save"): "any",
    ("DELETE", "/api/library/papers/{paper_id}"): "any",
    # each person's Zotero connection (mapping a collection to a project checks edit rights there)
    ("GET", "/api/zotero"): "any",
    ("PUT", "/api/zotero"): "any",
    ("DELETE", "/api/zotero"): "any",
    ("POST", "/api/zotero/connect"): "any",
    ("GET", "/api/zotero/collections"): "any",
    ("POST", "/api/zotero/sync"): "any",
    ("POST", "/api/zotero/push/{campaign_id}"): "any",
    # the shared memory: each person's own
    ("POST", "/api/memory/capture"): "any",
    ("POST", "/api/memory/capture/file"): "any",
    ("GET", "/api/memory/sources"): "any",
    ("GET", "/api/memory/sources/{source_id}"): "any",
    ("GET", "/api/memory/sources/{source_id}/snapshot"): "any",
    ("POST", "/api/memory/context"): "any",
    ("GET", "/api/memory/contexts"): "any",
    ("GET", "/api/memory/surface"): "any",
    ("GET", "/api/memory/search"): "any",
    ("GET", "/api/memory/units/{unit_id}"): "any",
    ("PATCH", "/api/memory/units/{unit_id}"): "any",
    ("POST", "/api/memory/units/{unit_id}/mark"): "any",
    ("POST", "/api/memory/evidence/{evidence_id}/split"): "any",
    ("POST", "/api/memory/units/{unit_id}/merge"): "any",
    ("GET", "/api/memory/relate"): "any",
    ("POST", "/api/memory/zotero"): "any",
    ("GET", "/api/memory/follows"): "any",
    ("POST", "/api/memory/follows"): "any",
    ("PATCH", "/api/memory/follows/{follow_id}"): "any",
    ("DELETE", "/api/memory/follows/{follow_id}"): "any",
    ("GET", "/api/memory/follows/due"): "any",
    ("POST", "/api/memory/follows/{follow_id}/items"): "any",
    ("GET", "/api/memory/digest"): "any",
    ("GET", "/api/memory/alerts"): "any",
    ("POST", "/api/memory/alerts/{alert_id}/seen"): "any",
    ("POST", "/api/memory/alerts/{alert_id}/verdict"): "any",
    ("POST", "/api/memory/alerts/check"): "any",
    ("POST", "/api/memory/cite"): "any",
    ("POST", "/api/memory/cite/hint"): "any",
    ("GET", "/api/memory/dissent"): "any",
    ("POST", "/api/memory/zotero-read"): "any",
    ("GET", "/api/memory/zotero-card"): "any",
    ("POST", "/api/memory/audit"): "any",
    ("GET", "/api/memory/audits"): "any",
    ("GET", "/api/memory/audits/{audit_id}"): "any",
    ("DELETE", "/api/memory/sources/{source_id}"): "any",
    # a vault folder read on the console's machine: only an admin may point the console at its disks; everyone
    # else sends notes from the Obsidian plugin
    ("GET", "/api/memory/vault"): "any",
    ("PUT", "/api/memory/vault"): "admin",
    ("POST", "/api/memory/vault/scan"): "any",
    ("GET", "/api/memory/notes"): "any",
    ("POST", "/api/memory/notes"): "any",
    ("DELETE", "/api/memory/notes"): "any",
    ("GET", "/api/memory/units/{unit_id}/markdown"): "any",
    ("POST", "/api/memory/units/{unit_id}/to-brainstorm"): "any",
    ("POST", "/api/memory/thought"): "any",
    ("GET", "/api/memory/digest/worth"): "any",
    ("POST", "/api/memory/to-read"): "any",
    ("POST", "/api/memory/compare"): "any",
    ("POST", "/api/memory/voice"): "any",
    ("GET", "/api/memory/voice/turns"): "any",
    ("POST", "/api/memory/voice/undo/{undo_id}"): "any",
    ("GET", "/api/memory/voice/digest"): "any",
    ("POST", "/api/memory/voice/speak"): "any",
    ("GET", "/api/memory/voice/status"): "any",
    ("POST", "/api/memory/voice/transcribe"): "any",
    ("POST", "/api/memory/ask"): "any",
    ("POST", "/api/memory/asks/{ask_id}/answer"): "any",
    ("GET", "/api/memory/questions"): "any",
    ("POST", "/api/memory/questions"): "any",
    ("DELETE", "/api/memory/questions/{question_id}"): "any",
    ("GET", "/api/memory/questions/{question_id}/synthesis"): "any",
    ("GET", "/api/memory/report"): "any",
    ("GET", "/api/memory/settings"): "any",
    ("PUT", "/api/memory/settings"): "any",
    # brainstorm spaces: a project's needs view (comment to write), checked inside; your own space is yours
    ("GET", "/api/brainstorm/spaces"): "any",
    ("GET", "/api/brainstorm/spaces/{space}"): "any",
    ("POST", "/api/brainstorm/spaces/{space}/chats"): "any",
    ("POST", "/api/brainstorm/spaces/{space}/cards"): "any",
    ("GET", "/api/brainstorm/chats/{chat_id}"): "any",
    ("PATCH", "/api/brainstorm/chats/{chat_id}"): "any",
    ("DELETE", "/api/brainstorm/chats/{chat_id}"): "any",
    ("POST", "/api/brainstorm/chats/{chat_id}/messages"): "any",
    ("POST", "/api/brainstorm/chats/{chat_id}/ask"): "any",
    ("POST", "/api/brainstorm/chats/{chat_id}/share"): "any",
    ("POST", "/api/brainstorm/messages/{msg_id}/consent"): "any",
    ("POST", "/api/brainstorm/messages/{msg_id}/act"): "any",
    ("GET", "/api/brainstorm/chats/{chat_id}/plan"): "any",
    ("GET", "/api/brainstorm/cards/{card_id}/versions"): "any",
    ("GET", "/api/brainstorm/spaces/{space}/ideation-metrics"): "any",
    ("POST", "/api/brainstorm/chats/{chat_id}/depth"): "any",
    ("PATCH", "/api/brainstorm/insights/{insight_id}"): "any",
    ("DELETE", "/api/brainstorm/insights/{insight_id}"): "any",
    ("PATCH", "/api/brainstorm/cards/{card_id}"): "any",
    ("DELETE", "/api/brainstorm/cards/{card_id}"): "any",
    ("POST", "/api/brainstorm/cards/{card_id}/promote"): "any",
    ("PUT", "/api/brainstorm/settings"): "any",
    ("GET", "/api/brainstorm/cards/{card_id}/thread"): "any",
    ("POST", "/api/brainstorm/cards/{card_id}/thread"): "any",
    ("POST", "/api/brainstorm/cards/{card_id}/links"): "any",
    ("DELETE", "/api/brainstorm/cards/{card_id}/links/{to_card}"): "any",
    ("POST", "/api/brainstorm/cards/{card_id}/move"): "any",
    ("POST", "/api/brainstorm/chats/{chat_id}/deep"): "any",
    ("POST", "/api/brainstorm/chats/{chat_id}/guide"): "any",
    ("POST", "/api/brainstorm/suggestions/{sug_id}/do"): "any",
    ("POST", "/api/brainstorm/suggestions/{sug_id}/dismiss"): "any",
    # each person's feed and the people and sources they follow (saving to a project, bringing an
    # item to a brainstorm or giving it to a front desk checks the rights there)
    ("GET", "/api/feed"): "any",
    ("POST", "/api/feed/refresh"): "any",
    ("GET", "/api/feed/saved"): "any",
    ("GET", "/api/feed/library"): "any",
    ("POST", "/api/feed/items/{item_id}/save"): "any",
    ("DELETE", "/api/feed/items/{item_id}/save"): "any",
    ("POST", "/api/feed/items/{item_id}/hide"): "any",
    ("DELETE", "/api/feed/items/{item_id}/hide"): "any",
    ("POST", "/api/feed/items/{item_id}/bring"): "any",
    ("POST", "/api/feed/items/{item_id}/ask"): "any",
    ("GET", "/api/feed/following"): "any",
    ("PUT", "/api/feed/settings"): "any",
    ("POST", "/api/feed/rss"): "any",
    ("DELETE", "/api/feed/rss"): "any",
    ("PUT", "/api/feed/x-key"): "any",
    ("DELETE", "/api/feed/x-key"): "any",
    ("POST", "/api/feed/digest/test"): "any",
    ("POST", "/api/feed/people/find"): "any",
    ("POST", "/api/feed/people/find/stream"): "any",
    ("POST", "/api/feed/people/identities"): "any",
    ("POST", "/api/feed/people"): "any",
    ("GET", "/api/feed/people/{person_id}"): "any",
    ("PATCH", "/api/feed/people/{person_id}"): "any",
    ("DELETE", "/api/feed/people/{person_id}"): "any",
    # console settings (the server's defaults); notification channels are each person's own
    ("GET", "/api/settings"): "any",
    ("PUT", "/api/settings"): "admin",
    ("GET", "/api/settings/notifications"): "any",
    ("PUT", "/api/settings/notifications"): "any",
    ("POST", "/api/settings/notifications/test"): "any",
    # skills
    ("GET", "/api/skills/template"): "any",
    ("GET", "/api/skills/pending"): "any",
    ("POST", "/api/skills/{skill_id}/approval"): "admin",
    ("GET", "/api/skills/catalog"): "any",
    ("POST", "/api/skills/catalog/{name}"): "member",
    ("GET", "/api/skills/suggestions"): "any",
    ("POST", "/api/skills/suggestions/{suggestion_id}/dismiss"): "member",
    ("GET", "/api/skills/"): "any",
    ("POST", "/api/skills/"): "member",
    ("GET", "/api/skills/{skill_id}"): "any",
    ("PATCH", "/api/skills/{skill_id}"): "member",
    ("DELETE", "/api/skills/{skill_id}"): "member",
    ("GET", "/api/skills/experiment/{experiment_id}"): "any",
    ("POST", "/api/skills/{skill_id}/revise"): "member",
    ("POST", "/api/skills/suggest"): "member",
    # search and analytics (filtered to what the user may see)
    ("GET", "/api/search"): "any",
    ("GET", "/api/analytics"): "any",
    # the first minutes: what is missing, a key without accounts, the image, a demo
    ("GET", "/api/setup"): "any",
    ("POST", "/api/setup/dismiss"): "any",
    ("PUT", "/api/setup/key"): "admin",
    ("POST", "/api/setup/pull"): "admin",
    ("POST", "/api/setup/claude"): "admin",
    ("POST", "/api/setup/docker"): "admin",
    ("POST", "/api/setup/docker/link"): "admin",
    ("PUT", "/api/setup/docker/path"): "admin",
    ("POST", "/api/demo"): "member",
    # the welcome wizard, the checklist, tours and tips (each person's own), the examples, help
    ("GET", "/api/onboarding"): "any",
    ("PUT", "/api/onboarding"): "any",
    ("PUT", "/api/onboarding/defaults"): "any",
    ("GET", "/api/examples"): "any",
    ("POST", "/api/examples/{example_id}/duplicate"): "member",
    ("POST", "/api/examples/{example_id}/install"): "admin",
    ("DELETE", "/api/examples/{example_id}"): "admin",
    ("GET", "/api/library/papers/{ident}"): "any",
    # the paper reader: notes are their owner's; a project's papers and shared notes are checked inside
    ("GET", "/api/reader/papers/{ident}"): "any",
    ("GET", "/api/reader/papers/{ident}/pdf"): "any",
    ("POST", "/api/reader/papers/{ident}/prefetch"): "any",
    ("GET", "/api/reader/papers/{ident}/pdf/progress"): "any",
    ("POST", "/api/reader/papers/{ident}/notes"): "any",
    ("POST", "/api/reader/papers/{ident}/read-along"): "any",
    ("POST", "/api/reader/papers/{ident}/zotero"): "any",
    ("PATCH", "/api/reader/notes/{ann_id}"): "any",
    ("DELETE", "/api/reader/notes/{ann_id}"): "any",
    ("POST", "/api/reader/notes/{ann_id}/restore"): "any",
    ("POST", "/api/reader/notes/{ann_id}/replies"): "any",
    ("PUT", "/api/reader/settings"): "any",
    ("POST", "/api/reader/sync"): "any",
    # zoom out (maps of a field) and zoom in (deep reads): a project's need view (comment to make one), checked inside
    ("GET", "/api/review/spaces/{space}/maps"): "any",
    ("POST", "/api/review/spaces/{space}/maps"): "any",
    ("GET", "/api/review/spaces/{space}/pane"): "any",
    ("GET", "/api/review/maps/{map_id}"): "any",
    ("PATCH", "/api/review/maps/{map_id}"): "any",
    ("DELETE", "/api/review/maps/{map_id}"): "any",
    ("POST", "/api/review/maps/{map_id}/run"): "any",
    ("POST", "/api/review/maps/{map_id}/proposals/{proposal_id}/accept"): "any",
    ("POST", "/api/review/maps/{map_id}/proposals/{proposal_id}/dismiss"): "any",
    ("POST", "/api/review/maps/{map_id}/framework"): "any",
    ("POST", "/api/review/maps/{map_id}/papers/{paper_id}/replace"): "any",
    ("POST", "/api/review/maps/{map_id}/challenges"): "any",
    ("POST", "/api/review/maps/{map_id}/challenges/{challenge_id}/apply"): "any",
    ("POST", "/api/review/maps/{map_id}/check-new"): "any",
    ("GET", "/api/review/maps/{map_id}/versions/{version}"): "any",
    ("POST", "/api/review/maps/{map_id}/problems/{problem_id}/brainstorm"): "any",
    ("POST", "/api/review/reads"): "any",
    ("GET", "/api/review/reads"): "any",
    ("GET", "/api/review/reads/{read_id}"): "any",
    ("PATCH", "/api/review/reads/{read_id}"): "any",
    ("POST", "/api/review/reads/{read_id}/again"): "any",
    ("POST", "/api/review/reads/{read_id}/followups"): "any",
    ("POST", "/api/review/reads/{read_id}/idea"): "any",
    ("GET", "/api/help/docs"): "any",
    ("GET", "/api/help/docs/{path:path}"): "any",
    ("GET", "/api/help/assets/{name}"): "any",
    ("POST", "/api/help/ask"): "any",
    ("POST", "/api/home/reply"): "any",
    # the signed-in user
    ("GET", "/api/me"): "any",
    ("PUT", "/api/me"): "any",
    ("GET", "/api/me/keys"): "any",
    ("GET", "/api/me/writes"): "any",
    ("GET", "/api/me/devices"): "any",
    ("POST", "/api/me/devices"): "any",
    ("DELETE", "/api/me/devices/{device_id}"): "any",
    ("PUT", "/api/me/writes"): "any",
    ("POST", "/api/me/writes/{log_id}/undo"): "any",
    ("POST", "/api/me/writes/log"): "any",
    ("PUT", "/api/me/keys/{provider}"): "any",
    ("DELETE", "/api/me/keys/{provider}"): "any",
    # the team
    ("GET", "/api/team"): "any",
    ("PUT", "/api/team"): "admin",
    ("GET", "/api/team/people"): "any",
    ("PUT", "/api/team/people/{user_id}"): "admin",
    ("DELETE", "/api/team/people/{user_id}"): "admin",
    ("GET", "/api/team/invites"): "admin",
    ("POST", "/api/team/invites"): "admin",
    ("DELETE", "/api/team/invites/{invite_id}"): "admin",
    ("GET", "/api/team/groups"): "any",
    ("POST", "/api/team/groups"): "admin",
    ("PUT", "/api/team/groups/{group_id}"): "admin",
    ("DELETE", "/api/team/groups/{group_id}"): "admin",
    ("GET", "/api/team/rules"): "any",
    ("PUT", "/api/team/rules"): "admin",
    ("GET", "/api/team/keys"): "admin",
    ("PUT", "/api/team/keys/{provider}"): "admin",
    ("DELETE", "/api/team/keys/{provider}"): "admin",
    ("GET", "/api/team/activity"): "admin",
    ("POST", "/api/team/transfer"): "admin",
}


def current_user() -> User:
    return CURRENT.get()


def accounts_on() -> bool:
    return auth_store.has_users()


def _token(conn: HTTPConnection) -> str | None:
    token = conn.cookies.get(COOKIE)
    if not token:
        header = conn.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    return token


def user_of(conn: HTTPConnection) -> User | None:
    """The user a request or WebSocket comes from; None when accounts are on and nobody is signed in."""
    if not accounts_on():
        return LOCAL_USER
    return auth_store.user_for_session(_token(conn))


def rights(user: User, campaign_id: str) -> frozenset[str]:
    from aurora.examples import is_example
    if is_example(campaign_id):          # examples that come with AURORA: everyone may look, nobody may change them
        return frozenset({"view"})
    if user.is_local:
        return frozenset(PERMS)
    return project_rights(user, auth_store.project_access(campaign_id), auth_store.members(campaign_id),
                          auth_store.rules())


def require(campaign_id: str, perm: str, user: User | None = None) -> None:
    user = user or current_user()
    have = rights(user, campaign_id)
    if "view" not in have:
        raise HTTPException(404, f"Project {campaign_id} not found")
    if perm not in have:
        raise HTTPException(403, f"You need the right to {perm} in this project")


def can_see(user: User, campaign_id: str) -> bool:
    return "view" in rights(user, campaign_id)


def visible(user: User, campaign_ids: list[str]) -> list[str]:
    return campaign_ids if user.is_local else [c for c in campaign_ids if can_see(user, c)]


async def guard(conn: HTTPConnection) -> User:
    """The dependency every router carries: who is asking, and may they use this route."""
    websocket = conn.scope["type"] == "websocket"
    user = user_of(conn)
    if user is None:
        if websocket:
            raise WebSocketException(code=1008, reason="Sign in first")
        raise HTTPException(401, "Sign in to use the console")
    CURRENT.set(user)
    route = conn.scope.get("route")
    method = "WS" if websocket else conn.scope.get("method", "GET")
    rule = RULES.get((method, getattr(route, "path", "")))
    try:
        if rule is None:
            raise HTTPException(403, "This route has no permission rule")
        if rule == "admin" and not user.is_admin:
            raise HTTPException(403, "Only team admins can do this")
        if rule == "member" and user.is_guest:
            raise HTTPException(403, "Guests can only work in the projects they are invited to")
        if rule in PERMS:
            require(conn.path_params.get("campaign_id", ""), rule, user)
    except HTTPException as exc:
        if websocket:
            raise WebSocketException(code=1008, reason=str(exc.detail)) from exc
        raise
    return user

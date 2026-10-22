"""Accounts, sessions, invites, groups, project sharing, team rules, secrets and the audit log.

Kept in the console's database (``AURORA_HOME/aurora.db``) next to projects and experiments.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from aurora.auth.passwords import hash_password, verify_password
from aurora.auth.permissions import DEFAULT_RULES, TEAM_ROLES, User

SESSION_DAYS = 30
INVITE_DAYS = 14

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    password_hash TEXT,
    team_role     TEXT NOT NULL DEFAULT 'member',
    org           TEXT DEFAULT '',
    created_at    REAL NOT NULL,
    last_active   REAL,
    disabled      INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS invites (
    token_hash   TEXT PRIMARY KEY,
    invite_id    TEXT UNIQUE NOT NULL,
    email        TEXT NOT NULL,
    team_role    TEXT NOT NULL,
    campaign_id  TEXT,
    project_role TEXT,
    created_by   TEXT,
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    accepted_at  REAL
);
CREATE TABLE IF NOT EXISTS groups (
    group_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS project_access (
    campaign_id  TEXT PRIMARY KEY,
    owner_id     TEXT,
    team_access  TEXT DEFAULT 'none',
    confidential INTEGER DEFAULT 0,
    deciders     TEXT DEFAULT 'starter',
    decider_user TEXT
);
CREATE TABLE IF NOT EXISTS report_links (     -- read-only links to a project's report, for anyone
    token       TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    created_by  TEXT,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    revoked_at  REAL
);
CREATE TABLE IF NOT EXISTS project_members (
    campaign_id TEXT NOT NULL,
    principal   TEXT NOT NULL,          -- user:<id> or group:<id>
    role        TEXT NOT NULL,          -- owner | editor | commenter | viewer | custom
    perms_json  TEXT,                   -- the rights of a custom member
    PRIMARY KEY (campaign_id, principal)
);
CREATE TABLE IF NOT EXISTS team_settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS secrets (
    owner      TEXT NOT NULL,           -- user:<id> or team
    provider   TEXT NOT NULL,
    ciphertext BLOB NOT NULL,
    hint       TEXT DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (owner, provider)
);
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id    TEXT NOT NULL,
    key        TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    user_id TEXT,
    action  TEXT NOT NULL,
    target  TEXT DEFAULT '',
    detail  TEXT DEFAULT ''
);
"""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


class AuthStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from aurora.paths import aurora_home
            data_dir = aurora_home()
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(Path(data_dir) / "aurora.db"), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, args)
        self._conn.commit()
        return cur

    # ------------------------------------------------------------------ users

    def has_users(self) -> bool:
        return self._conn.execute("SELECT 1 FROM users WHERE disabled=0 LIMIT 1").fetchone() is not None

    def add_user(self, email: str, name: str, password: str | None = None, team_role: str = "member",
                 org: str = "") -> dict[str, Any]:
        if team_role not in TEAM_ROLES:
            raise ValueError(f"team role must be one of {TEAM_ROLES}")
        email = email.strip().lower()
        if self.user_by_email(email):
            raise ValueError(f"{email} already has an account")
        user_id = "u_" + uuid.uuid4().hex[:10]
        self._x("INSERT INTO users (user_id, email, name, password_hash, team_role, org, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, email, name.strip() or email.split("@")[0], hash_password(password) if password else None,
                 team_role, org, time.time()))
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return _row(self._conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone())

    def user_by_email(self, email: str) -> dict[str, Any] | None:
        return _row(self._conn.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone())

    def list_users(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM users WHERE disabled=0 ORDER BY created_at")]

    def update_user(self, user_id: str, **fields: Any) -> None:
        allowed = {"name", "team_role", "org", "disabled", "last_active"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if "password" in fields:
            sets["password_hash"] = hash_password(fields["password"])
        if not sets:
            return
        self._x(f"UPDATE users SET {', '.join(f'{k}=?' for k in sets)} WHERE user_id=?", (*sets.values(), user_id))

    def remove_user(self, user_id: str) -> None:
        """Disable the account and end its sessions; what the person made stays theirs."""
        self._x("UPDATE users SET disabled=1 WHERE user_id=?", (user_id,))
        self._x("DELETE FROM sessions WHERE user_id=?", (user_id,))
        self._x("DELETE FROM group_members WHERE user_id=?", (user_id,))

    def admins(self) -> list[dict[str, Any]]:
        return [u for u in self.list_users() if u["team_role"] == "admin"]

    def check_login(self, email: str, password: str) -> dict[str, Any] | None:
        u = self.user_by_email(email)
        if u and not u["disabled"] and verify_password(password, u["password_hash"]):
            return u
        return None

    def as_user(self, row: dict[str, Any]) -> User:
        groups = frozenset(r["group_id"] for r in self._conn.execute(
            "SELECT group_id FROM group_members WHERE user_id=?", (row["user_id"],)))
        return User(row["user_id"], row["email"], row["name"], row["team_role"], groups=groups)

    # ------------------------------------------------------------------ sessions

    def new_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._x("INSERT INTO sessions VALUES (?, ?, ?, ?)", (_hash_token(token), user_id, now, now + SESSION_DAYS * 86400))
        return token

    def user_for_session(self, token: str | None) -> User | None:
        if not token:
            return None
        r = self._conn.execute("SELECT u.* FROM sessions s JOIN users u ON u.user_id = s.user_id "
                               "WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0",
                               (_hash_token(token), time.time())).fetchone()
        if r is None:
            return None
        if (time.time() - (r["last_active"] or 0)) > 60:
            self._x("UPDATE users SET last_active=? WHERE user_id=?", (time.time(), r["user_id"]))
        return self.as_user(dict(r))

    def end_session(self, token: str) -> None:
        self._x("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))

    # ------------------------------------------------------------------ invites

    def new_invite(self, email: str, team_role: str, created_by: str, campaign_id: str | None = None,
                   project_role: str | None = None) -> tuple[str, dict[str, Any]]:
        token = secrets.token_urlsafe(24)
        invite_id = "inv_" + uuid.uuid4().hex[:8]
        now = time.time()
        self._x("INSERT INTO invites (token_hash, invite_id, email, team_role, campaign_id, project_role, created_by, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_hash_token(token), invite_id, email.strip().lower(), team_role, campaign_id, project_role, created_by,
                 now, now + INVITE_DAYS * 86400))
        return token, self.invite_by_token(token)

    def invite_by_token(self, token: str) -> dict[str, Any] | None:
        r = self._conn.execute("SELECT * FROM invites WHERE token_hash=? AND accepted_at IS NULL AND expires_at>?",
                               (_hash_token(token), time.time())).fetchone()
        return _row(r)

    def list_invites(self, campaign_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM invites WHERE accepted_at IS NULL AND expires_at>?"
        args: tuple = (time.time(),)
        if campaign_id:
            sql += " AND campaign_id=?"
            args += (campaign_id,)
        return [dict(r) for r in self._conn.execute(sql + " ORDER BY created_at DESC", args)]

    def revoke_invite(self, invite_id: str) -> None:
        self._x("DELETE FROM invites WHERE invite_id=?", (invite_id,))

    def mark_invite_used(self, token: str) -> None:
        self._x("UPDATE invites SET accepted_at=? WHERE token_hash=?", (time.time(), _hash_token(token)))

    # ------------------------------------------------------------------ groups

    def add_group(self, name: str) -> dict[str, Any]:
        gid = "g_" + uuid.uuid4().hex[:8]
        self._x("INSERT INTO groups VALUES (?, ?, ?)", (gid, name.strip(), time.time()))
        return {"group_id": gid, "name": name.strip(), "members": []}

    def list_groups(self) -> list[dict[str, Any]]:
        out = []
        for g in self._conn.execute("SELECT * FROM groups ORDER BY created_at"):
            members = [r["user_id"] for r in self._conn.execute(
                "SELECT user_id FROM group_members WHERE group_id=?", (g["group_id"],))]
            out.append({**dict(g), "members": members})
        return out

    def set_group_members(self, group_id: str, user_ids: list[str]) -> None:
        self._x("DELETE FROM group_members WHERE group_id=?", (group_id,))
        for u in user_ids:
            self._x("INSERT OR IGNORE INTO group_members VALUES (?, ?)", (group_id, u))

    def rename_group(self, group_id: str, name: str) -> None:
        self._x("UPDATE groups SET name=? WHERE group_id=?", (name.strip(), group_id))

    def remove_group(self, group_id: str) -> None:
        self._x("DELETE FROM group_members WHERE group_id=?", (group_id,))
        self._x("DELETE FROM project_members WHERE principal=?", (f"group:{group_id}",))
        self._x("DELETE FROM groups WHERE group_id=?", (group_id,))

    # ------------------------------------------------------------------ project sharing

    def project_access(self, campaign_id: str) -> dict[str, Any] | None:
        return _row(self._conn.execute("SELECT * FROM project_access WHERE campaign_id=?", (campaign_id,)).fetchone())

    def init_project(self, campaign_id: str, owner_id: str, confidential: bool = False) -> None:
        self._x("INSERT OR IGNORE INTO project_access (campaign_id, owner_id, confidential) VALUES (?, ?, ?)",
                (campaign_id, owner_id, int(confidential)))
        self.set_member(campaign_id, f"user:{owner_id}", "owner")

    def update_project_access(self, campaign_id: str, **fields: Any) -> None:
        allowed = {"owner_id", "team_access", "confidential", "deciders", "decider_user"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if sets:
            self._x(f"UPDATE project_access SET {', '.join(f'{k}=?' for k in sets)} WHERE campaign_id=?",
                    (*sets.values(), campaign_id))

    def members(self, campaign_id: str) -> list[dict[str, Any]]:
        return [{**dict(r), "perms": json.loads(r["perms_json"]) if r["perms_json"] else None}
                for r in self._conn.execute("SELECT * FROM project_members WHERE campaign_id=? ORDER BY rowid",
                                            (campaign_id,))]

    def set_member(self, campaign_id: str, principal: str, role: str, perms: list[str] | None = None) -> None:
        self._x("INSERT INTO project_members VALUES (?, ?, ?, ?) ON CONFLICT(campaign_id, principal) "
                "DO UPDATE SET role=excluded.role, perms_json=excluded.perms_json",
                (campaign_id, principal, role, json.dumps(perms) if role == "custom" else None))

    def remove_member(self, campaign_id: str, principal: str) -> None:
        self._x("DELETE FROM project_members WHERE campaign_id=? AND principal=?", (campaign_id, principal))

    # ------------------------------------------------------------------ read-only report links

    def active_link(self, campaign_id: str) -> dict[str, Any] | None:
        return _row(self._conn.execute(
            "SELECT * FROM report_links WHERE campaign_id=? AND revoked_at IS NULL AND expires_at>? "
            "ORDER BY created_at DESC LIMIT 1", (campaign_id, time.time())).fetchone())

    def set_link(self, campaign_id: str, created_by: str, days: int) -> dict[str, Any]:
        """The project's link, made now or kept (with its new lifetime)."""
        current = self.active_link(campaign_id)
        if current:
            self._x("UPDATE report_links SET expires_at=? WHERE token=?",
                    (current["created_at"] + days * 86400, current["token"]))
            return self.link(current["token"])  # type: ignore[return-value]
        token = secrets.token_urlsafe(18)
        now = time.time()
        self._x("INSERT INTO report_links (token, campaign_id, created_by, created_at, expires_at) VALUES (?,?,?,?,?)",
                (token, campaign_id, created_by, now, now + days * 86400))
        return self.link(token)  # type: ignore[return-value]

    def link(self, token: str) -> dict[str, Any] | None:
        return _row(self._conn.execute("SELECT * FROM report_links WHERE token=?", (token,)).fetchone())

    def revoke_links(self, campaign_id: str) -> int:
        return self._x("UPDATE report_links SET revoked_at=? WHERE campaign_id=? AND revoked_at IS NULL",
                       (time.time(), campaign_id)).rowcount

    def drop_project(self, campaign_id: str) -> None:
        self._x("DELETE FROM report_links WHERE campaign_id=?", (campaign_id,))
        self._x("DELETE FROM project_members WHERE campaign_id=?", (campaign_id,))
        self._x("DELETE FROM project_access WHERE campaign_id=?", (campaign_id,))

    def projects_of(self, user: User) -> set[str]:
        """Projects the user is a member of, directly or through a group."""
        principals = [f"user:{user.user_id}", *(f"group:{g}" for g in user.groups)]
        marks = ",".join("?" * len(principals))
        return {r["campaign_id"] for r in self._conn.execute(
            f"SELECT DISTINCT campaign_id FROM project_members WHERE principal IN ({marks})", principals)}

    def adopt_unowned(self, campaign_ids: list[str], owner_id: str) -> int:
        """Give projects made before accounts existed to *owner_id*."""
        n = 0
        for cid in campaign_ids:
            access = self.project_access(cid)
            if access is None:
                self.init_project(cid, owner_id)
                n += 1
            elif access["owner_id"] in (None, "local"):
                self.update_project_access(cid, owner_id=owner_id)
                self.remove_member(cid, "user:local")
                self.set_member(cid, f"user:{owner_id}", "owner")
                n += 1
        return n

    # ------------------------------------------------------------------ team

    def rules(self) -> dict[str, Any]:
        r = self._conn.execute("SELECT value_json FROM team_settings WHERE key='rules'").fetchone()
        return {**DEFAULT_RULES, **(json.loads(r["value_json"]) if r else {})}

    def set_rules(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(DEFAULT_RULES)
        if unknown:
            raise ValueError(f"unknown rules: {', '.join(sorted(unknown))}")
        rules = {**self.rules(), **changes}
        self._x("INSERT OR REPLACE INTO team_settings VALUES ('rules', ?)", (json.dumps(rules),))
        return rules

    def team_name(self) -> str:
        r = self._conn.execute("SELECT value_json FROM team_settings WHERE key='name'").fetchone()
        return json.loads(r["value_json"]) if r else "My team"

    def set_team_name(self, name: str) -> None:
        self._x("INSERT OR REPLACE INTO team_settings VALUES ('name', ?)", (json.dumps(name.strip()),))

    # ------------------------------------------------------------------ secrets (encrypted elsewhere)

    def put_secret(self, owner: str, provider: str, ciphertext: bytes, hint: str) -> None:
        self._x("INSERT OR REPLACE INTO secrets VALUES (?, ?, ?, ?, ?)", (owner, provider, ciphertext, hint, time.time()))

    def get_secret(self, owner: str, provider: str) -> dict[str, Any] | None:
        return _row(self._conn.execute("SELECT * FROM secrets WHERE owner=? AND provider=?", (owner, provider)).fetchone())

    def delete_secret(self, owner: str, provider: str) -> None:
        self._x("DELETE FROM secrets WHERE owner=? AND provider=?", (owner, provider))

    # ------------------------------------------------------------------ per-user preferences

    def get_pref(self, user_id: str, key: str, default: Any = None) -> Any:
        r = self._conn.execute("SELECT value_json FROM user_prefs WHERE user_id=? AND key=?", (user_id, key)).fetchone()
        return json.loads(r["value_json"]) if r else default

    def set_pref(self, user_id: str, key: str, value: Any) -> None:
        self._x("INSERT OR REPLACE INTO user_prefs VALUES (?, ?, ?)", (user_id, key, json.dumps(value)))

    def usage_facts(self, user_id: str) -> dict[str, bool]:
        """What a person has tried (for the getting-started checklist): brainstormed, kept papers,
        connected Zotero.  Tables of other stores in this database may not exist yet."""
        def has(sql: str, args: tuple) -> bool:
            try:
                return self._conn.execute(sql, args).fetchone() is not None
            except sqlite3.OperationalError:
                return False
        return {
            "brainstormed": has("SELECT 1 FROM bs_messages WHERE who=? LIMIT 1", (user_id,)),
            "papers": has("SELECT 1 FROM lit_items WHERE (added_by=? OR owner=?) AND campaign_id NOT LIKE 'ex\\_%' "
                          "ESCAPE '\\' LIMIT 1", (user_id, user_id)),
            "zotero": has("SELECT 1 FROM zotero_maps WHERE user_id=? LIMIT 1", (user_id,)),
        }

    # ------------------------------------------------------------------ audit

    def log(self, user_id: str | None, action: str, target: str = "", detail: str = "") -> None:
        self._x("INSERT INTO audit_log (ts, user_id, action, target, detail) VALUES (?, ?, ?, ?, ?)",
                (time.time(), user_id, action, target, detail))

    def audit(self, limit: int = 200) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]


class LazyAuthStore:
    """Opened on first use, so ``AURORA_HOME`` is read then (tests switch it per test)."""

    def __init__(self) -> None:
        self._store: AuthStore | None = None

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = AuthStore()
        return getattr(self._store, name)


auth_store = LazyAuthStore()

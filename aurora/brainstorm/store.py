"""Brainstorm spaces: conversations, idea cards and how they relate, with who may see what.

A **space** is a project (its ID) or a person's own global space (``user:<id>``). In it:

* **chats** (brainstorms): a conversation of a person with the Skeptic, Methods and the Librarian,
  private to its owner until they share it with the project;
* **cards**: ideas, questions, evidence (a paper or a run's result) and notes, each private to its
  author or shared with the project;
* **links** between cards: evidence *supports* or *challenges* an idea, a question asks about it,
  an idea *builds on* another;
* **messages**, some of them meant for one person only (an agent asking before it uses their
  private material in a shared conversation).

Nobody else sees a private chat or card, project owners and admins included.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

CARD_TYPES = ("idea", "question", "material", "note")
RELATIONS = ("supports", "challenges", "builds on", "asks about", "related")
VISIBILITY = ("private", "project")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bs_chats (
    chat_id    TEXT PRIMARY KEY,
    space      TEXT NOT NULL,
    owner      TEXT NOT NULL,
    title      TEXT NOT NULL,
    vis        TEXT NOT NULL DEFAULT 'private',
    run_id     TEXT NOT NULL DEFAULT '',          -- a live round table's run
    focus      TEXT NOT NULL DEFAULT '',          -- the idea the conversation is about
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS bs_chats_space ON bs_chats(space, updated_at);
CREATE TABLE IF NOT EXISTS bs_messages (
    msg_id     TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL,
    who        TEXT NOT NULL,                     -- a user ID, or Skeptic | Methods | Librarian | a seat
    kind       TEXT NOT NULL DEFAULT 'text',      -- text | consent | system
    text       TEXT NOT NULL DEFAULT '',
    steps      TEXT NOT NULL DEFAULT '[]',
    effects    TEXT NOT NULL DEFAULT '[]',        -- [[what, card_id], ...]
    only_for   TEXT NOT NULL DEFAULT '',          -- shown to this user only
    consent    TEXT NOT NULL DEFAULT '',          -- the card a consent message asks about
    decided    TEXT NOT NULL DEFAULT '',
    pending    INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS bs_messages_chat ON bs_messages(chat_id, created_at);
CREATE TABLE IF NOT EXISTS bs_cards (
    card_id     TEXT PRIMARY KEY,
    space       TEXT NOT NULL,
    owner       TEXT NOT NULL,
    type        TEXT NOT NULL,
    text        TEXT NOT NULL,
    stand       TEXT NOT NULL DEFAULT '',          -- one sentence: where the idea stands
    by          TEXT NOT NULL DEFAULT '',          -- who made it (a user ID or an agent)
    vis         TEXT NOT NULL DEFAULT 'private',
    answer      TEXT NOT NULL DEFAULT '',
    answered_by TEXT NOT NULL DEFAULT '',
    paper_id    TEXT NOT NULL DEFAULT '',
    run_id      TEXT NOT NULL DEFAULT '',
    chat_id     TEXT NOT NULL DEFAULT '',
    live        INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS bs_cards_space ON bs_cards(space);
CREATE TABLE IF NOT EXISTS bs_suggestions (   -- what an agent brings up on its own ("What's new")
    sug_id     TEXT PRIMARY KEY,
    space      TEXT NOT NULL,
    owner      TEXT NOT NULL,                     -- the person it is for
    by         TEXT NOT NULL,
    kind       TEXT NOT NULL,                     -- open | evidence | combine | caveat
    short      TEXT NOT NULL,
    act        TEXT NOT NULL,
    cards      TEXT NOT NULL DEFAULT '[]',
    paper_id   TEXT NOT NULL DEFAULT '',
    rel        TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL DEFAULT '',          -- '' | done | dismissed
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS bs_suggestions_space ON bs_suggestions(space, owner, created_at);
CREATE TABLE IF NOT EXISTS bs_links (
    from_card TEXT NOT NULL,
    to_card   TEXT NOT NULL,
    rel       TEXT NOT NULL,
    PRIMARY KEY (from_card, to_card, rel)
);
"""


def _new(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class BrainstormStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from aurora.paths import aurora_home
            data_dir = aurora_home()
        self._conn = sqlite3.connect(str(Path(data_dir) / "aurora.db"), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(bs_messages)")}
        if "attachments" not in cols:        # papers and files given with a message
            self._conn.execute("ALTER TABLE bs_messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
        if "status" not in {r[1] for r in self._conn.execute("PRAGMA table_info(bs_chats)")}:
            self._conn.execute("ALTER TABLE bs_chats ADD COLUMN status TEXT NOT NULL DEFAULT ''")   # a live meeting's
        card_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(bs_cards)")}
        for col in ("source", "url"):     # material from the feed that is not a paper: a post, a blog
            if col not in card_cols:
                self._conn.execute(f"ALTER TABLE bs_cards ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        self._conn.commit()
        self._lock = threading.RLock()
        self._presence: dict[str, dict[str, float]] = {}      # chat -> {user: last seen}

    def close(self) -> None:
        self._conn.close()

    def _x(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, args)
            self._conn.commit()

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    # ------------------------------------------------------------------ chats

    def new_chat(self, space: str, owner: str, title: str, vis: str = "private", run_id: str = "") -> dict[str, Any]:
        now = time.time()
        cid = _new("b")
        self._x("INSERT INTO bs_chats (chat_id, space, owner, title, vis, run_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)", (cid, space, owner, title[:200], vis, run_id, now, now))
        return self.chat(cid)  # type: ignore[return-value]

    def chat(self, chat_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM bs_chats WHERE chat_id=?", (chat_id,))
        return rows[0] if rows else None

    def chats(self, space: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM bs_chats WHERE space=? ORDER BY updated_at DESC", (space,))

    def chat_for_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM bs_chats WHERE run_id=?", (run_id,))
        return rows[0] if rows else None

    def update_chat(self, chat_id: str, **fields: Any) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("title", "vis", "focus", "status")}
        if allowed:
            sets = ", ".join(f"{k}=?" for k in allowed)
            self._x(f"UPDATE bs_chats SET {sets}, updated_at=? WHERE chat_id=?", (*allowed.values(), time.time(), chat_id))

    def delete_chat(self, chat_id: str) -> None:
        with self._lock:
            self._x("DELETE FROM bs_messages WHERE chat_id=?", (chat_id,))
            self._x("DELETE FROM bs_chats WHERE chat_id=?", (chat_id,))

    # ------------------------------------------------------------------ messages

    def add_message(self, chat_id: str, who: str, text: str = "", *, kind: str = "text", steps: list[str] | None = None,
                    effects: list[list[str]] | None = None, only_for: str = "", consent: str = "",
                    pending: bool = False, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        mid = _new("m")
        now = time.time()
        with self._lock:
            self._x("INSERT INTO bs_messages (msg_id, chat_id, who, kind, text, steps, effects, only_for, consent, "
                    "pending, created_at, attachments) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, chat_id, who, kind, text, json.dumps(steps or []), json.dumps(effects or []), only_for,
                     consent, int(pending), now, json.dumps(attachments or [])))
            self._x("UPDATE bs_chats SET updated_at=? WHERE chat_id=?", (now, chat_id))
        return self.message(mid)  # type: ignore[return-value]

    def message(self, msg_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM bs_messages WHERE msg_id=?", (msg_id,))
        return self._msg(rows[0]) if rows else None

    @staticmethod
    def _msg(r: dict[str, Any]) -> dict[str, Any]:
        r["steps"], r["effects"], r["pending"] = json.loads(r["steps"]), json.loads(r["effects"]), bool(r["pending"])
        r["attachments"] = json.loads(r.get("attachments") or "[]")
        return r

    def update_message(self, msg_id: str, **fields: Any) -> None:
        cols = {}
        for k, v in fields.items():
            if k in ("steps", "effects"):
                cols[k] = json.dumps(v)
            elif k == "pending":
                cols[k] = int(v)
            elif k in ("text", "decided"):
                cols[k] = v
        if cols:
            self._x(f"UPDATE bs_messages SET {', '.join(f'{k}=?' for k in cols)} WHERE msg_id=?", (*cols.values(), msg_id))

    def messages(self, chat_id: str, viewer: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM bs_messages WHERE chat_id=? ORDER BY created_at LIMIT ?", (chat_id, limit))
        return [self._msg(r) for r in rows if viewer is None or not r["only_for"] or r["only_for"] == viewer]

    # ------------------------------------------------------------------ cards

    def add_card(self, space: str, owner: str, type_: str, text: str, *, by: str = "", vis: str = "private",
                 stand: str = "", paper_id: str = "", run_id: str = "", chat_id: str = "", live: bool = False,
                 source: str = "", url: str = "") -> dict[str, Any]:
        if type_ not in CARD_TYPES:
            raise ValueError(f"type must be one of {CARD_TYPES}")
        cid = _new("c")
        now = time.time()
        self._x("INSERT INTO bs_cards (card_id, space, owner, type, text, stand, by, vis, paper_id, run_id, chat_id, "
                "live, source, url, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, space, owner, type_, text.strip()[:4000], stand[:600], by or owner, vis, paper_id, run_id,
                 chat_id, int(live), source[:200], url[:1000], now, now))
        return self.card(cid)  # type: ignore[return-value]

    def card(self, card_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM bs_cards WHERE card_id=?", (card_id,))
        return rows[0] if rows else None

    def cards(self, space: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM bs_cards WHERE space=? ORDER BY created_at", (space,))

    def update_card(self, card_id: str, **fields: Any) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("text", "stand", "vis", "answer", "answered_by")}
        if allowed:
            sets = ", ".join(f"{k}=?" for k in allowed)
            self._x(f"UPDATE bs_cards SET {sets}, updated_at=? WHERE card_id=?", (*allowed.values(), time.time(), card_id))

    def delete_card(self, card_id: str) -> None:
        with self._lock:
            self._x("DELETE FROM bs_links WHERE from_card=? OR to_card=?", (card_id, card_id))
            self._x("DELETE FROM bs_cards WHERE card_id=?", (card_id,))

    def link(self, from_card: str, to_card: str, rel: str) -> None:
        if rel not in RELATIONS:
            raise ValueError(f"rel must be one of {RELATIONS}")
        self._x("INSERT OR IGNORE INTO bs_links (from_card, to_card, rel) VALUES (?,?,?)", (from_card, to_card, rel))

    def links(self, card_ids: set[str]) -> list[dict[str, Any]]:
        if not card_ids:
            return []
        rows = self._all("SELECT * FROM bs_links")
        return [r for r in rows if r["from_card"] in card_ids and r["to_card"] in card_ids]

    def unlink(self, from_card: str, to_card: str, rel: str | None = None) -> None:
        if rel:
            self._x("DELETE FROM bs_links WHERE from_card=? AND to_card=? AND rel=?", (from_card, to_card, rel))
        else:
            self._x("DELETE FROM bs_links WHERE from_card=? AND to_card=?", (from_card, to_card))

    def move_card(self, card_id: str, space: str, vis: str) -> list[str]:
        """Move a card (and the cards that hang only on it: evidence, questions) to another space."""
        moved = [card_id]
        with self._lock:
            for lk in self._all("SELECT * FROM bs_links WHERE to_card=?", (card_id,)):
                src = self.card(lk["from_card"])
                if src and src["type"] in ("material", "question", "note") and \
                        not self._all("SELECT 1 FROM bs_links WHERE from_card=? AND to_card!=?", (src["card_id"], card_id)):
                    moved.append(src["card_id"])
            # links to cards that stay behind are dropped
            for cid in moved:
                for lk in self._all("SELECT * FROM bs_links WHERE from_card=? OR to_card=?", (cid, cid)):
                    if lk["from_card"] not in moved or lk["to_card"] not in moved:
                        self.unlink(lk["from_card"], lk["to_card"], lk["rel"])
            for cid in moved:
                self._x("UPDATE bs_cards SET space=?, vis=?, updated_at=? WHERE card_id=?", (space, vis, time.time(), cid))
        return moved

    def thread_counts(self, card_ids: set[str]) -> dict[str, int]:
        rows = self._all("SELECT chat_id, COUNT(*) AS n FROM bs_messages WHERE chat_id LIKE 'card:%' GROUP BY chat_id")
        return {r["chat_id"][5:]: r["n"] for r in rows if r["chat_id"][5:] in card_ids}

    # ------------------------------------------------------------------ suggestions ("What's new")

    def add_suggestion(self, space: str, owner: str, by: str, kind: str, short: str, act: str, *,
                       cards: list[str] | None = None, paper_id: str = "", rel: str = "") -> dict[str, Any]:
        sid = _new("s")
        self._x("INSERT INTO bs_suggestions (sug_id, space, owner, by, kind, short, act, cards, paper_id, rel, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (sid, space, owner, by, kind, short[:400], act[:60],
                                                   json.dumps(cards or []), paper_id, rel, time.time()))
        return self.suggestion(sid)  # type: ignore[return-value]

    def suggestion(self, sug_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM bs_suggestions WHERE sug_id=?", (sug_id,))
        if not rows:
            return None
        rows[0]["cards"] = json.loads(rows[0]["cards"])
        return rows[0]

    def suggestions(self, space: str, owner: str, *, open_only: bool = True) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM bs_suggestions WHERE space=? AND owner=? ORDER BY created_at DESC", (space, owner))
        for r in rows:
            r["cards"] = json.loads(r["cards"])
        return [r for r in rows if not open_only or not r["state"]]

    def set_suggestion(self, sug_id: str, state: str) -> None:
        self._x("UPDATE bs_suggestions SET state=? WHERE sug_id=?", (state, sug_id))

    def recent_suggestion(self, space: str, owner: str, kind: str, key: str) -> bool:
        """Whether this was suggested already (so it is not brought up twice)."""
        rows = self._all("SELECT cards, paper_id FROM bs_suggestions WHERE space=? AND owner=? AND kind=?", (space, owner, kind))
        return any(key in (r["cards"] + r["paper_id"]) for r in rows)

    def spaces_of(self, owner: str, since: float) -> list[str]:
        """Spaces a person was active in lately."""
        rows = self._all("SELECT DISTINCT c.space FROM bs_messages m JOIN bs_chats c ON c.chat_id = m.chat_id "
                         "WHERE m.who=? AND m.created_at>?", (owner, since))
        return [r["space"] for r in rows]

    # ------------------------------------------------------------------ who is here

    def seen(self, chat_id: str, user_id: str) -> None:
        self._presence.setdefault(chat_id, {})[user_id] = time.time()

    def here(self, chat_id: str, within: float = 45.0) -> list[str]:
        now = time.time()
        return [u for u, t in self._presence.get(chat_id, {}).items() if now - t < within]

    def drop_space(self, space: str) -> None:
        """Forget a deleted project's brainstorms."""
        with self._lock:
            for c in self.cards(space):
                self._x("DELETE FROM bs_links WHERE from_card=? OR to_card=?", (c["card_id"], c["card_id"]))
            self._x("DELETE FROM bs_cards WHERE space=?", (space,))
            for ch in self.chats(space):
                self._x("DELETE FROM bs_messages WHERE chat_id=?", (ch["chat_id"],))
            self._x("DELETE FROM bs_chats WHERE space=?", (space,))
            self._x("DELETE FROM bs_suggestions WHERE space=?", (space,))


class _Lazy:
    def __init__(self) -> None:
        self._store: BrainstormStore | None = None

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = BrainstormStore()
        return getattr(self._store, name)


bs_store = _Lazy()

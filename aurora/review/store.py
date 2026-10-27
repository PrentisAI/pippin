"""Field maps (zoom out) and deep reads (zoom in), kept in ``AURORA_HOME/aurora.db``.

* ``rv_maps``      a map of a research direction in a space (a project, or ``user:<id>``), its current
  content (one JSON document, see :mod:`aurora.review.fieldmap`) and where building it got to;
* ``rv_versions``  every version of a map's content, with why it changed;
* ``rv_papers``    the papers of a map: their role (core, held out, joined later) and their card;
* ``rv_log``       what the map's build did, step by step (shown live);
* ``rv_reads``     a person's deep read of one paper: the answers to the twelve questions, their own
  guess before the authors' path is shown, and which of its moves they had made.

A map is private to its owner until shared with its project; a deep read is its reader's.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rv_maps (
    map_id     TEXT PRIMARY KEY,
    space      TEXT NOT NULL,
    owner      TEXT NOT NULL,
    topic      TEXT NOT NULL,
    brief      TEXT NOT NULL DEFAULT '',      -- what the person said about the scope and their interest
    vis        TEXT NOT NULL DEFAULT 'private',
    state      TEXT NOT NULL DEFAULT 'new',   -- new | building | ready | failed
    step       TEXT NOT NULL DEFAULT '',      -- the step being worked on
    error      TEXT NOT NULL DEFAULT '',
    version    INTEGER NOT NULL DEFAULT 0,
    content    TEXT NOT NULL DEFAULT '{}',
    checked_at REAL NOT NULL DEFAULT 0,       -- when new papers of the space were last looked at
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS rv_maps_space ON rv_maps(space, updated_at);
CREATE TABLE IF NOT EXISTS rv_versions (
    map_id     TEXT NOT NULL,
    version    INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (map_id, version)
);
CREATE TABLE IF NOT EXISTS rv_papers (
    map_id   TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    role     TEXT NOT NULL DEFAULT 'core',    -- core | held_out | joined | adjacent
    card     TEXT NOT NULL DEFAULT '{}',
    added_at REAL NOT NULL,
    PRIMARY KEY (map_id, paper_id)
);
CREATE TABLE IF NOT EXISTS rv_log (
    map_id TEXT NOT NULL,
    at     REAL NOT NULL,
    step   TEXT NOT NULL,
    text   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rv_log_map ON rv_log(map_id, at);
CREATE TABLE IF NOT EXISTS rv_reads (
    read_id    TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    paper_id   TEXT NOT NULL,
    space      TEXT NOT NULL DEFAULT '',
    map_id     TEXT NOT NULL DEFAULT '',
    answers    TEXT NOT NULL DEFAULT '{}',    -- {"1": {state, ...}, ...}
    guess      TEXT NOT NULL DEFAULT '',
    revealed   INTEGER NOT NULL DEFAULT 0,
    ticks      TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (owner, paper_id, space)
);
"""


class ReviewStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from aurora.paths import aurora_home
            data_dir = aurora_home()
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(Path(data_dir) / "aurora.db"), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        if "chat_id" not in {r[1] for r in self._conn.execute("PRAGMA table_info(rv_maps)")}:
            # the brainstorm it was started from: its steps are told there
            self._conn.execute("ALTER TABLE rv_maps ADD COLUMN chat_id TEXT NOT NULL DEFAULT ''")
        read_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(rv_reads)")}
        if "chat_id" not in read_cols:
            self._conn.execute("ALTER TABLE rv_reads ADD COLUMN chat_id TEXT NOT NULL DEFAULT ''")
        if "mine" not in read_cols:          # the reader's own answers ({"9": {"text", "at"}}), and the AI answers shown
            self._conn.execute("ALTER TABLE rv_reads ADD COLUMN mine TEXT NOT NULL DEFAULT '{}'")
            self._conn.execute("ALTER TABLE rv_reads ADD COLUMN shown TEXT NOT NULL DEFAULT '[]'")
        self._conn.commit()
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    # ------------------------------------------------------------------ maps

    def new_map(self, space: str, owner: str, topic: str, brief: str = "", chat_id: str = "") -> dict[str, Any]:
        map_id = "m_" + uuid.uuid4().hex[:12]
        now = time.time()
        self._x("INSERT INTO rv_maps (map_id, space, owner, topic, brief, chat_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)", (map_id, space, owner, topic.strip()[:300], brief.strip()[:4000], chat_id, now, now))
        return self.map(map_id)  # type: ignore[return-value]

    def map(self, map_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM rv_maps WHERE map_id=?", (map_id,))
        if not rows:
            return None
        r = rows[0]
        r["content"] = json.loads(r["content"] or "{}")
        return r

    def maps(self, space: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT map_id, space, owner, topic, vis, state, step, version, chat_id, created_at, updated_at "
                         "FROM rv_maps WHERE space=? ORDER BY updated_at DESC", (space,))
        return rows

    def set_state(self, map_id: str, **fields: Any) -> None:
        allowed = {"state", "step", "error", "vis", "topic", "brief", "checked_at", "chat_id"}
        sets = [f"{k}=?" for k in fields if k in allowed]
        if not sets:
            return
        self._x(f"UPDATE rv_maps SET {', '.join(sets)}, updated_at=? WHERE map_id=?",
                (*[fields[k] for k in fields if k in allowed], time.time(), map_id))

    def put_content(self, map_id: str, content: dict[str, Any]) -> None:
        """The content being built (not yet a version)."""
        self._x("UPDATE rv_maps SET content=?, updated_at=? WHERE map_id=?", (json.dumps(content), time.time(), map_id))

    def commit(self, map_id: str, content: dict[str, Any], reason: str) -> int:
        """The content as a new version, with why it changed."""
        with self._lock:
            m = self.map(map_id)
            version = (m["version"] if m else 0) + 1
            content = {**content, "history": [*(content.get("history") or []),
                                              {"version": version, "reason": reason, "at": time.time()}]}
            self._x("INSERT OR REPLACE INTO rv_versions VALUES (?,?,?,?,?)",
                    (map_id, version, reason, json.dumps(content), time.time()))
            self._x("UPDATE rv_maps SET content=?, version=?, updated_at=? WHERE map_id=?",
                    (json.dumps(content), version, time.time(), map_id))
            return version

    def versions(self, map_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT version, reason, created_at FROM rv_versions WHERE map_id=? ORDER BY version", (map_id,))

    def version(self, map_id: str, version: int) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM rv_versions WHERE map_id=? AND version=?", (map_id, version))
        return json.loads(rows[0]["content"]) if rows else None

    def delete_map(self, map_id: str) -> None:
        with self._lock:
            for table in ("rv_versions", "rv_papers", "rv_log"):
                self._x(f"DELETE FROM {table} WHERE map_id=?", (map_id,))
            self._x("DELETE FROM rv_maps WHERE map_id=?", (map_id,))

    # ------------------------------------------------------------------ papers

    def add_paper(self, map_id: str, paper_id: str, role: str = "core", card: dict[str, Any] | None = None) -> None:
        self._x("INSERT OR REPLACE INTO rv_papers (map_id, paper_id, role, card, added_at) VALUES (?,?,?,?,?)",
                (map_id, paper_id, role, json.dumps(card or {}), time.time()))

    def set_card(self, map_id: str, paper_id: str, card: dict[str, Any]) -> None:
        self._x("UPDATE rv_papers SET card=? WHERE map_id=? AND paper_id=?", (json.dumps(card), map_id, paper_id))

    def set_role(self, map_id: str, paper_id: str, role: str) -> None:
        self._x("UPDATE rv_papers SET role=? WHERE map_id=? AND paper_id=?", (role, map_id, paper_id))

    def papers(self, map_id: str, role: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM rv_papers WHERE map_id=?" + (" AND role=?" if role else "") + " ORDER BY added_at"
        rows = self._all(sql, (map_id, role) if role else (map_id,))
        for r in rows:
            r["card"] = json.loads(r["card"] or "{}")
        return rows

    # ------------------------------------------------------------------ the build's log

    def log(self, map_id: str, step: str, text: str) -> None:
        self._x("INSERT INTO rv_log VALUES (?,?,?,?)", (map_id, time.time(), step, text[:500]))

    def logs(self, map_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM rv_log WHERE map_id=? ORDER BY at DESC LIMIT ?", (map_id, limit))
        return list(reversed(rows))

    # ------------------------------------------------------------------ deep reads

    def read(self, owner: str, paper_id: str, space: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM rv_reads WHERE owner=? AND paper_id=? AND space=?", (owner, paper_id, space))
        return self._read_row(rows[0]) if rows else None

    def read_by_id(self, read_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM rv_reads WHERE read_id=?", (read_id,))
        return self._read_row(rows[0]) if rows else None

    @staticmethod
    def _read_row(r: dict[str, Any]) -> dict[str, Any]:
        r["answers"] = json.loads(r["answers"] or "{}")
        r["ticks"] = json.loads(r["ticks"] or "[]")
        r["revealed"] = bool(r["revealed"])
        r["mine"] = json.loads(r.get("mine") or "{}")
        r["shown"] = json.loads(r.get("shown") or "[]")
        # question 3's guess and reveal are its own answer and its showing
        if r["guess"] and "3" not in r["mine"]:
            r["mine"]["3"] = {"text": r["guess"], "at": r["updated_at"]}
        if r["revealed"] and 3 not in r["shown"]:
            r["shown"].append(3)
        return r

    def open_read(self, owner: str, paper_id: str, space: str, map_id: str = "", chat_id: str = "") -> dict[str, Any]:
        got = self.read(owner, paper_id, space)
        if got:
            if chat_id and not got["chat_id"]:
                self._x("UPDATE rv_reads SET chat_id=? WHERE read_id=?", (chat_id, got["read_id"]))
                got["chat_id"] = chat_id
            return got
        now = time.time()
        self._x("INSERT OR IGNORE INTO rv_reads (read_id, owner, paper_id, space, map_id, chat_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)", ("d_" + uuid.uuid4().hex[:12], owner, paper_id, space, map_id, chat_id, now, now))
        return self.read(owner, paper_id, space)  # type: ignore[return-value]

    def set_answer(self, read_id: str, q: int, answer: dict[str, Any]) -> None:
        with self._lock:
            r = self.read_by_id(read_id)
            if r is None:
                return
            answers = {**r["answers"], str(q): answer}
            self._x("UPDATE rv_reads SET answers=?, updated_at=? WHERE read_id=?", (json.dumps(answers), time.time(), read_id))

    def update_read(self, read_id: str, **fields: Any) -> None:
        sets, args = [], []
        for k, v in fields.items():
            if k not in ("guess", "revealed", "ticks", "mine", "shown"):
                raise ValueError(k)
            sets.append(f"{k}=?")
            args.append(json.dumps(v) if k in ("ticks", "mine", "shown") else int(v) if k == "revealed" else v)
        self._x(f"UPDATE rv_reads SET {', '.join(sets)}, updated_at=? WHERE read_id=?", (*args, time.time(), read_id))


class _Lazy:
    def __init__(self) -> None:
        self._store: ReviewStore | None = None

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = ReviewStore()
        return getattr(self._store, name)


review_store = _Lazy()

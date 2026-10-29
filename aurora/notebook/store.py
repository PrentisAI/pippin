"""What the notebook keeps that runs do not: clips, notes, your notes on a run, and the judged comparison of what a
run expected with what it found (shared by everyone who can see the run)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nb_clips (
    clip_id     TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    paper_id    TEXT NOT NULL,
    paper_title TEXT DEFAULT '',
    space       TEXT DEFAULT '',
    page_index  INTEGER DEFAULT 0,
    rects_json  TEXT DEFAULT '[]',
    quote       TEXT NOT NULL,
    words       TEXT DEFAULT '',        -- the researcher's own words for it
    campaign_id TEXT DEFAULT '',        -- what it is for: a project, and a run in it
    run_id      TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS nb_clips_owner ON nb_clips(owner, created_at);
CREATE TABLE IF NOT EXISTS nb_notes (
    note_id     TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    title       TEXT DEFAULT '',
    html        TEXT DEFAULT '',
    text        TEXT DEFAULT '',        -- the same, as plain text (search, Pippin)
    campaign_id TEXT DEFAULT '',
    run_id      TEXT DEFAULT '',        -- set: your notes on that run (one per person and run)
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS nb_notes_owner ON nb_notes(owner, updated_at);
CREATE TABLE IF NOT EXISTS nb_matches (
    campaign_id TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    key         TEXT NOT NULL,          -- what it was judged from; another key: judge again
    state       TEXT NOT NULL,          -- judging | done | failed
    json        TEXT DEFAULT '{}',
    at          REAL NOT NULL,
    PRIMARY KEY (campaign_id, run_id)
);
"""


def _clip(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["rects"] = json.loads(d.pop("rects_json") or "[]")
    return d


class NotebookStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from aurora.paths import aurora_home
            data_dir = aurora_home()
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(Path(data_dir) / "aurora.db"), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()

    def _x(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, args)
            self._conn.commit()

    def _all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # ------------------------------------------------------------------ clips

    def add_clip(self, owner: str, *, paper_id: str, quote: str, paper_title: str = "", space: str = "", page_index: int = 0,
                 rects: list | None = None, words: str = "", campaign_id: str = "", run_id: str = "") -> dict[str, Any]:
        cid, now = "c_" + uuid.uuid4().hex[:12], time.time()
        self._x("INSERT INTO nb_clips VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, owner, paper_id, paper_title, space, page_index, json.dumps(rects or []), quote, words,
                 campaign_id, run_id, now, now))
        return self.clip(cid)  # type: ignore[return-value]

    def clip(self, clip_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM nb_clips WHERE clip_id=?", (clip_id,))
        return _clip(rows[0]) if rows else None

    def clips(self, owner: str, *, paper_id: str = "", campaign_id: str = "", run_id: str = "") -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM nb_clips WHERE owner=?", [owner]
        for col, v in (("paper_id", paper_id), ("campaign_id", campaign_id), ("run_id", run_id)):
            if v:
                sql += f" AND {col}=?"
                args.append(v)
        return [_clip(r) for r in self._all(sql + " ORDER BY created_at DESC", tuple(args))]

    def change_clip(self, clip_id: str, **fields: Any) -> dict[str, Any] | None:
        for k in ("words", "campaign_id", "run_id"):
            if k in fields and fields[k] is not None:
                self._x(f"UPDATE nb_clips SET {k}=?, updated_at=? WHERE clip_id=?", (str(fields[k]), time.time(), clip_id))
        return self.clip(clip_id)

    def delete_clip(self, clip_id: str) -> None:
        self._x("DELETE FROM nb_clips WHERE clip_id=?", (clip_id,))

    # ------------------------------------------------------------------ notes

    def add_note(self, owner: str, *, title: str = "", html: str = "", text: str = "", campaign_id: str = "",
                 run_id: str = "") -> dict[str, Any]:
        nid, now = "n_" + uuid.uuid4().hex[:12], time.time()
        self._x("INSERT INTO nb_notes VALUES (?,?,?,?,?,?,?,?,?)", (nid, owner, title, html, text, campaign_id, run_id, now, now))
        return self.note(nid)  # type: ignore[return-value]

    def note(self, note_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM nb_notes WHERE note_id=?", (note_id,))
        return dict(rows[0]) if rows else None

    def notes(self, owner: str) -> list[dict[str, Any]]:
        """Their free notes (not those on a run), the last changed first, without their bodies."""
        return [dict(r) for r in self._all("SELECT note_id, title, campaign_id, substr(text, 1, 200) AS preview, created_at, "
                                           "updated_at FROM nb_notes WHERE owner=? AND run_id='' ORDER BY updated_at DESC", (owner,))]

    def save_note(self, note_id: str, **fields: Any) -> dict[str, Any] | None:
        for k in ("title", "html", "text", "campaign_id"):
            if k in fields and fields[k] is not None:
                self._x(f"UPDATE nb_notes SET {k}=?, updated_at=? WHERE note_id=?", (str(fields[k]), time.time(), note_id))
        return self.note(note_id)

    def delete_note(self, note_id: str) -> None:
        self._x("DELETE FROM nb_notes WHERE note_id=?", (note_id,))

    def run_note(self, owner: str, campaign_id: str, run_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM nb_notes WHERE owner=? AND campaign_id=? AND run_id=?", (owner, campaign_id, run_id))
        return dict(rows[0]) if rows else None

    def save_run_note(self, owner: str, campaign_id: str, run_id: str, html: str, text: str) -> dict[str, Any]:
        have = self.run_note(owner, campaign_id, run_id)
        if have is None:
            return self.add_note(owner, html=html, text=text, campaign_id=campaign_id, run_id=run_id)
        return self.save_note(have["note_id"], html=html, text=text)  # type: ignore[return-value]

    # ------------------------------------------------------------------ expected vs actual

    def match(self, campaign_id: str, run_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM nb_matches WHERE campaign_id=? AND run_id=?", (campaign_id, run_id))
        if not rows:
            return None
        d = dict(rows[0])
        d["result"] = json.loads(d.pop("json") or "{}")
        return d

    def set_match(self, campaign_id: str, run_id: str, key: str, state: str, result: dict[str, Any] | None = None) -> None:
        self._x("INSERT OR REPLACE INTO nb_matches VALUES (?,?,?,?,?,?)",
                (campaign_id, run_id, key, state, json.dumps(result or {}, ensure_ascii=False), time.time()))


class _Lazy:
    def __init__(self) -> None:
        self._store: NotebookStore | None = None

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = NotebookStore()
        return getattr(self._store, name)


nb_store = _Lazy()

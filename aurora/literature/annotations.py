"""Notes on a paper's PDF, made by a person and the brainstorm's agents together.

A note marks a passage of the PDF (where it is: the page and the rectangles of the words, in PDF
points, the way Zotero keeps them) and may say something about it. It belongs to the person who reads
the paper; an agent's note is a **suggestion** until that person keeps it. Replies make a note a
conversation with the agents.

Notes are private to their owner. A note made in a project can be shared with it; then the project's
members see it too.

A kept note goes to Zotero, as an annotation on the paper's PDF, when the person's Zotero has the paper
(see :mod:`aurora.literature.zotero`); notes made in Zotero come back the same way.

Tables (in ``AURORA_HOME/aurora.db``): ``lit_annotations``, ``lit_ann_replies`` and ``lit_ann_reads``
(which agents read along with which paper, for which idea).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

STATES = ("kept", "suggested", "draft")
AGENT_COLORS = {"Librarian": "#2ea8e5", "Skeptic": "#f19837", "Methods": "#a28ae5"}
OWN_COLOR = "#ffd400"
RELATIONS = ("supports", "challenges", "asks about", "builds on")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lit_annotations (
    ann_id         TEXT PRIMARY KEY,
    owner          TEXT NOT NULL,              -- whose reading it is (a user ID)
    paper_id       TEXT NOT NULL,
    author         TEXT NOT NULL DEFAULT '',   -- '' : the owner; else the agent who wrote it
    state          TEXT NOT NULL DEFAULT 'kept',   -- kept | suggested (an agent's, not kept yet) | draft
    text           TEXT NOT NULL DEFAULT '',   -- the passage
    comment        TEXT NOT NULL DEFAULT '',
    color          TEXT NOT NULL DEFAULT '',
    page_index     INTEGER NOT NULL DEFAULT 0,
    page_label     TEXT NOT NULL DEFAULT '',
    rects          TEXT NOT NULL DEFAULT '[]', -- [[x1, y1, x2, y2], ...] in PDF points ([] : not placed yet)
    sort_index     TEXT NOT NULL DEFAULT '',
    space          TEXT NOT NULL DEFAULT '',   -- where it was made: a project, or user:<id>
    shared         INTEGER NOT NULL DEFAULT 0, -- shared with that project
    card_id        TEXT NOT NULL DEFAULT '',   -- the idea it bears on
    rel            TEXT NOT NULL DEFAULT '',
    chat_id        TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT 'aurora',  -- aurora | zotero (made there)
    zotero_lib     TEXT NOT NULL DEFAULT '',
    zotero_key     TEXT NOT NULL DEFAULT '',
    zotero_version INTEGER NOT NULL DEFAULT 0,
    dirty          INTEGER NOT NULL DEFAULT 0,  -- changed since Zotero last had it
    sync_error     TEXT NOT NULL DEFAULT '',
    deleted_at     REAL,                        -- deleted; Zotero is told a little later (Undo until then)
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS lit_annotations_paper ON lit_annotations(paper_id, owner);
CREATE INDEX IF NOT EXISTS lit_annotations_zotero ON lit_annotations(owner, zotero_key);
CREATE TABLE IF NOT EXISTS lit_ann_replies (
    reply_id   TEXT PRIMARY KEY,
    ann_id     TEXT NOT NULL,
    who        TEXT NOT NULL,                   -- a user ID or an agent
    text       TEXT NOT NULL DEFAULT '',
    pending    INTEGER NOT NULL DEFAULT 0,      -- an agent is still reading
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS lit_ann_replies_ann ON lit_ann_replies(ann_id, created_at);
CREATE TABLE IF NOT EXISTS lit_ann_reads (       -- an agent reading a paper along with a person, for an idea
    owner    TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    space    TEXT NOT NULL,
    card_id  TEXT NOT NULL,
    agent    TEXT NOT NULL,
    state    TEXT NOT NULL,                     -- reading | done | failed
    detail   TEXT NOT NULL DEFAULT '',
    at       REAL NOT NULL,
    PRIMARY KEY (owner, paper_id, space, card_id, agent)
);
"""

_FIELDS = ("text", "comment", "color", "page_index", "page_label", "rects", "sort_index", "state", "shared",
           "card_id", "rel", "zotero_lib", "zotero_key", "zotero_version", "dirty", "sync_error", "deleted_at")


def color_of(author: str) -> str:
    return AGENT_COLORS.get(author, OWN_COLOR)


class AnnotationStore:
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

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [self._row(dict(r)) for r in self._conn.execute(sql, args).fetchall()]

    @staticmethod
    def _row(r: dict[str, Any]) -> dict[str, Any]:
        if "rects" in r:
            r["rects"] = json.loads(r["rects"] or "[]")
        for k in ("shared", "dirty", "pending"):
            if k in r:
                r[k] = bool(r[k])
        return r

    # ------------------------------------------------------------------ notes

    def add(self, owner: str, paper_id: str, *, author: str = "", state: str = "kept", text: str = "",
            comment: str = "", page_index: int = 0, page_label: str = "", rects: list[list[float]] | None = None,
            sort_index: str = "", space: str = "", card_id: str = "", rel: str = "", chat_id: str = "",
            source: str = "aurora", color: str = "", zotero_lib: str = "", zotero_key: str = "",
            zotero_version: int = 0, shared: bool = False) -> dict[str, Any]:
        ann_id = "a_" + uuid.uuid4().hex[:12]
        now = time.time()
        self._x("INSERT INTO lit_annotations (ann_id, owner, paper_id, author, state, text, comment, color, page_index, "
                "page_label, rects, sort_index, space, shared, card_id, rel, chat_id, source, zotero_lib, zotero_key, "
                "zotero_version, dirty, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ann_id, owner, paper_id, author, state if state in STATES else "kept", text[:4000], comment[:8000],
                 color or color_of(author), int(page_index), page_label, json.dumps(rects or []), sort_index, space,
                 int(shared), card_id, rel if rel in RELATIONS else "", chat_id, source, zotero_lib, zotero_key,
                 zotero_version, int(state == "kept" and source == "aurora"), now, now))
        return self.get(ann_id)  # type: ignore[return-value]

    def get(self, ann_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM lit_annotations WHERE ann_id=?", (ann_id,))
        return rows[0] if rows else None

    def update(self, ann_id: str, *, touch: bool = True, **fields: Any) -> dict[str, Any] | None:
        """Change a note; ``touch`` marks it changed (Zotero gets the change at the next push)."""
        sets, args = [], []
        for k, v in fields.items():
            if k not in _FIELDS:
                raise ValueError(f"unknown field {k}")
            sets.append(f"{k}=?")
            args.append(json.dumps(v) if k == "rects" else int(v) if isinstance(v, bool) else v)
        if touch:
            sets += ["updated_at=?", "dirty=1"]
            args.append(time.time())
        if sets:
            self._x(f"UPDATE lit_annotations SET {', '.join(sets)} WHERE ann_id=?", (*args, ann_id))
        return self.get(ann_id)

    def remove(self, ann_id: str) -> None:
        """Deleted: kept as a tombstone while Zotero still has it (and for Undo); else gone."""
        a = self.get(ann_id)
        if a is None:
            return
        self._x("UPDATE lit_annotations SET deleted_at=?, updated_at=? WHERE ann_id=?", (time.time(), time.time(), ann_id))

    def restore(self, ann_id: str) -> dict[str, Any] | None:
        self._x("UPDATE lit_annotations SET deleted_at=NULL WHERE ann_id=?", (ann_id,))
        return self.get(ann_id)

    def purge(self, ann_id: str) -> None:
        with self._lock:
            self._x("DELETE FROM lit_ann_replies WHERE ann_id=?", (ann_id,))
            self._x("DELETE FROM lit_annotations WHERE ann_id=?", (ann_id,))

    def for_paper(self, paper_id: str, viewer: str, space: str = "") -> list[dict[str, Any]]:
        """What the viewer sees on a paper: their own notes, and notes shared with the project they are in."""
        rows = self._all("SELECT * FROM lit_annotations WHERE paper_id=? AND deleted_at IS NULL AND "
                         "(owner=? OR (shared=1 AND space=? AND space!='' AND space NOT LIKE 'user:%')) "
                         "ORDER BY page_index, sort_index, created_at", (paper_id, viewer, space))
        for r in rows:
            r["replies"] = self.replies(r["ann_id"])
        return rows

    def of_owner(self, owner: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM lit_annotations WHERE owner=?", (owner,))

    def by_zotero_key(self, owner: str, key: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM lit_annotations WHERE owner=? AND zotero_key=?", (owner, key))
        return rows[0] if rows else None

    def to_push(self, owner: str, grace: float = 8.0) -> list[dict[str, Any]]:
        """Notes Zotero should hear about: kept and changed, or deleted (after the grace for Undo)."""
        cutoff = time.time() - grace
        return self._all("SELECT * FROM lit_annotations WHERE owner=? AND ((deleted_at IS NULL AND state='kept' AND "
                         "dirty=1) OR (deleted_at IS NOT NULL AND deleted_at<?)) ORDER BY created_at", (owner, cutoff))

    def waiting_deletions(self, owner: str) -> bool:
        return bool(self._all("SELECT 1 FROM lit_annotations WHERE owner=? AND deleted_at IS NOT NULL", (owner,)))

    # ------------------------------------------------------------------ replies

    def add_reply(self, ann_id: str, who: str, text: str = "", *, pending: bool = False) -> dict[str, Any]:
        rid = "r_" + uuid.uuid4().hex[:12]
        self._x("INSERT INTO lit_ann_replies (reply_id, ann_id, who, text, pending, created_at) VALUES (?,?,?,?,?,?)",
                (rid, ann_id, who, text[:8000], int(pending), time.time()))
        return self._all("SELECT * FROM lit_ann_replies WHERE reply_id=?", (rid,))[0]

    def finish_reply(self, reply_id: str, text: str) -> None:
        self._x("UPDATE lit_ann_replies SET text=?, pending=0 WHERE reply_id=?", (text[:8000], reply_id))

    def replies(self, ann_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM lit_ann_replies WHERE ann_id=? ORDER BY created_at", (ann_id,))

    # ------------------------------------------------------------------ agents reading along

    def set_read(self, owner: str, paper_id: str, space: str, card_id: str, agent: str, state: str,
                 detail: str = "") -> None:
        self._x("INSERT OR REPLACE INTO lit_ann_reads (owner, paper_id, space, card_id, agent, state, detail, at) "
                "VALUES (?,?,?,?,?,?,?,?)", (owner, paper_id, space, card_id, agent, state, detail, time.time()))

    def reads(self, owner: str, paper_id: str, space: str, card_id: str) -> dict[str, dict[str, Any]]:
        rows = self._all("SELECT * FROM lit_ann_reads WHERE owner=? AND paper_id=? AND space=? AND card_id=?",
                         (owner, paper_id, space, card_id))
        return {r["agent"]: r for r in rows}

    def forget_user(self, owner: str) -> None:
        with self._lock:
            ids = [r["ann_id"] for r in self._all("SELECT ann_id FROM lit_annotations WHERE owner=?", (owner,))]
            for i in ids:
                self.purge(i)
            self._x("DELETE FROM lit_ann_reads WHERE owner=?", (owner,))


class _Lazy:
    """Opened on first use, so ``AURORA_HOME`` is read then (tests switch it per test)."""

    def __init__(self) -> None:
        self._store: AnnotationStore | None = None

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = AnnotationStore()
        return getattr(self._store, name)


annotations = _Lazy()


# ---------------------------------------------------------------------------
# The paper's text (what the agents read, and how a quoted passage is found again)


def fold_text(text: str) -> str:
    """Letters and digits only, lower case: a passage matches across line breaks, hyphens and ligatures."""
    import unicodedata
    text = unicodedata.normalize("NFKC", text or "")
    return "".join(ch for ch in text.lower() if ch.isalnum())


def find_page(pages: list[str], quote: str) -> int:
    """The page (0-based) a quoted passage is on, or -1 when the paper does not contain it."""
    q = fold_text(quote)
    if len(q) < 12:
        return -1
    folded = [fold_text(p) for p in pages]
    for i, p in enumerate(folded):
        if q in p:
            return i
    for i in range(len(folded) - 1):         # a passage across a page break
        if q in folded[i] + folded[i + 1]:
            return i
    return -1

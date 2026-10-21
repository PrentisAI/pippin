"""The Library: the papers AURORA knows, which projects they belong to, and how they got there.

Tables (in ``AURORA_HOME/aurora.db``):

* ``lit_papers``   one row per real paper (it came from an index), with its short key (``Behrens12``);
* ``lit_items``    a paper in a project's literature, or in one person's own library (``campaign_id=''``);
* ``lit_searches`` / ``lit_results``  every search (by an agent or a person) and what it found;
* ``lit_refs``     references that are **not** verified (not found, wrong ID, not checked yet), per
  project, with what a person decided about them;
* ``lit_uses``     where a paper or a flagged reference was cited (ideation, screening, the report).

A paper that is not verified never enters ``lit_papers``: it lives only as a flagged reference.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from aurora.literature.check import Check
from aurora.literature.records import Paper, fold

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lit_papers (
    paper_id   TEXT PRIMARY KEY,
    key        TEXT UNIQUE NOT NULL,
    doi        TEXT NOT NULL DEFAULT '',
    arxiv_id   TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS lit_papers_doi ON lit_papers(doi);
CREATE INDEX IF NOT EXISTS lit_papers_arxiv ON lit_papers(arxiv_id);
CREATE TABLE IF NOT EXISTS lit_items (
    campaign_id TEXT NOT NULL,              -- '' : a person's own library
    owner       TEXT NOT NULL DEFAULT '',   -- the person, for their own library
    paper_id    TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',   -- agent | search | person | bibtex | zotero | feed
    added_by    TEXT NOT NULL DEFAULT '',
    search_id   TEXT NOT NULL DEFAULT '',
    starred     INTEGER NOT NULL DEFAULT 0,
    added_at    REAL NOT NULL,
    PRIMARY KEY (campaign_id, owner, paper_id)
);
CREATE TABLE IF NOT EXISTS lit_searches (
    search_id   TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    run_id      TEXT NOT NULL DEFAULT '',
    by          TEXT NOT NULL DEFAULT '',
    context     TEXT NOT NULL DEFAULT '',
    query       TEXT NOT NULL,
    reached     TEXT NOT NULL DEFAULT '[]',
    failed      TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS lit_searches_campaign ON lit_searches(campaign_id, created_at);
CREATE TABLE IF NOT EXISTS lit_results (
    search_id TEXT NOT NULL,
    paper_id  TEXT NOT NULL,
    rank      INTEGER NOT NULL,
    cited     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (search_id, paper_id)
);
CREATE TABLE IF NOT EXISTS lit_refs (
    ref_id      TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    run_id      TEXT NOT NULL DEFAULT '',
    sig         TEXT NOT NULL,
    status      TEXT NOT NULL,              -- not_found | mismatch | unchecked
    reference   TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    closest     TEXT NOT NULL DEFAULT '',
    searched    TEXT NOT NULL DEFAULT '[]',
    by          TEXT NOT NULL DEFAULT '',
    resolution  TEXT NOT NULL DEFAULT '',   -- '' | replaced | removed | have
    paper_id    TEXT NOT NULL DEFAULT '',   -- the real paper it was replaced with
    decided_by  TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    UNIQUE (campaign_id, sig)
);
CREATE TABLE IF NOT EXISTS lit_notes (     -- a person's highlights and notes on a paper (from Zotero)
    campaign_id TEXT NOT NULL,              -- the project they reach ('' : the person's own library)
    owner       TEXT NOT NULL,
    paper_id    TEXT NOT NULL,
    note_id     TEXT NOT NULL,              -- the source's ID (a Zotero item or annotation key)
    kind        TEXT NOT NULL,              -- highlight | note
    text        TEXT NOT NULL,
    comment     TEXT NOT NULL DEFAULT '',
    page        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'zotero',
    updated_at  REAL NOT NULL,
    PRIMARY KEY (campaign_id, owner, paper_id, note_id)
);
CREATE TABLE IF NOT EXISTS lit_uses (
    campaign_id TEXT NOT NULL,
    run_id      TEXT NOT NULL DEFAULT '',
    paper_id    TEXT NOT NULL DEFAULT '',
    ref_id      TEXT NOT NULL DEFAULT '',
    place       TEXT NOT NULL,              -- "Ideation (Seat B, round 1)", "Screening", "Report"
    by          TEXT NOT NULL DEFAULT '',
    at          REAL NOT NULL,
    UNIQUE (campaign_id, run_id, paper_id, ref_id, place)
);
"""

IMPORT_RULES = ("all", "cited", "none")


def _paper_id(p: Paper) -> str:
    basis = p.doi or (f"arxiv:{p.arxiv_id}" if p.arxiv_id else f"{fold(p.title)}|{p.year or ''}")
    return "p_" + hashlib.sha1(basis.encode()).hexdigest()[:12]


def ref_signature(ref: dict[str, Any]) -> str:
    return ref.get("doi") or ref.get("arxiv_id") or f"{fold(ref.get('title', ''))}|{ref.get('year') or ''}"


class LibraryStore:
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

    # ------------------------------------------------------------------ papers

    def _find(self, p: Paper) -> dict[str, Any] | None:
        rows: list[dict[str, Any]] = []
        if p.doi:
            rows += self._all("SELECT * FROM lit_papers WHERE doi=?", (p.doi,))
        if p.arxiv_id:
            rows += self._all("SELECT * FROM lit_papers WHERE arxiv_id=?", (p.arxiv_id,))
        if not rows:
            rows = self._all("SELECT * FROM lit_papers WHERE paper_id=?", (_paper_id(p),))
        if not rows:   # same title and first author under another identifier (preprint vs journal)
            stem = p.short_key()
            rows = [r for r in self._all("SELECT * FROM lit_papers WHERE key LIKE ?", (stem[:-2] + "%",))
                    if Paper.from_dict(json.loads(r["data"])).same_work(p)]
        return rows[0] if rows else None

    def _new_key(self, p: Paper) -> str:
        base = p.short_key()
        taken = {r["key"] for r in self._all("SELECT key FROM lit_papers WHERE key LIKE ?", (base + "%",))}
        if base not in taken:
            return base
        for suffix in "bcdefghijklmnopqrstuvwxyz":
            if base + suffix not in taken:
                return base + suffix
        return f"{base}_{uuid.uuid4().hex[:4]}"

    def upsert_paper(self, p: Paper) -> dict[str, Any]:
        """Store a real paper (or complete the stored record); returns ``{paper_id, key, paper}``."""
        with self._lock:
            row = self._find(p)
            now = time.time()
            if row is None:
                pid, key = _paper_id(p), self._new_key(p)
                self._x("INSERT INTO lit_papers (paper_id, key, doi, arxiv_id, data, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?)", (pid, key, p.doi, p.arxiv_id, json.dumps(p.to_dict()), now, now))
                return {"paper_id": pid, "key": key, "paper": p}
            stored = Paper.from_dict(json.loads(row["data"]))
            stored.merge(p)
            self._x("UPDATE lit_papers SET data=?, doi=?, arxiv_id=?, updated_at=? WHERE paper_id=?",
                    (json.dumps(stored.to_dict()), stored.doi, stored.arxiv_id, now, row["paper_id"]))
            return {"paper_id": row["paper_id"], "key": row["key"], "paper": stored}

    def paper(self, ident: str) -> dict[str, Any] | None:
        """A stored paper by its ID or key: ``{paper_id, key, paper}``."""
        rows = self._all("SELECT * FROM lit_papers WHERE paper_id=? OR key=?", (ident, ident))
        if not rows:
            return None
        r = rows[0]
        return {"paper_id": r["paper_id"], "key": r["key"], "paper": Paper.from_dict(json.loads(r["data"]))}

    def papers(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for pid in dict.fromkeys(ids):
            got = self.paper(pid)
            if got:
                out[got["paper_id"]] = got
        return out

    # ------------------------------------------------------------------ items

    def add_item(self, campaign_id: str, paper_id: str, *, source: str, added_by: str = "", search_id: str = "",
                 owner: str = "") -> bool:
        """Put a paper in a project's literature (or a person's library); False if it was there."""
        cur = self._x("INSERT OR IGNORE INTO lit_items (campaign_id, owner, paper_id, source, added_by, search_id, added_at) "
                      "VALUES (?,?,?,?,?,?,?)", (campaign_id, owner, paper_id, source, added_by, search_id, time.time()))
        return cur.rowcount > 0

    def remove_item(self, campaign_id: str, paper_id: str, owner: str = "") -> bool:
        return self._x("DELETE FROM lit_items WHERE campaign_id=? AND owner=? AND paper_id=?",
                       (campaign_id, owner, paper_id)).rowcount > 0

    def star(self, campaign_id: str, paper_id: str, starred: bool, owner: str = "") -> None:
        self._x("UPDATE lit_items SET starred=? WHERE campaign_id=? AND owner=? AND paper_id=?",
                (int(starred), campaign_id, owner, paper_id))

    def items(self, campaign_id: str, owner: str = "") -> list[dict[str, Any]]:
        return self._all("SELECT * FROM lit_items WHERE campaign_id=? AND owner=? ORDER BY added_at DESC",
                         (campaign_id, owner))

    def has_item(self, campaign_id: str, paper_id: str, owner: str = "") -> bool:
        return bool(self._all("SELECT 1 FROM lit_items WHERE campaign_id=? AND owner=? AND paper_id=?",
                              (campaign_id, owner, paper_id)))

    def known_to(self, campaign_id: str) -> set[str]:
        """Papers a project has seen: in its literature, found by its searches, or cited in it."""
        rows = self._all(
            "SELECT paper_id FROM lit_items WHERE campaign_id=? "
            "UNION SELECT r.paper_id FROM lit_results r JOIN lit_searches s ON s.search_id=r.search_id WHERE s.campaign_id=? "
            "UNION SELECT paper_id FROM lit_uses WHERE campaign_id=? AND paper_id!=''",
            (campaign_id, campaign_id, campaign_id))
        return {r["paper_id"] for r in rows}

    def projects_of(self, paper_id: str) -> list[str]:
        return [r["campaign_id"] for r in self._all(
            "SELECT DISTINCT campaign_id FROM lit_items WHERE paper_id=? AND campaign_id!=''", (paper_id,))]

    # ------------------------------------------------------------------ searches

    def record_search(self, campaign_id: str, *, query: str, paper_ids: list[str], by: str = "", context: str = "",
                      run_id: str = "", reached: list[str] | None = None, failed: list[str] | None = None) -> str:
        sid = "s_" + uuid.uuid4().hex[:12]
        with self._lock:
            self._x("INSERT INTO lit_searches (search_id, campaign_id, run_id, by, context, query, reached, failed, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (sid, campaign_id, run_id, by, context, query,
                                                   json.dumps(reached or []), json.dumps(failed or []), time.time()))
            for rank, pid in enumerate(dict.fromkeys(paper_ids)):
                self._x("INSERT OR IGNORE INTO lit_results (search_id, paper_id, rank) VALUES (?,?,?)", (sid, pid, rank))
        return sid

    def mark_cited(self, campaign_id: str, paper_id: str, run_id: str = "", by: str = "") -> None:
        """A paper an agent cited: marked as such in the searches of that run (by that agent) that found it."""
        sql = ("UPDATE lit_results SET cited=1 WHERE paper_id=? AND search_id IN "
               "(SELECT search_id FROM lit_searches WHERE campaign_id=?" + (" AND run_id=?" if run_id else "")
               + (" AND by=?" if by else "") + ")")
        args = (paper_id, campaign_id, *([run_id] if run_id else []), *([by] if by else []))
        self._x(sql, args)

    def searches(self, campaign_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM lit_searches WHERE campaign_id=? ORDER BY created_at DESC LIMIT ?",
                         (campaign_id, limit))
        for r in rows:
            r["reached"], r["failed"] = json.loads(r["reached"]), json.loads(r["failed"])
            r["results"] = self._all("SELECT paper_id, rank, cited FROM lit_results WHERE search_id=? ORDER BY rank",
                                     (r["search_id"],))
        return rows

    def search(self, search_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT campaign_id FROM lit_searches WHERE search_id=?", (search_id,))
        if not rows:
            return None
        return next((s for s in self.searches(rows[0]["campaign_id"], 10_000) if s["search_id"] == search_id), None)

    # ------------------------------------------------------------------ flagged references

    def record_ref(self, campaign_id: str, result: Check, *, by: str = "", run_id: str = "") -> str:
        """A reference that is not verified; the same reference in a project is kept once."""
        ref = result.reference.to_dict()
        sig = ref_signature(ref)
        with self._lock:
            rows = self._all("SELECT ref_id, resolution FROM lit_refs WHERE campaign_id=? AND sig=?", (campaign_id, sig))
            closest = json.dumps(result.closest.to_dict()) if result.closest else ""
            if rows:
                # a later check may know more (e.g. it was unchecked, now not found); a decision stays
                self._x("UPDATE lit_refs SET status=?, reason=?, closest=?, searched=? WHERE ref_id=?",
                        (result.status, result.reason, closest, json.dumps(result.searched), rows[0]["ref_id"]))
                return rows[0]["ref_id"]
            rid = "r_" + uuid.uuid4().hex[:12]
            self._x("INSERT INTO lit_refs (ref_id, campaign_id, run_id, sig, status, reference, reason, closest, searched, "
                    "by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, campaign_id, run_id, sig, result.status, json.dumps(ref), result.reason, closest,
                     json.dumps(result.searched), by, time.time()))
            return rid

    def refs(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM lit_refs WHERE campaign_id=? ORDER BY created_at DESC", (campaign_id,))
        for r in rows:
            r["reference"] = json.loads(r["reference"])
            r["closest"] = json.loads(r["closest"]) if r["closest"] else None
            r["searched"] = json.loads(r["searched"])
        return rows

    def ref(self, ref_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT campaign_id FROM lit_refs WHERE ref_id=?", (ref_id,))
        return next((r for r in self.refs(rows[0]["campaign_id"]) if r["ref_id"] == ref_id), None) if rows else None

    def resolve_ref(self, ref_id: str, resolution: str, *, decided_by: str = "", paper_id: str = "") -> None:
        self._x("UPDATE lit_refs SET resolution=?, decided_by=?, paper_id=? WHERE ref_id=?",
                (resolution, decided_by, paper_id, ref_id))

    def known_ref(self, campaign_id: str, ref: dict[str, Any]) -> dict[str, Any] | None:
        sig = ref_signature(ref)
        return next((r for r in self.refs(campaign_id) if r["sig"] == sig), None)

    # ------------------------------------------------------------------ highlights and notes

    def set_notes(self, campaign_id: str, owner: str, paper_id: str, notes: list[dict[str, Any]],
                  source: str = "zotero") -> None:
        """Replace a person's notes on a paper (as their source now has them)."""
        with self._lock:
            self._x("DELETE FROM lit_notes WHERE campaign_id=? AND owner=? AND paper_id=? AND source=?",
                    (campaign_id, owner, paper_id, source))
            now = time.time()
            for n in notes:
                self._x("INSERT OR REPLACE INTO lit_notes (campaign_id, owner, paper_id, note_id, kind, text, comment, "
                        "page, source, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (campaign_id, owner, paper_id, n["note_id"], n["kind"], n["text"], n.get("comment", ""),
                         n.get("page", ""), source, now))

    def notes(self, campaign_id: str, paper_ids: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
        """Highlights and notes that reach a project, by paper."""
        rows = self._all("SELECT * FROM lit_notes WHERE campaign_id=? ORDER BY paper_id, page, updated_at", (campaign_id,))
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            if paper_ids is None or r["paper_id"] in paper_ids:
                out.setdefault(r["paper_id"], []).append(r)
        return out

    # ------------------------------------------------------------------ uses

    def record_use(self, campaign_id: str, place: str, *, paper_id: str = "", ref_id: str = "", run_id: str = "",
                   by: str = "") -> None:
        self._x("INSERT OR IGNORE INTO lit_uses (campaign_id, run_id, paper_id, ref_id, place, by, at) "
                "VALUES (?,?,?,?,?,?,?)", (campaign_id, run_id, paper_id, ref_id, place, by, time.time()))

    def uses(self, campaign_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id is None:
            return self._all("SELECT * FROM lit_uses WHERE campaign_id=? ORDER BY at", (campaign_id,))
        return self._all("SELECT * FROM lit_uses WHERE campaign_id=? AND run_id=? ORDER BY at", (campaign_id, run_id))

    def drop_project(self, campaign_id: str) -> None:
        """Forget a deleted project's literature (the papers stay: other projects may have them)."""
        with self._lock:
            for table in ("lit_items", "lit_refs", "lit_uses", "lit_notes"):
                self._x(f"DELETE FROM {table} WHERE campaign_id=?", (campaign_id,))
            sids = [r["search_id"] for r in self._all("SELECT search_id FROM lit_searches WHERE campaign_id=?",
                                                       (campaign_id,))]
            for sid in sids:
                self._x("DELETE FROM lit_results WHERE search_id=?", (sid,))
            self._x("DELETE FROM lit_searches WHERE campaign_id=?", (campaign_id,))


class LazyLibraryStore:
    """Opened on first use, so ``AURORA_HOME`` is read then (tests switch it per test)."""

    def __init__(self) -> None:
        self._store: LibraryStore | None = None

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = LibraryStore()
        return getattr(self._store, name)


library = LazyLibraryStore()

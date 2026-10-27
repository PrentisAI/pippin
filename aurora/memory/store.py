"""The shared memory: sources, idea units and their evidence, research questions, and what the researcher did.

* a **source** is one thing saved or fetched (a paper, a post, a page, a note), with a snapshot of its text;
* an **idea unit** is one claim, said by one or more sources; each source's words for it are its **evidence**;
* units are linked when they **contradict** each other (or one refines another);
* a unit **bears on** research questions, with how much;
* the researcher's **marks** on a unit (let it sink, it changed my view, an answer) are kept with their time.

Everything is the person's own (``owner``); the memory is personal for now.
"""

from __future__ import annotations

import array
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mem_sources (
    source_id  TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    platform   TEXT NOT NULL DEFAULT 'web',      -- paper | arxiv | zhihu | x | reddit | xiaohongshu | blog | github | web | note | text
    url        TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL DEFAULT '',
    author     TEXT NOT NULL DEFAULT '',
    published  TEXT NOT NULL DEFAULT '',
    lang       TEXT NOT NULL DEFAULT '',
    paper_id   TEXT NOT NULL DEFAULT '',          -- a Library paper, when it is one
    via        TEXT NOT NULL DEFAULT 'manual',    -- manual | extension | feed | obsidian | voice
    selection  TEXT NOT NULL DEFAULT '',          -- what the researcher highlighted when they saved it
    digest     TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed
    error      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '{}',        -- what saving it did (merged into, new, conflicts)
    created_at REAL NOT NULL,
    done_at    REAL
);
CREATE INDEX IF NOT EXISTS mem_sources_owner ON mem_sources(owner, created_at);
CREATE TABLE IF NOT EXISTS mem_units (
    unit_id    TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    claim      TEXT NOT NULL,
    gist       TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'finding',   -- finding | method | opinion | tool | background
    sunk       INTEGER NOT NULL DEFAULT 0,        -- the researcher let it sink
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS mem_units_owner ON mem_units(owner, updated_at);
CREATE TABLE IF NOT EXISTS mem_evidence (
    evidence_id TEXT PRIMARY KEY,
    unit_id     TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    quote       TEXT NOT NULL,                    -- the source's own words, found in its snapshot
    stance      TEXT NOT NULL DEFAULT 'asserts',  -- asserts | qualifies | disputes
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS mem_evidence_unit ON mem_evidence(unit_id);
CREATE INDEX IF NOT EXISTS mem_evidence_source ON mem_evidence(source_id);
CREATE TABLE IF NOT EXISTS mem_vectors (
    key   TEXT PRIMARY KEY,                       -- u:<unit> | q:<question>
    model TEXT NOT NULL,
    vec   BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS mem_questions (
    question_id TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    text        TEXT NOT NULL,
    origin      TEXT NOT NULL DEFAULT 'manual',   -- manual | mentor (settled with the Mentor)
    ref         TEXT NOT NULL DEFAULT '',         -- the brainstorm it was settled in
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mem_bears (
    unit_id     TEXT NOT NULL,
    question_id TEXT NOT NULL,
    relevance   REAL NOT NULL,
    PRIMARY KEY (unit_id, question_id)
);
CREATE TABLE IF NOT EXISTS mem_links (
    a          TEXT NOT NULL,
    b          TEXT NOT NULL,
    rel        TEXT NOT NULL,                     -- contradicts | refines
    note       TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    PRIMARY KEY (a, b, rel)
);
CREATE TABLE IF NOT EXISTS mem_marks (
    mark_id    TEXT PRIMARY KEY,
    unit_id    TEXT NOT NULL,
    owner      TEXT NOT NULL,
    kind       TEXT NOT NULL,                     -- sunk | raised | moved | unmoved | answer | edited | split
    data       TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS mem_marks_unit ON mem_marks(unit_id, created_at);
CREATE TABLE IF NOT EXISTS mem_follows (    -- people, topics and threads followed: new posts come in on their own
    follow_id  TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    platform   TEXT NOT NULL,                     -- zhihu | x | xiaohongshu | reddit | rss | web
    url        TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    via        TEXT NOT NULL,                     -- extension (read in the person's browser) | server
    every_min  INTEGER NOT NULL DEFAULT 60,
    seen       TEXT NOT NULL DEFAULT '[]',        -- the posts already taken in (their ids)
    last_run   REAL NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    paused     INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mem_asks (
    ask_id      TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    context     TEXT NOT NULL DEFAULT '',
    question    TEXT NOT NULL,
    basis       TEXT NOT NULL DEFAULT '[]',       -- the units it is asked from
    answer      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    answered_at REAL
);
"""


def _new(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def pack(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def unpack(blob: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(blob)
    return a.tolist()


class MemoryStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from aurora.paths import aurora_home
            data_dir = aurora_home()
        self.dir = Path(data_dir) / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)
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

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ sources and their snapshots

    def add_source(self, owner: str, **fields: Any) -> dict[str, Any]:
        sid = _new("s")
        cols = {k: v for k, v in fields.items() if k in ("platform", "url", "title", "author", "published", "lang",
                                                          "paper_id", "via", "selection")}
        names = ", ".join(["source_id", "owner", "created_at", *cols])
        marks = ", ".join("?" * (3 + len(cols)))
        self._x(f"INSERT INTO mem_sources ({names}) VALUES ({marks})", (sid, owner, time.time(), *cols.values()))
        return self.source(sid)  # type: ignore[return-value]

    def source(self, source_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM mem_sources WHERE source_id=?", (source_id,))
        if r:
            r["result"] = json.loads(r["result"] or "{}")
        return r

    def sources(self, owner: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM mem_sources WHERE owner=? ORDER BY created_at DESC LIMIT ?", (owner, limit))
        for r in rows:
            r["result"] = json.loads(r["result"] or "{}")
        return rows

    def find_source(self, owner: str, *, url: str = "", digest: str = "") -> dict[str, Any] | None:
        if url:
            r = self._one("SELECT source_id FROM mem_sources WHERE owner=? AND url=? AND state='done'", (owner, url))
            if r:
                return self.source(r["source_id"])
        if digest:
            r = self._one("SELECT source_id FROM mem_sources WHERE owner=? AND digest=? AND state='done'", (owner, digest))
            if r:
                return self.source(r["source_id"])
        return None

    def update_source(self, source_id: str, **fields: Any) -> None:
        cols = {k: (json.dumps(v) if k == "result" else v) for k, v in fields.items()
                if k in ("platform", "url", "title", "author", "published", "lang", "paper_id", "digest", "state",
                         "error", "result", "done_at")}
        if cols:
            self._x(f"UPDATE mem_sources SET {', '.join(f'{k}=?' for k in cols)} WHERE source_id=?",
                    (*cols.values(), source_id))

    def snapshot_path(self, source_id: str, ext: str = "txt") -> Path:
        return self.dir / "snapshots" / f"{source_id}.{ext}"

    def save_snapshot(self, source_id: str, text: str, html: str = "") -> None:
        p = self.snapshot_path(source_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if html:
            self.snapshot_path(source_id, "html").write_text(html, encoding="utf-8")

    def snapshot(self, source_id: str) -> str:
        p = self.snapshot_path(source_id)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    # ------------------------------------------------------------------ units, evidence, links

    def add_unit(self, owner: str, claim: str, gist: str = "", kind: str = "finding") -> dict[str, Any]:
        uid = _new("u")
        now = time.time()
        self._x("INSERT INTO mem_units (unit_id, owner, claim, gist, kind, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (uid, owner, claim.strip()[:600], gist.strip()[:2000], kind, now, now))
        return self.unit(uid)  # type: ignore[return-value]

    def unit(self, unit_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM mem_units WHERE unit_id=?", (unit_id,))

    def units(self, owner: str, *, include_sunk: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mem_units WHERE owner=?" + ("" if include_sunk else " AND sunk=0") + " ORDER BY updated_at DESC"
        return self._all(sql, (owner,))

    def update_unit(self, unit_id: str, **fields: Any) -> None:
        cols = {k: v for k, v in fields.items() if k in ("claim", "gist", "kind", "sunk")}
        self._x(f"UPDATE mem_units SET {', '.join(f'{k}=?' for k in cols)}, updated_at=? WHERE unit_id=?",
                (*cols.values(), time.time(), unit_id))

    def delete_unit(self, unit_id: str) -> None:
        with self._lock:
            for sql in ("DELETE FROM mem_evidence WHERE unit_id=?", "DELETE FROM mem_bears WHERE unit_id=?",
                        "DELETE FROM mem_marks WHERE unit_id=?", "DELETE FROM mem_units WHERE unit_id=?"):
                self._conn.execute(sql, (unit_id,))
            self._conn.execute("DELETE FROM mem_links WHERE a=? OR b=?", (unit_id, unit_id))
            self._conn.execute("DELETE FROM mem_vectors WHERE key=?", (f"u:{unit_id}",))
            self._conn.commit()

    def add_evidence(self, unit_id: str, source_id: str, quote: str, stance: str = "asserts") -> dict[str, Any]:
        eid = _new("e")
        self._x("INSERT INTO mem_evidence (evidence_id, unit_id, source_id, quote, stance, created_at) VALUES (?,?,?,?,?,?)",
                (eid, unit_id, source_id, quote.strip()[:1500], stance, time.time()))
        self._x("UPDATE mem_units SET updated_at=? WHERE unit_id=?", (time.time(), unit_id))
        return self._one("SELECT * FROM mem_evidence WHERE evidence_id=?", (eid,))  # type: ignore[return-value]

    def evidence(self, unit_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT e.*, s.platform, s.url, s.title, s.author, s.lang, s.paper_id, s.published, s.via "
                         "FROM mem_evidence e JOIN mem_sources s ON s.source_id=e.source_id WHERE e.unit_id=? "
                         "ORDER BY e.created_at", (unit_id,))

    def evidence_item(self, evidence_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM mem_evidence WHERE evidence_id=?", (evidence_id,))

    def move_evidence(self, evidence_id: str, unit_id: str) -> None:
        self._x("UPDATE mem_evidence SET unit_id=? WHERE evidence_id=?", (unit_id, evidence_id))

    def merge_units(self, src: str, into: str) -> None:
        """Two units make the same claim: the evidence, links, marks and questions of ``src`` go to ``into``."""
        with self._lock:
            self._conn.execute("UPDATE mem_evidence SET unit_id=? WHERE unit_id=?", (into, src))
            self._conn.execute("UPDATE mem_marks SET unit_id=? WHERE unit_id=?", (into, src))
            for r in self._conn.execute("SELECT * FROM mem_links WHERE a=? OR b=?", (src, src)).fetchall():
                a, b = (into if r["a"] == src else r["a"]), (into if r["b"] == src else r["b"])
                if a != b:
                    if r["rel"] == "contradicts":
                        a, b = sorted((a, b))
                    self._conn.execute("INSERT OR IGNORE INTO mem_links (a, b, rel, note, created_at) VALUES (?,?,?,?,?)",
                                       (a, b, r["rel"], r["note"], r["created_at"]))
            self._conn.execute("DELETE FROM mem_links WHERE a=? OR b=?", (src, src))
            for r in self._conn.execute("SELECT * FROM mem_bears WHERE unit_id=?", (src,)).fetchall():
                self._conn.execute("INSERT INTO mem_bears (unit_id, question_id, relevance) VALUES (?,?,?) ON CONFLICT"
                                   "(unit_id, question_id) DO UPDATE SET relevance=MAX(relevance, excluded.relevance)",
                                   (into, r["question_id"], r["relevance"]))
            for sql in ("DELETE FROM mem_bears WHERE unit_id=?", "DELETE FROM mem_units WHERE unit_id=?"):
                self._conn.execute(sql, (src,))
            self._conn.execute("DELETE FROM mem_vectors WHERE key=?", (f"u:{src}",))
            self._conn.execute("UPDATE mem_units SET updated_at=? WHERE unit_id=?", (time.time(), into))
            self._conn.commit()

    def source_counts(self, unit_ids: list[str]) -> dict[str, int]:
        if not unit_ids:
            return {}
        q = ",".join("?" * len(unit_ids))
        rows = self._all(f"SELECT unit_id, COUNT(DISTINCT source_id) AS n FROM mem_evidence WHERE unit_id IN ({q}) "
                         "GROUP BY unit_id", tuple(unit_ids))
        return {r["unit_id"]: r["n"] for r in rows}

    def link(self, a: str, b: str, rel: str, note: str = "") -> None:
        a, b = (a, b) if rel == "refines" else tuple(sorted((a, b)))
        self._x("INSERT OR IGNORE INTO mem_links (a, b, rel, note, created_at) VALUES (?,?,?,?,?)",
                (a, b, rel, note[:600], time.time()))

    def links(self, unit_ids: list[str] | None = None, owner: str = "") -> list[dict[str, Any]]:
        if unit_ids is not None:
            if not unit_ids:
                return []
            q = ",".join("?" * len(unit_ids))
            return self._all(f"SELECT * FROM mem_links WHERE a IN ({q}) OR b IN ({q})", (*unit_ids, *unit_ids))
        return self._all("SELECT l.* FROM mem_links l JOIN mem_units u ON u.unit_id=l.a WHERE u.owner=?", (owner,))

    # ------------------------------------------------------------------ vectors

    def put_vector(self, key: str, model: str, vec: list[float]) -> None:
        self._x("INSERT OR REPLACE INTO mem_vectors (key, model, vec) VALUES (?,?,?)", (key, model, pack(vec)))

    def vectors(self, keys: list[str], model: str) -> dict[str, list[float]]:
        if not keys:
            return {}
        out: dict[str, list[float]] = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = ",".join("?" * len(chunk))
            for r in self._all(f"SELECT key, vec FROM mem_vectors WHERE model=? AND key IN ({q})", (model, *chunk)):
                out[r["key"]] = unpack(r["vec"])
        return out

    # ------------------------------------------------------------------ questions and what bears on them

    def add_question(self, owner: str, text: str, origin: str = "manual", ref: str = "") -> dict[str, Any]:
        qid = _new("q")
        self._x("INSERT INTO mem_questions (question_id, owner, text, origin, ref, created_at) VALUES (?,?,?,?,?,?)",
                (qid, owner, text.strip()[:600], origin, ref, time.time()))
        return self.question(qid)  # type: ignore[return-value]

    def question(self, question_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM mem_questions WHERE question_id=?", (question_id,))

    def questions(self, owner: str, *, archived: bool = False) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM mem_questions WHERE owner=? AND archived=? ORDER BY created_at",
                         (owner, int(archived)))

    def question_by_ref(self, owner: str, ref: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM mem_questions WHERE owner=? AND ref=?", (owner, ref))

    def update_question(self, question_id: str, **fields: Any) -> None:
        cols = {k: v for k, v in fields.items() if k in ("text", "archived")}
        if cols:
            self._x(f"UPDATE mem_questions SET {', '.join(f'{k}=?' for k in cols)} WHERE question_id=?",
                    (*cols.values(), question_id))

    def set_bears(self, unit_id: str, question_id: str, relevance: float) -> None:
        self._x("INSERT OR REPLACE INTO mem_bears (unit_id, question_id, relevance) VALUES (?,?,?)",
                (unit_id, question_id, round(relevance, 4)))

    def bears(self, *, unit_id: str = "", question_id: str = "") -> list[dict[str, Any]]:
        if unit_id:
            return self._all("SELECT * FROM mem_bears WHERE unit_id=?", (unit_id,))
        return self._all("SELECT * FROM mem_bears WHERE question_id=? ORDER BY relevance DESC", (question_id,))

    # ------------------------------------------------------------------ marks and asks

    def mark(self, unit_id: str, owner: str, kind: str, data: dict[str, Any] | None = None) -> None:
        self._x("INSERT INTO mem_marks (mark_id, unit_id, owner, kind, data, created_at) VALUES (?,?,?,?,?,?)",
                (_new("m"), unit_id, owner, kind, json.dumps(data or {}), time.time()))

    def marks(self, unit_ids: list[str] | None = None, owner: str = "", since: float = 0) -> list[dict[str, Any]]:
        if unit_ids is not None:
            if not unit_ids:
                return []
            q = ",".join("?" * len(unit_ids))
            rows = self._all(f"SELECT * FROM mem_marks WHERE unit_id IN ({q}) ORDER BY created_at", tuple(unit_ids))
        else:
            rows = self._all("SELECT * FROM mem_marks WHERE owner=? AND created_at>=? ORDER BY created_at", (owner, since))
        return [{**r, "data": json.loads(r["data"])} for r in rows]

    def add_ask(self, owner: str, context: str, question: str, basis: list[str]) -> dict[str, Any]:
        aid = _new("a")
        self._x("INSERT INTO mem_asks (ask_id, owner, context, question, basis, created_at) VALUES (?,?,?,?,?,?)",
                (aid, owner, context, question, json.dumps(basis), time.time()))
        return self.ask(aid)  # type: ignore[return-value]

    def ask(self, ask_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM mem_asks WHERE ask_id=?", (ask_id,))
        if r:
            r["basis"] = json.loads(r["basis"])
        return r

    def asks(self, owner: str, since: float = 0) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM mem_asks WHERE owner=? AND created_at>=? ORDER BY created_at", (owner, since))
        return [{**r, "basis": json.loads(r["basis"])} for r in rows]

    def answer_ask(self, ask_id: str, answer: str) -> None:
        self._x("UPDATE mem_asks SET answer=?, answered_at=? WHERE ask_id=?", (answer.strip()[:3000], time.time(), ask_id))

    # ------------------------------------------------------------------ follows

    def add_follow(self, owner: str, platform: str, url: str, label: str, via: str, every_min: int = 60) -> dict[str, Any]:
        fid = _new("f")
        self._x("INSERT INTO mem_follows (follow_id, owner, platform, url, label, via, every_min, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)", (fid, owner, platform, url, label[:200], via, max(15, every_min), time.time()))
        return self.follow(fid)  # type: ignore[return-value]

    def follow(self, follow_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM mem_follows WHERE follow_id=?", (follow_id,))
        if r:
            r["seen"] = json.loads(r["seen"])
        return r

    def follows(self, owner: str = "", via: str = "") -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM mem_follows WHERE 1=1", []
        if owner:
            sql, args = sql + " AND owner=?", [*args, owner]
        if via:
            sql, args = sql + " AND via=?", [*args, via]
        rows = self._all(sql + " ORDER BY created_at", tuple(args))
        for r in rows:
            r["seen"] = json.loads(r["seen"])
        return rows

    def update_follow(self, follow_id: str, **fields: Any) -> None:
        cols = {k: (json.dumps(v[-500:]) if k == "seen" else v) for k, v in fields.items()
                if k in ("label", "every_min", "seen", "last_run", "last_error", "paused")}
        if cols:
            self._x(f"UPDATE mem_follows SET {', '.join(f'{k}=?' for k in cols)} WHERE follow_id=?",
                    (*cols.values(), follow_id))

    def delete_follow(self, follow_id: str) -> None:
        self._x("DELETE FROM mem_follows WHERE follow_id=?", (follow_id,))

    def version(self, owner: str) -> str:
        """Changes when the person's memory changes (for caches)."""
        r = self._one("SELECT COUNT(*) AS n, MAX(updated_at) AS t FROM mem_units WHERE owner=?", (owner,))
        m = self._one("SELECT COUNT(*) AS n FROM mem_marks WHERE owner=?", (owner,))
        return f"{r['n']}:{r['t']}:{m['n']}" if r and m else "0"


class _Lazy:
    def __init__(self) -> None:
        self._store: MemoryStore | None = None

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = MemoryStore()
        return getattr(self._store, name)


mem_store = _Lazy()

"""What the feed keeps: the items each person's sources brought, how each fits each project,
what was saved or hidden, and the people they follow.

Items are per person (everyone follows their own sources); an item fetched twice (a paper both
on arXiv and HF Daily Papers) is kept once, under its arXiv ID, DOI or link.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

KINDS = ("paper", "post", "blog", "code", "video", "discussion")
WANTS = ("papers", "posts", "blog", "code", "videos", "about")          # what to follow of a person

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_items (
    item_id      TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    ukey         TEXT NOT NULL,                 -- arXiv ID, DOI or link: the same item fetched twice
    kind         TEXT NOT NULL,                 -- paper | post | blog | code | video | discussion
    src          TEXT NOT NULL,                 -- arxiv | openalex | s2 | hf | bluesky | mastodon | rss | x | discussion
    source       TEXT NOT NULL DEFAULT '',      -- as shown: "arXiv · cs.CY", "Bluesky · @handle"
    url          TEXT NOT NULL DEFAULT '',
    pdf_url      TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    points       TEXT NOT NULL DEFAULT '[]',    -- a discussion's main points
    places       TEXT NOT NULL DEFAULT '[]',    -- a discussion's sources: [[label, url], ...]
    people       TEXT NOT NULL DEFAULT '[]',    -- [[name, person_id or ''], ...]
    person_ids   TEXT NOT NULL DEFAULT '[]',    -- the followed people it is by
    about        TEXT NOT NULL DEFAULT '',      -- a discussion: the person whose work it is about
    idv          TEXT NOT NULL DEFAULT '',      -- "arXiv:2610.08812", "DOI 10.…": checked identity
    paper        TEXT NOT NULL DEFAULT '',      -- the paper record, for saving it to the literature
    extra        TEXT NOT NULL DEFAULT '',      -- "34 replies", "9 min read"
    published_at REAL NOT NULL DEFAULT 0,
    fetched_at   REAL NOT NULL,
    ranked       INTEGER NOT NULL DEFAULT 0,
    hidden       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, ukey)
);
CREATE INDEX IF NOT EXISTS feed_items_user ON feed_items(user_id, fetched_at);
CREATE TABLE IF NOT EXISTS feed_ranks (
    item_id     TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    rel         INTEGER NOT NULL,              -- 1..5
    why         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (item_id, campaign_id)
);
CREATE TABLE IF NOT EXISTS feed_saves (
    user_id     TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    campaign_id TEXT NOT NULL DEFAULT '',      -- '' : saved for later
    saved_at    REAL NOT NULL,
    PRIMARY KEY (user_id, item_id)
);
CREATE TABLE IF NOT EXISTS feed_people (
    person_id  TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    aff        TEXT NOT NULL DEFAULT '',
    ids        TEXT NOT NULL DEFAULT '[]',     -- identities: [{kind, handle, url, label, state, follow}, ...]
    wants      TEXT NOT NULL DEFAULT '{}',     -- {papers, posts, blog, code, videos, about}: bool
    coauthors  TEXT NOT NULL DEFAULT '[]',
    removed    INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    seen_at    REAL NOT NULL DEFAULT 0         -- their page was last opened
);
CREATE INDEX IF NOT EXISTS feed_people_user ON feed_people(user_id);
"""

_JSON = ("points", "places", "people", "person_ids", "ids", "wants", "coauthors", "signals")
_MIGRATIONS = (    # columns added after the first release: (table, column, definition)
    ("feed_items", "signals", "TEXT NOT NULL DEFAULT '{}'"),     # {arxiv: category, hf: upvotes, x: [{who, url, text}]}
    ("feed_items", "touched_at", "REAL NOT NULL DEFAULT 0"),     # the last time a new signal came in
)


def merge_signals(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """One paper seen in several places: the category it is in, the most HF upvotes, every X post about it."""
    out = dict(old or {})
    for k, v in (new or {}).items():
        if k == "hf":
            out["hf"] = max(int(out.get("hf") or 0), int(v or 0))
        elif k == "x":
            have = {m.get("url") for m in out.get("x") or []}
            out["x"] = [*(out.get("x") or []), *[m for m in v or [] if m.get("url") not in have]][-20:]
        elif v and not out.get(k):
            out[k] = v
    return out


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for k in _JSON:
        if k in d:
            d[k] = json.loads(d[k] or ("{}" if k in ("wants", "signals") else "[]"))
    if d.get("paper"):
        d["paper"] = json.loads(d["paper"])
    return d


class FeedStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from aurora.paths import aurora_home
            data_dir = aurora_home()
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(Path(data_dir) / "aurora.db"), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        for table, col, definition in _MIGRATIONS:
            if col not in {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
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
            return [_row(r) for r in self._conn.execute(sql, args).fetchall()]

    # ------------------------------------------------------------------ items

    def add_item(self, user_id: str, item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Keep an item; returns it and whether it is new.  A known item gains the people and
        sources the new copy names (a followed author's paper that was also on arXiv)."""
        old = self._all("SELECT * FROM feed_items WHERE user_id=? AND ukey=?", (user_id, item["ukey"]))
        if old:
            row = old[0]
            sig = merge_signals(row.get("signals") or {}, item.get("signals") or {})
            if sig != (row.get("signals") or {}):
                self._x("UPDATE feed_items SET signals=?, touched_at=? WHERE item_id=?",
                        (json.dumps(sig), time.time(), row["item_id"]))
            pids = sorted(set(row["person_ids"]) | set(item.get("person_ids") or []))
            if pids != sorted(row["person_ids"]):
                people = row["people"]
                for name, pid in item.get("people") or []:
                    if pid:
                        people = [[n, pid if n == name else p] for n, p in people]
                self._x("UPDATE feed_items SET person_ids=?, people=? WHERE item_id=?",
                        (json.dumps(pids), json.dumps(people), row["item_id"]))
            return self.item(row["item_id"]), False  # type: ignore[return-value]
        iid = f"f_{uuid.uuid4().hex[:12]}"
        paper = item.get("paper")
        self._x("INSERT INTO feed_items (item_id, user_id, ukey, kind, src, source, url, pdf_url, title, summary, "
                "points, places, people, person_ids, about, idv, paper, extra, published_at, fetched_at, signals) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (iid, user_id, item["ukey"], item["kind"], item["src"], item.get("source", ""), item.get("url", ""),
                 item.get("pdf_url", ""), item["title"][:500], (item.get("summary") or "")[:3000],
                 json.dumps(item.get("points") or []), json.dumps(item.get("places") or []),
                 json.dumps(item.get("people") or []), json.dumps(item.get("person_ids") or []),
                 item.get("about", ""), item.get("idv", ""), json.dumps(paper) if paper else "",
                 item.get("extra", ""), float(item.get("published_at") or 0), time.time(),
                 json.dumps(item.get("signals") or {})))
        return self.item(iid), True  # type: ignore[return-value]

    def item(self, item_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM feed_items WHERE item_id=?", (item_id,))
        return rows[0] if rows else None

    def update_item(self, item_id: str, **fields: Any) -> None:
        allowed = {k: (json.dumps(v) if k in _JSON else v) for k, v in fields.items()
                   if k in ("summary", "points", "places", "extra", "people", "ranked", "hidden", "title")}
        if allowed:
            sets = ", ".join(f"{k}=?" for k in allowed)
            self._x(f"UPDATE feed_items SET {sets} WHERE item_id=?", (*allowed.values(), item_id))

    def items(self, user_id: str, since: float = 0, *, hidden: bool | None = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM feed_items WHERE user_id=? AND fetched_at>=?"
        if hidden is not None:
            sql += f" AND hidden={int(hidden)}"
        return self._all(sql + " ORDER BY fetched_at DESC", (user_id, since))

    def news(self, user_id: str, since: float) -> list[dict[str, Any]]:
        """What came in, or was newly talked about (a new signal), since *since*."""
        return self._all("SELECT * FROM feed_items WHERE user_id=? AND hidden=0 AND (fetched_at>=? OR touched_at>=?) "
                         "ORDER BY fetched_at DESC", (user_id, since, since))

    def unranked(self, user_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM feed_items WHERE user_id=? AND ranked=0 AND hidden=0 "
                         "ORDER BY fetched_at DESC LIMIT ?", (user_id, limit))

    def of_person(self, person_id: str) -> list[dict[str, Any]]:
        like = f'%"{person_id}"%'
        return self._all("SELECT * FROM feed_items WHERE (person_ids LIKE ? OR about=?) AND hidden=0 "
                         "ORDER BY COALESCE(NULLIF(published_at, 0), fetched_at) DESC", (like, person_id))

    def known_keys(self, user_id: str) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute("SELECT ukey FROM feed_items WHERE user_id=?", (user_id,))}

    def prune(self, user_id: str, older_than: float) -> None:
        """Forget old items nobody saved."""
        with self._lock:
            ids = [r[0] for r in self._conn.execute(
                "SELECT item_id FROM feed_items WHERE user_id=? AND fetched_at<? AND item_id NOT IN "
                "(SELECT item_id FROM feed_saves WHERE user_id=?)", (user_id, older_than, user_id))]
            for iid in ids:
                self._conn.execute("DELETE FROM feed_ranks WHERE item_id=?", (iid,))
                self._conn.execute("DELETE FROM feed_items WHERE item_id=?", (iid,))
            self._conn.commit()

    # ------------------------------------------------------------------ ranks

    def set_ranks(self, item_id: str, ranks: list[tuple[str, int, str]]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM feed_ranks WHERE item_id=?", (item_id,))
            for cid, rel, why in ranks:
                self._conn.execute("INSERT OR REPLACE INTO feed_ranks (item_id, campaign_id, rel, why) VALUES (?,?,?,?)",
                                   (item_id, cid, max(1, min(5, int(rel))), why[:600]))
            self._conn.execute("UPDATE feed_items SET ranked=1 WHERE item_id=?", (item_id,))
            self._conn.commit()

    def ranks(self, item_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {i: [] for i in item_ids}
        for i in range(0, len(item_ids), 500):
            chunk = item_ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self._all(f"SELECT * FROM feed_ranks WHERE item_id IN ({marks}) ORDER BY rel DESC", tuple(chunk)):
                out[r["item_id"]].append(r)
        return out

    def drop_project(self, campaign_id: str) -> None:
        self._x("DELETE FROM feed_ranks WHERE campaign_id=?", (campaign_id,))
        self._x("UPDATE feed_saves SET campaign_id='' WHERE campaign_id=?", (campaign_id,))

    # ------------------------------------------------------------------ saved

    def save(self, user_id: str, item_id: str, campaign_id: str) -> None:
        self._x("INSERT OR REPLACE INTO feed_saves (user_id, item_id, campaign_id, saved_at) VALUES (?,?,?,?)",
                (user_id, item_id, campaign_id, time.time()))

    def unsave(self, user_id: str, item_id: str) -> None:
        self._x("DELETE FROM feed_saves WHERE user_id=? AND item_id=?", (user_id, item_id))

    def saves(self, user_id: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {r["item_id"]: dict(r) for r in self._conn.execute(
                "SELECT * FROM feed_saves WHERE user_id=? ORDER BY saved_at DESC", (user_id,))}

    # ------------------------------------------------------------------ people

    def add_person(self, user_id: str, name: str, *, aff: str = "", ids: list[dict[str, Any]] | None = None,
                   coauthors: list[str] | None = None) -> dict[str, Any]:
        pid = f"p_{uuid.uuid4().hex[:10]}"
        self._x("INSERT INTO feed_people (person_id, user_id, name, aff, ids, wants, coauthors, created_at, seen_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, user_id, name.strip()[:200], aff[:300], json.dumps(ids or []),
                 json.dumps({w: True for w in WANTS}), json.dumps(coauthors or []), time.time(), time.time()))
        return self.person(pid)  # type: ignore[return-value]

    def person(self, person_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM feed_people WHERE person_id=?", (person_id,))
        return rows[0] if rows else None

    def people(self, user_id: str, *, removed: bool = False) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM feed_people WHERE user_id=? AND removed=? ORDER BY created_at",
                         (user_id, int(removed)))

    def update_person(self, person_id: str, **fields: Any) -> None:
        allowed = {k: (json.dumps(v) if k in _JSON else v) for k, v in fields.items()
                   if k in ("name", "aff", "ids", "wants", "coauthors", "removed", "seen_at")}
        if allowed:
            sets = ", ".join(f"{k}=?" for k in allowed)
            self._x(f"UPDATE feed_people SET {sets} WHERE person_id=?", (*allowed.values(), person_id))


class LazyFeedStore:
    """Opened on first use, so ``AURORA_HOME`` is read then (tests switch it per test)."""

    def __init__(self) -> None:
        self._store: FeedStore | None = None

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = FeedStore()
        return getattr(self._store, name)


feed_store = LazyFeedStore()

"""What Pippin may write outside itself, per person: each kind of write is a switch, off by default.

Pippin reads freely (pages, Zotero, notes, a manuscript) but writes into a person's own tools only as far as they
allow, kind by kind:

* ``off`` (the default): never; the console offers what it would have written to copy instead;
* ``ask``: only when they do it themselves, one action at a time (a click, a sentence said);
* ``auto``: also on its own in the background (a sync sending papers to a collection).

Every write is logged with what it did and how to undo it. Changing a manuscript's text is never offered.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

LEVELS = ("off", "ask", "auto")
WRITES: dict[str, dict[str, str]] = {
    "zotero.save": {"target": "Zotero", "label": "Save papers and their PDFs to Zotero"},
    "zotero.note": {"target": "Zotero", "label": "Write notes on Zotero items"},
    "zotero.annotate": {"target": "Zotero", "label": "Send the notes you keep on a PDF as Zotero annotations"},
    "zotero.toread": {"target": "Zotero", "label": "Mark items to read"},
    "overleaf.cite": {"target": "Overleaf", "label": "Insert citations in the manuscript"},
    "overleaf.bib": {"target": "Overleaf", "label": "Add entries to the .bib file"},
    "obsidian.create": {"target": "Obsidian", "label": "Create notes in your vault"},
    "obsidian.append": {"target": "Obsidian", "label": "Append to an existing note"},
}
PREF = "writes"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS write_log (
    log_id     TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    write      TEXT NOT NULL,          -- a key of WRITES
    detail     TEXT NOT NULL DEFAULT '{}',
    undo       TEXT NOT NULL DEFAULT '{}',   -- what undoing it takes
    created_at REAL NOT NULL,
    undone_at  REAL
);
CREATE INDEX IF NOT EXISTS write_log_owner ON write_log(owner, created_at);
"""


def levels(user_id: str) -> dict[str, str]:
    from aurora.auth.store import auth_store
    saved = auth_store.get_pref(user_id, PREF, {}) or {}
    return {k: saved.get(k) if saved.get(k) in LEVELS else "off" for k in WRITES}


def level(user_id: str, key: str) -> str:
    return levels(user_id).get(key, "off")


def set_levels(user_id: str, changes: dict[str, str]) -> dict[str, str]:
    from aurora.auth.store import auth_store
    bad = [k for k, v in changes.items() if k not in WRITES or v not in LEVELS]
    if bad:
        raise ValueError(f"unknown write or level: {', '.join(bad)}")
    current = levels(user_id)
    current.update(changes)
    auth_store.set_pref(user_id, PREF, current)
    return current


def allowed(user_id: str, key: str, *, background: bool = False) -> bool:
    """May Pippin make this write now? ``background``: on its own, not as the person's own action."""
    lv = level(user_id, key)
    return lv == "auto" if background else lv in ("ask", "auto")


class WriteLog:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from aurora.paths import aurora_home
            data_dir = aurora_home()
        self._conn = sqlite3.connect(str(Path(data_dir) / "aurora.db"), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()

    def record(self, owner: str, write: str, detail: dict[str, Any], undo: dict[str, Any] | None = None) -> str:
        lid = f"w_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._conn.execute("INSERT INTO write_log (log_id, owner, write, detail, undo, created_at) VALUES (?,?,?,?,?,?)",
                               (lid, owner, write, json.dumps(detail), json.dumps(undo or {}), time.time()))
            self._conn.commit()
        return lid

    def entries(self, owner: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM write_log WHERE owner=? ORDER BY created_at DESC LIMIT ?",
                                      (owner, limit)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"]), "undo": json.loads(r["undo"])} for r in rows]

    def entry(self, log_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM write_log WHERE log_id=?", (log_id,)).fetchone()
        return {**dict(r), "detail": json.loads(r["detail"]), "undo": json.loads(r["undo"])} if r else None

    def mark_undone(self, log_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE write_log SET undone_at=? WHERE log_id=?", (time.time(), log_id))
            self._conn.commit()


class _Lazy:
    def __init__(self) -> None:
        self._log: WriteLog | None = None

    def reset(self) -> None:
        if self._log is not None:
            self._log.close()
        self._log = None

    def __getattr__(self, name: str) -> Any:
        if self._log is None:
            self._log = WriteLog()
        return getattr(self._log, name)


write_log = _Lazy()


async def undo(owner: str, log_id: str) -> dict[str, Any]:
    """Undo a write Pippin made. Writes made in the browser or in a note app (Overleaf, Obsidian) are undone there,
    by the entry point that made them: the answer says so."""
    entry = write_log.entry(log_id)
    if entry is None or entry["owner"] != owner:
        raise ValueError("no such write")
    if entry["undone_at"]:
        raise ValueError("it was already undone")
    u = entry["undo"]
    kind = u.get("kind", "")
    if kind in ("zotero_item", "zotero_note"):
        from aurora.literature.zotero import client_for
        client = client_for(owner)
        item = await client.item(u["library"], u["item_key"])
        if item is not None and not await client.delete(u["library"], u["item_key"], int(item.get("version") or 0)):
            raise ValueError("it was changed in Zotero since; undo it there")
    elif kind == "zotero_tag":
        from aurora.literature.zotero import client_for
        client = client_for(owner)
        item = await client.item(u["library"], u["item_key"])
        if item is not None:
            tags = [t for t in (item.get("data") or {}).get("tags") or [] if t.get("tag") != u["tag"]]
            if await client.patch(u["library"], u["item_key"], {"tags": tags}, int(item.get("version") or 0)) is None:
                raise ValueError("it was changed in Zotero since; undo it there")
    elif kind in ("overleaf", "obsidian", "zotero_app"):
        return {"undone": False, "where": kind, "how": u.get("how", "Undo it in the editor (⌘Z).")}
    else:
        raise ValueError("this write cannot be undone from here")
    write_log.mark_undone(log_id)
    return {"undone": True}

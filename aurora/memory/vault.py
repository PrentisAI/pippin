"""Your own notes, from an Obsidian vault (or any folder of Markdown files), in your memory.

Notes come in two ways: the console reads a vault folder on the machine it runs on (Memory → Your notes: the folder,
and which subfolders), or the Obsidian plugin sends notes as you write them (when the console runs elsewhere). Either
way a note is a source of platform ``note``: what it claims is merged with what you saved from elsewhere, and a
conflict between your own note and a paper is shown as such. When a note changes, its old version is retired (its
evidence goes, and claims only it made go with it) and the new version is read.

AURORA never writes into the vault from here; writing (a literature note, an appended passage) is the plugin's, and
only as far as you allowed it.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aurora.memory import ingest
from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)

PER_RUN = 20                  # notes read per run (the rest wait for the next)
MIN_CHARS = 200               # shorter notes are not worth reading
MAX_FILE = 400_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mem_notes (
    owner      TEXT NOT NULL,
    vault      TEXT NOT NULL,
    path       TEXT NOT NULL,            -- relative to the vault
    digest     TEXT NOT NULL,
    source_id  TEXT NOT NULL DEFAULT '',
    mtime      REAL NOT NULL DEFAULT 0,
    seen_at    REAL NOT NULL,
    PRIMARY KEY (owner, vault, path)
);
"""


def _db() -> Any:
    mem_store._conn.executescript(_SCHEMA)
    return mem_store._conn


def settings(owner: str) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    s = auth_store.get_pref(owner, "memory_vault", None) or {}
    return {"path": s.get("path", ""), "folders": s.get("folders", []), "exclude": s.get("exclude", []),
            "on": bool(s.get("on", False))}


def set_settings(owner: str, path: str, folders: list[str], exclude: list[str], on: bool) -> dict[str, Any]:
    from aurora.auth.store import auth_store
    p = Path(path).expanduser() if path else None
    if on and (p is None or not p.is_dir()):
        raise ValueError(f"There is no folder at {path!r} on the machine the console runs on")
    auth_store.set_pref(owner, "memory_vault", {"path": str(p) if p else "", "folders": [f.strip("/") for f in folders if f.strip()],
                                                "exclude": [f.strip("/") for f in exclude if f.strip()], "on": on})
    return settings(owner)


def owners() -> list[str]:
    """Everyone with a vault folder to read."""
    from aurora.auth.store import auth_store
    rows = auth_store._conn.execute("SELECT user_id FROM user_prefs WHERE key='memory_vault'").fetchall()
    return [r[0] for r in rows if settings(r[0])["on"]]


def notes(owner: str) -> list[dict[str, Any]]:
    with mem_store._lock:
        rows = _db().execute("SELECT * FROM mem_notes WHERE owner=? ORDER BY seen_at DESC", (owner,)).fetchall()
    return [dict(r) for r in rows]


def _row(owner: str, vault: str, path: str) -> dict[str, Any] | None:
    with mem_store._lock:
        r = _db().execute("SELECT * FROM mem_notes WHERE owner=? AND vault=? AND path=?", (owner, vault, path)).fetchone()
    return dict(r) if r else None


def link(vault: str, path: str) -> str:
    return f"obsidian://open?vault={quote(vault)}&file={quote(path.removesuffix('.md'))}"


def _body(text: str) -> str:
    """The note without its front matter."""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end > 0:
            return text[end + 4:].lstrip()
    return text


def upsert(access: Any, models: dict[str, str], owner: str, vault: str, path: str, text: str,
           mtime: float = 0.0) -> dict[str, Any]:
    """A note, new or changed: read it into the memory (its old version retired). Unchanged notes are left alone."""
    body = _body(text)
    digest = hashlib.sha256(body.encode()).hexdigest()
    have = _row(owner, vault, path)
    if have and have["digest"] == digest:
        return {"state": "unchanged", "source_id": have["source_id"]}
    if have and have["source_id"]:
        mem_store.retire_source(have["source_id"])
    sid = ""
    if len(body.strip()) >= MIN_CHARS:
        src = ingest.start(access, models, owner, url=link(vault, path), text=body, title=Path(path).stem, via="obsidian")
        sid = src["source_id"]
    with mem_store._lock:
        _db().execute("INSERT OR REPLACE INTO mem_notes (owner, vault, path, digest, source_id, mtime, seen_at) "
                      "VALUES (?,?,?,?,?,?,?)", (owner, vault, path, digest, sid, mtime, time.time()))
        mem_store._conn.commit()
    return {"state": "read" if sid else "too_short", "source_id": sid, "changed": bool(have)}


def remove(owner: str, vault: str, path: str) -> bool:
    """A note deleted in the vault: what only it said goes from the memory."""
    have = _row(owner, vault, path)
    if have is None:
        return False
    if have["source_id"]:
        mem_store.retire_source(have["source_id"])
    with mem_store._lock:
        _db().execute("DELETE FROM mem_notes WHERE owner=? AND vault=? AND path=?", (owner, vault, path))
        mem_store._conn.commit()
    return True


def _wanted(rel: str, folders: list[str], exclude: list[str]) -> bool:
    parts = rel.split("/")
    if any(p.startswith(".") for p in parts):                 # .obsidian, .trash
        return False
    if any(rel == f or rel.startswith(f + "/") for f in exclude):
        return False
    return not folders or any(rel.startswith(f + "/") for f in folders)


def scan(access: Any, models: dict[str, str], owner: str) -> dict[str, int]:
    """Read the vault folder's new and changed notes (at most PER_RUN a run); forget deleted ones."""
    s = settings(owner)
    if not s["on"] or not s["path"]:
        return {"read": 0, "removed": 0}
    root = Path(s["path"])
    if not root.is_dir():
        return {"read": 0, "removed": 0}
    vault = root.name
    known = {n["path"]: n for n in notes(owner) if n["vault"] == vault}
    present: set[str] = set()
    read = 0
    for f in sorted(root.rglob("*.md"), key=lambda p: -p.stat().st_mtime):
        rel = f.relative_to(root).as_posix()
        if not _wanted(rel, s["folders"], s["exclude"]):
            continue
        present.add(rel)
        st = f.stat()
        if rel in known and known[rel]["mtime"] >= st.st_mtime:
            continue
        if read >= PER_RUN or st.st_size > MAX_FILE:
            continue
        try:
            out = upsert(access, models, owner, vault, rel, f.read_text(encoding="utf-8", errors="replace"), st.st_mtime)
        except Exception:
            logger.info("reading note %s failed", rel, exc_info=True)
            continue
        if out["state"] == "unchanged":
            with mem_store._lock:
                _db().execute("UPDATE mem_notes SET mtime=? WHERE owner=? AND vault=? AND path=?", (st.st_mtime, owner, vault, rel))
                mem_store._conn.commit()
        elif out["state"] == "read":
            read += 1
    removed = sum(remove(owner, vault, rel) for rel in set(known) - present)
    return {"read": read, "removed": removed}


def markdown(unit_id: str) -> str:
    """An idea unit as Markdown, for a note: the claim, its sources with their words, what it conflicts with."""
    u = mem_store.unit(unit_id)
    if u is None:
        return ""
    lines = [f"## {u['claim']}", ""]
    if u.get("gist"):
        lines += [u["gist"], ""]
    for e in mem_store.evidence(unit_id):
        s = mem_store.source(e["source_id"]) or {}
        title = s.get("title") or s.get("url") or "a source"
        where = f"[{title}]({s['url']})" if s.get("url") else title
        lines.append(f"> {e['quote']}\n> — {where}")
        lines.append("")
    for lk in mem_store.links([unit_id]):
        other = mem_store.unit(lk["b"] if lk["a"] == unit_id else lk["a"])
        if other and lk["rel"] == "contradicts":
            lines.append(f"- Conflicts with: {other['claim']}")
        elif other and lk["rel"] == "refines":
            lines.append(f"- Refines / is refined by: {other['claim']}")
    lines.append(f"\n<!-- aurora:{unit_id} -->")
    return "\n".join(lines).strip() + "\n"

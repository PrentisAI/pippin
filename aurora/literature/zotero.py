"""Zotero: each person's Zotero collections, kept in step with their projects.

A person connects either

* their **Zotero account** (a private API key from zotero.org): their own library and their group
  libraries, both ways, wherever AURORA runs; or
* **Zotero 7 on the same computer** as AURORA (its local API): read-only, while Zotero is open.

They choose which collection goes with which project (or with their own Library). Then, every few
minutes and when a project's Literature page is opened:

* **from Zotero**: the collection's items are checked against the indexes and join the project's
  literature (an item no index has is kept as one the person vouches for); their PDFs are copied into
  the project's files (``papers/``); their highlights and notes reach the agents;
* **to Zotero**: verified papers of the project that are not in the collection yet are added to it,
  tagged ``aurora`` (an item already in the library is put into the collection, not duplicated).

Nothing is ever deleted in Zotero, and a reference that was not verified is never sent.
"""

from __future__ import annotations

import asyncio
import copy
import html
import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from aurora.auth import writes
from aurora.auth.writes import write_log
from aurora.literature.check import Check, Reference, check
from aurora.literature.indexes import indexes
from aurora.literature.library import library
from aurora.literature.records import Paper, clean_arxiv, clean_doi, fold

logger = logging.getLogger(__name__)

WEB_API = "https://api.zotero.org"
LOCAL_API = "http://localhost:23119/api"
TAG = "aurora"
SYNC_MINUTES = 5
MAX_PDF_BYTES = 60 * 1024 * 1024
MAX_PUSH_PER_SYNC = 100
_SKIP_TYPES = {"attachment", "note", "annotation"}

DEFAULT_OPTIONS = {"pdfs": True, "notes": True, "push": True, "citekeys": True, "idea_notes": False}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS zotero_links (
    user_id     TEXT PRIMARY KEY,
    method      TEXT NOT NULL,             -- web | local
    zotero_user TEXT NOT NULL DEFAULT '',
    username    TEXT NOT NULL DEFAULT '',
    access      TEXT NOT NULL DEFAULT '{}',
    options     TEXT NOT NULL DEFAULT '{}',
    last_sync   REAL,
    last_error  TEXT NOT NULL DEFAULT '',
    last_report TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS zotero_maps (
    user_id        TEXT NOT NULL,
    library        TEXT NOT NULL,          -- user | group:<id>
    collection_key TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    campaign_id    TEXT NOT NULL,          -- '' : the person's own Library
    version        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, library, collection_key)
);
CREATE TABLE IF NOT EXISTS zotero_notes (       -- idea summaries written as notes on Zotero items
    user_id  TEXT NOT NULL,
    library  TEXT NOT NULL,
    card_id  TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    note_key TEXT NOT NULL,
    PRIMARY KEY (user_id, library, card_id, paper_id)
);
CREATE TABLE IF NOT EXISTS zotero_items (
    user_id   TEXT NOT NULL,
    library   TEXT NOT NULL,
    item_key  TEXT NOT NULL,
    paper_id  TEXT NOT NULL DEFAULT '',
    ref_id    TEXT NOT NULL DEFAULT '',
    version   INTEGER NOT NULL DEFAULT 0,
    citekey   TEXT NOT NULL DEFAULT '',
    pdf       TEXT NOT NULL DEFAULT '',    -- the attachment copied (its key)
    synced_at REAL NOT NULL,
    PRIMARY KEY (user_id, library, item_key)
);
CREATE TABLE IF NOT EXISTS zotero_ann_versions (  -- how far a library's annotations were read
    user_id TEXT NOT NULL,
    library TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, library)
);
"""


class ZoteroError(Exception):
    """Zotero refused or could not be reached; the message says what to do."""


# ---------------------------------------------------------------------------
# Items: Zotero's JSON <-> references and papers


def _year(date: str) -> int | None:
    m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", date or "")
    return int(m.group(1)) if m else None


def _extra(data: dict[str, Any], name: str) -> str:
    for line in (data.get("extra") or "").splitlines():
        if line.lower().startswith(name.lower() + ":"):
            return line.split(":", 1)[1].strip()
    return ""


def item_reference(data: dict[str, Any]) -> Reference:
    """What a Zotero item says a paper is, as a reference to check."""
    creators = [c for c in data.get("creators") or [] if c.get("creatorType") in ("author", None)] or \
        list(data.get("creators") or [])
    first = creators[0] if creators else {}
    author = (f"{first.get('lastName', '')}, {first.get('firstName', '')}".strip(", ")
              if first.get("lastName") else first.get("name", ""))
    arxiv = clean_arxiv(data.get("archiveID") or "") or clean_arxiv(_extra(data, "arXiv"))
    if not arxiv and "arxiv.org" in (data.get("url") or ""):
        arxiv = clean_arxiv(data["url"])
    from aurora.literature.records import arxiv_version
    version = arxiv_version(data.get("archiveID")) or arxiv_version(data.get("url")) or arxiv_version(_extra(data, "arXiv"))
    return Reference(title=data.get("title") or "", authors=author, year=_year(data.get("date", "")),
                     doi=clean_doi(data.get("DOI") or _extra(data, "DOI")), arxiv_id=arxiv, arxiv_version=version,
                     text=f"Zotero item {data.get('key', '')}")


def citekey(data: dict[str, Any]) -> str:
    """The Better BibTeX citation key, when the item has one."""
    return data.get("citationKey") or _extra(data, "Citation Key")


def paper_item(p: Paper, collection_key: str) -> dict[str, Any]:
    """A new Zotero item for a verified paper, in a collection, tagged ``aurora``."""
    creators = []
    for name in p.authors[:50]:
        if "," in name:
            last, firstn = [x.strip() for x in name.split(",", 1)]
        else:
            parts = name.split()
            last, firstn = (parts[-1], " ".join(parts[:-1])) if parts else (name, "")
        creators.append({"creatorType": "author", "firstName": firstn, "lastName": last})
    preprint = bool(p.arxiv_id and not p.doi)
    item: dict[str, Any] = {
        "itemType": "preprint" if preprint else "journalArticle",
        "title": p.title, "creators": creators, "date": str(p.year or ""), "url": p.url,
        "abstractNote": p.abstract[:10000], "DOI": p.doi,
        "collections": [collection_key], "tags": [{"tag": TAG}],
    }
    if preprint:
        item.update({"repository": "arXiv", "archiveID": f"arXiv:{p.arxiv_id}"})
    else:
        item["publicationTitle"] = p.venue
        if p.arxiv_id:
            item["extra"] = f"arXiv: {p.arxiv_id}"
    return item


def _note_text(note_html: str) -> str:
    text = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", note_html or "", flags=re.I)
    return "\n".join(line.strip() for line in html.unescape(re.sub(r"<[^>]+>", "", text)).splitlines() if line.strip())


# ---------------------------------------------------------------------------
# The Zotero API


def library_path(library: str, user_id: str) -> str:
    return f"groups/{library.split(':', 1)[1]}" if library.startswith("group:") else f"users/{user_id}"


class ZoteroClient:
    """Zotero's web API (or the local API of Zotero 7, which speaks the same, read-only)."""

    def __init__(self, method: str, key: str = "", user_id: str = "0", *, client: httpx.AsyncClient | None = None) -> None:
        self.method = method
        self.key = key
        self.user_id = "0" if method == "local" else str(user_id)
        self.base = LOCAL_API if method == "local" else WEB_API
        self._client = client

    async def _req(self, verb: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None,
                   headers: dict[str, str] | None = None, raw: bool = False, allow: tuple[int, ...] = (),
                   content: bytes | str | None = None) -> httpx.Response:
        hdrs = {"Zotero-API-Version": "3", **(headers or {})}
        if self.key:
            hdrs["Zotero-API-Key"] = self.key
        url = path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"
        for attempt in range(4):
            try:
                kw: dict[str, Any] = {"params": params, "headers": hdrs}
                kw.update({"content": content} if content is not None else {"json": body})
                if self._client is not None:
                    r = await self._client.request(verb, url, **kw)
                else:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True) as c:
                        r = await c.request(verb, url, **kw)
            except httpx.HTTPError as exc:
                if self.method == "local":
                    raise ZoteroError("Zotero on this computer does not answer. Is Zotero 7 open, with “Allow other "
                                      "applications on this computer to communicate with Zotero” on?") from exc
                if attempt == 3:
                    raise ZoteroError(f"Zotero could not be reached ({type(exc).__name__})") from exc
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 503) and attempt < 3:
                wait = float(r.headers.get("Retry-After") or r.headers.get("Backoff") or 5)
                await asyncio.sleep(min(wait, 60.0))
                continue
            if r.status_code in allow:
                return r
            if r.status_code == 403:
                raise ZoteroError("Zotero refused the key: check that it is right and has access to this library"
                                  + (" with write access" if verb != "GET" else ""))
            if r.status_code == 404 and not raw:
                raise ZoteroError(f"Zotero has no {path}")
            if r.status_code >= 400 and r.status_code != 404:
                raise ZoteroError(f"Zotero answered {r.status_code}: {r.text[:200]}")
            if (backoff := r.headers.get("Backoff")) and self._client is None:
                await asyncio.sleep(min(float(backoff), 30.0))
            return r
        raise ZoteroError("Zotero is busy; try again later")

    async def _all(self, path: str, params: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
        """Every page of a listing, and the library version it reflects."""
        out: list[dict[str, Any]] = []
        start, version = 0, 0
        while True:
            r = await self._req("GET", path, params={**(params or {}), "limit": 100, "start": start, "format": "json"})
            page = r.json() if r.status_code != 404 else []
            version = int(r.headers.get("Last-Modified-Version") or version or 0)
            out += page
            total = int(r.headers.get("Total-Results") or len(out))
            start += len(page)
            if not page or start >= total:
                return out, version

    async def whoami(self) -> dict[str, Any]:
        """The key's owner and what it may do."""
        if self.method == "local":
            await self._req("GET", "users/0/collections", params={"limit": 1})
            return {"userID": "0", "username": "Zotero on this computer",
                    "access": {"user": {"library": True, "files": True, "notes": True, "write": False}}}
        r = await self._req("GET", "keys/current")
        info = r.json()
        self.user_id = str(info.get("userID", self.user_id))
        return info

    async def groups(self) -> list[dict[str, Any]]:
        if self.method == "local":
            return []
        groups, _ = await self._all(f"users/{self.user_id}/groups")
        return [{"id": str(g.get("id")), "name": (g.get("data") or {}).get("name", "")} for g in groups]

    async def collections(self, library: str) -> list[dict[str, Any]]:
        rows, _ = await self._all(f"{library_path(library, self.user_id)}/collections")
        return rows

    async def items(self, library: str, collection_key: str, since: int = 0) -> tuple[list[dict[str, Any]], int]:
        params = {"since": since} if since else {}
        return await self._all(f"{library_path(library, self.user_id)}/collections/{collection_key}/items/top", params)

    async def children(self, library: str, item_key: str) -> list[dict[str, Any]]:
        rows, _ = await self._all(f"{library_path(library, self.user_id)}/items/{item_key}/children")
        return rows

    async def file(self, library: str, item_key: str) -> bytes:
        r = await self._req("GET", f"{library_path(library, self.user_id)}/items/{item_key}/file", raw=True)
        if r.status_code == 404:
            raise ZoteroError("the file is not stored in Zotero")
        if len(r.content) > MAX_PDF_BYTES:
            raise ZoteroError("the PDF is larger than 60 MB")
        return r.content

    async def find(self, library: str, text: str) -> list[dict[str, Any]]:
        r = await self._req("GET", f"{library_path(library, self.user_id)}/items",
                            params={"q": text, "qmode": "everything", "limit": 10, "format": "json"})
        return r.json() if r.status_code != 404 else []

    async def create(self, library: str, items: list[dict[str, Any]]) -> dict[str, str]:
        """Create items; returns {index: item key} of those created."""
        return {k: v["key"] for k, v in (await self.create_items(library, items)).items()}

    async def create_items(self, library: str, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Create items; returns {index: {key, version}} of those created, and {index: {error}} of the others."""
        r = await self._req("POST", f"{library_path(library, self.user_id)}/items", body=items,
                            headers={"Zotero-Write-Token": hex(int(time.time() * 1e6))[2:].rjust(32, "0")[-32:]})
        res = r.json()
        out: dict[str, dict[str, Any]] = {}
        for k, v in (res.get("successful") or {}).items():
            out[k] = {"key": v.get("key") or (v.get("data") or {}).get("key"), "version": int(v.get("version") or 0)}
        if not out:     # older servers answer {"success": {index: key}}
            version = int(r.headers.get("Last-Modified-Version") or 0)
            out = {k: {"key": v, "version": version} for k, v in (res.get("success") or {}).items()}
        for k, v in (res.get("failed") or {}).items():
            out[k] = {"error": str((v or {}).get("message") or v)[:200]}
        return out

    async def item(self, library: str, key: str) -> dict[str, Any] | None:
        r = await self._req("GET", f"{library_path(library, self.user_id)}/items/{key}", params={"format": "json"},
                            raw=True)
        return r.json() if r.status_code == 200 else None

    async def patch(self, library: str, key: str, data: dict[str, Any], version: int) -> int | None:
        """Change an item; its new version, or None when it changed in Zotero meanwhile (or is gone)."""
        r = await self._req("PATCH", f"{library_path(library, self.user_id)}/items/{key}", body=data, raw=True,
                            headers={"If-Unmodified-Since-Version": str(version)}, allow=(412,))
        if r.status_code in (412, 404):
            return None
        return int(r.headers.get("Last-Modified-Version") or version)

    async def delete(self, library: str, key: str, version: int) -> bool:
        """Delete an item; False when it changed in Zotero meanwhile."""
        r = await self._req("DELETE", f"{library_path(library, self.user_id)}/items/{key}", raw=True,
                            headers={"If-Unmodified-Since-Version": str(version)}, allow=(412,))
        return r.status_code != 412

    async def annotations_since(self, library: str, since: int) -> tuple[list[dict[str, Any]], int]:
        """The library's annotations changed since a version (all of them for 0)."""
        params: dict[str, Any] = {"itemType": "annotation"}
        if since:
            params["since"] = since
        return await self._all(f"{library_path(library, self.user_id)}/items", params)

    async def deleted_since(self, library: str, since: int) -> list[str]:
        """Keys of the items deleted since a version (the local API cannot say: nothing then)."""
        if self.method == "local" or not since:
            return []
        r = await self._req("GET", f"{library_path(library, self.user_id)}/deleted", params={"since": since}, raw=True)
        return list((r.json() or {}).get("items") or []) if r.status_code == 200 else []

    async def upload_pdf(self, library: str, parent_key: str, filename: str, content: bytes) -> str:
        """Attach a PDF to an item, stored in Zotero; returns the attachment's key."""
        import hashlib
        made = await self.create_items(library, [{
            "itemType": "attachment", "parentItem": parent_key, "linkMode": "imported_file", "title": "Full Text PDF",
            "contentType": "application/pdf", "filename": filename, "tags": [{"tag": TAG}]}])
        att = (made.get("0") or {}).get("key")
        if not att:
            raise ZoteroError(f"Zotero did not take the PDF: {(made.get('0') or {}).get('error', 'no answer')}")
        path = f"{library_path(library, self.user_id)}/items/{att}/file"
        form = {"md5": hashlib.md5(content).hexdigest(), "filename": filename, "filesize": str(len(content)),
                "mtime": str(int(time.time() * 1000))}
        r = await self._req("POST", path, content=urlencode(form),
                            headers={"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"})
        auth = r.json()
        if auth.get("exists"):
            return att
        body = auth["prefix"].encode() + content + auth["suffix"].encode()
        # the file goes to Zotero's storage host, which must not get the API key
        try:
            if self._client is not None:
                up = await self._client.post(auth["url"], content=body, headers={"Content-Type": auth["contentType"]})
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as c:
                    up = await c.post(auth["url"], content=body, headers={"Content-Type": auth["contentType"]})
        except httpx.HTTPError as exc:
            raise ZoteroError(f"the PDF upload failed ({type(exc).__name__})") from exc
        if up.status_code not in (200, 201, 204):
            raise ZoteroError(f"the PDF upload was refused ({up.status_code})")
        await self._req("POST", path, content=f"upload={auth['uploadKey']}",
                        headers={"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"})
        return att

    async def add_to_collection(self, library: str, item: dict[str, Any], collection_key: str) -> None:
        data = item.get("data") or item
        cols = list(dict.fromkeys([*(data.get("collections") or []), collection_key]))
        tags = data.get("tags") or []
        if not any(t.get("tag") == TAG for t in tags):
            tags = [*tags, {"tag": TAG}]
        await self._req("PATCH", f"{library_path(library, self.user_id)}/items/{data['key']}",
                        body={"collections": cols, "tags": tags},
                        headers={"If-Unmodified-Since-Version": str(item.get("version") or data.get("version") or 0)})


# ---------------------------------------------------------------------------
# What each person connected


class ZoteroStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from aurora.paths import aurora_home
            data_dir = aurora_home()
        self._conn = sqlite3.connect(str(Path(data_dir) / "aurora.db"), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        if "attachment" not in {r[1] for r in self._conn.execute("PRAGMA table_info(zotero_items)")}:
            # the item's PDF attachment (its key), where notes made on the paper go
            self._conn.execute("ALTER TABLE zotero_items ADD COLUMN attachment TEXT NOT NULL DEFAULT ''")
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

    def link(self, user_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM zotero_links WHERE user_id=?", (user_id,))
        if not rows:
            return None
        r = rows[0]
        for k in ("access", "options", "last_report"):
            r[k] = json.loads(r[k] or "{}")
        r["options"] = {**DEFAULT_OPTIONS, **r["options"]}
        return r

    def links(self) -> list[dict[str, Any]]:
        return [x for x in (self.link(r["user_id"]) for r in self._all("SELECT user_id FROM zotero_links")) if x]

    def save_link(self, user_id: str, method: str, zotero_user: str, username: str, access: dict[str, Any]) -> None:
        self._x("INSERT INTO zotero_links (user_id, method, zotero_user, username, access, options, created_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET method=excluded.method, "
                "zotero_user=excluded.zotero_user, username=excluded.username, access=excluded.access",
                (user_id, method, zotero_user, username, json.dumps(access), json.dumps(DEFAULT_OPTIONS), time.time()))

    def set_options(self, user_id: str, options: dict[str, Any]) -> None:
        current = (self.link(user_id) or {}).get("options", DEFAULT_OPTIONS)
        merged = {**current, **{k: bool(v) for k, v in options.items() if k in DEFAULT_OPTIONS}}
        self._x("UPDATE zotero_links SET options=? WHERE user_id=?", (json.dumps(merged), user_id))

    def note_sync(self, user_id: str, error: str = "", report: dict[str, Any] | None = None) -> None:
        self._x("UPDATE zotero_links SET last_sync=?, last_error=?, last_report=? WHERE user_id=?",
                (time.time(), error, json.dumps(report or {}), user_id))

    def forget(self, user_id: str) -> None:
        for table in ("zotero_links", "zotero_maps", "zotero_items", "zotero_notes", "zotero_ann_versions"):
            self._x(f"DELETE FROM {table} WHERE user_id=?", (user_id,))

    def maps(self, user_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM zotero_maps WHERE user_id=? ORDER BY name", (user_id,))

    def set_maps(self, user_id: str, maps: list[dict[str, Any]]) -> None:
        """Replace the person's mappings; a collection that stays keeps its sync version."""
        with self._lock:
            old = {(m["library"], m["collection_key"]): m for m in self.maps(user_id)}
            self._x("DELETE FROM zotero_maps WHERE user_id=?", (user_id,))
            for m in maps:
                prev = old.get((m["library"], m["collection_key"]))
                version = prev["version"] if prev and prev["campaign_id"] == m["campaign_id"] else 0
                self._x("INSERT INTO zotero_maps (user_id, library, collection_key, name, campaign_id, version) "
                        "VALUES (?,?,?,?,?,?)", (user_id, m["library"], m["collection_key"], m.get("name", ""),
                                                 m["campaign_id"], version))

    def set_version(self, user_id: str, library: str, collection_key: str, version: int) -> None:
        self._x("UPDATE zotero_maps SET version=? WHERE user_id=? AND library=? AND collection_key=?",
                (version, user_id, library, collection_key))

    def item(self, user_id: str, library: str, item_key: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM zotero_items WHERE user_id=? AND library=? AND item_key=?",
                         (user_id, library, item_key))
        return rows[0] if rows else None

    def put_item(self, user_id: str, library: str, item_key: str, **fields: Any) -> None:
        prev = self.item(user_id, library, item_key) or {}
        row = {"paper_id": "", "ref_id": "", "version": 0, "citekey": "", "pdf": "", "attachment": "", **prev, **fields}
        self._x("INSERT OR REPLACE INTO zotero_items (user_id, library, item_key, paper_id, ref_id, version, citekey, "
                "pdf, attachment, synced_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (user_id, library, item_key, row["paper_id"], row["ref_id"], row["version"], row["citekey"],
                 row["pdf"], row["attachment"], time.time()))

    def attachment_of(self, user_id: str, paper_id: str) -> tuple[str, str] | None:
        """(library, attachment key) of the paper's PDF in the person's Zotero, if Zotero has one."""
        rows = self._all("SELECT library, attachment FROM zotero_items WHERE user_id=? AND paper_id=? AND attachment!='' "
                         "ORDER BY synced_at DESC", (user_id, paper_id))
        return (rows[0]["library"], rows[0]["attachment"]) if rows else None

    def paper_of_attachment(self, user_id: str, library: str, attachment: str) -> str:
        rows = self._all("SELECT paper_id FROM zotero_items WHERE user_id=? AND library=? AND attachment=? AND paper_id!=''",
                         (user_id, library, attachment))
        return rows[0]["paper_id"] if rows else ""

    def item_of_paper(self, user_id: str, paper_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM zotero_items WHERE user_id=? AND paper_id=? ORDER BY synced_at DESC",
                         (user_id, paper_id))
        return rows[0] if rows else None

    def ann_version(self, user_id: str, library: str) -> int:
        rows = self._all("SELECT version FROM zotero_ann_versions WHERE user_id=? AND library=?", (user_id, library))
        return rows[0]["version"] if rows else 0

    def set_ann_version(self, user_id: str, library: str, version: int) -> None:
        self._x("INSERT OR REPLACE INTO zotero_ann_versions VALUES (?,?,?)", (user_id, library, version))

    def item_key(self, user_id: str, library: str, paper_id: str) -> str:
        rows = self._all("SELECT item_key FROM zotero_items WHERE user_id=? AND library=? AND paper_id=?",
                         (user_id, library, paper_id))
        return rows[0]["item_key"] if rows else ""

    def noted(self, user_id: str, library: str, card_id: str, paper_id: str) -> bool:
        return bool(self._all("SELECT 1 FROM zotero_notes WHERE user_id=? AND library=? AND card_id=? AND paper_id=?",
                              (user_id, library, card_id, paper_id)))

    def note_written(self, user_id: str, library: str, card_id: str, paper_id: str, note_key: str) -> None:
        self._x("INSERT OR REPLACE INTO zotero_notes VALUES (?,?,?,?,?)", (user_id, library, card_id, paper_id, note_key))

    def papers_in(self, user_id: str, library: str | None = None) -> set[str]:
        sql = "SELECT paper_id FROM zotero_items WHERE user_id=? AND paper_id!=''" + (" AND library=?" if library else "")
        return {r["paper_id"] for r in self._all(sql, (user_id, library) if library else (user_id,))}

    def citekeys(self, paper_ids: list[str]) -> dict[str, str]:
        """Better BibTeX keys of papers (any person's), for exports."""
        out: dict[str, str] = {}
        for r in self._all("SELECT paper_id, citekey FROM zotero_items WHERE citekey!='' AND paper_id!=''"):
            if r["paper_id"] in paper_ids:
                out.setdefault(r["paper_id"], r["citekey"])
        return out


class _Lazy:
    def __init__(self) -> None:
        self._store: ZoteroStore | None = None

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = ZoteroStore()
        return getattr(self._store, name)


zotero_store = _Lazy()


# ---------------------------------------------------------------------------
# Syncing


@dataclass
class SyncReport:
    collections: int = 0
    seen: int = 0
    added: int = 0                  # verified papers that joined a project or Library
    vouched: int = 0                # items no index has, kept as the person's own
    pdfs: int = 0
    linked_pdfs: int = 0            # PDFs that are files on the person's computer (not in Zotero's storage)
    notes: int = 0
    pushed: int = 0                 # papers sent to Zotero
    idea_notes: int = 0             # idea summaries written as notes on Zotero items
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = self.__dict__.copy()
        if self.linked_pdfs:
            out["problems"] = [*self.problems, f"{self.linked_pdfs} PDFs are linked files on your computer (as ZotFile "
                               "makes them), not stored in Zotero, so they cannot be copied; their highlights still come "
                               "through. To have them copied, store the files in Zotero (or its WebDAV storage)."]
        return out


_running: dict[str, asyncio.Task] = {}
# the HTTP client used to reach Zotero (None: a new one per request); tests put a fake server here
http_client: httpx.AsyncClient | None = None


def client_for(user_id: str, *, http: httpx.AsyncClient | None = None) -> ZoteroClient:
    link = zotero_store.link(user_id)
    if link is None:
        raise ZoteroError("Zotero is not connected")
    from aurora.auth import secrets
    key = secrets.get_key(f"user:{user_id}", "zotero") or "" if link["method"] == "web" else ""
    if link["method"] == "web" and not key:
        raise ZoteroError("The Zotero key is missing; connect Zotero again")
    return ZoteroClient(link["method"], key, link["zotero_user"], client=http or http_client)


def _pdf_dir(campaign_id: str, user_id: str) -> Path:
    if campaign_id:
        from aurora.api.routes.files import project_files_dir
        return project_files_dir(campaign_id) / "papers"
    from aurora.paths import aurora_home
    return Path(aurora_home()) / "library" / user_id / "papers"


async def _check(ref: Reference) -> Check:
    """Check a Zotero item: by ID first; titles without arXiv (an item rarely needs it, and it is slow)."""
    if ref.doi or ref.arxiv_id:
        return await check(ref, indexes())
    shared = indexes()
    fast = copy.copy(shared)             # the same indexes (and settings), minus the slow ones
    fast.use = tuple(i for i in shared.use if i in ("crossref", "openalex"))
    result = await check(ref, fast)
    return result if result.status != "not_found" else await check(ref, shared)


def _standalone_pdf(data: dict[str, Any]) -> bool:
    """A PDF put into Zotero on its own, without an item around it."""
    return data.get("itemType") == "attachment" and not data.get("parentItem") \
        and data.get("contentType") == "application/pdf"


_ZOTFILE = re.compile(r"^(?P<authors>[^_]{2,80}?)_(?P<year>(?:1[89]|20)\d\d)_(?P<title>.+)$")


def pdf_reference(data: dict[str, Any]) -> Reference:
    """What a standalone PDF's title (often its file name) says: "Zhang et al_2024_Agent-SafetyBench.pdf"."""
    name = re.sub(r"\.pdf$", "", data.get("title") or data.get("filename") or "", flags=re.I).strip()
    m = _ZOTFILE.match(name)
    if m:
        authors = re.sub(r"\s+et al\.?$", "", m.group("authors").replace("_", " ")).strip()
        title, year = m.group("title"), int(m.group("year"))
    else:
        authors, title, year = "", name, None
    # file names lose punctuation and get cut off: the indexes' title may continue where this one stops
    return Reference(title=title.replace("_", " "), authors=authors, year=year, text=f"Zotero PDF {data.get('key', '')}",
                     truncated=True)


def _own_note(text: str) -> bool:
    """A note the person wrote, not one an importer made ("Comment: 23 pages, 9 figures" from arXiv)."""
    return bool(text) and not re.match(r"^(Comment|Full Text|Accepted|Submitted)\b:?", text, flags=re.I)


def _note_of(ad: dict[str, Any]) -> dict[str, Any] | None:
    """A Zotero annotation as a highlight or note the agents read."""
    text = (ad.get("annotationText") or "").strip()
    comment = (ad.get("annotationComment") or "").strip()
    if not (text or comment):
        return None
    return {"note_id": ad["key"], "kind": "highlight" if text else "note", "text": (text or comment)[:4000],
            "comment": comment if text else "", "page": str(ad.get("annotationPageLabel") or "")}


async def _annotations(client: ZoteroClient, lib: str, attachment_key: str,
                       found: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The highlights and notes on a PDF; ``found`` collects the annotations themselves (with their place)."""
    out = []
    for ann in await client.children(lib, attachment_key):
        ad = ann.get("data") or {}
        if ad.get("itemType") != "annotation":
            continue
        if found is not None:
            found.append({**ad, "version": int(ann.get("version") or ad.get("version") or 0)})
        if note := _note_of(ad):
            out.append(note)
    return out


async def _copy_pdf(client: ZoteroClient, user_id: str, lib: str, cid: str, item_key: str, att: dict[str, Any],
                    paper_id: str, report: SyncReport, title: str) -> None:
    """Copy an attachment's PDF into the project's files, once per version of the file."""
    data = att.get("data") or att
    stamp = f"{data['key']}@{att.get('version') or data.get('version')}"
    if data.get("linkMode") == "linked_file":
        report.linked_pdfs += 1
        return
    if data.get("linkMode") not in ("imported_file", "imported_url"):
        return
    if (zotero_store.item(user_id, lib, item_key) or {}).get("pdf") == stamp:
        return
    try:
        content = await client.file(lib, data["key"])
    except ZoteroError as exc:
        report.problems.append(f"PDF of “{title[:50]}”: {exc}")
        return
    stored = library.paper(paper_id)
    name = (zotero_store.item(user_id, lib, item_key) or {}).get("citekey") or (stored["key"] if stored else data["key"])
    target = _pdf_dir(cid, user_id) / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    zotero_store.put_item(user_id, lib, item_key, pdf=stamp)
    report.pdfs += 1


async def _pull_item(user_id: str, who: str, m: dict[str, Any], item: dict[str, Any], client: ZoteroClient,
                     options: dict[str, Any], report: SyncReport) -> None:
    data = item.get("data") or item
    standalone = _standalone_pdf(data)
    if data.get("itemType") in _SKIP_TYPES and not standalone:
        return
    report.seen += 1
    lib, cid = m["library"], m["campaign_id"]
    whose = "Your Zotero" if who == "You" else f"{who}'s Zotero"
    known = zotero_store.item(user_id, lib, data["key"])
    owner = "" if cid else user_id
    paper_id, ref_id = (known or {}).get("paper_id", ""), (known or {}).get("ref_id", "")
    title = data.get("title") or ""
    if not paper_id and not ref_id:
        ref = pdf_reference(data) if standalone else item_reference(data)
        if not (ref.title or ref.doi or ref.arxiv_id):
            return
        result = await _check(ref)
        if result.verified and result.paper is not None:
            paper_id = library.upsert_paper(result.paper)["paper_id"]
        elif result.status == "unchecked":
            report.problems.append(f"“{ref.title[:60]}” could not be checked now; it is tried again next time")
            return
        else:
            # the person's own item: kept as one they vouch for, never sent anywhere as a checked paper
            ref_id = library.record_ref(cid or f"user:{user_id}", result, by=whose)
            library.resolve_ref(ref_id, "have", decided_by=f"{who} (in Zotero)")
            report.vouched += 1
    cited_version = (pdf_reference(data) if standalone else item_reference(data)).arxiv_version
    if paper_id and library.add_item(cid, paper_id, source="zotero", added_by=whose, owner=owner,
                                     cited_version=cited_version):
        report.added += 1
    zotero_store.put_item(user_id, lib, data["key"], paper_id=paper_id, ref_id=ref_id,
                          version=int(item.get("version") or 0),
                          citekey=citekey(data) if options["citekeys"] and not standalone else "")
    if not paper_id or not (options["pdfs"] or options["notes"]):
        return
    notes: list[dict[str, Any]] = []
    found: list[dict[str, Any]] = []
    try:
        if standalone:          # the PDF is the item: its highlights are its own children
            zotero_store.put_item(user_id, lib, data["key"], attachment=data["key"])
            if options["pdfs"]:
                await _copy_pdf(client, user_id, lib, cid, data["key"], item, paper_id, report, title)
            if options["notes"]:
                notes += await _annotations(client, lib, data["key"], found)
        else:
            for ch in await client.children(lib, data["key"]):
                cd = ch.get("data") or {}
                if cd.get("itemType") == "note" and options["notes"]:
                    text = _note_text(cd.get("note", ""))
                    if _own_note(text):
                        notes.append({"note_id": cd["key"], "kind": "note", "text": text[:4000]})
                if cd.get("itemType") == "attachment" and cd.get("contentType") == "application/pdf":
                    if not (zotero_store.item(user_id, lib, data["key"]) or {}).get("attachment"):
                        zotero_store.put_item(user_id, lib, data["key"], attachment=cd["key"])
                    if options["pdfs"]:
                        await _copy_pdf(client, user_id, lib, cid, data["key"], ch, paper_id, report, title)
                    if options["notes"]:   # highlights live on the PDF, also when the file is not in Zotero
                        notes += await _annotations(client, lib, cd["key"], found)
    except ZoteroError as exc:
        report.problems.append(f"“{title[:50]}”: {exc}")
        return
    if options["notes"]:
        library.set_notes(cid, user_id, paper_id, notes)
        report.notes += len(notes)
        for ad in found:            # into the reader, with their place on the page
            take_annotation(user_id, lib, paper_id, cid or f"user:{user_id}", ad)


async def push(user_id: str, m: dict[str, Any], client: ZoteroClient, report: SyncReport,
               only: set[str] | None = None) -> None:
    """Verified papers of the project (or the person's Library) that the collection lacks (or just ``only``)."""
    lib, cid, ck = m["library"], m["campaign_id"], m["collection_key"]
    have = zotero_store.papers_in(user_id, lib)
    items = library.items(cid, owner="" if cid else user_id)
    missing = [i["paper_id"] for i in items if i["paper_id"] not in have
               and (i["paper_id"] in only if only is not None else i["source"] != "zotero")][:MAX_PUSH_PER_SYNC]
    new: list[tuple[str, dict[str, Any]]] = []
    for pid in missing:
        entry = library.paper(pid)
        if entry is None:
            continue
        p: Paper = entry["paper"]
        # already in the person's library (another collection)? then put it into this one
        existing = None
        if p.doi or p.arxiv_id:
            try:
                for hit in await client.find(lib, p.doi or p.arxiv_id):
                    hd = hit.get("data") or {}
                    if (p.doi and clean_doi(hd.get("DOI") or _extra(hd, "DOI")) == p.doi) or \
                            (p.arxiv_id and item_reference(hd).arxiv_id == p.arxiv_id):
                        existing = hit
                        break
            except ZoteroError as exc:
                report.problems.append(str(exc))
                return
        if existing is not None:
            await client.add_to_collection(lib, existing, ck)
            zotero_store.put_item(user_id, lib, existing["key"], paper_id=pid, version=int(existing.get("version") or 0))
            report.pushed += 1
        else:
            new.append((pid, paper_item(p, ck)))
    for start in range(0, len(new), 50):
        batch = new[start:start + 50]
        created = await client.create(lib, [it for _, it in batch])
        for idx, key in created.items():
            pid = batch[int(idx)][0]
            zotero_store.put_item(user_id, lib, key, paper_id=pid)
            report.pushed += 1


async def sync(user_id: str, who: str, *, campaign_id: str | None = None,
               http: httpx.AsyncClient | None = None) -> SyncReport:
    """Bring a person's mapped collections and projects in step (one project, or all of them)."""
    link = zotero_store.link(user_id)
    if link is None:
        raise ZoteroError("Zotero is not connected")
    client = client_for(user_id, http=http)
    options = link["options"]
    report = SyncReport()
    error = ""
    try:
        for m in zotero_store.maps(user_id):
            if campaign_id is not None and m["campaign_id"] != campaign_id:
                continue
            report.collections += 1
            items, version = await client.items(m["library"], m["collection_key"], since=m["version"])
            for item in items:
                try:
                    await _pull_item(user_id, who, m, item, client, options, report)
                except ZoteroError as exc:
                    report.problems.append(str(exc))
            zotero_store.set_version(user_id, m["library"], m["collection_key"], version or m["version"])
            can_write = link["method"] == "web" and _may_write(link["access"], m["library"])
            # writing into the person's Zotero on its own: only as far as they allowed (off by default)
            if options["push"] and can_write and writes.allowed(user_id, "zotero.save", background=True):
                try:
                    await push(user_id, m, client, report)
                except ZoteroError as exc:
                    report.problems.append(f"Sending papers to “{m['name']}”: {exc}")
            if options.get("idea_notes") and can_write and writes.allowed(user_id, "zotero.note", background=True):
                try:
                    await idea_notes(user_id, m, client, report)
                except ZoteroError as exc:
                    report.problems.append(f"Writing idea notes in “{m['name']}”: {exc}")
        if options["notes"]:
            try:
                await sync_notes(user_id, client)
            except ZoteroError as exc:
                report.problems.append(f"Notes on PDFs: {exc}")
    except ZoteroError as exc:
        error = str(exc)
        raise
    finally:
        zotero_store.note_sync(user_id, error, report.to_dict())
    return report


async def idea_notes(user_id: str, m: dict[str, Any], client: ZoteroClient, report: SyncReport) -> None:
    """Each idea a paper is evidence for, as a note on the paper's Zotero item (written once; never removed)."""
    from aurora.brainstorm.store import bs_store
    lib, cid = m["library"], m["campaign_id"]
    space = cid or f"user:{user_id}"
    cards = {c["card_id"]: c for c in bs_store.cards(space) if c["owner"] == user_id or c["vis"] == "project"}
    notes: list[tuple[str, str, dict[str, Any]]] = []
    for lk in bs_store.links(set(cards)):
        ev, idea = cards.get(lk["from_card"]), cards.get(lk["to_card"])
        if not ev or not idea or not ev["paper_id"] or idea["type"] != "idea" or lk["rel"] not in ("supports", "challenges"):
            continue
        parent = zotero_store.item_key(user_id, lib, ev["paper_id"])
        if not parent or zotero_store.noted(user_id, lib, idea["card_id"], ev["paper_id"]):
            continue
        how = "supports" if lk["rel"] == "supports" else "challenges"
        body = (f"<h2>AURORA idea</h2><p><b>{html.escape(idea['text'])}</b></p>"
                f"<p>This paper {how} it: {html.escape(ev['text'])}</p>"
                + (f"<p>Where it stands: {html.escape(idea['stand'])}</p>" if idea["stand"] else ""))
        notes.append((idea["card_id"], ev["paper_id"], {"itemType": "note", "parentItem": parent, "note": body,
                                                         "tags": [{"tag": TAG}]}))
    for start in range(0, len(notes), 50):
        batch = notes[start:start + 50]
        created = await client.create(lib, [n for *_, n in batch])
        for idx, key in created.items():
            card_id, paper_id, _ = batch[int(idx)]
            zotero_store.note_written(user_id, lib, card_id, paper_id, key)
            report.idea_notes += 1


# ---------------------------------------------------------------------------
# Notes on PDFs: the reader's notes <-> Zotero annotations on the paper's PDF
#
# A kept note becomes a highlight annotation on the PDF attachment: the same words (its page and
# rectangles), the author's colour, tagged ``aurora`` (and ``aurora:<agent>`` for an agent's note). The
# comment says who wrote it, the idea it bears on and the replies. Annotations made in Zotero come back
# as the person's notes. Changed on both sides: the later edit wins. Deleted on one side: deleted on the
# other (AURORA waits a few seconds first, for Undo).

AURORA_MARK = " (AURORA)"
_SORT_INDEX = re.compile(r"\d{5}\|\d{6}\|\d{5}")
_note_tasks: dict[str, asyncio.Task] = {}
_note_again: set[str] = set()


def _iso_time(value: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def compose_comment(a: dict[str, Any], replies: list[dict[str, Any]], names: dict[str, str] | None = None) -> str:
    """The Zotero comment of a note: who wrote it, what it says, the idea it bears on, the replies."""
    from aurora.literature.annotations import AGENT_COLORS
    names = names or {}
    head = a.get("comment") or ""
    if a.get("author"):
        head = f"{a['author']}{AURORA_MARK}: {head}" if head else f"{a['author']}{AURORA_MARK}"
    parts = [head] if head else []
    if a.get("idea_text"):
        parts.append(f"Idea: {a['idea_text']}")
    for r in replies:
        if r.get("pending") or not r.get("text"):
            continue
        who = r["who"]
        name = f"{who}{AURORA_MARK}" if who in AGENT_COLORS else names.get(who, "You")
        parts.append(f"— {name}: {r['text']}")
    return "\n\n".join(parts)


def parse_comment(comment: str, author: str = "") -> str:
    """A note's own words, from a Zotero comment AURORA wrote (without who, the idea and the replies)."""
    head = (comment or "").split("\n\n— ", 1)[0]
    head = head.split("\n\nIdea: ", 1)[0]
    if author:
        if head.startswith(f"{author}{AURORA_MARK}:"):
            head = head[len(author) + len(AURORA_MARK) + 1:]
        elif head.strip() == f"{author}{AURORA_MARK}":
            head = ""
    return head.strip()


def annotation_json(a: dict[str, Any], attachment: str, comment: str) -> dict[str, Any]:
    page = int(a.get("page_index") or 0)
    tags = [{"tag": TAG}] + ([{"tag": f"{TAG}:{a['author'].lower()}"}] if a.get("author") else [])
    rects = [[round(float(x), 3) for x in r[:4]] for r in a.get("rects") or []]
    sort_index = a.get("sort_index") or ""
    return {"itemType": "annotation", "parentItem": attachment, "annotationType": "highlight",
            "annotationText": a.get("text") or "", "annotationComment": comment,
            "annotationColor": a.get("color") or "#ffd400", "annotationPageLabel": a.get("page_label") or str(page + 1),
            "annotationSortIndex": sort_index if _SORT_INDEX.fullmatch(sort_index) else f"{page:05d}|000000|00000",
            "annotationPosition": json.dumps({"pageIndex": page, "rects": rects}), "tags": tags}


def take_annotation(user_id: str, lib: str, paper_id: str, space: str, ad: dict[str, Any]) -> None:
    """An annotation as Zotero has it: a new note, or a change to (or the end of) one the reader has."""
    from aurora.literature.annotations import AGENT_COLORS, annotations
    if ad.get("annotationType") not in ("highlight", "underline", "note", "text"):
        return                       # ink and image areas have no words to show
    key, version = ad.get("key", ""), int(ad.get("version") or 0)
    existing = annotations.by_zotero_key(user_id, key)
    if ad.get("deleted"):            # in Zotero's trash
        if existing:
            annotations.purge(existing["ann_id"])
        return
    tags = {t.get("tag", "") for t in ad.get("tags") or []}
    author = next((t.split(":", 1)[1].capitalize() for t in tags if t.startswith(f"{TAG}:")), "")
    author = author if author in AGENT_COLORS else ""
    raw = ad.get("annotationComment") or ""
    comment = parse_comment(raw, author) if TAG in tags else raw
    try:
        pos = json.loads(ad.get("annotationPosition") or "{}")
    except json.JSONDecodeError:
        pos = {}
    fields = {"text": ad.get("annotationText") or "", "comment": comment, "color": ad.get("annotationColor") or "",
              "page_index": int(pos.get("pageIndex") or 0), "page_label": str(ad.get("annotationPageLabel") or ""),
              "rects": pos.get("rects") or [], "sort_index": ad.get("annotationSortIndex") or ""}
    if existing is None:
        annotations.add(user_id, paper_id, author=author, state="kept", space=space, source="zotero", zotero_lib=lib,
                        zotero_key=key, zotero_version=version, **fields)
        return
    if version <= existing["zotero_version"] or existing["deleted_at"]:
        return
    if existing["dirty"] and _iso_time(ad.get("dateModified", "")) < existing["updated_at"]:
        # changed on both sides, here later: AURORA's version goes to Zotero over this one
        annotations.update(existing["ann_id"], touch=False, zotero_version=version)
        return
    annotations.update(existing["ann_id"], touch=False, zotero_version=version, dirty=False, sync_error="", **fields)


def _space_of(user_id: str, paper_id: str) -> str:
    """Where a note made in Zotero shows: the mapped project that has the paper, else the person's own space."""
    for m in zotero_store.maps(user_id):
        if m["campaign_id"] and library.has_item(m["campaign_id"], paper_id):
            return m["campaign_id"]
    return f"user:{user_id}"


async def pull_note_changes(user_id: str, lib: str, client: ZoteroClient) -> int:
    """Annotations changed (or deleted) in Zotero since the last look, on PDFs of papers AURORA knows."""
    from aurora.literature.annotations import annotations
    since = zotero_store.ann_version(user_id, lib)
    items, version = await client.annotations_since(lib, since)
    seen = 0
    for it in items:
        ad = {**(it.get("data") or {}), "version": int(it.get("version") or (it.get("data") or {}).get("version") or 0)}
        paper_id = zotero_store.paper_of_attachment(user_id, lib, ad.get("parentItem", ""))
        if not paper_id:
            continue
        existing = annotations.by_zotero_key(user_id, ad.get("key", ""))
        take_annotation(user_id, lib, paper_id, existing["space"] if existing else _space_of(user_id, paper_id), ad)
        seen += 1
    for key in await client.deleted_since(lib, since):
        if a := annotations.by_zotero_key(user_id, key):
            annotations.purge(a["ann_id"])
    zotero_store.set_ann_version(user_id, lib, version or since)
    return seen


async def push_notes(user_id: str, client: ZoteroClient) -> int:
    """Kept notes Zotero does not have as they are now, and deletions; returns how many went."""
    from aurora.brainstorm.store import bs_store
    from aurora.literature.annotations import annotations
    link = zotero_store.link(user_id)
    if link is None or link["method"] != "web":
        return 0
    sent = 0
    for a in annotations.to_push(user_id):
        if a["deleted_at"] is not None:
            if a["zotero_key"]:
                try:
                    if not await client.delete(a["zotero_lib"], a["zotero_key"], a["zotero_version"]):
                        cur = await client.item(a["zotero_lib"], a["zotero_key"])   # changed there meanwhile
                        if cur is not None:
                            await client.delete(a["zotero_lib"], a["zotero_key"], int(cur.get("version") or 0))
                except ZoteroError as exc:
                    annotations.update(a["ann_id"], touch=False, sync_error=str(exc))
                    continue
                sent += 1
            annotations.purge(a["ann_id"])
            continue
        target = zotero_store.attachment_of(user_id, a["paper_id"])
        if target is None or not a["rects"] or not _may_write(link["access"], target[0]) \
                or not writes.allowed(user_id, "zotero.annotate"):
            continue                 # it stays in AURORA; the reader says why
        lib, att = target
        card = bs_store.card(a["card_id"]) if a["card_id"] else None
        names = {a["owner"]: _name(a["owner"])}
        body = annotation_json(a, att, compose_comment({**a, "idea_text": card["text"] if card else ""},
                                                       annotations.replies(a["ann_id"]), names))
        try:
            if a["zotero_key"] and a["zotero_lib"] == lib:
                changes = {k: v for k, v in body.items() if k not in ("itemType", "parentItem")}
                version = await client.patch(lib, a["zotero_key"], changes, a["zotero_version"])
                if version is None:
                    cur = await client.item(lib, a["zotero_key"])
                    if cur is None:              # deleted in Zotero: so it is here
                        annotations.purge(a["ann_id"])
                        continue
                    cd = {**(cur.get("data") or {}), "version": int(cur.get("version") or 0)}
                    if _iso_time(cd.get("dateModified", "")) >= a["updated_at"]:
                        annotations.update(a["ann_id"], touch=False, dirty=False)
                        take_annotation(user_id, lib, a["paper_id"], a["space"], cd)       # Zotero's is later
                        continue
                    version = await client.patch(lib, a["zotero_key"], changes, cd["version"])
                if version is not None:
                    _pushed(a, zotero_version=version)
                    sent += 1
            else:
                made = (await client.create_items(lib, [body])).get("0") or {}
                if made.get("key"):
                    _pushed(a, zotero_lib=lib, zotero_key=made["key"], zotero_version=made["version"])
                    sent += 1
                else:
                    annotations.update(a["ann_id"], touch=False, sync_error=made.get("error", "Zotero did not take it"))
        except ZoteroError as exc:
            annotations.update(a["ann_id"], touch=False, sync_error=str(exc))
    return sent


def _pushed(a: dict[str, Any], **fields: Any) -> None:
    """Zotero has the note as it was sent; still changed if it was edited while being sent."""
    from aurora.literature.annotations import annotations
    now = annotations.get(a["ann_id"])
    still = now is not None and now["updated_at"] > a["updated_at"]
    annotations.update(a["ann_id"], touch=False, dirty=still, sync_error="", **fields)


async def sync_notes(user_id: str, client: ZoteroClient | None = None) -> None:
    """Notes both ways, for every library holding a PDF of the person's papers."""
    link = zotero_store.link(user_id)
    if link is None:
        return
    client = client or client_for(user_id)
    libs = {r["library"] for r in zotero_store._all(
        "SELECT DISTINCT library FROM zotero_items WHERE user_id=? AND attachment!=''", (user_id,))}
    for lib in sorted(libs):
        await pull_note_changes(user_id, lib, client)
    await push_notes(user_id, client)


def notes_soon(user_id: str, delay: float = 1.5) -> None:
    """Bring the person's notes to Zotero (and Zotero's back) in a moment; changes in a burst go together."""
    from aurora.literature.annotations import annotations
    if zotero_store.link(user_id) is None:
        for a in annotations.to_push(user_id, grace=8.0):
            if a["deleted_at"] is not None:
                annotations.purge(a["ann_id"])
        return
    task = _note_tasks.get(user_id)
    if task is not None and not task.done():
        _note_again.add(user_id)
        return

    async def run() -> None:
        await asyncio.sleep(delay)
        for _ in range(20):
            _note_again.discard(user_id)
            try:
                await sync_notes(user_id)
            except ZoteroError as exc:
                logger.info("Zotero notes of %s: %s", user_id, exc)
            except Exception:
                logger.warning("Zotero notes of %s failed", user_id, exc_info=True)
            if user_id in _note_again:
                await asyncio.sleep(delay)
                continue
            if annotations.waiting_deletions(user_id):      # told after the time for Undo
                await asyncio.sleep(9)
                continue
            break

    _note_tasks[user_id] = asyncio.create_task(run())


def notes_target(user_id: str, paper_id: str) -> dict[str, Any]:
    """Where the person's notes on a paper go in Zotero, or why they stay in AURORA."""
    link = zotero_store.link(user_id)
    if link is None:
        return {"state": "off"}
    if not writes.allowed(user_id, "zotero.annotate"):
        return {"last_sync": link["last_sync"], "error": link["last_error"], "method": link["method"],
                "state": "writes_off"}
    can_write = link["method"] == "web" and any(_may_write(link["access"], m["library"]) for m in zotero_store.maps(user_id))
    item = zotero_store.item_of_paper(user_id, paper_id)
    base = {"last_sync": link["last_sync"], "error": link["last_error"], "method": link["method"]}
    if item is None:
        return {**base, "state": "not_in_zotero", "can_add": can_write and bool(zotero_store.maps(user_id))}
    lib = item["library"]
    where = "My Library" if lib == "user" else "a group library"
    if link["method"] != "web" or not _may_write(link["access"], lib):
        return {**base, "state": "read_only", "library": where}
    if not item["attachment"]:
        return {**base, "state": "no_pdf", "library": where, "can_add": True}
    return {**base, "state": "ok", "library": where}


async def add_paper(user_id: str, who: str, paper_id: str, campaign_id: str, pdf: Path | None) -> dict[str, Any]:
    """Put a paper (and its PDF) into the person's Zotero, in the collection of the project, so notes can follow."""
    link = zotero_store.link(user_id)
    if link is None or link["method"] != "web":
        raise ZoteroError("Connect your Zotero account (with a key that can write) first")
    if not writes.allowed(user_id, "zotero.save"):
        raise ZoteroError("Writing to Zotero is off. Turn on “Save papers and their PDFs to Zotero” under the account "
                          "button → What AURORA may write")
    maps = [m for m in zotero_store.maps(user_id) if m["campaign_id"] == campaign_id and _may_write(link["access"], m["library"])]
    maps = maps or [m for m in zotero_store.maps(user_id) if m["campaign_id"] == "" and _may_write(link["access"], m["library"])]
    if not maps:
        raise ZoteroError("Choose a Zotero collection for this project (or your Library) first, in Settings › Zotero")
    m = maps[0]
    client = client_for(user_id)
    item = zotero_store.item_of_paper(user_id, paper_id)
    if item is None:
        owner = "" if m["campaign_id"] else user_id
        library.add_item(m["campaign_id"], paper_id, source="person", added_by=who, owner=owner)
        await push(user_id, m, client, SyncReport(), only={paper_id})
        item = zotero_store.item_of_paper(user_id, paper_id)
        if item is None:
            raise ZoteroError("Zotero did not take the paper")
    if not item["attachment"]:
        if pdf is None:
            raise ZoteroError("There is no PDF of this paper to put into Zotero")
        entry = library.paper(paper_id)
        att = await client.upload_pdf(item["library"], item["item_key"], f"{entry['key'] if entry else paper_id}.pdf",
                                      pdf.read_bytes())
        zotero_store.put_item(user_id, item["library"], item["item_key"], attachment=att)
    write_log.record(user_id, "zotero.save", {"paper_id": paper_id, "library": item["library"],
                                              "item_key": item["item_key"]},
                     {"kind": "zotero_item", "library": item["library"], "item_key": item["item_key"]})
    notes_soon(user_id, delay=0.2)
    return notes_target(user_id, paper_id)


def _may_write(access: dict[str, Any], library: str) -> bool:
    if library == "user":
        return bool((access.get("user") or {}).get("write"))
    groups = access.get("groups") or {}
    gid = library.split(":", 1)[1]
    return bool((groups.get(gid) or groups.get("all") or {}).get("write"))


def start_sync(user_id: str, who: str, campaign_id: str | None = None, *, min_gap: float = 0.0) -> bool:
    """Sync in the background (once at a time per person); False if one is running or ran very recently."""
    task = _running.get(user_id)
    if task is not None and not task.done():
        return False
    link = zotero_store.link(user_id)
    if link is None or (min_gap and link["last_sync"] and time.time() - link["last_sync"] < min_gap):
        return False

    async def run() -> None:
        try:
            await sync(user_id, who, campaign_id=campaign_id)
        except ZoteroError as exc:
            logger.info("Zotero sync for %s: %s", user_id, exc)
        except Exception:
            logger.warning("Zotero sync for %s failed", user_id, exc_info=True)
            zotero_store.note_sync(user_id, "The sync failed unexpectedly; see the server log")

    _running[user_id] = asyncio.create_task(run())
    return True


def syncing(user_id: str) -> bool:
    task = _running.get(user_id)
    return task is not None and not task.done()


async def sync_loop() -> None:
    """Every few minutes, everyone's mapped collections (started with the web console)."""
    while True:
        await asyncio.sleep(SYNC_MINUTES * 60)
        for link in zotero_store.links():
            if zotero_store.maps(link["user_id"]):
                start_sync(link["user_id"], _name(link["user_id"]), min_gap=SYNC_MINUTES * 60 - 30)


def _name(user_id: str) -> str:
    if user_id == "local":
        return "You"
    from aurora.auth.store import auth_store
    row = auth_store.get_user(user_id)
    return row["name"] if row else "Someone"


def suggest(collections: list[dict[str, Any]], projects: list[dict[str, str]]) -> dict[str, str]:
    """Collections whose name matches a project's: {collection_key: campaign_id}."""
    out: dict[str, str] = {}
    for c in collections:
        name = fold(c["name"].split("/")[-1])
        for p in projects:
            pname = fold(p["name"])
            if name and pname and (name == pname or (len(name) >= 5 and (name in pname or pname in name))):
                out[c["collection_key"]] = p["campaign_id"]
                break
    return out

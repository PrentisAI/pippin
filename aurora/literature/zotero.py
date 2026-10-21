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

import httpx

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
                   headers: dict[str, str] | None = None, raw: bool = False) -> httpx.Response:
        hdrs = {"Zotero-API-Version": "3", **(headers or {})}
        if self.key:
            hdrs["Zotero-API-Key"] = self.key
        url = path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"
        for attempt in range(4):
            try:
                if self._client is not None:
                    r = await self._client.request(verb, url, params=params, json=body, headers=hdrs)
                else:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True) as c:
                        r = await c.request(verb, url, params=params, json=body, headers=hdrs)
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
        r = await self._req("POST", f"{library_path(library, self.user_id)}/items", body=items,
                            headers={"Zotero-Write-Token": hex(int(time.time() * 1e6))[2:].rjust(32, "0")[-32:]})
        res = r.json()
        return {k: (v.get("key") if isinstance(v, dict) else v) for k, v in (res.get("successful") or res.get("success") or {}).items()}

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
        for table in ("zotero_links", "zotero_maps", "zotero_items", "zotero_notes"):
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
        row = {"paper_id": "", "ref_id": "", "version": 0, "citekey": "", "pdf": "", **prev, **fields}
        self._x("INSERT OR REPLACE INTO zotero_items (user_id, library, item_key, paper_id, ref_id, version, citekey, "
                "pdf, synced_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, library, item_key, row["paper_id"], row["ref_id"], row["version"], row["citekey"],
                 row["pdf"], time.time()))

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


async def _annotations(client: ZoteroClient, lib: str, attachment_key: str) -> list[dict[str, Any]]:
    out = []
    for ann in await client.children(lib, attachment_key):
        ad = ann.get("data") or {}
        if ad.get("itemType") != "annotation":
            continue
        text = (ad.get("annotationText") or "").strip()
        comment = (ad.get("annotationComment") or "").strip()
        if text or comment:
            out.append({"note_id": ad["key"], "kind": "highlight" if text else "note",
                        "text": (text or comment)[:4000], "comment": comment if text else "",
                        "page": str(ad.get("annotationPageLabel") or "")})
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
    try:
        if standalone:          # the PDF is the item: its highlights are its own children
            if options["pdfs"]:
                await _copy_pdf(client, user_id, lib, cid, data["key"], item, paper_id, report, title)
            if options["notes"]:
                notes += await _annotations(client, lib, data["key"])
        else:
            for ch in await client.children(lib, data["key"]):
                cd = ch.get("data") or {}
                if cd.get("itemType") == "note" and options["notes"]:
                    text = _note_text(cd.get("note", ""))
                    if _own_note(text):
                        notes.append({"note_id": cd["key"], "kind": "note", "text": text[:4000]})
                if cd.get("itemType") == "attachment" and cd.get("contentType") == "application/pdf":
                    if options["pdfs"]:
                        await _copy_pdf(client, user_id, lib, cid, data["key"], ch, paper_id, report, title)
                    if options["notes"]:   # highlights live on the PDF, also when the file is not in Zotero
                        notes += await _annotations(client, lib, cd["key"])
    except ZoteroError as exc:
        report.problems.append(f"“{title[:50]}”: {exc}")
        return
    if options["notes"]:
        library.set_notes(cid, user_id, paper_id, notes)
        report.notes += len(notes)


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
            if options["push"] and can_write:
                try:
                    await push(user_id, m, client, report)
                except ZoteroError as exc:
                    report.problems.append(f"Sending papers to “{m['name']}”: {exc}")
            if options.get("idea_notes") and can_write:
                try:
                    await idea_notes(user_id, m, client, report)
                except ZoteroError as exc:
                    report.problems.append(f"Writing idea notes in “{m['name']}”: {exc}")
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

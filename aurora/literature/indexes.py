"""The scholarly indexes papers are found and checked in: OpenAlex, Crossref, arXiv, Semantic Scholar.

All four are free.  OpenAlex and Crossref answer faster with a contact address
(``AURORA_CONTACT_EMAIL``, their "polite pool"); OpenAlex also wants its own free key
(``OPENALEX_API_KEY``), without which it allows only a small daily budget per IP address.  Semantic Scholar limits anonymous use
hard; with ``SEMANTIC_SCHOLAR_API_KEY`` it is asked like the others, without it only
when it is not busy (a 429 pauses it for a few minutes).

An index that cannot be reached is reported as such, so that "not found" always means
"looked for and not there", never "could not look".
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import httpx

from aurora.literature.records import (
    Paper,
    arxiv_version,
    clean_arxiv,
    clean_doi,
    clean_text,
    clean_title,
    fold,
    merge_all,
)

logger = logging.getLogger(__name__)

INDEXES = ("openalex", "crossref", "arxiv", "semantic_scholar")
INDEX_NAMES = {"openalex": "OpenAlex", "crossref": "Crossref", "arxiv": "arXiv", "semantic_scholar": "Semantic Scholar"}
_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
_CACHE_SECONDS = 24 * 3600
_CACHE_MAX = 4000
_ARXIV_GAP = 3.0                 # arXiv asks for at most one request every 3 seconds
_S2_PAUSE = 300.0                # after a 429 from Semantic Scholar without a key


def openalex_params(params: dict[str, Any], contact: str = "") -> dict[str, Any]:
    """OpenAlex's own key (``OPENALEX_API_KEY``, free) and a contact address, when set.  Without a
    key, requests count against a small daily budget shared by everyone on the same IP address."""
    out = dict(params)
    if os.environ.get("OPENALEX_API_KEY"):
        out["api_key"] = os.environ["OPENALEX_API_KEY"]
    contact = contact or os.environ.get("AURORA_CONTACT_EMAIL", "")
    if contact:
        out["mailto"] = contact
    return out


class Unreachable(Exception):
    """The index did not answer (network, timeout, 5xx, rate limit)."""


@dataclass
class Found:
    """What a question to the indexes gave: papers, and which indexes answered."""

    papers: list[Paper] = field(default_factory=list)
    reached: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # every index's own records, before merging: a check compares against each, since one
    # index's record can be wrong (a year, the authors) where another's is right
    raw: list[Paper] = field(default_factory=list)

    def add(self, other: Found) -> None:
        self.raw = self.raw + other.raw
        self.papers = merge_all(self.papers + other.papers)
        self.reached = sorted(set(self.reached) | set(other.reached))
        self.failed = sorted((set(self.failed) | set(other.failed)) - set(self.reached))


_cache: dict[tuple, tuple[float, Any]] = {}


def clear_cache() -> None:
    _cache.clear()


def _cached(key: tuple) -> Any:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    return None


def _remember(key: tuple, value: Any) -> None:
    if len(_cache) >= _CACHE_MAX:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[: _CACHE_MAX // 4]:
            _cache.pop(k, None)
    _cache[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# Each index: its answer as Paper records


def _openalex_paper(w: dict[str, Any]) -> Paper:
    ids = w.get("ids") or {}
    loc = w.get("primary_location") or {}
    best = w.get("best_oa_location") or {}
    arxiv = ""
    for place in [loc, best, *(w.get("locations") or [])]:
        url = (place or {}).get("landing_page_url") or ""
        if "arxiv.org" in url:
            arxiv = clean_arxiv(url)
            break
    abstract = ""
    inv = w.get("abstract_inverted_index")
    if isinstance(inv, dict) and inv:
        words = sorted((pos, word) for word, positions in inv.items() for pos in positions)
        abstract = " ".join(word for _, word in words)
    return Paper(
        title=clean_title(w.get("title") or w.get("display_name") or ""),
        authors=[(a.get("author") or {}).get("display_name", "") for a in w.get("authorships") or []],
        year=w.get("publication_year"),
        venue=((loc.get("source") or {}).get("display_name") or ""),
        doi=clean_doi(ids.get("doi") or w.get("doi")),
        arxiv_id=arxiv,
        openalex_id=(w.get("id") or "").rsplit("/", 1)[-1],
        url=w.get("doi") or loc.get("landing_page_url") or w.get("id") or "",
        pdf_url=best.get("pdf_url") or "",
        abstract=abstract,
        cited_by=w.get("cited_by_count"),
        found_in=["openalex"],
    )


def _crossref_paper(m: dict[str, Any]) -> Paper:
    authors = []
    for a in m.get("author") or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
        authors.append(name)
    year = None
    for k in ("published-print", "published-online", "issued", "created"):
        parts = ((m.get(k) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            year = int(parts[0])
            break
    title = clean_title((m.get("title") or [""])[0])
    sub = clean_title((m.get("subtitle") or [""])[0])
    doi = clean_doi(m.get("DOI"))
    return Paper(
        title=f"{title}: {sub}" if sub and fold(sub) not in fold(title) else title,
        authors=authors, year=year,
        venue=(m.get("container-title") or [""])[0],
        doi=doi, url=f"https://doi.org/{doi}" if doi else m.get("URL", ""),
        abstract=clean_text(m.get("abstract") or ""),
        cited_by=m.get("is-referenced-by-count"), found_in=["crossref"],
    )


_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _arxiv_papers(xml: str) -> list[Paper]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise Unreachable(f"arXiv answered with something that is not a feed: {exc}") from exc
    out = []
    for e in root.findall("a:entry", _ATOM):
        raw_id = e.findtext("a:id", "", _ATOM)
        aid = clean_arxiv(raw_id)
        title = clean_title(e.findtext("a:title", "", _ATOM))
        if not aid or not title or title.lower() == "error":
            continue
        doi = clean_doi(e.findtext("arxiv:doi", "", _ATOM))
        pdf = next((ln.get("href", "") for ln in e.findall("a:link", _ATOM) if ln.get("title") == "pdf"), "")
        published = e.findtext("a:published", "", _ATOM)
        out.append(Paper(
            title=title,
            authors=[a.findtext("a:name", "", _ATOM) for a in e.findall("a:author", _ATOM)],
            year=int(published[:4]) if published[:4].isdigit() else None,
            venue=e.findtext("arxiv:journal_ref", "", _ATOM) or "arXiv",
            doi=doi, arxiv_id=aid, url=f"https://arxiv.org/abs/{aid}", pdf_url=pdf,
            abstract=" ".join(e.findtext("a:summary", "", _ATOM).split()), found_in=["arxiv"],
            arxiv_version=arxiv_version(raw_id), arxiv_comment=" ".join(e.findtext("arxiv:comment", "", _ATOM).split()),
        ))
    return out


def _s2_paper(p: dict[str, Any]) -> Paper:
    ext = p.get("externalIds") or {}
    return Paper(
        title=clean_title(p.get("title") or ""), authors=[a.get("name", "") for a in p.get("authors") or []],
        year=p.get("year"), venue=p.get("venue") or "",
        doi=clean_doi(ext.get("DOI")), arxiv_id=clean_arxiv(ext.get("ArXiv")), s2_id=p.get("paperId") or "",
        url=p.get("url") or "", pdf_url=((p.get("openAccessPdf") or {}).get("url") or ""),
        abstract=p.get("abstract") or "", cited_by=p.get("citationCount"), found_in=["semantic_scholar"],
    )


_STOPWORDS = set("a an the of in on for to and or with by from at as is are be is all you we our via using its into "
                 "than that this these those can do does not".split())
_S2_FIELDS = "title,authors,year,venue,externalIds,citationCount,abstract,openAccessPdf,url"
_OA_SELECT = ("id,doi,title,display_name,publication_year,cited_by_count,authorships,primary_location,"
              "best_oa_location,locations,ids,abstract_inverted_index")


class Indexes:
    """Asks the indexes; one instance can be shared (requests to arXiv are spaced out)."""

    def __init__(self, *, client: httpx.AsyncClient | None = None, contact: str | None = None,
                 s2_key: str | None = None, use: tuple[str, ...] = INDEXES) -> None:
        self._client = client
        self.contact = contact if contact is not None else os.environ.get("AURORA_CONTACT_EMAIL", "")
        self.s2_key = s2_key if s2_key is not None else os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
        self.use = tuple(i for i in use if i in INDEXES)
        self._arxiv_lock = asyncio.Lock()
        self._arxiv_last = 0.0
        self._s2_paused_until = 0.0

    # -- plumbing --------------------------------------------------------------

    async def _get(self, index: str, url: str, params: dict[str, Any] | None = None,
                   headers: dict[str, str] | None = None) -> httpx.Response:
        ua = "AURORA research assistant" + (f" (mailto:{self.contact})" if self.contact else "")
        client = self._client
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as c:
                    r = await c.get(url, params=params, headers={"User-Agent": ua, **(headers or {})})
            else:
                r = await client.get(url, params=params, headers={"User-Agent": ua, **(headers or {})})
        except httpx.HTTPError as exc:
            raise Unreachable(f"{INDEX_NAMES[index]}: {type(exc).__name__} {exc}") from exc
        if r.status_code == 404:
            return r
        if r.status_code == 429 and index == "semantic_scholar" and not self.s2_key:
            self._s2_paused_until = time.time() + _S2_PAUSE
        if r.status_code >= 400:
            raise Unreachable(f"{INDEX_NAMES[index]} answered {r.status_code}")
        return r

    def _active(self, index: str) -> bool:
        if index not in self.use:
            return False
        return not (index == "semantic_scholar" and time.time() < self._s2_paused_until)

    async def _arxiv(self, params: dict[str, Any]) -> list[Paper]:
        async with self._arxiv_lock:
            wait = self._arxiv_last + _ARXIV_GAP - time.monotonic()
            if wait > 0 and self._client is None:
                await asyncio.sleep(wait)
            try:
                r = await self._get("arxiv", "https://export.arxiv.org/api/query", params)
            finally:
                self._arxiv_last = time.monotonic()
        return _arxiv_papers(r.text)

    async def _ask(self, jobs: dict[str, Any]) -> Found:
        """Run one coroutine per index; collect papers and who answered."""
        found = Found()
        names = list(jobs)
        results = await asyncio.gather(*jobs.values(), return_exceptions=True)
        for name, res in zip(names, results, strict=True):
            if isinstance(res, Unreachable):
                logger.info("literature: %s", res)
                found.failed.append(name)
            elif isinstance(res, BaseException):
                logger.warning("literature: %s failed", name, exc_info=res)
                found.failed.append(name)
            else:
                found.reached.append(name)
                found.raw += [Paper.from_dict(p.to_dict()) for p in res]
                found.papers = merge_all(found.papers + list(res))
        found.reached.sort()
        found.failed.sort()
        return found

    def _polite(self, params: dict[str, Any]) -> dict[str, Any]:
        """Crossref's polite pool: a contact address."""
        return {**params, "mailto": self.contact} if self.contact else params

    def _oa(self, params: dict[str, Any]) -> dict[str, Any]:
        """OpenAlex: its key and a contact address."""
        return openalex_params(params, self.contact)

    # -- by identifier -----------------------------------------------------------

    async def _openalex_doi(self, doi: str) -> list[Paper]:
        r = await self._get("openalex", f"https://api.openalex.org/works/doi:{doi}", self._oa({"select": _OA_SELECT}))
        return [] if r.status_code == 404 else [_openalex_paper(r.json())]

    async def _crossref_doi(self, doi: str) -> list[Paper]:
        r = await self._get("crossref", f"https://api.crossref.org/works/{doi}", self._polite({}))
        return [] if r.status_code == 404 else [_crossref_paper(r.json().get("message") or {})]

    async def _s2_id(self, ident: str) -> list[Paper]:
        headers = {"x-api-key": self.s2_key} if self.s2_key else {}
        r = await self._get("semantic_scholar", f"https://api.semanticscholar.org/graph/v1/paper/{ident}",
                            {"fields": _S2_FIELDS}, headers)
        return [] if r.status_code == 404 else [_s2_paper(r.json())]

    async def by_doi(self, doi: str) -> Found:
        doi = clean_doi(doi)
        key = ("doi", doi)
        if (hit := _cached(key)) is not None:
            return hit
        jobs: dict[str, Any] = {}
        if self._active("crossref"):
            jobs["crossref"] = self._crossref_doi(doi)
        if self._active("openalex"):
            jobs["openalex"] = self._openalex_doi(doi)
        if not jobs and self._active("semantic_scholar"):
            jobs["semantic_scholar"] = self._s2_id(f"DOI:{doi}")
        found = await self._ask(jobs)
        if not found.failed:
            _remember(key, found)
        return found

    async def by_arxiv(self, arxiv_id: str) -> Found:
        arxiv_id = clean_arxiv(arxiv_id)
        key = ("arxiv", arxiv_id)
        if (hit := _cached(key)) is not None:
            return hit
        jobs: dict[str, Any] = {}
        if self._active("arxiv"):
            jobs["arxiv"] = self._arxiv({"id_list": arxiv_id, "max_results": 1})
        if self._active("semantic_scholar"):
            jobs["semantic_scholar"] = self._s2_id(f"ARXIV:{arxiv_id}")
        found = await self._ask(jobs)
        if not found.failed:
            _remember(key, found)
        return found

    # -- by words ----------------------------------------------------------------

    async def _openalex_search(self, query: str, limit: int, title_only: bool = False) -> list[Paper]:
        params = {"per-page": limit, "select": _OA_SELECT}
        # "search" also matches full texts, which brings in papers that only mention a word
        where = "title.search" if title_only else "title_and_abstract.search"
        params["filter"] = f"{where}:{fold(query)[:300]}"
        r = await self._get("openalex", "https://api.openalex.org/works", self._oa(params))
        papers = [_openalex_paper(w) for w in r.json().get("results") or []]
        if not title_only and len(papers) < max(2, limit // 2):
            # every word must appear in the title or abstract: few hits; widen, and let rank() sort it out
            wide = {"per-page": limit, "select": _OA_SELECT, "search": query}
            r = await self._get("openalex", "https://api.openalex.org/works", self._oa(wide))
            papers += [_openalex_paper(w) for w in r.json().get("results") or []]
        return papers

    async def _crossref_lookup(self, title: str, bib: str, limit: int = 5) -> list[Paper]:
        """Crossref for a cited title.  Crossref keeps chemical formulas with their subscripts
        marked up ("Al<sub>2</sub>O<sub>3</sub>"), and its ranking misses "Al2O3": a title with
        formulas is also asked with the numbers apart ("Al 2 O 3")."""
        split = re.sub(r"(?<=[A-Za-z])(\d+)", r" \1 ", title)
        if split == title:
            return await self._crossref_search(bib, limit)
        first, second = await asyncio.gather(self._crossref_search(bib, limit),
                                             self._crossref_search(" ".join(split.split()), limit))
        return first + second

    async def _crossref_search(self, query: str, limit: int) -> list[Paper]:
        r = await self._get("crossref", "https://api.crossref.org/works",
                            self._polite({"query.bibliographic": query, "rows": limit,
                                          "select": "DOI,title,subtitle,author,issued,published-print,"
                                                    "published-online,created,container-title,"
                                                    "is-referenced-by-count,abstract,URL"}))
        return [_crossref_paper(m) for m in (r.json().get("message") or {}).get("items") or []]

    async def _arxiv_search(self, query: str, limit: int, title_only: bool = False) -> list[Paper]:
        words = fold(query).split()[:20]
        if not words:
            return []
        field_ = "ti" if title_only else "all"
        papers = await self._arxiv({"search_query": f'{field_}:"{" ".join(words)}"', "max_results": limit,
                                    "sortBy": "relevance"})
        if not papers:      # a phrase is strict; fall back to the words that carry meaning
            content = [w for w in words if w not in _STOPWORDS and len(w) > 1][:8]
            if content:
                papers = await self._arxiv({"search_query": " AND ".join(f"{field_}:{w}" for w in content),
                                            "max_results": limit, "sortBy": "relevance"})
        return papers

    async def _s2_search(self, query: str, limit: int) -> list[Paper]:
        headers = {"x-api-key": self.s2_key} if self.s2_key else {}
        r = await self._get("semantic_scholar", "https://api.semanticscholar.org/graph/v1/paper/search",
                            {"query": query[:300], "limit": limit, "fields": _S2_FIELDS}, headers)
        return [_s2_paper(p) for p in r.json().get("data") or []]

    async def search(self, query: str, limit: int = 10) -> Found:
        """Papers about a topic, from every index, one record per work."""
        query = " ".join(query.split())
        key = ("search", fold(query), limit, self.use)
        if (hit := _cached(key)) is not None:
            return hit
        jobs: dict[str, Any] = {}
        if self._active("openalex"):
            jobs["openalex"] = self._openalex_search(query, limit)
        if self._active("arxiv"):
            jobs["arxiv"] = self._arxiv_search(query, limit)
        if self._active("semantic_scholar"):
            jobs["semantic_scholar"] = self._s2_search(query, limit)
        if self._active("crossref"):
            jobs["crossref"] = self._crossref_lookup(query, query, max(3, limit // 2))
        found = await self._ask(jobs)
        found.papers = rank(query, found.papers)
        if not found.failed:
            _remember(key, found)
        return found

    async def look_up(self, title: str, authors: str = "", year: int | None = None) -> Found:
        """Candidates for a cited title (and authors and year), from every index."""
        bib = " ".join(x for x in (title, authors, str(year or "")) if x)
        key = ("lookup", fold(bib), self.use)
        if (hit := _cached(key)) is not None:
            return hit
        jobs: dict[str, Any] = {}
        if self._active("crossref"):
            jobs["crossref"] = self._crossref_lookup(title, bib)
        if self._active("openalex"):
            jobs["openalex"] = self._openalex_search(title, 5, title_only=True)
        if self._active("arxiv"):
            jobs["arxiv"] = self._arxiv_search(title, 5, title_only=True)
        if self._active("semantic_scholar"):
            jobs["semantic_scholar"] = self._s2_search(title, 5)
        found = await self._ask(jobs)
        if not found.failed:
            _remember(key, found)
        return found


def rank(query: str, papers: list[Paper]) -> list[Paper]:
    """Most relevant first: the query's words in the title count most, in the abstract less.

    Each index ranks by its own notion of relevance, and full-text matching lets papers in that
    only mention a word somewhere; those that share too little with the query are dropped.
    """
    words = {w for w in fold(query).split() if w not in _STOPWORDS and len(w) > 2}
    if not words:
        return papers

    def stem(w: str) -> str:
        return w[:-1] if w.endswith("s") and len(w) > 4 else w
    words = {stem(w) for w in words}
    scored = []
    for order, p in enumerate(papers):
        title = {stem(w) for w in fold(p.title).split()}
        abstract = {stem(w) for w in fold(p.abstract).split()}
        in_title = len(words & title)
        covered = len(words & (title | abstract))
        if covered / len(words) < 0.5:
            continue
        scored.append((-(2 * in_title + covered) / len(words), order, p))
    if not scored:
        return []
    best = -min(x[0] for x in scored)
    return [p for score, _, p in sorted(scored, key=lambda x: (x[0], x[1])) if -score >= 0.4 * best]


_shared: Indexes | None = None


def indexes() -> Indexes:
    """The process's shared instance (environment read on first use)."""
    global _shared
    if _shared is None:
        _shared = Indexes()
    return _shared


def set_indexes(value: Indexes | None) -> None:
    """Swap the shared instance (tests)."""
    global _shared
    _shared = value

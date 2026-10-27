"""What a saved thing says: its platform, its text (kept as a snapshot), and the paper it is, if it is one.

An entry point that has the page open (the browser extension, the desktop app) sends its text along, which is
the only way into pages behind a login (Zhihu, Xiaohongshu, X); otherwise the page is fetched here. A paper (an
arXiv ID, a DOI, a paper's page) is looked up in the scholarly indexes and kept in the Library, and its open
PDF is read.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

MAX_TEXT = 120_000

PLATFORMS = {
    "arxiv.org": "arxiv", "zhihu.com": "zhihu", "x.com": "x", "twitter.com": "x", "reddit.com": "reddit",
    "xiaohongshu.com": "xiaohongshu", "xhslink.com": "xiaohongshu", "github.com": "github",
    "openreview.net": "paper", "aclanthology.org": "paper", "doi.org": "paper", "semanticscholar.org": "paper",
    "medium.com": "blog", "substack.com": "blog", "notion.site": "blog",
}


def platform_of(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for domain, name in PLATFORMS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return "web" if url else "text"


class _Text(HTMLParser):
    """The readable text of a page: no scripts, styles, menus or footers; a line per block."""

    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "svg", "form", "button"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "blockquote", "pre", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            a = {k: v or "" for k, v in attrs}
            key = a.get("property") or a.get("name") or ""
            if key in ("og:title", "author", "article:author", "article:published_time", "citation_title",
                       "citation_doi", "citation_arxiv_id", "citation_author", "og:description", "description"):
                self.meta.setdefault(key, a.get("content", ""))
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def html_text(html: str) -> tuple[str, str, dict[str, str]]:
    """(title, text, meta) of a page."""
    p = _Text()
    try:
        p.feed(html)
    except Exception:
        logger.debug("parsing a page failed", exc_info=True)
    lines = [" ".join(line.split()) for line in "".join(p.out).split("\n")]
    text = "\n".join(line for line in lines if line)
    title = p.meta.get("og:title") or p.meta.get("citation_title") or " ".join(p.title.split())
    return title, text[:MAX_TEXT], p.meta


def digest(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:24]


@dataclass
class Got:
    platform: str
    title: str = ""
    text: str = ""
    html: str = ""
    author: str = ""
    published: str = ""
    paper_id: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


async def get(owner: str, *, url: str = "", text: str = "", html: str = "", title: str = "") -> Got:
    """The thing saved, as text. Raises ValueError when there is nothing to read."""
    from aurora.literature.records import clean_arxiv, clean_doi
    platform = platform_of(url)
    meta: dict[str, Any] = {}
    if html and not text:
        t, text, meta = html_text(html)
        title = title or t
    arxiv = clean_arxiv(url) if platform == "arxiv" else ""
    doi = clean_doi(url) or clean_doi(str(meta.get("citation_doi", "")))
    arxiv = arxiv or clean_arxiv(str(meta.get("citation_arxiv_id", "")))
    if arxiv or doi:
        got = await _paper(owner, arxiv=arxiv, doi=doi)
        if got is not None:
            return got
    if url and not text:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                         headers={"User-Agent": "Mozilla/5.0 (AURORA research assistant)"}) as c:
                r = await c.get(url)
            r.raise_for_status()
        except Exception as exc:
            raise ValueError(f"could not fetch the page ({str(exc)[:120]}); save it from the browser extension, "
                             "which reads it as you see it") from None
        if "pdf" in r.headers.get("content-type", ""):
            from aurora.literature.pdfs import _text
            text, _ = _text(r.content, pages=60)
        else:
            html = r.text
            t, text, meta = html_text(html)
            title = title or t
    text = (text or "").strip()
    if len(text) < 20:
        raise ValueError("there is nothing to read in it")
    return Got(platform=platform, title=title.strip()[:300], text=text[:MAX_TEXT], html=html[:2_000_000],
               author=str(meta.get("author") or meta.get("article:author") or meta.get("citation_author") or ""),
               published=str(meta.get("article:published_time") or ""), meta=meta)


async def _paper(owner: str, *, arxiv: str = "", doi: str = "") -> Got | None:
    """A paper: verified in the indexes, kept in the person's Library, read from its open PDF (or abstract)."""
    from aurora.literature.check import Reference, check
    from aurora.literature.library import library
    from aurora.literature.reading import fetch_open_pdf, page_texts
    try:
        result = await check(Reference(arxiv_id=arxiv, doi=doi))
    except Exception:
        logger.info("looking up %s failed", arxiv or doi, exc_info=True)
        return None
    if not result.verified or result.paper is None:
        return None
    stored = library.upsert_paper(result.paper)
    library.add_item("", stored["paper_id"], source="person", added_by="memory", owner=owner)
    p = stored["paper"]
    text = ""
    try:
        path = await fetch_open_pdf(stored["paper_id"])
        if path is not None:
            text = "\n\n".join(page_texts(path))
    except Exception:
        logger.info("reading the PDF of %s failed", stored["paper_id"], exc_info=True)
    if len(text) < 200:
        text = f"{p.title}\n\n{p.abstract or ''}"
    return Got(platform="paper", title=p.title, text=text[:MAX_TEXT], author=p.byline(),
               published=str(p.year or ""), paper_id=stored["paper_id"])


def find_quote(text: str, quote: str) -> bool:
    """Is ``quote`` in ``text`` (ignoring spacing and case)?"""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip().lower().strip("\"'“”‘’")
    q = norm(quote)
    return len(q) >= 6 and q in norm(text)

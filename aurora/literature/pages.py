"""Web pages in the Library: a blog post, a thread, a README, any page worth keeping, beside the papers.

A page kept in the Library is a record like a paper's (:class:`~aurora.literature.records.Paper` with another
``kind``), identified by its address, with a **snapshot** taken when it was saved: its readable HTML (the article,
without scripts, menus or ads) and its text. The snapshot is what you read in Pippin's reader, what Pippin and the
agents read, and what notes are anchored on, so a page that changes or disappears afterwards is still there.

Pages come in from wherever something is saved: the browser extension, the notebook's box, the feed's Save. The
pages saved before pages were Library entries are brought in once (:func:`adopt_saved`).

Files: ``AURORA_HOME/library/pages/<id>.html`` and ``.txt``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

logger = logging.getLogger(__name__)

KINDS = ("page", "blog", "post", "code", "video", "discussion")
# what a saved thing's platform makes it in the Library
KIND_OF_PLATFORM = {"blog": "blog", "x": "post", "reddit": "discussion", "zhihu": "post", "xiaohongshu": "post",
                    "github": "code", "web": "page", "arxiv": "page", "paper": "page"}
MAX_HTML = 1_500_000


def normal_url(url: str) -> str:
    """The address without its fragment, tracking parameters or a trailing slash: the same page saved twice."""
    u = urlparse(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(u.query) if not k.lower().startswith(("utm_", "ref", "fbclid", "gclid"))])
    path = u.path.rstrip("/") or "/"
    return urlunparse((u.scheme.lower(), u.netloc.lower().removeprefix("www."), path, "", query, ""))


def page_id(url: str) -> str:
    return "w_" + hashlib.sha1(normal_url(url).encode()).hexdigest()[:12]


def _dir() -> Path:
    from aurora.paths import aurora_home
    d = aurora_home() / "library" / "pages"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Readable HTML: the article, kept to the tags that carry it


_KEEP = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "code", "em", "strong", "b",
         "i", "a", "img", "figure", "figcaption", "table", "thead", "tbody", "tr", "th", "td", "br", "hr", "sup", "sub",
         "div", "section", "dl", "dt", "dd", "math"}
_DROP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "svg", "form", "button", "iframe",
         "template", "select", "input", "textarea", "dialog", "canvas", "video", "audio", "object", "embed"}
_VOID = {"img", "br", "hr"}
_BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "figcaption", "tr", "div", "section",
          "dt", "dd", "br", "hr"}


class _Readable(HTMLParser):
    """Two copies at once: everything readable, and only what is inside <article> or <main> (preferred when it has
    most of the text)."""

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.all: list[str] = []
        self.main: list[str] = []
        self.drop = 0
        self.in_main = 0
        self.title = ""
        self._title = False
        self.meta: dict[str, str] = {}

    def _out(self, s: str) -> None:
        self.all.append(s)
        if self.in_main:
            self.main.append(s)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            if k and a.get("content"):
                self.meta.setdefault(k, a["content"])
            return
        if tag == "title":
            self._title = True
            return
        if tag in _DROP and tag not in _VOID:
            self.drop += 1
            return
        if self.drop:
            return
        if tag in ("article", "main"):
            self.in_main += 1
            return
        if tag not in _KEEP:
            return
        if tag == "a":
            href = a.get("href", "")
            if href and not href.lower().startswith(("javascript:", "data:")):
                self._out(f'<a href="{escape(urljoin(self.base, href), quote=True)}" target="_blank" rel="noopener noreferrer">')
            else:
                self._out("<a>")
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or a.get("data-original") or ""
            if src and not src.lower().startswith(("javascript:", "data:")):
                self._out(f'<img src="{escape(urljoin(self.base, src), quote=True)}" alt="{escape(a.get("alt", ""), quote=True)}" loading="lazy">')
        else:
            self._out(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._title = False
            return
        if tag in _DROP and tag not in _VOID:
            self.drop = max(0, self.drop - 1)
            return
        if self.drop:
            return
        if tag in ("article", "main"):
            self.in_main = max(0, self.in_main - 1)
            return
        if tag in _KEEP and tag not in _VOID:
            self._out(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._title:
            self.title += data
            return
        if not self.drop:
            self._out(escape(data, quote=False))


def _text_of(html: str) -> str:
    """The text of readable HTML, a line per block (what notes are anchored on, and what the agents read)."""
    out = re.sub(r"<(/?)(%s)\b[^>]*>" % "|".join(_BLOCK), "\n", html)
    out = re.sub(r"<[^>]+>", "", out)
    lines = [" ".join(line.split()) for line in unescape(out).split("\n")]
    return "\n".join(line for line in lines if line)


def readable(html: str, base_url: str = "") -> tuple[str, str, dict[str, str], str]:
    """(readable HTML, its text, the page's meta tags, its title)."""
    p = _Readable(base_url)
    try:
        p.feed(html[:MAX_HTML * 3])
        p.close()
    except Exception:
        logger.debug("reading a page's HTML failed", exc_info=True)
    every, main = "".join(p.all), "".join(p.main)
    body = main if len(_text_of(main)) >= 0.5 * len(_text_of(every)) and len(_text_of(main)) > 400 else every
    body = re.sub(r"<(div|section|p|li|span)>\s*</\1>", "", body)
    return body[:MAX_HTML], _text_of(body), p.meta, " ".join(p.title.split())


def text_html(text: str) -> str:
    """Text without its page (sent by the extension, or only a summary): paragraphs."""
    return "".join(f"<p>{escape(par.strip())}</p>" for par in re.split(r"\n\s*\n|\n", text) if par.strip())


# ---------------------------------------------------------------------------
# Snapshots


def save_snapshot(pid: str, html: str, text: str) -> None:
    d = _dir()
    (d / f"{pid}.html").write_text(html, encoding="utf-8")
    (d / f"{pid}.txt").write_text(text, encoding="utf-8")


def snapshot(pid: str) -> dict[str, str] | None:
    """The page as it was saved: ``{html, text}``, or None when it has no snapshot yet."""
    d = _dir()
    h, t = d / f"{pid}.html", d / f"{pid}.txt"
    if not t.is_file():
        return None
    return {"html": h.read_text(encoding="utf-8") if h.is_file() else text_html(t.read_text(encoding="utf-8")),
            "text": t.read_text(encoding="utf-8")}


def texts(pid: str) -> list[str]:
    """The page's text as the agents read a paper's pages: one 'page'."""
    s = snapshot(pid)
    return [s["text"]] if s else []


# ---------------------------------------------------------------------------
# Keeping a page


def save_page(owner: str, *, url: str, title: str = "", kind: str = "page", author: str = "", site: str = "",
              published: str = "", text: str = "", html: str = "", campaign_id: str = "", source: str = "person",
              added_by: str = "") -> dict[str, Any]:
    """Keep a page in the Library (your own, or a project's): its record, its snapshot when one came along.
    Returns the Library entry ``{paper_id, key, paper}``."""
    from aurora.literature.library import library
    from aurora.literature.records import Paper
    if not url.startswith(("http://", "https://")):
        raise ValueError("a page is kept by its address")
    meta: dict[str, str] = {}
    page_title = ""
    if html:
        html, text, meta, page_title = readable(html, url)
    elif text:
        html = text_html(text)
    host = urlparse(url).netloc.lower().removeprefix("www.")
    author = author or meta.get("author") or meta.get("article:author") or ""
    published = published or meta.get("article:published_time") or meta.get("date") or ""
    year = int(m.group(0)) if (m := re.search(r"\b(19|20)\d{2}\b", published)) else None
    h1 = re.search(r"<h1>(.*?)</h1>", html or "", re.S)
    heading = " ".join(unescape(re.sub(r"<[^>]+>", "", h1.group(1))).split()) if h1 else ""
    if kind == "page" and meta.get("og:type") == "article":
        kind = "blog"
    page_title = re.split(r"\s+[|·–—-]\s+", page_title)[0] if page_title else ""   # "Title | Site" → Title
    p = Paper(title=(title or meta.get("og:title") or heading or page_title or host)[:300], authors=[author[:120]] if author else [],
              year=year, venue=site or meta.get("og:site_name") or host, url=url, abstract=" ".join(text.split())[:600],
              found_in=["web"], kind=kind if kind in KINDS else "page", site=site or meta.get("og:site_name") or host,
              published=published[:40])
    entry = library.upsert_paper(p)
    if html and len(text.strip()) >= 20 and (snapshot(entry["paper_id"]) is None or len(text) > 200):
        save_snapshot(entry["paper_id"], html, text)
    if campaign_id:
        library.add_item(campaign_id, entry["paper_id"], source=source, added_by=added_by)
    else:
        library.add_item("", entry["paper_id"], source=source, added_by=added_by, owner=owner)
    return entry


async def fetch_html(url: str) -> tuple[str, str]:
    """(the page's HTML, its final address); ('', url) when it cannot be fetched or is not a page."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (Pippin research assistant)"}) as c:
            r = await c.get(url)
        r.raise_for_status()
    except Exception:
        logger.info("fetching the page %s failed", url, exc_info=True)
        return "", url
    if "html" not in r.headers.get("content-type", "html"):
        return "", str(r.url)
    return r.text, str(r.url)


async def fill(pid: str) -> bool:
    """Take the snapshot of a page kept without one (saved from the feed, or before pages had snapshots)."""
    from aurora.literature.library import library
    entry = library.paper(pid)
    if entry is None or not entry["paper"].url:
        return False
    s = snapshot(pid)
    if s and len(s["text"]) > 600:
        return True
    raw, final = await fetch_html(entry["paper"].url)
    if not raw:
        return False
    html, text, _, _ = readable(raw, final)
    if len(text) < 20:
        return False
    save_snapshot(pid, html, text)
    return True


def adopt_saved(owner: str) -> int:
    """Pages saved before pages were Library entries (in the notebook, or saved from the feed) become entries of your
    own Library, once. Returns how many came in."""
    from aurora.auth.store import auth_store
    if auth_store.get_pref(owner, "pages_adopted", False):
        return 0
    n = 0
    try:
        from aurora.memory.store import mem_store
        for s in mem_store._all("SELECT * FROM mem_sources WHERE owner=? AND url LIKE 'http%' AND paper_id='' "
                                "AND state='done'", (owner,)):
            if s["platform"] in ("note", "text"):
                continue
            html_path = mem_store.snapshot_path(s["source_id"], "html")
            try:
                entry = save_page(owner, url=s["url"], title=s["title"], kind=KIND_OF_PLATFORM.get(s["platform"], "page"),
                                  author=s["author"], published=s["published"], text=mem_store.snapshot(s["source_id"]),
                                  html=html_path.read_text(encoding="utf-8") if html_path.is_file() else "",
                                  added_by="notebook")
            except ValueError:
                continue
            mem_store.update_source(s["source_id"], paper_id=entry["paper_id"])
            n += 1
    except Exception:
        logger.info("bringing the notebook's pages into the Library failed", exc_info=True)
    try:
        from aurora.feed.store import feed_store
        for item_id, save in feed_store.saves(owner).items():
            it = feed_store.item(item_id)
            if it is None or it.get("paper") or not str(it.get("url", "")).startswith("http"):
                continue
            save_page(owner, url=it["url"], title=it["title"], kind=it["kind"] if it["kind"] in KINDS else "page",
                      site=it.get("source", "").split(" · ")[0], text=it.get("summary", ""),
                      campaign_id=save["campaign_id"], source="feed", added_by="feed")
            n += 1
    except Exception:
        logger.info("bringing the feed's saved pages into the Library failed", exc_info=True)
    auth_store.set_pref(owner, "pages_adopted", True)
    return n

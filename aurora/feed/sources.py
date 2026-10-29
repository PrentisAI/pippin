"""Where feed items come from.  Every source here is free and needs no account, except X.

* arXiv: new submissions in chosen categories, or matching keywords (the arXiv API);
* OpenAlex: new works matching keywords, and a followed author's new works;
* Semantic Scholar: a followed author's papers (for people found there);
* HF Daily Papers: the papers the Hugging Face community picks each day;
* Bluesky and Mastodon: a followed person's posts (their public APIs);
* RSS and Atom: blogs and newsletters;
* X: a followed account's posts, only with an X API key (X has no free API);
* for discussions around a paper: Bluesky posts and Hacker News threads that link it, and its
  OpenReview forum.

A source that does not answer raises :class:`SourceError`; the caller records it and goes on.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx

from aurora.literature.indexes import _OA_SELECT, _S2_FIELDS, _arxiv_papers, _openalex_paper, _s2_paper, openalex_params
from aurora.literature.records import Paper, clean_arxiv, clean_doi, title_similarity

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
_UA = "Pippin research assistant (feed)"
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
BSKY = "https://api.bsky.app/xrpc"
SOURCE_NAMES = {"arxiv": "arXiv", "openalex": "OpenAlex", "s2": "Semantic Scholar", "hf": "HF Daily",
                "bluesky": "Bluesky", "mastodon": "Mastodon", "rss": "RSS", "x": "X", "discussion": "Discussions",
                "github": "GitHub", "youtube": "YouTube"}


class SourceError(Exception):
    """A source did not answer, or answered with something unreadable."""


def text_of(markup: str) -> str:
    """Plain text from HTML: paragraphs become line breaks, tags go, entities are decoded."""
    s = re.sub(r"(?i)<br\s*/?>|</p>\s*<p[^>]*>", "\n", markup or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return "\n".join(" ".join(line.split()) for line in s.splitlines()).strip()


def clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def _ts(value: str | None) -> float:
    """Seconds since the epoch from an ISO 8601 or RFC 822 date; 0 if unreadable."""
    if not value:
        return 0.0
    value = value.strip()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError):
        return 0.0


def replies(n: int) -> str:
    return "" if not n else "1 reply" if n == 1 else f"{n} replies"


def first_line(text: str, n: int = 140) -> str:
    line = next((x.strip() for x in (text or "").splitlines() if x.strip()), "")
    return clip(line, n)


def paper_item(p: Paper, src: str, source: str, *, published_at: float = 0, extra: str = "") -> dict[str, Any]:
    """A paper as a feed item."""
    ukey = f"arxiv:{p.arxiv_id}" if p.arxiv_id else f"doi:{p.doi}" if p.doi else f"url:{p.url or p.title.lower()}"
    idv = f"arXiv:{p.arxiv_id}" if p.arxiv_id else f"DOI {p.doi}" if p.doi else ""
    return {"ukey": ukey, "kind": "paper", "src": src, "source": source, "title": p.title,
            "url": p.url or (f"https://arxiv.org/abs/{p.arxiv_id}" if p.arxiv_id else ""),
            "pdf_url": p.pdf_url or (f"https://arxiv.org/pdf/{p.arxiv_id}" if p.arxiv_id else ""),
            "summary": clip(p.abstract, 900), "people": [[a, ""] for a in p.authors[:8]],
            "idv": idv, "paper": p.to_dict(), "extra": extra, "published_at": published_at}


class Sources:
    """Asks the sources; one instance can be shared (requests to arXiv are spaced out)."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._arxiv_lock = asyncio.Lock()
        self._arxiv_last = 0.0

    async def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                  what: str = "", timeout: httpx.Timeout = _TIMEOUT) -> httpx.Response:
        h = {"User-Agent": _UA, **(headers or {})}
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
                    r = await c.get(url, params=params, headers=h)
            else:
                r = await self._client.get(url, params=params, headers=h)
        except httpx.HTTPError as exc:
            raise SourceError(f"{what or urlparse(url).netloc}: {type(exc).__name__}") from exc
        if r.status_code == 429 and "openalex" in url and not os.environ.get("OPENALEX_API_KEY"):
            raise SourceError("OpenAlex: the free daily budget without a key is used up; set OPENALEX_API_KEY "
                              "(free at openalex.org)")
        if r.status_code >= 400:
            raise SourceError(f"{what or urlparse(url).netloc} answered {r.status_code}")
        return r

    async def json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                   what: str = "") -> Any:
        if "api.openalex.org" in url:
            params = openalex_params(params or {})
        r = await self.get(url, params, {"Accept": "application/json", **(headers or {})}, what)
        try:
            return r.json()
        except ValueError as exc:
            raise SourceError(f"{what or urlparse(url).netloc} did not answer with JSON") from exc

    # ------------------------------------------------------------------ papers

    async def _arxiv(self, params: dict[str, Any]) -> str:
        async with self._arxiv_lock:
            wait = self._arxiv_last + 3.0 - time.monotonic()
            if wait > 0 and self._client is None:
                await asyncio.sleep(wait)
            try:                                     # arXiv is slow with long queries
                return (await self.get("https://export.arxiv.org/api/query", params, what="arXiv",
                                       timeout=httpx.Timeout(90.0, connect=10.0))).text
            finally:
                self._arxiv_last = time.monotonic()

    async def arxiv_recent(self, categories: list[str], keywords: list[str], days: int = 3,
                           limit: int = 60) -> list[dict[str, Any]]:
        """New arXiv submissions in *categories*, or (all categories) matching *keywords*."""
        parts = [f"cat:{c}" for c in categories]
        parts += [f'all:"{k}"' if " " in k else f"all:{k}" for k in keywords]
        if not parts:
            return []
        params = {"search_query": " OR ".join(parts), "sortBy": "submittedDate", "sortOrder": "descending",
                  "max_results": limit}
        try:
            xml = await self._arxiv(params)
        except SourceError:
            xml = await self._arxiv(params)          # once more: arXiv often times out once
        papers = _arxiv_papers(xml)
        try:
            entries = ET.fromstring(xml).findall("a:entry", _ATOM)
        except ET.ParseError:
            entries = []
        meta: dict[str, tuple[str, float]] = {}
        for e in entries:
            aid = clean_arxiv(e.findtext("a:id", "", _ATOM))
            prim = e.find("arxiv:primary_category", _ATOM)
            meta[aid] = (prim.get("term", "") if prim is not None else "", _ts(e.findtext("a:published", "", _ATOM)))
        since = time.time() - days * 86400
        out = []
        for p in papers:
            cat, when = meta.get(p.arxiv_id, ("", 0.0))
            if when and when < since:
                continue
            out.append(paper_item(p, "arxiv", f"arXiv · {cat}" if cat else "arXiv", published_at=when))
        return out

    async def openalex_recent(self, keywords: list[str], days: int = 7, limit: int = 15) -> list[dict[str, Any]]:
        """New works in OpenAlex matching each keyword."""
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        out: list[dict[str, Any]] = []
        for kw in keywords[:8]:
            data = await self.json("https://api.openalex.org/works", {
                "search": kw, "filter": f"from_publication_date:{since}", "sort": "publication_date:desc",
                "per_page": limit, "select": _OA_SELECT + ",publication_date"}, what="OpenAlex")
            for w in data.get("results") or []:
                p = _openalex_paper(w)
                if p.title:
                    out.append(paper_item(p, "openalex", "OpenAlex", published_at=_ts(w.get("publication_date"))))
        return out

    async def openalex_author(self, author_id: str, days: int = 30) -> list[dict[str, Any]]:
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        data = await self.json("https://api.openalex.org/works", {
            "filter": f"author.id:{author_id},from_publication_date:{since}", "sort": "publication_date:desc",
            "per_page": 25, "select": _OA_SELECT + ",publication_date"}, what="OpenAlex")
        out = []
        for w in data.get("results") or []:
            p = _openalex_paper(w)
            if p.title:
                where = f"arXiv · {p.venue}" if p.arxiv_id and p.venue and p.venue != "arXiv" else (p.venue or "OpenAlex")
                out.append(paper_item(p, "openalex", clip(where, 60), published_at=_ts(w.get("publication_date"))))
        return out

    async def arxiv_author(self, name: str, days: int = 30, limit: int = 20) -> list[dict[str, Any]]:
        """New arXiv papers with *name* among the authors (arXiv knows names, not people: namesakes too)."""
        from aurora.feed.people import same_name
        parts = name.split()
        if not parts:
            return []
        query = f"au:{parts[-1]}_{parts[0]}" if len(parts) > 1 else f"au:{parts[0]}"
        xml = await self._arxiv({"search_query": query, "sortBy": "submittedDate", "sortOrder": "descending",
                                 "max_results": limit})
        try:
            entries = {clean_arxiv(e.findtext("a:id", "", _ATOM)): _ts(e.findtext("a:published", "", _ATOM))
                       for e in ET.fromstring(xml).findall("a:entry", _ATOM)}
        except ET.ParseError:
            entries = {}
        since = time.time() - days * 86400
        return [paper_item(p, "arxiv", "arXiv", published_at=entries.get(p.arxiv_id, 0.0))
                for p in _arxiv_papers(xml)
                if any(same_name(a, name) for a in p.authors) and entries.get(p.arxiv_id, 0.0) >= since]

    async def s2_author(self, author_id: str, days: int = 30) -> list[dict[str, Any]]:
        data = await self.json(f"https://api.semanticscholar.org/graph/v1/author/{author_id}/papers",
                               {"fields": _S2_FIELDS + ",publicationDate", "limit": 50}, what="Semantic Scholar")
        since = time.time() - days * 86400
        out = []
        for d in data.get("data") or []:
            when = _ts(d.get("publicationDate"))
            if not when or when < since:
                continue
            p = _s2_paper(d)
            out.append(paper_item(p, "s2", clip(p.venue or "Semantic Scholar", 60), published_at=when))
        return out

    async def hf_daily(self, limit: int = 30) -> list[dict[str, Any]]:
        data = await self.json("https://huggingface.co/api/daily_papers", {"limit": limit}, what="HF Daily Papers")
        out = []
        for d in data or []:
            p = d.get("paper") or {}
            aid = clean_arxiv(p.get("id") or "")
            if not aid or not p.get("title"):
                continue
            paper = Paper(title=" ".join(p["title"].split()), authors=[a.get("name", "") for a in p.get("authors") or []],
                          year=int(str(p.get("publishedAt", ""))[:4]) if str(p.get("publishedAt", ""))[:4].isdigit() else None,
                          venue="arXiv", arxiv_id=aid, url=f"https://arxiv.org/abs/{aid}",
                          pdf_url=f"https://arxiv.org/pdf/{aid}", abstract=p.get("summary") or "", found_in=["arxiv"])
            up = p.get("upvotes") or 0
            out.append(paper_item(paper, "hf", "HF Daily Papers · trending", published_at=_ts(p.get("publishedAt")),
                                  extra=f"{up} upvotes" if up else ""))
        return out

    # ------------------------------------------------------------------ social

    async def bluesky_author(self, handle: str, limit: int = 30) -> list[dict[str, Any]]:
        data = await self.json(f"{BSKY}/app.bsky.feed.getAuthorFeed",
                               {"actor": handle, "limit": limit, "filter": "posts_and_author_threads"}, what="Bluesky")
        threads: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for f in data.get("feed") or []:
            if f.get("reason"):                       # a repost
                continue
            post = f.get("post") or {}
            rec = post.get("record") or {}
            author = (post.get("author") or {})
            reply = rec.get("reply")
            root_uri = ((reply or {}).get("root") or {}).get("uri") or post.get("uri", "")
            if reply and not root_uri.startswith(f"at://{author.get('did', '')}"):
                continue                              # a reply to someone else
            t = threads.get(root_uri)
            if t is None:
                t = threads[root_uri] = {"posts": [], "replies": 0, "author": author}
                order.append(root_uri)
            t["posts"].append(post)
            t["replies"] += int(post.get("replyCount") or 0)
        out = []
        for root in order:
            t = threads[root]
            posts = sorted(t["posts"], key=lambda p: _ts((p.get("record") or {}).get("createdAt")))
            first = posts[0]
            texts = [(p.get("record") or {}).get("text", "") for p in posts]
            body = "\n\n".join(x for x in texts if x)
            if not body.strip():
                continue
            handle_ = t["author"].get("handle", handle)
            rkey = first.get("uri", "").rsplit("/", 1)[-1]
            out.append({"ukey": f"bsky:{first.get('uri')}", "kind": "post", "src": "bluesky",
                        "source": f"Bluesky · @{handle_}" + (f" · a thread, {len(posts)} posts" if len(posts) > 1 else ""),
                        "url": f"https://bsky.app/profile/{handle_}/post/{rkey}", "title": first_line(body),
                        "summary": clip(body, 1200),
                        "people": [[t["author"].get("displayName") or handle_, ""]],
                        "extra": replies(t["replies"]),
                        "published_at": _ts((first.get("record") or {}).get("createdAt")), "links": _links_in(first)})
        return out

    async def mastodon_account(self, acct: str, limit: int = 20) -> list[dict[str, Any]]:
        """Posts of ``user@instance`` (no replies, no boosts)."""
        user, _, host = acct.lstrip("@").partition("@")
        if not host:
            raise SourceError("A Mastodon account looks like @user@instance")
        who = await self.json(f"https://{host}/api/v1/accounts/lookup", {"acct": user}, what=f"Mastodon ({host})")
        posts = await self.json(f"https://{host}/api/v1/accounts/{who['id']}/statuses",
                                {"limit": limit, "exclude_replies": "true", "exclude_reblogs": "true"},
                                what=f"Mastodon ({host})")
        out = []
        for s in posts or []:
            body = text_of(s.get("content", ""))
            if not body:
                continue
            n = int(s.get("replies_count") or 0)
            out.append({"ukey": f"masto:{s.get('url') or s.get('uri')}", "kind": "post", "src": "mastodon",
                        "source": f"Mastodon · @{user}@{host}", "url": s.get("url") or s.get("uri", ""),
                        "title": first_line(body), "summary": clip(body, 1200),
                        "people": [[who.get("display_name") or user, ""]], "extra": replies(n),
                        "published_at": _ts(s.get("created_at"))})
        return out

    async def x_user(self, username: str, bearer: str, limit: int = 10) -> list[dict[str, Any]]:
        """An X account's posts (needs an X API key: a bearer token)."""
        h = {"Authorization": f"Bearer {bearer}"}
        who = await self.json(f"https://api.x.com/2/users/by/username/{username.lstrip('@')}", headers=h, what="X")
        uid = (who.get("data") or {}).get("id")
        if not uid:
            raise SourceError(f"X has no account @{username}")
        data = await self.json(f"https://api.x.com/2/users/{uid}/tweets", {
            "max_results": max(5, limit), "exclude": "replies,retweets",
            "tweet.fields": "created_at,public_metrics"}, headers=h, what="X")
        name = (who.get("data") or {}).get("name") or username
        out = []
        for t in data.get("data") or []:
            body = html.unescape(t.get("text", ""))
            n = int((t.get("public_metrics") or {}).get("reply_count") or 0)
            out.append({"ukey": f"x:{t['id']}", "kind": "post", "src": "x", "source": f"X · @{username.lstrip('@')}",
                        "url": f"https://x.com/{username.lstrip('@')}/status/{t['id']}", "title": first_line(body),
                        "summary": clip(body, 1200), "people": [[name, ""]], "extra": replies(n),
                        "published_at": _ts(t.get("created_at"))})
        return out

    # ------------------------------------------------------------------ blogs

    async def rss(self, url: str, limit: int = 15) -> tuple[str, list[dict[str, Any]]]:
        """A feed's title and its latest entries."""
        r = await self.get(url, what="the feed")
        title, entries = parse_feed(r.text, str(r.url))
        out = []
        for e in entries[:limit]:
            words = len(text_of(e["body"]).split())
            out.append({"ukey": f"url:{e['link'] or e['title']}", "kind": "blog", "src": "rss",
                        "source": f"{title} (RSS)" if title else "RSS", "url": e["link"], "title": e["title"],
                        "summary": clip(text_of(e["summary"] or e["body"]), 900),
                        "people": [[e["author"], ""]] if e["author"] else [],
                        "extra": f"{max(1, round(words / 230))} min read" if words > 120 else "",
                        "published_at": e["when"]})
        return title, out

    async def discover_feed(self, url: str) -> tuple[str, str]:
        """The RSS or Atom feed of a blog or homepage: (feed URL, its title).  Raises SourceError."""
        if not re.match(r"https?://", url):
            url = "https://" + url
        r = await self.get(url, what=urlparse(url).netloc)
        text = r.text
        if _is_feed(text):
            return str(r.url), parse_feed(text, str(r.url))[0]
        tried = []
        for m in re.finditer(r"<link\b[^>]*>", text, re.I):
            tag = m.group(0)
            if re.search(r"rel=[\"']?alternate", tag, re.I) and re.search(r"(rss|atom)\+xml", tag, re.I):
                href = re.search(r"href=[\"']([^\"']+)", tag, re.I)
                if href:
                    tried.append(urljoin(str(r.url), html.unescape(href.group(1))))
        tried += [urljoin(str(r.url), p) for p in ("/feed.xml", "/index.xml", "/rss.xml", "/atom.xml", "/feed")]
        for feed_url in dict.fromkeys(tried):
            try:
                f = await self.get(feed_url, what="the feed")
            except SourceError:
                continue
            if _is_feed(f.text):
                return str(f.url), parse_feed(f.text, str(f.url))[0]
        raise SourceError(f"No RSS or Atom feed found at {url}")

    # ------------------------------------------------------------------ code and videos

    async def github_repos(self, user: str, days: int = 60) -> list[dict[str, Any]]:
        """Repositories *user* made lately (forks left out)."""
        rows = await self.json(f"https://api.github.com/users/{user}/repos",
                               {"sort": "created", "direction": "desc", "per_page": 15}, what="GitHub")
        since = time.time() - days * 86400
        out = []
        for r in rows if isinstance(rows, list) else []:
            when = _ts(r.get("created_at"))
            if r.get("fork") or when < since:
                continue
            stars = int(r.get("stargazers_count") or 0)
            out.append({"ukey": f"url:{r['html_url']}", "kind": "code", "src": "github", "source": f"GitHub · {user}",
                        "url": r["html_url"], "title": r.get("full_name") or r.get("name", ""),
                        "summary": clip(r.get("description") or "", 900), "people": [],
                        "extra": f"★ {stars}" if stars else "", "published_at": when})
        return out

    async def youtube_channel_id(self, url: str) -> tuple[str, str]:
        """A YouTube channel's ID and name, from its address (/@handle, /channel/UC…, /c/…).  Raises SourceError."""
        if m := re.search(r"/channel/(UC[\w-]{22})", url):
            cid = m.group(1)
        else:
            page = (await self.get(url, what="YouTube")).text
            m = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', page) or \
                re.search(r"channel_id=(UC[\w-]{22})", page)
            if not m:
                raise SourceError(f"No YouTube channel at {url}")
            cid = m.group(1)
        title, _ = await self.youtube(cid)
        return cid, title

    async def youtube(self, channel_id: str, limit: int = 15) -> tuple[str, list[dict[str, Any]]]:
        """A YouTube channel's latest videos (its public feed)."""
        title, items = await self.rss(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", limit)
        for it in items:
            it.update(kind="video", src="youtube", source=f"YouTube · {title}", extra="")
        return title, items

    async def youtube_search(self, query: str) -> list[dict[str, str]]:
        """YouTube's own search (its results page): [{url, title, channel, about}].  Raises SourceError."""
        r = await self.get("https://www.youtube.com/results", {"search_query": query},
                           headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"}, what="YouTube")
        m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text, re.S)
        try:
            data = json.loads(m.group(1)) if m else {}
        except ValueError:
            data = {}
        out: list[dict[str, str]] = []

        def runs(x: Any) -> str:
            return "".join(t.get("text", "") for t in (x or {}).get("runs", []))

        def walk(x: Any) -> None:
            if isinstance(x, dict):
                if v := x.get("videoRenderer"):
                    about = " ".join(runs(sn.get("snippetText")) for sn in v.get("detailedMetadataSnippets") or [])
                    out.append({"url": f"https://www.youtube.com/watch?v={v.get('videoId')}", "title": runs(v.get("title")),
                                "channel": runs(v.get("ownerText")), "about": about})
                for y in x.values():
                    walk(y)
            elif isinstance(x, list):
                for y in x:
                    walk(y)
        walk(data)
        return out[:20]

    async def video_title(self, url: str) -> tuple[str, str]:
        """A video's title and channel, when it exists (YouTube and Vimeo say; other pages must answer).
        Raises SourceError."""
        host = urlparse(url).netloc.lower()
        if "youtube.com" in host or "youtu.be" in host or "vimeo.com" in host:
            api = "https://www.youtube.com/oembed" if "vimeo" not in host else "https://vimeo.com/api/oembed.json"
            d = await self.json(api, {"url": url, "format": "json"}, what=host)
            return str(d.get("title") or ""), str(d.get("author_name") or "")
        r = await self.get(url, what=host)
        t = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
        return (clip(text_of(t.group(1)), 140) if t else ""), host

    # ------------------------------------------------------------------ discussions

    async def bluesky_search(self, query: str, limit: int = 40) -> list[dict[str, Any]]:
        data = await self.json(f"{BSKY}/app.bsky.feed.searchPosts", {"q": query, "limit": limit, "sort": "top"},
                               what="Bluesky")
        out = []
        for p in data.get("posts") or []:
            a = p.get("author") or {}
            text = (p.get("record") or {}).get("text", "")
            out.append({"who": a.get("displayName") or a.get("handle", ""), "handle": a.get("handle", ""),
                        "text": text, "replies": int(p.get("replyCount") or 0), "likes": int(p.get("likeCount") or 0),
                        "url": f"https://bsky.app/profile/{a.get('handle', '')}/post/{p.get('uri', '').rsplit('/', 1)[-1]}"})
        return out

    async def hn_threads(self, query: str) -> list[dict[str, Any]]:
        """Hacker News stories linking to *query* (an arXiv ID or a URL), with their top comments."""
        data = await self.json("https://hn.algolia.com/api/v1/search",
                               {"query": query, "restrictSearchableAttributes": "url", "tags": "story"}, what="Hacker News")
        out = []
        for h in (data.get("hits") or [])[:2]:
            n = int(h.get("num_comments") or 0)
            if not n:
                continue
            comments: list[str] = []
            try:
                item = await self.json(f"https://hn.algolia.com/api/v1/items/{h['objectID']}", what="Hacker News")
                comments = [text_of(c.get("text") or "") for c in (item.get("children") or [])[:12] if c.get("text")]
            except SourceError:
                pass
            out.append({"title": h.get("title", ""), "comments": n, "texts": comments,
                        "url": f"https://news.ycombinator.com/item?id={h['objectID']}"})
        return out

    async def openreview_forum(self, title: str) -> str:
        """The OpenReview forum of a paper with this title, or ''."""
        data = await self.json("https://api2.openreview.net/notes/search", {"term": title, "limit": 3}, what="OpenReview")
        for n in data.get("notes") or []:
            t = ((n.get("content") or {}).get("title") or {})
            t = t.get("value", "") if isinstance(t, dict) else str(t)
            if t and title_similarity(t, title) >= 0.93:
                return f"https://openreview.net/forum?id={n.get('forum') or n.get('id')}"
        return ""


def _links_in(post: dict[str, Any]) -> list[str]:
    """Links a Bluesky post carries (an arXiv paper it announces)."""
    out = []
    ext = ((post.get("embed") or {}).get("external") or {})
    if ext.get("uri"):
        out.append(ext["uri"])
    for f in (post.get("record") or {}).get("facets") or []:
        for feat in f.get("features") or []:
            if feat.get("uri"):
                out.append(feat["uri"])
    return out


def _is_feed(text: str) -> bool:
    head = text.lstrip()[:600].lower()
    return head.startswith("<?xml") and ("<rss" in head or "<feed" in head or "<rdf" in head) or \
        head.startswith("<rss") or head.startswith("<feed")


def parse_feed(text: str, base: str = "") -> tuple[str, list[dict[str, Any]]]:
    """An RSS 2.0, RSS 1.0 or Atom feed's title and entries (title, link, summary, body, author, when)."""
    try:
        root = ET.fromstring(text.encode() if isinstance(text, str) else text)
    except ET.ParseError as exc:
        raise SourceError(f"The feed could not be read: {exc}") from exc

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def child(el: ET.Element, *names: str) -> ET.Element | None:
        for c in el:
            if local(c.tag) in names:
                return c
        return None

    def txt(el: ET.Element, *names: str) -> str:
        c = child(el, *names)
        return (c.text or "").strip() if c is not None else ""

    entries: list[dict[str, Any]] = []
    if local(root.tag) == "feed":                       # Atom
        title = txt(root, "title")
        for e in root:
            if local(e.tag) != "entry":
                continue
            link = ""
            for ln in e:
                if local(ln.tag) == "link" and ln.get("rel", "alternate") == "alternate":
                    link = urljoin(base, ln.get("href", ""))
                    break
            author = child(e, "author")
            entries.append({"title": text_of(txt(e, "title")), "link": link, "summary": txt(e, "summary"),
                            "body": txt(e, "content") or txt(e, "summary"),
                            "author": txt(author, "name") if author is not None else "",
                            "when": _ts(txt(e, "published") or txt(e, "updated"))})
        return text_of(title), entries
    channel = child(root, "channel")
    title = txt(channel, "title") if channel is not None else ""
    items = [e for e in (channel if channel is not None and child(channel, "item") is not None else root)
             if local(e.tag) == "item"]
    for e in items:
        entries.append({"title": text_of(txt(e, "title")), "link": urljoin(base, txt(e, "link")),
                        "summary": txt(e, "description"), "body": txt(e, "encoded") or txt(e, "description"),
                        "author": txt(e, "creator", "author"), "when": _ts(txt(e, "pubDate", "date"))})
    return text_of(title), entries


def paper_ids_in(links: list[str]) -> tuple[str, str]:
    """(arXiv ID, DOI) named by any of these links."""
    for u in links:
        if "arxiv.org" in u and clean_arxiv(u):
            return clean_arxiv(u), ""
    for u in links:
        if clean_doi(u):
            return "", clean_doi(u)
    return "", ""


_shared: Sources | None = None


def sources() -> Sources:
    global _shared
    if _shared is None:
        _shared = Sources()
    return _shared


def set_sources(value: Sources | None) -> None:
    """Swap the shared instance (tests)."""
    global _shared
    _shared = value


def bsky_profile_url(handle: str) -> str:
    return f"https://bsky.app/profile/{quote(handle)}"

"""Following people, topics and threads: what they post comes into the notebook on its own.

Two ways in:

* **read in the browser** (Zhihu, X, Xiaohongshu): these sites show their pages only to a signed-in person, so the
  browser extension opens the followed pages in the background, in the person's own browser, and sends back what is
  new (``take``). Nothing about the person's login leaves their browser.
* **read here** (Reddit, RSS and Atom feeds of blogs and newsletters): fetched on a schedule by the server.

Each new post is saved like anything else (claims out, merged, conflicts linked), marked as coming from the feed.
"""

from __future__ import annotations

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

from aurora.memory import fetch, ingest
from aurora.memory.store import mem_store

logger = logging.getLogger(__name__)

IN_BROWSER = ("zhihu", "x", "xiaohongshu")
PER_RUN = 10                 # new posts taken in per follow and run (the rest wait for the next run)


def kind_of(url: str) -> tuple[str, str]:
    """(platform, via) of a page to follow."""
    platform = fetch.platform_of(url)
    if platform in IN_BROWSER:
        return platform, "extension"
    if platform == "reddit":
        return "reddit", "server"
    return "rss", "server"


def due(owner: str, via: str) -> list[dict[str, Any]]:
    now = time.time()
    return [f for f in mem_store.follows(owner, via) if not f["paused"] and now - f["last_run"] >= f["every_min"] * 60]


def take(access: Any, models: dict[str, str], follow: dict[str, Any], items: list[dict[str, Any]], *,
         error: str = "") -> int:
    """What a follow showed on this run: the posts not seen before are saved (at most ``PER_RUN``)."""
    seen = list(follow["seen"])
    fresh = [it for it in items if str(it.get("id") or it.get("url") or "") and str(it.get("id") or it.get("url")) not in seen]
    taken = 0
    for it in fresh[:PER_RUN]:
        text = str(it.get("text") or "").strip()
        title = str(it.get("title") or "").strip()
        if len(text) + len(title) < 20:
            continue
        ingest.start(access, models, follow["owner"], url=str(it.get("url") or ""), title=title[:300],
                     text=f"{title}\n\n{text}".strip(), via="feed")
        taken += 1
    seen.extend(str(it.get("id") or it.get("url")) for it in fresh)
    mem_store.update_follow(follow["follow_id"], seen=seen, last_run=time.time(), last_error=error[:300])
    return taken


# ---------------------------------------------------------------------------
# Read here: Reddit and feeds


def _reddit_json(url: str) -> str:
    p = urlparse(url)
    path = p.path.rstrip("/")
    if "/comments/" in path:
        return f"https://www.reddit.com{path}.json?limit=50"
    if path.startswith("/user/") or path.startswith("/u/"):
        return f"https://www.reddit.com{path}/submitted.json?limit=25"
    return f"https://www.reddit.com{path}/new.json?limit=25"


def reddit_items(data: Any) -> list[dict[str, Any]]:
    """Posts (and, for a thread, its comments) from Reddit's JSON."""
    out = []
    listings = data if isinstance(data, list) else [data]
    for listing in listings:
        for child in ((listing or {}).get("data") or {}).get("children") or []:
            d = child.get("data") or {}
            body = d.get("selftext") or d.get("body") or ""
            if not (d.get("title") or body):
                continue
            out.append({"id": d.get("name") or d.get("id"), "title": d.get("title") or "",
                        "text": body, "url": "https://www.reddit.com" + (d.get("permalink") or ""),
                        "author": d.get("author") or ""})
    return out


def feed_items(xml: str) -> list[dict[str, Any]]:
    """Entries of an RSS or Atom feed."""
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        body = it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or it.findtext("description") or ""
        out.append({"id": it.findtext("guid") or link, "title": it.findtext("title") or "", "url": link,
                    "text": fetch.html_text(body)[1] if "<" in body else body})
    for e in root.findall("a:entry", ns):
        link_el = e.find("a:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        body = e.findtext("a:content", "", ns) or e.findtext("a:summary", "", ns)
        out.append({"id": e.findtext("a:id", "", ns) or link, "title": e.findtext("a:title", "", ns), "url": link,
                    "text": fetch.html_text(body)[1] if "<" in body else body})
    return out


async def read_here(follow: dict[str, Any]) -> list[dict[str, Any]]:
    import httpx
    url = _reddit_json(follow["url"]) if follow["platform"] == "reddit" else follow["url"]
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"User-Agent": "Pippin research memory (by the person who follows)"}) as c:
        r = await c.get(url)
    r.raise_for_status()
    return reddit_items(r.json()) if follow["platform"] == "reddit" else feed_items(r.text)


async def tick() -> int:
    """Run the follows read here that are due, for everyone; check what people rely on for retractions."""
    from aurora.memory import alerts
    taken = 0
    owners = {r["owner"] for r in mem_store._all("SELECT DISTINCT owner FROM mem_sources")}
    for owner in owners:
        try:
            await alerts.check_retractions(owner)
        except Exception:
            logger.info("retraction check for %s failed", owner, exc_info=True)
    from aurora.memory import vault
    for owner in vault.owners():
        try:
            access, models = _access_of(owner)
            vault.scan(access, models, owner)
        except Exception:
            logger.info("reading the vault of %s failed", owner, exc_info=True)
    for f in [x for x in mem_store.follows(via="server") if not x["paused"]
              and time.time() - x["last_run"] >= x["every_min"] * 60]:
        try:
            access, models = _access_of(f["owner"])
        except Exception:
            continue                      # no key for this person: their follows wait
        try:
            items = await read_here(f)
            taken += take(access, models, f, items)
        except Exception as exc:
            logger.info("reading follow %s failed", f["follow_id"], exc_info=True)
            mem_store.update_follow(f["follow_id"], last_run=time.time(), last_error=str(exc)[:300])
    return taken


def _access_of(owner: str) -> tuple[Any, dict[str, str]]:
    from aurora.api.routes.memory import _access
    from aurora.auth.guard import CURRENT
    from aurora.auth.permissions import LOCAL_USER
    from aurora.auth.store import auth_store
    user = LOCAL_USER
    if owner != LOCAL_USER.user_id:
        row = auth_store.get_user(owner)
        if row is None:
            raise ValueError("no such person")
        user = auth_store.as_user(row)
    token = CURRENT.set(user)
    try:
        return _access()
    finally:
        CURRENT.reset(token)


async def loop() -> None:
    """Started with the web console."""
    while True:
        await asyncio.sleep(120)
        try:
            await tick()
        except Exception:
            logger.warning("memory follow loop failed", exc_info=True)

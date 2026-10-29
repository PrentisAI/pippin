"""Finding a person to follow, and where they publish and post.

By name or homepage, a model searches the web for them and lists their places (homepage, blog, X, Bluesky,
Mastodon, OpenAlex); each place it names is then checked (the blog has a feed, the Bluesky and Mastodon
accounts exist, the OpenAlex author has their name) and what does not hold is left out.

Without a model that can search, or for a handle, ORCID or profile link, someone is found by name (OpenAlex's authors, with affiliation, number of works, latest paper
and frequent co-authors to tell namesakes apart), by ORCID, by an OpenAlex or Semantic Scholar
profile, by a Bluesky or Mastodon handle, by an X handle, or by their homepage.  Their other
places are then looked up: the links in their ORCID record and on their homepage (Bluesky,
Mastodon, X, LinkedIn, the blog's feed), and Bluesky by name.  What could only be guessed is
offered unticked, with a note.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from aurora.feed.sources import BSKY, SourceError, Sources, clip, text_of
from aurora.literature.records import fold

_ORCID = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])")
_OA_AUTHOR = re.compile(r"(?:openalex\.org/(?:authors/)?)?\b(A\d{6,12})\b", re.I)
_S2_AUTHOR = re.compile(r"semanticscholar\.org/author/(?:[^/]+/)?(\d+)")
_BSKY = re.compile(r"(?:bsky\.app/profile/)([A-Za-z0-9.\-:]+)|^@?([a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.(?:bsky\.social|[a-z]{2,}))$", re.I)
_MASTO_HANDLE = re.compile(r"^@?([A-Za-z0-9_]+)@([a-z0-9.\-]+\.[a-z]{2,})$", re.I)
_MASTO_URL = re.compile(r"https?://([a-z0-9.\-]+\.[a-z]{2,})/@([A-Za-z0-9_]+)/?$", re.I)
_X = re.compile(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})(?:[/?#]|$)", re.I)
_NOT_X = {"intent", "share", "home", "search", "i", "hashtag"}
# sites with /@user addresses that are not Mastodon
_NOT_MASTODON = ("threads.net", "medium.com", "youtube.com", "tiktok.com", "instagram.com", "substack.com",
                 "github.com", "huggingface.co", "figma.com", "vimeo.com", "flickr.com", "behance.net")


def identity(kind: str, *, handle: str = "", url: str = "", label: str = "", state: str = "ok",
             follow: bool = True, note: str = "", works: int | None = None) -> dict[str, Any]:
    return {"kind": kind, "handle": handle, "url": url, "label": label, "state": state, "follow": follow,
            "note": note, **({"works": works} if works is not None else {})}


def x_identity(user: str, have_key: bool, follow: bool = True) -> dict[str, Any]:
    if have_key:
        return identity("x", handle=user, url=f"https://x.com/{user}", label=f"X · @{user}", follow=follow)
    return identity("x", handle=user, url=f"https://x.com/{user}", label=f"X · @{user} · needs an X API key",
                    state="warn", follow=False, note="Paste an X API key on this page to follow X accounts.")


async def _openalex_candidate(src: Sources, a: dict[str, Any]) -> dict[str, Any]:
    aid = (a.get("id") or "").rsplit("/", 1)[-1]
    inst = (a.get("last_known_institutions") or [{}])
    aff = (inst[0] or {}).get("display_name", "") if inst else ""
    works = int(a.get("works_count") or 0)
    latest, coauthors = "", []
    try:
        data = await src.json("https://api.openalex.org/works", {
            "filter": f"author.id:{aid}", "sort": "publication_date:desc", "per_page": 25,
            "select": "title,display_name,publication_year,authorships"}, what="OpenAlex")
        rows = data.get("results") or []
        if rows:
            w = rows[0]
            latest = f"Latest: “{clip(w.get('title') or w.get('display_name') or '', 90)}” ({w.get('publication_year') or '?'})."
        count: Counter[str] = Counter()
        for w in rows:
            for au in w.get("authorships") or []:
                other = (au.get("author") or {})
                if (other.get("id") or "").rsplit("/", 1)[-1] != aid and other.get("display_name"):
                    count[other["display_name"]] += 1
        coauthors = [n for n, _ in count.most_common(6)]
    except SourceError:
        pass
    orcid = _ORCID.search((a.get("ids") or {}).get("orcid") or a.get("orcid") or "")
    detail = latest + (f" Co-authors include {', '.join(coauthors[:2])}." if coauthors else "")
    return {"cid": f"openalex:{aid}", "name": a.get("display_name", ""), "aff": aff, "works": works,
            "detail": detail.strip(), "coauthors": coauthors, "orcid": orcid.group(1) if orcid else "",
            "ids": [identity("openalex", handle=aid, url=f"https://openalex.org/{aid}",
                             label=f"Papers · OpenAlex · {works} works", works=works)]}


async def find(src: Sources, query: str, *, have_x_key: bool = False, access: Any = None) -> list[dict[str, Any]]:
    """The people *query* may mean (a name, a profile link, an ORCID or a handle).  A name or a homepage is
    searched for on the web by the model when one can search; links between their places are followed otherwise."""
    q = query.strip()
    if not q:
        return []
    if m := _ORCID.search(q):
        data = await src.json("https://api.openalex.org/authors", {"filter": f"orcid:{m.group(1)}"}, what="OpenAlex")
        rows = data.get("results") or []
        if rows:
            return [await _openalex_candidate(src, rows[0])]
        return [{"cid": f"orcid:{m.group(1)}", "name": q, "aff": "", "works": 0, "detail": "ORCID record; no papers in OpenAlex yet.",
                 "coauthors": [], "orcid": m.group(1), "ids": []}]
    if (m := _OA_AUTHOR.search(q)) and ("openalex" in q.lower() or re.fullmatch(r"A\d{6,12}", q, re.I)):
        a = await src.json(f"https://api.openalex.org/authors/{m.group(1).upper()}", what="OpenAlex")
        return [await _openalex_candidate(src, a)]
    if m := _S2_AUTHOR.search(q):
        a = await src.json(f"https://api.semanticscholar.org/graph/v1/author/{m.group(1)}",
                           {"fields": "name,affiliations,paperCount,homepage"}, what="Semantic Scholar")
        n = int(a.get("paperCount") or 0)
        return [{"cid": f"s2:{m.group(1)}", "name": a.get("name", ""), "aff": ", ".join(a.get("affiliations") or []),
                 "works": n, "detail": "", "coauthors": [], "orcid": "", "homepage": a.get("homepage") or "",
                 "ids": [identity("s2", handle=m.group(1), url=f"https://www.semanticscholar.org/author/{m.group(1)}",
                                  label=f"Papers · Semantic Scholar · {n} works", works=n)]}]
    if m := _MASTO_HANDLE.match(q) or _MASTO_URL.match(q):
        user, host = (m.group(1), m.group(2)) if _MASTO_HANDLE.match(q) else (m.group(2), m.group(1))
        a = await src.json(f"https://{host}/api/v1/accounts/lookup", {"acct": user}, what=f"Mastodon ({host})")
        return [{"cid": f"mastodon:{user}@{host}", "name": a.get("display_name") or user, "aff": clip(text_of(a.get("note", "")), 120),
                 "works": 0, "detail": "", "coauthors": [], "orcid": "", "homepage": _first_site(a),
                 "ids": [identity("mastodon", handle=f"{user}@{host}", url=a.get("url") or f"https://{host}/@{user}",
                                  label=f"Mastodon · @{user}@{host}")]}]
    if (m := _BSKY.search(q)) and ("bsky" in q.lower() or q.startswith("@") and "." in q):
        handle = m.group(1) or m.group(2)
        p = await src.json(f"{BSKY}/app.bsky.actor.getProfile", {"actor": handle}, what="Bluesky")
        return [{"cid": f"bluesky:{p.get('handle', handle)}", "name": p.get("displayName") or p.get("handle", handle),
                 "aff": clip(p.get("description") or "", 120), "works": 0, "detail": "", "coauthors": [], "orcid": "",
                 "homepage": _site_in(p.get("description") or ""),
                 "ids": [identity("bluesky", handle=p.get("handle", handle), url=f"https://bsky.app/profile/{p.get('handle', handle)}",
                                  label=f"Bluesky · @{p.get('handle', handle)}")]}]
    if (m := _X.search(q)) and m.group(1).lower() not in _NOT_X:
        return [{"cid": f"x:{m.group(1)}", "name": f"@{m.group(1)}", "aff": "", "works": 0, "detail": "",
                 "coauthors": [], "orcid": "", "ids": [x_identity(m.group(1), have_x_key)]}]
    found = await web_find(src, q, access, have_x_key=have_x_key)
    if found:
        return found
    if re.match(r"https?://", q) or re.match(r"^[a-z0-9\-]+(\.[a-z0-9\-]+)+(/\S*)?$", q, re.I):
        url = q if q.startswith("http") else "https://" + q
        r = await src.get(url, what=urlparse(url).netloc)
        title = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
        name = clip(text_of(title.group(1)) if title else urlparse(url).netloc, 80)
        name = re.split(r"\s+[|\-–·:]\s+", name)[0]
        return [{"cid": f"home:{url}", "name": name, "aff": urlparse(url).netloc, "works": 0, "detail": "",
                 "coauthors": [], "orcid": "", "homepage": url, "ids": []}]
    try:
        data = await src.json("https://api.openalex.org/authors", {"search": q, "per_page": 5}, what="OpenAlex")
    except SourceError:
        return await _s2_candidates(src, q)          # OpenAlex busy or out of budget: Semantic Scholar
    rows = [a for a in data.get("results") or [] if a.get("works_count")]
    return list(await asyncio.gather(*(_openalex_candidate(src, a) for a in rows[:4])))


async def _s2_candidates(src: Sources, name: str) -> list[dict[str, Any]]:
    data = await src.json("https://api.semanticscholar.org/graph/v1/author/search",
                          {"query": name, "limit": 4, "fields": "name,affiliations,paperCount,homepage,papers.title,papers.year"},
                          what="Semantic Scholar")
    out = []
    for a in data.get("data") or []:
        if not a.get("paperCount"):
            continue
        papers = sorted(a.get("papers") or [], key=lambda p: -(p.get("year") or 0))
        latest = f"Latest: “{clip(papers[0]['title'], 90)}” ({papers[0].get('year') or '?'})." if papers else ""
        n = int(a["paperCount"])
        out.append({"cid": f"s2:{a['authorId']}", "name": a.get("name", name), "aff": ", ".join(a.get("affiliations") or []),
                    "works": n, "detail": latest, "coauthors": [], "orcid": "", "homepage": a.get("homepage") or "",
                    "ids": [identity("s2", handle=a["authorId"], url=f"https://www.semanticscholar.org/author/{a['authorId']}",
                                     label=f"Papers · Semantic Scholar · {n} works", works=n)]})
    return sorted(out, key=lambda c: -c["works"])


def _site_in(text: str) -> str:
    m = re.search(r"https?://[^\s)]+", text or "")
    return m.group(0) if m else ""


def _first_site(account: dict[str, Any]) -> str:
    for f in account.get("fields") or []:
        site = _site_in(text_of(f.get("value", "")))
        if site:
            return site
    return ""


def links_on(page: str, base: str) -> dict[str, list[str]]:
    """The profiles a homepage links to: bluesky, mastodon, x, linkedin, orcid."""
    hrefs = [m.group(1) for m in re.finditer(r"href=[\"']([^\"']+)", page, re.I)]
    out: dict[str, list[str]] = {"bluesky": [], "mastodon": [], "x": [], "linkedin": [], "orcid": []}
    for h in hrefs:
        if m := re.search(r"bsky\.app/profile/([A-Za-z0-9.\-:]+)", h):
            out["bluesky"].append(m.group(1))
        elif (m := _X.search(h)) and m.group(1).lower() not in _NOT_X:
            out["x"].append(m.group(1))
        elif "linkedin.com/in/" in h:
            out["linkedin"].append(h)
        elif m := _ORCID.search(h if "orcid.org" in h else ""):
            out["orcid"].append(m.group(1))
        elif (m := _MASTO_URL.match(h.rstrip("/"))) and not m.group(1).lower().endswith(_NOT_MASTODON):
            out["mastodon"].append(f"{m.group(2)}@{m.group(1)}")
    for m in re.finditer(r"<a\b[^>]*rel=[\"'][^\"']*\bme\b[^\"']*[\"'][^>]*>", page, re.I):   # rel="me" links
        if (h := re.search(r"href=[\"']https?://([^/\"']+)/@([A-Za-z0-9_]+)", m.group(0))) and \
                not h.group(1).lower().endswith(_NOT_MASTODON):
            out["mastodon"].append(f"{h.group(2)}@{h.group(1)}")
    return {k: list(dict.fromkeys(v)) for k, v in out.items()}


async def identities(src: Sources, cand: dict[str, Any], *, have_x_key: bool = False) -> list[dict[str, Any]]:
    """Where else the person publishes and posts, ticked when it is certain."""
    ids = list(cand.get("ids") or [])
    if cand.get("checked"):                        # found on the web, and checked already
        return ids
    have = {i["kind"] for i in ids}
    sites: list[str] = [cand["homepage"]] if cand.get("homepage") else []
    if cand.get("orcid"):
        try:
            data = await src.json(f"https://pub.orcid.org/v3.0/{cand['orcid']}/researcher-urls", what="ORCID")
            for u in data.get("researcher-url") or []:
                url = ((u.get("url") or {}).get("value") or "").strip()
                if url:
                    sites.append(url)
        except SourceError:
            pass
    found: dict[str, list[str]] = {"bluesky": [], "mastodon": [], "x": [], "linkedin": [], "orcid": []}
    blog = ""
    for site in list(dict.fromkeys(sites))[:4]:
        host = urlparse(site).netloc.lower()
        for kind, pattern in (("bluesky", r"bsky\.app/profile/([A-Za-z0-9.\-:]+)"),):
            if m := re.search(pattern, site):
                found[kind].append(m.group(1))
        if (m := _X.search(site)) and m.group(1).lower() not in _NOT_X:
            found["x"].append(m.group(1))
            continue
        if "linkedin.com" in host:
            found["linkedin"].append(site)
            continue
        if any(h in host for h in ("bsky.app", "orcid.org", "scholar.google", "github.com", "openalex.org")):
            continue
        try:
            page = await src.get(site, what=host)
            for k, v in links_on(page.text, str(page.url)).items():
                found[k] += v
            if not blog:
                try:
                    blog, blog_title = await src.discover_feed(str(page.url))
                except SourceError:
                    blog = ""
        except SourceError:
            continue
    if "bluesky" not in have:
        for h in dict.fromkeys(found["bluesky"]):
            ids.append(identity("bluesky", handle=h, url=f"https://bsky.app/profile/{h}", label=f"Bluesky · @{h}"))
            have.add("bluesky")
            break
    if "bluesky" not in have and cand.get("name") and not cand["name"].startswith("@"):
        try:
            data = await src.json(f"{BSKY}/app.bsky.actor.searchActors", {"q": cand["name"], "limit": 5}, what="Bluesky")
            same = [a for a in data.get("actors") or [] if fold(a.get("displayName") or "") == fold(cand["name"])]
            if len(same) == 1:
                h = same[0]["handle"]
                ids.append(identity("bluesky", handle=h, url=f"https://bsky.app/profile/{h}", label=f"Bluesky · @{h}",
                                    follow=False, note="Found by name: check it is them."))
                have.add("bluesky")
        except SourceError:
            pass
    if "mastodon" not in have:
        for h in list(dict.fromkeys(found["mastodon"]))[:3]:
            user, host = h.split("@", 1)
            try:                                   # only a real Mastodon account
                await src.json(f"https://{host}/api/v1/accounts/lookup", {"acct": user}, what=f"Mastodon ({host})")
            except SourceError:
                continue
            ids.append(identity("mastodon", handle=h, url=f"https://{host}/@{user}", label=f"Mastodon · @{h}"))
            break
    if "x" not in have:
        for h in dict.fromkeys(found["x"]):
            ids.append(x_identity(h, have_x_key))
            break
    if blog:
        ids.append(identity("blog", url=blog, handle=blog, label=f"Blog · {urlparse(blog).netloc}"))
    elif "blog" not in have:
        ids.append(identity("blog", label="Blog · none found", state="muted", follow=False))
    for h in found["linkedin"][:1]:
        ids.append(identity("linkedin", url=h, label="LinkedIn · cannot be followed (no open API)", state="muted",
                            follow=False))
    if not any(i["kind"] in ("openalex", "s2") for i in ids) and cand.get("name") and not cand["name"].startswith("@"):
        try:
            data = await src.json("https://api.openalex.org/authors", {"search": cand["name"], "per_page": 2}, what="OpenAlex")
            rows = [a for a in data.get("results") or [] if fold(a.get("display_name", "")) == fold(cand["name"])]
            if len(rows) == 1:
                a = rows[0]
                aid = a["id"].rsplit("/", 1)[-1]
                n = int(a.get("works_count") or 0)
                ids.insert(0, identity("openalex", handle=aid, url=f"https://openalex.org/{aid}",
                                       label=f"Papers · OpenAlex · {n} works", works=n, follow=False,
                                       note="Found by name: check it is them."))
        except SourceError:                         # OpenAlex busy: Semantic Scholar
            try:
                same = [c for c in await _s2_candidates(src, cand["name"]) if fold(c["name"]) == fold(cand["name"])]
                if same:
                    best = same[0]["ids"][0]
                    ids.insert(0, {**best, "follow": False, "note": "Found by name (the one with most papers): check it is them."})
            except SourceError:
                pass
    return ids


# ---------------------------------------------------------------------------- found on the web

WEB_PROMPT = """Find where this researcher publishes and posts online: {query}

Search the web. If the words could mean several different people, list each (at most 3, the most likely first).
For each person give only places you actually saw on a page while searching, never guessed ones:
- homepage: their personal or lab page
- blog: their blog, if it is not the homepage
- x, bluesky, mastodon: their handles (mastodon as user@server)
- openalex: their OpenAlex author ID (A followed by digits), if you saw it
- orcid, scholar (Google Scholar profile URL), linkedin (URL)
- aff: where they work now, in a few words (e.g. "OpenAI"); detail: one short sentence on what they are known for, to tell namesakes apart

Answer with JSON only:
{{"people": [{{"name": "", "aff": "", "detail": "", "homepage": "", "blog": "", "x": "", "bluesky": "", \
"mastodon": "", "openalex": "", "orcid": "", "scholar": "", "linkedin": ""}}]}}
Leave out what you did not find. If nobody fits, answer {{"people": []}}."""


def same_name(a: str, b: str) -> bool:
    """One name's words all in the other's ("Lilian Weng" and "Lilian L. Weng")."""
    x, y = set(fold(a).split()), set(fold(b).split())
    return bool(x and y) and (x <= y or y <= x)


async def _web_ask(query: str, access: Any, client: Any) -> list[dict[str, Any]] | None:
    import json
    try:
        resp = await client.chat.completions.create(
            model=access.model, temperature=0, max_tokens=1500,
            messages=[{"role": "user", "content": WEB_PROMPT.format(query=query)}],
            extra_body={"plugins": [{"id": "web", "max_results": 8}]})
        raw = resp.choices[0].message.content or ""
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))  # type: ignore[union-attr]
    except Exception:
        return None
    people = data.get("people") if isinstance(data, dict) else None
    return [p for p in people if isinstance(p, dict) and str(p.get("name", "")).strip()][:3] \
        if isinstance(people, list) else None


def _s(p: dict[str, Any], key: str) -> str:
    return str(p.get(key) or "").strip()


async def _checked(src: Sources, p: dict[str, Any], have_x_key: bool) -> dict[str, Any]:
    """What the model found, each place checked where it can be: what does not hold is left out."""
    name = _s(p, "name")
    ids: list[dict[str, Any]] = []
    works = 0

    async def papers() -> None:
        nonlocal works
        aid = (m.group(1).upper() if (m := _OA_AUTHOR.search(_s(p, "openalex"))) else "")
        try:
            if aid:
                a = await src.json(f"https://api.openalex.org/authors/{aid}", what="OpenAlex")
                rows, sure = ([a] if same_name(a.get("display_name", ""), name) else []), True
            else:
                data = await src.json("https://api.openalex.org/authors", {"search": name, "per_page": 5}, what="OpenAlex")
                rows = [a for a in data.get("results") or [] if a.get("works_count") and same_name(a.get("display_name", ""), name)]
                sure = len(rows) == 1
            if rows:
                a = rows[0]
                aid = a["id"].rsplit("/", 1)[-1]
                works = int(a.get("works_count") or 0)
                ids.append(identity("openalex", handle=aid, url=f"https://openalex.org/{aid}",
                                    label=f"Papers · OpenAlex · {works} works", works=works, follow=sure,
                                    note="" if sure else "Found by name: check it is them."))
        except SourceError:
            pass

    async def blog() -> None:
        for site in dict.fromkeys(s for s in (_s(p, "blog"), _s(p, "homepage")) if s):
            try:
                url, _ = await src.discover_feed(site)
            except SourceError:
                continue
            ids.append(identity("blog", url=url, handle=url, label=f"Blog · {urlparse(url).netloc}"))
            return

    async def bluesky() -> None:
        if not (h := _s(p, "bluesky").lstrip("@")):
            return
        h = (m.group(1) if (m := re.search(r"bsky\.app/profile/([A-Za-z0-9.\-:]+)", h)) else h)
        try:
            prof = await src.json(f"{BSKY}/app.bsky.actor.getProfile", {"actor": h}, what="Bluesky")
        except SourceError:
            return
        h = prof.get("handle", h)
        ids.append(identity("bluesky", handle=h, url=f"https://bsky.app/profile/{h}", label=f"Bluesky · @{h}"))

    async def mastodon() -> None:
        h = _s(p, "mastodon").lstrip("@")
        m = _MASTO_HANDLE.match(h) or _MASTO_URL.match(h.rstrip("/"))
        if not m:
            return
        user, host = (m.group(1), m.group(2)) if _MASTO_HANDLE.match(h) else (m.group(2), m.group(1))
        try:
            await src.json(f"https://{host}/api/v1/accounts/lookup", {"acct": user}, what=f"Mastodon ({host})")
        except SourceError:
            return
        ids.append(identity("mastodon", handle=f"{user}@{host}", url=f"https://{host}/@{user}", label=f"Mastodon · @{user}@{host}"))

    await asyncio.gather(papers(), blog(), bluesky(), mastodon())
    x = _s(p, "x").lstrip("@")
    x = m.group(1) if (m := _X.search(x)) else x
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", x) and x.lower() not in _NOT_X:
        ids.append(x_identity(x, have_x_key))              # X cannot be checked without a key
    if not any(i["kind"] == "blog" for i in ids):
        ids.append(identity("blog", label="Blog · none found", state="muted", follow=False))
    if (li := _s(p, "linkedin")) and "linkedin.com/" in li:
        ids.append(identity("linkedin", url=li, label="LinkedIn · cannot be followed (no open API)", state="muted", follow=False))
    order = ["openalex", "blog", "bluesky", "mastodon", "x", "linkedin"]
    ids.sort(key=lambda i: order.index(i["kind"]))
    orcid = _ORCID.search(_s(p, "orcid"))
    return {"cid": f"web:{fold(name)}:{_s(p, 'aff')[:40]}", "name": name, "aff": _s(p, "aff"), "works": works,
            "detail": clip(_s(p, "detail"), 200), "coauthors": [], "orcid": orcid.group(1) if orcid else "",
            "homepage": _s(p, "homepage"), "ids": ids, "checked": True}


async def web_find(src: Sources, query: str, access: Any, *, have_x_key: bool = False,
                   client: Any = None) -> list[dict[str, Any]] | None:
    """The people *query* (a name or a homepage) may mean, found by a model searching the web, every place it
    names checked.  None when no model can search (no key, not OpenRouter) or the search failed."""
    if access is None or not access.api_key or "/" not in access.model:   # the web plugin is OpenRouter's
        return None
    if client is None:
        from aurora.brainstorm.agents import _client
        client = _client(access)
    people = await _web_ask(query.strip(), access, client)
    if people is None:
        return None
    found = list(await asyncio.gather(*(_checked(src, p, have_x_key) for p in people)))
    # someone with nothing that held up (only a guess by name, or nothing) is left out
    return [c for c in found if any(i["follow"] or i["kind"] == "x" for i in c["ids"])]

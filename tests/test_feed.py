"""The feed: sources, finding people to follow, ranking against projects, saving, bringing, the digest."""

import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm import agents as ag
from aurora.brainstorm.store import bs_store
from aurora.feed import people as ppl
from aurora.feed import service as svc
from aurora.feed import sources as src_mod
from aurora.feed.sources import Sources, parse_feed
from aurora.feed.store import feed_store
from aurora.literature.library import library

NOW = datetime.now(UTC)
ISO = (NOW - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
DAY = NOW.strftime("%Y-%m-%d")

ARXIV = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2610.08812v1</id><published>{ISO}</published>
<title>Station-level demand after e-bike price changes in bike-share systems</title>
<summary>We estimate how classic and e-bike demand at each bike-share station responds to price changes,
separating weekday commuting from weekend leisure trips, using public trip data.</summary>
<author><name>Ravi Iyer</name></author><author><name>Maya Okafor</name></author>
<link title="pdf" href="http://arxiv.org/pdf/2610.08812v1"/>
<arxiv:primary_category term="cs.CY"/></entry>
<entry><id>http://arxiv.org/abs/2610.09999v1</id><published>{ISO}</published>
<title>Dust lanes in barred spiral galaxies</title>
<summary>Spectroscopy of interstellar dust in galactic bars.</summary>
<author><name>A. Stellar</name></author>
<arxiv:primary_category term="astro-ph.GA"/></entry>
</feed>"""

RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>Urban Mobility Lab notes</title>
<item><title>What we got wrong about rebalancing trucks</title><link>https://lab.example/rebalancing</link>
<description>&lt;p&gt;Rebalancing models for bike-share stations that ignore truck routing time overstate demand served.&lt;/p&gt;</description>
<pubDate>{NOW.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate><dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Tomás Reyes</dc:creator></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Okafor's notes</title>
<entry><title>Notes on cleaning six years of trip data</title><link href="/posts/cleaning"/>
<published>2026-10-18T10:00:00Z</published><summary>Duplicated trips around daylight-saving changes.</summary>
<author><name>Maya Okafor</name></author></entry></feed>"""

HOME = """<html><head><title>Maya Okafor | Urban data science</title>
<link rel="alternate" type="application/atom+xml" href="/atom.xml"></head><body>
<a href="https://bsky.app/profile/mokafor.bsky.social">Bluesky</a> <a href="https://x.com/mayaokafor">X</a>
<a href="https://www.linkedin.com/in/mokafor">LinkedIn</a></body></html>"""


def bsky_post(uri, text, replies=0, root=None, did="did:plc:maya"):
    rec = {"text": text, "createdAt": ISO}
    if root:
        rec["reply"] = {"root": {"uri": root}, "parent": {"uri": root}}
    return {"post": {"uri": uri, "author": {"did": did, "handle": "mokafor.bsky.social", "displayName": "Maya Okafor"},
                     "record": rec, "replyCount": replies,
                     "embed": {"external": {"uri": "https://arxiv.org/abs/2610.08812"}}}}


def web(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    host, path, q = request.url.host, request.url.path, dict(request.url.params)
    if host == "export.arxiv.org":
        return httpx.Response(200, text=ARXIV)
    if host == "api.openalex.org" and path == "/authors":
        if "search" in q:
            return httpx.Response(200, json={"results": [
                {"id": "https://openalex.org/A111", "display_name": "Ada Kowalski", "works_count": 38,
                 "last_known_institutions": [{"display_name": "Technical University"}],
                 "ids": {"orcid": "https://orcid.org/0000-0002-1825-0097"}},
                {"id": "https://openalex.org/A222", "display_name": "Ada Kowalski", "works_count": 6,
                 "last_known_institutions": [{"display_name": "Medical School"}], "ids": {}}]})
        return httpx.Response(200, json={"results": []})
    if host == "api.openalex.org" and path == "/authors/A111111":
        return httpx.Response(200, json={"id": "https://openalex.org/A111", "display_name": "Ada Kowalski", "works_count": 38})
    if host == "api.openalex.org" and path == "/works":
        f = q.get("filter", "")
        if "author.id:A111" in f and "from_publication_date" not in f:
            return httpx.Response(200, json={"results": [
                {"title": "Rebalancing with uncertain demand", "publication_year": 2026,
                 "authorships": [{"author": {"id": "A111", "display_name": "Ada Kowalski"}},
                                 {"author": {"id": "A9", "display_name": "Maya Okafor"}}]}]})
        if "author.id:A222" in f:
            return httpx.Response(200, json={"results": [{"title": "A cardiology study", "publication_year": 2024,
                                                          "authorships": []}]})
        return httpx.Response(200, json={"results": []})
    if host == "pub.orcid.org":
        return httpx.Response(200, json={"researcher-url": [{"url": {"value": "https://maya.example/"}}]})
    if host == "maya.example" and path in ("/", ""):
        return httpx.Response(200, text=HOME)
    if host == "maya.example" and path == "/atom.xml":
        return httpx.Response(200, text=ATOM)
    if host == "lab.example":
        return httpx.Response(200, text=RSS)
    if host == "api.bsky.app" and path.endswith("getAuthorFeed"):
        root = "at://did:plc:maya/app.bsky.feed.post/aaa"
        return httpx.Response(200, json={"feed": [
            bsky_post(root, "Announcing our paper on e-bike prices.\nThe six cities were picked for clean data.", 4),
            bsky_post("at://did:plc:maya/app.bsky.feed.post/bbb", "Please don't read our numbers as national.", 2, root=root),
            bsky_post("at://did:plc:maya/app.bsky.feed.post/ccc", "Reply to someone else", 0,
                      root="at://did:plc:other/app.bsky.feed.post/zzz"),
        ]})
    if host == "api.bsky.app" and path.endswith("searchPosts"):
        return httpx.Response(200, json={"posts": [
            {"uri": f"at://did:plc:p{i}/app.bsky.feed.post/x{i}", "author": {"handle": f"p{i}.bsky.social", "displayName": f"P{i}"},
             "record": {"text": t}, "replyCount": 3, "likeCount": 10 - i}
            for i, t in enumerate(["Were the six systems picked for clean data?", "Did they control for weather?",
                                   "The fleet expansion could explain part of it."])]})
    if host == "api.bsky.app" and path.endswith("getProfile") and q.get("actor") == "mokafor.bsky.social":
        return httpx.Response(200, json={"handle": "mokafor.bsky.social", "displayName": "Maya Okafor"})
    if host == "api.bsky.app" and path.endswith("searchActors"):
        return httpx.Response(200, json={"actors": []})
    if host == "hn.algolia.com" and path.endswith("/search"):
        return httpx.Response(200, json={"hits": []})
    if host == "api2.openreview.net":
        return httpx.Response(200, json={"notes": []})
    if host == "huggingface.co":
        return httpx.Response(200, json=[{"paper": {"id": "2610.07730", "title": "Calendar-aware graph models for battery fade",
                                                     "summary": "Grid battery capacity fade.", "authors": [{"name": "S. Nakamura"}],
                                                     "publishedAt": ISO, "upvotes": 12}}])
    return httpx.Response(404, text=f"no fake for {url}")


@pytest.fixture()
def sources(monkeypatch):
    s = Sources(client=httpx.AsyncClient(transport=httpx.MockTransport(web)))
    monkeypatch.setattr(src_mod, "_shared", s)
    return s


@pytest.fixture()
def client(sources):
    from aurora.api.app import create_app
    return TestClient(create_app())


# ---------------------------------------------------------------------------- sources

def test_rss_and_atom_feeds_are_read():
    title, entries = parse_feed(RSS)
    assert title == "Urban Mobility Lab notes"
    assert entries[0]["title"] == "What we got wrong about rebalancing trucks" and entries[0]["author"] == "Tomás Reyes"
    title, entries = parse_feed(ATOM, "https://maya.example/atom.xml")
    assert title == "Okafor's notes" and entries[0]["link"] == "https://maya.example/posts/cleaning"


async def test_arxiv_bluesky_and_blogs_become_items(sources):
    items = await sources.arxiv_recent(["cs.CY"], [])
    assert items[0]["source"] == "arXiv · cs.CY" and items[0]["idv"] == "arXiv:2610.08812"
    assert items[0]["pdf_url"].endswith("2610.08812v1") and items[0]["kind"] == "paper"
    posts = await sources.bluesky_author("mokafor.bsky.social")
    assert len(posts) == 1                                   # a thread of two, a reply to someone else left out
    assert posts[0]["source"].endswith("a thread, 2 posts") and posts[0]["extra"] == "6 replies"
    assert "national" in posts[0]["summary"]
    url, title = await sources.discover_feed("https://maya.example/")
    assert url == "https://maya.example/atom.xml" and title == "Okafor's notes"


async def test_namesakes_are_told_apart_and_their_places_found(sources):
    found = await ppl.find(sources, "Ada Kowalski")
    assert [c["aff"] for c in found] == ["Technical University", "Medical School"]
    assert "Rebalancing with uncertain demand" in found[0]["detail"] and "Maya Okafor" in found[0]["detail"]
    ids = await ppl.identities(sources, found[0])
    kinds = {i["kind"]: i for i in ids}
    assert kinds["openalex"]["label"] == "Papers · OpenAlex · 38 works"
    assert kinds["bluesky"]["handle"] == "mokafor.bsky.social" and kinds["bluesky"]["follow"]
    assert kinds["x"]["state"] == "warn" and not kinds["x"]["follow"]          # no X API key
    assert kinds["blog"]["url"] == "https://maya.example/atom.xml"
    assert kinds["linkedin"]["state"] == "muted"


class WebSearcher:
    """A model that searched the web and found what it is given."""

    def __init__(self, people):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.people, self.asked = people, []

    async def create(self, **kw):
        self.asked.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="Here: " + json.dumps({"people": self.people})))])


async def test_found_on_the_web_and_each_place_checked(sources):
    access = ag.ModelAccess(api_key="k", base_url="u", model="openai/some-model")
    model = WebSearcher([
        {"name": "Ada Kowalski", "aff": "Technical University", "detail": "Bike-share rebalancing.",
         "homepage": "https://maya.example/", "openalex": "A111111", "bluesky": "@mokafor.bsky.social",
         "mastodon": "ghost@nowhere.example", "x": "https://x.com/akowalski", "orcid": "0000-0002-1825-0097"},
        {"name": "Ada Kowalski", "aff": "Nowhere", "bluesky": "made-up.bsky.social", "openalex": "A99999999"}])
    found = await ppl.web_find(sources, "Ada Kowalski", access, client=model)
    assert model.asked[0]["extra_body"]["plugins"][0]["id"] == "web"
    (ada,) = found                                  # the second: nothing it named held up
    kinds = {i["kind"]: i for i in ada["ids"]}
    assert kinds["openalex"]["follow"] and ada["works"] == 38
    assert kinds["blog"]["url"] == "https://maya.example/atom.xml"
    assert kinds["bluesky"]["handle"] == "mokafor.bsky.social"
    assert "mastodon" not in kinds                  # no such account
    assert kinds["x"]["handle"] == "akowalski" and not kinds["x"]["follow"]   # no X key
    assert await ppl.identities(sources, ada) == ada["ids"]
    # a model that cannot search (not OpenRouter), or no key: the links are followed instead
    assert await ppl.web_find(sources, "Ada Kowalski", ag.ModelAccess("k", "u", "gpt-x"), client=model) is None
    assert await ppl.web_find(sources, "Ada Kowalski", ag.ModelAccess("", "u", "openai/x"), client=model) is None


async def test_a_failed_web_search_falls_back_to_the_links(sources, monkeypatch):
    class Broken:
        chat = SimpleNamespace(completions=SimpleNamespace(create=None))

        def __init__(self):
            async def fail(**kw):
                raise RuntimeError("down")
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=fail))
    monkeypatch.setattr(ag, "client_factory", lambda access: Broken())
    found = await ppl.find(sources, "Ada Kowalski", access=ag.ModelAccess("k", "u", "openai/x"))
    assert [c["aff"] for c in found] == ["Technical University", "Medical School"]


# ---------------------------------------------------------------------------- the feed

def _project(c, prompt="Weekend bike-share demand at Citi Bike stations, station-level trips and e-bike prices"):
    return c.post("/api/campaigns/quick", json={"prompt": prompt}).json()["campaign_id"]


def _refresh(c):
    import asyncio

    from aurora.auth.permissions import LOCAL_USER
    return asyncio.run(svc.refresh(LOCAL_USER))


def test_a_feed_ranked_against_projects(client):
    cid = _project(client)
    client.put("/api/feed/settings", json={"topics": ["cs.CY"], "from_objectives": False})
    client.post("/api/feed/rss", json={"url": "https://lab.example/feed"})
    found = client.post("/api/feed/people/find", json={"query": "Ada Kowalski"}).json()["candidates"]
    ids = client.post("/api/feed/people/identities", json={"candidate": found[0]}).json()["ids"]
    person = client.post("/api/feed/people", json={"candidate": {**found[0], "name": "Maya Okafor"}, "ids": ids}).json()["person"]
    result = _refresh(client)
    assert result["counts"]["arxiv"] == 2 and result["counts"]["rss"] >= 1 and result["counts"]["bluesky"] == 1
    assert result["counts"]["x"] == "off"
    feed = client.get("/api/feed").json()
    by_title = {i["title"]: i for i in feed["items"]}
    paper = by_title["Station-level demand after e-bike price changes in bike-share systems"]
    assert paper["project"] and paper["rel"] >= 3 and not paper["folded"] and paper["why"]
    assert paper["extra"] == "Maya Okafor is an author"
    assert ["Maya Okafor", person["person_id"]] in paper["people"]
    galaxy = by_title["Dust lanes in barred spiral galaxies"]
    assert galaxy["folded"]
    assert feed["people"][0]["fresh"] >= 1 and "post" in feed["people"][0]["activity"]
    # discussions around the project's papers: none until the paper is in the project
    r = client.post(f"/api/feed/items/{paper['item_id']}/save", json={"campaign_id": cid}).json()
    assert r["in_literature"] and r["saved"]["project"]
    lit = client.get(f"/api/campaigns/{cid}/literature").json()
    assert any(p["title"].startswith("Station-level demand") and p.get("source") == "feed" for p in lit["papers"]) or \
        any(i["source"] == "feed" for i in library.items(cid))
    assert client.get("/api/feed/saved").json()["items"][0]["item_id"] == paper["item_id"]
    # not interested: hidden, and back with undo
    assert client.post(f"/api/feed/items/{galaxy['item_id']}/hide").status_code == 200
    assert galaxy["item_id"] not in {i["item_id"] for i in client.get("/api/feed").json()["items"]}
    client.delete(f"/api/feed/items/{galaxy['item_id']}/hide")
    assert galaxy["item_id"] in {i["item_id"] for i in client.get("/api/feed").json()["items"]}


def test_discussions_around_a_projects_papers(client):
    cid = _project(client)
    from aurora.literature.records import Paper
    entry = library.upsert_paper(Paper(title="Station-level demand after e-bike price changes", authors=["Ravi Iyer"],
                                       arxiv_id="2610.08812", year=2026))
    library.add_item(cid, entry["paper_id"], source="person")
    client.put("/api/feed/settings", json={"from_objectives": False, "hf": False})
    _refresh(client)
    items = client.get("/api/feed").json()["items"]
    disc = next(i for i in items if i["kind"] == "discussion")
    assert disc["source"] == "Around arXiv:2610.08812" and disc["campaign_id"] == cid and disc["rel"] >= 3
    assert len(disc["points"]) == 3 and disc["places"][0][0].startswith("12 posts and replies on Bluesky")


def test_the_model_ranks_and_says_why(client, monkeypatch):
    cid = _project(client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")

    class Ranker:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            self.prompts = []

        async def create(self, *, model, messages, **kw):
            text = messages[0]["content"]
            self.prompts.append(text)
            if "search phrases" in text:
                body = {"keywords": ["bike share demand"], "categories": ["cs.CY"]}
            elif "discussing" in text:
                body = {"summary": "s", "points": ["a"]}
            else:
                ids = [line.split()[2] for line in text.splitlines() if line.startswith("- id ")]
                body = {"items": [{"id": i, "fits": [{"project": cid, "rel": 5, "why": "Same station-level trips."}]}
                                  for i in ids]}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))])
    fake = Ranker()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    _refresh(client)
    items = client.get("/api/feed").json()["items"]
    assert items and all(i["why"] == "Same station-level trips." and i["rel"] == 5 for i in items)
    assert any("search phrases" in p for p in fake.prompts)       # topics from the project's objective


def test_bring_to_a_brainstorm_and_give_to_the_front_desk(client):
    cid = _project(client)
    client.put("/api/feed/settings", json={"topics": ["cs.CY"], "from_objectives": False, "hf": False})
    client.post("/api/feed/rss", json={"url": "https://lab.example/feed"})
    _refresh(client)
    items = client.get("/api/feed").json()["items"]
    blog = next(i for i in items if i["kind"] == "blog")
    paper = next(i for i in items if i["kind"] == "paper" and "bike" in i["title"])
    r = client.post(f"/api/feed/items/{blog['item_id']}/bring", json={"space": cid}).json()
    assert r["card"]["type"] == "material" and r["card"]["url"] == "https://lab.example/rebalancing"
    assert r["card"]["source"].startswith("Blog · Urban Mobility Lab notes")
    r = client.post(f"/api/feed/items/{paper['item_id']}/bring", json={"space": "global"}).json()
    assert r["card"]["paper"]["arxiv_id"] == "2610.08812"
    assert bs_store.card(r["card"]["card_id"])["space"].startswith("user:")
    r = client.post(f"/api/feed/items/{paper['item_id']}/ask", json={"campaign_id": cid}).json()
    assert r["seed"] and r["ref"]["kind"] == "feed"
    assert any(u["place"] == "seed" for u in library.uses(cid))
    from aurora.api.routes.campaigns import _with_library
    text, shown = _with_library("Could we test it?", [r["ref"]])
    assert "From the researcher's Library" in text and "e-bike" in text and shown[0]["title"] == paper["title"]
    lib = client.get("/api/feed/library").json()["items"]
    assert any(x["item_id"] == paper["item_id"] for x in lib if x["kind"] == "feed")


def test_a_followed_persons_page(client):
    cid = _project(client)
    found = client.post("/api/feed/people/find", json={"query": "Ada Kowalski"}).json()["candidates"]
    ids = client.post("/api/feed/people/identities", json={"candidate": found[0]}).json()["ids"]
    pid = client.post("/api/feed/people", json={"candidate": {**found[0], "name": "Maya Okafor"}, "ids": ids}).json()["person"]["person_id"]
    client.put("/api/feed/settings", json={"topics": ["cs.CY"], "from_objectives": False, "hf": False})
    _refresh(client)
    item = next(i for i in client.get("/api/feed").json()["items"] if i["kind"] == "paper" and "bike" in i["title"])
    client.post(f"/api/feed/items/{item['item_id']}/save", json={"campaign_id": cid})
    page = client.get(f"/api/feed/people/{pid}").json()
    kinds = {e["k"] for e in page["timeline"]}
    assert {"paper", "post", "blog"} <= kinds
    assert any(e["in_project"].startswith("Saved to") for e in page["timeline"])
    assert page["theirs"][0]["project"] and "saved from the feed" in page["theirs"][0]["how"]
    assert page["coauthors"] == ["Maya Okafor"]
    # unfollow and follow again; what to follow of them
    client.patch(f"/api/feed/people/{pid}", json={"wants": {"posts": False}})
    assert client.get("/api/feed/following").json()["people"][0]["wants"]["posts"] is False
    client.delete(f"/api/feed/people/{pid}")
    assert client.get("/api/feed/following").json()["people"] == []
    client.patch(f"/api/feed/people/{pid}", json={"removed": False})
    assert len(client.get("/api/feed/following").json()["people"]) == 1


def test_the_daily_digest(client, monkeypatch):
    _project(client)
    from aurora.api.routes import campaigns
    from aurora.config import NotificationsConfig
    chan = {"id": "lab", "type": "webhook", "target": "https://hooks.example/x", "name": "Lab group"}
    monkeypatch.setattr(campaigns, "live_notifications", lambda user_id=None: NotificationsConfig.model_validate({"channels": [chan]}))
    sent = []

    async def deliver(channel, kind, title, text, url="", extra=None):
        sent.append((channel.name, kind, title, text))
        return ""
    import aurora.notify
    monkeypatch.setattr(aurora.notify, "deliver", deliver)
    r = client.put("/api/feed/settings", json={"topics": ["cs.CY"], "from_objectives": False,
                                               "digest": {"to": "lab", "at": "7:05", "top": 3}})
    assert r.json()["digest"]["at"] == "07:05" and r.json()["digest"]["label"].startswith("Lab group")
    assert client.put("/api/feed/settings", json={"digest": {"to": "nowhere"}}).status_code == 400
    _refresh(client)
    best = max(i["rel"] for i in client.get("/api/feed").json()["items"])
    r = client.post("/api/feed/digest/test")
    if best < 4:                                   # nothing fits well: the day is skipped
        assert r.json()["detail"] == "Nothing fits a project well today"
        client.put("/api/feed/settings", json={"digest": {"skip": False}})
        r = client.post("/api/feed/digest/test")
    assert r.status_code == 200, r.text
    assert sent and sent[0][1] == "daily_digest" and "e-bike" in sent[0][3]
    from aurora.auth.store import auth_store
    auth_store.set_pref("local", "feed_used", True)
    assert svc._due("local", datetime.now().replace(hour=23, minute=59))
    assert not svc._due("local", datetime.now().replace(hour=0, minute=1))


def test_x_needs_a_key(client):
    found = client.post("/api/feed/people/find", json={"query": "https://x.com/mayaokafor"}).json()["candidates"]
    assert found[0]["ids"][0]["state"] == "warn"
    assert client.get("/api/feed/following").json()["x_key"] is False


def test_feed_items_are_each_persons_own():
    from aurora.feed.store import FeedStore  # noqa: F401
    row, new = feed_store.add_item("u1", {"ukey": "url:x", "kind": "post", "src": "bluesky", "title": "t"})
    assert new and not feed_store.add_item("u1", {"ukey": "url:x", "kind": "post", "src": "bluesky", "title": "t"})[1]
    assert feed_store.add_item("u2", {"ukey": "url:x", "kind": "post", "src": "bluesky", "title": "t"})[1]
    assert time.time() - row["fetched_at"] < 5
    assert DAY


def test_old_work_of_someone_just_followed_is_not_news(client):
    _project(client)
    old = time.time() - 400 * 86400
    row, _ = feed_store.add_item("local", {"ukey": "bsky:old", "kind": "post", "src": "bluesky", "title": "Bike-share stations, a 2024 post",
                                           "published_at": old, "people": [["Maya Okafor", ""]]})
    import asyncio

    from aurora.auth.permissions import LOCAL_USER
    asyncio.run(svc.rank_new(LOCAL_USER, svc.project_profiles(LOCAL_USER), None))
    assert feed_store.item(row["item_id"])["ranked"] == 1
    client.get("/api/feed")
    assert row["item_id"] not in {i["item_id"] for i in client.get("/api/feed").json()["items"]}

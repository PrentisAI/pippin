"""Pages in the Library: a blog post or any page kept beside the papers, with a snapshot to read and annotate."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from aurora.literature import pages
from aurora.literature.library import library

URL = "https://example.org/posts/agents/"
HTML = """<html><head><title>Agents | Blog</title><meta property="og:site_name" content="Lil Log">
<meta name="author" content="Ada Kowalski"><meta property="article:published_time" content="2023-06-23T00:00:00Z"></head>
<body><nav>Home · About</nav><article><h1>LLM Powered Agents</h1>
<p>Building agents with a language model as its core controller is a cool concept, and planning is its first part.</p>
<p>Memory is the second part: <a href="/memory">short and long term</a>.</p><script>track()</script>
<img data-src="fig1.png" alt="Overview"></article><footer>© 2023</footer></body></html>"""


@pytest.fixture()
def client(monkeypatch):
    async def fetch_html(url):
        return (HTML, url) if "example.org" in url else ("", url)
    monkeypatch.setattr(pages, "fetch_html", fetch_html)
    from aurora.api.app import create_app
    return TestClient(create_app())


def test_a_page_is_read_as_its_article():
    html, text, meta, title = pages.readable(HTML, URL)
    assert "<script" not in html and "Home · About" not in text and "© 2023" not in text
    assert '<a href="https://example.org/memory" target="_blank"' in html
    assert '<img src="https://example.org/posts/agents/fig1.png" alt="Overview"' in html
    assert text.splitlines()[:2] == ["LLM Powered Agents", "Building agents with a language model as its core "
                                     "controller is a cool concept, and planning is its first part."]
    assert (meta["og:site_name"], title) == ("Lil Log", "Agents | Blog")
    assert pages.normal_url("https://www.Example.org/posts/agents/?utm_source=x&b=1#top") == "https://example.org/posts/agents?b=1"
    assert pages.page_id(URL) == pages.page_id("https://example.org/posts/agents?utm_medium=rss")


def test_a_page_kept_twice_is_one_entry_with_its_snapshot():
    a = pages.save_page("u1", url=URL, html=HTML, kind="blog")
    b = pages.save_page("u1", url=URL + "#memory", text="a shorter text sent later")
    assert a["paper_id"] == b["paper_id"] and a["paper_id"].startswith("w_")
    p = library.paper(a["paper_id"])["paper"]
    assert (p.kind, p.title, p.authors, p.year, p.site, p.is_page) == ("blog", "LLM Powered Agents", ["Ada Kowalski"], 2023, "Lil Log", True)
    snap = pages.snapshot(a["paper_id"])
    assert "planning is its first part" in snap["text"]           # the fuller snapshot is kept
    assert pages.texts(a["paper_id"]) == [snap["text"]]
    assert library.has_item("", a["paper_id"], owner="u1")
    with pytest.raises(ValueError):
        pages.save_page("u1", url="obsidian://open?file=x")


def test_any_link_is_kept_as_a_page_and_read_in_the_reader(client):
    r = client.post("/api/library/papers", json={"text": URL}).json()
    assert r["added"] and r["page"] and r["paper_id"].startswith("w_")
    row = next(x for x in client.get("/api/library").json()["papers"] if x["paper_id"] == r["paper_id"])
    assert (row["kind"], row["site"], row["mine"]) == ("page", "Lil Log", True)
    v = client.get(f"/api/reader/papers/{r['paper_id']}", params={"space": "global"}).json()
    assert v["page"] == {"url": URL} and v["pdf"] == {"available": False, "openable": False}
    snap = client.get(f"/api/reader/papers/{r['paper_id']}/page").json()
    assert snap["url"] == URL and "<h1>LLM Powered Agents</h1>" in snap["html"]
    # notes on a page are anchored on its words
    n = client.post(f"/api/reader/papers/{r['paper_id']}/notes", json={
        "space": "global", "text": "planning is its first part", "comment": "And reflection?", "page_index": 0, "rects": [],
        "sort_index": "web|00000090"}).json()
    assert n["comment"] == "And reflection?" and n["rects"] == []
    assert client.post("/api/library/papers", json={"text": "https://nowhere.invalid/x"}).status_code == 400
    assert client.get("/api/reader/papers/Behrens12/page").status_code in (400, 404)


def test_a_post_saved_from_the_feed_is_kept_as_a_page(client):
    from aurora.feed.store import feed_store
    item, _ = feed_store.add_item("local", {"ukey": URL, "kind": "blog", "src": "rss", "source": "Lil Log · RSS",
                                            "url": URL, "title": "LLM Powered Agents", "summary": "Agents, planned."})
    r = client.post(f"/api/feed/items/{item['item_id']}/save", json={}).json()
    assert r["paper_id"] == pages.page_id(URL)
    assert library.has_item("", r["paper_id"], owner="local")
    client.delete(f"/api/feed/items/{item['item_id']}/save")
    assert not library.has_item("", r["paper_id"], owner="local")


def test_pages_saved_before_come_into_the_library_once():
    from aurora.memory.store import mem_store
    src = mem_store.add_source("u2", platform="blog", url=URL, title="Agents")
    mem_store.save_snapshot(src["source_id"], "Building agents with a language model as its core controller.")
    mem_store.update_source(src["source_id"], state="done")
    note = mem_store.add_source("u2", platform="note", url="obsidian://x", title="Mine")
    mem_store.update_source(note["source_id"], state="done")
    assert pages.adopt_saved("u2") == 1
    assert mem_store.source(src["source_id"])["paper_id"] == pages.page_id(URL)
    assert library.has_item("", pages.page_id(URL), owner="u2")
    assert pages.adopt_saved("u2") == 0


def test_the_agents_read_a_page_from_its_snapshot(client):
    from aurora.api.routes import reader
    entry = pages.save_page("local", url=URL, html=HTML)
    got = asyncio.run(reader._texts(library.paper(entry["paper_id"]), ""))
    assert got and "planning is its first part" in got[0]

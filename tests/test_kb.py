"""One reader for everything: pages met anywhere open in it, and what you keep goes to Obsidian or Notion."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from aurora.auth.writes import set_levels
from aurora.literature import kb, pages
from aurora.literature.annotations import annotations
from aurora.memory import vault
from tests.test_pages import HTML


@pytest.fixture()
def client(monkeypatch):
    async def fetch_html(url):
        return (HTML, url) if "example.org" in url else ("", url)
    monkeypatch.setattr(pages, "fetch_html", fetch_html)
    from aurora.api.app import create_app
    return TestClient(create_app())


def _page_with_notes() -> str:
    pid = pages.save_page("local", url="https://example.org/agents", title="LLM agents: an overview", text="x " * 50)["paper_id"]
    annotations.add("local", pid, text="planning is its first part", comment="Planning first, really?", space="user:local")
    a = annotations.add("local", pid, author="Pippin", state="kept", text="Memory is the second part",
                        comment="Compare with your notebook's memory model.", kind="connect", space="user:local")
    annotations.add_reply(a["ann_id"], "local", "Will do.")
    annotations.add("local", pid, author="Skeptic", state="suggested", text="cool concept", comment="Not kept", space="user:local")
    return pid


def test_a_link_met_anywhere_opens_in_the_reader(client):
    r = client.post("/api/reader/pages", json={"url": "https://example.org/agents", "space": "global"})
    assert r.status_code == 200, r.text
    pid = r.json()["paper_id"]
    assert r.json()["space"] == "global"
    page = client.get(f"/api/reader/papers/{pid}/page").json()      # its snapshot is taken as it opens
    assert "planning is its first part" in page["text"]
    assert client.post("/api/reader/pages", json={"url": "not a link"}).status_code == 400


def test_the_note_holds_what_was_kept_and_nothing_else():
    pid = _page_with_notes()
    md = kb.note("local", pid)["markdown"]
    assert "## My notes" in md and "> planning is its first part\n\nPlanning first, really?" in md
    assert "**Pippin · connect:** Compare" in md and "- **You:** Will do." in md
    assert "Not kept" not in md


def test_obsidian_follows_its_switch_and_keeps_what_you_wrote(client, tmp_path):
    pid = _page_with_notes()
    v = tmp_path / "vault"
    v.mkdir()
    vault.set_settings("local", str(v), [], [], False)
    assert client.post(f"/api/reader/papers/{pid}/kb", json={"target": "obsidian"}).status_code == 403   # off
    assert "markdown" in client.get(f"/api/reader/papers/{pid}/kb").json()                                # to copy
    set_levels("local", {"obsidian.create": "ask"})
    r = client.post(f"/api/reader/papers/{pid}/kb", json={"target": "obsidian"})
    assert r.status_code == 200, r.text
    f = v / r.json()["where"]
    assert f.parent.name == "Pippin" and "Planning first" in f.read_text()
    f.write_text("my heading\n" + f.read_text() + "\nmy own thoughts\n")
    annotations.add("local", pid, text="short and long term", comment="Two kinds of memory.", space="user:local")
    client.post(f"/api/reader/papers/{pid}/kb", json={"target": "obsidian"})
    text = f.read_text()
    assert text.startswith("my heading") and text.rstrip().endswith("my own thoughts") and "Two kinds of memory." in text
    assert client.get(f"/api/reader/papers/{pid}/kb").json()["sent"]["obsidian"]["ref"] == r.json()["where"]


class FakeNotion:
    def __init__(self):
        self.pages: dict[str, list] = {}
        self.calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer secret_test_token" and req.headers["Notion-Version"]
        path, m = req.url.path, req.method
        self.calls.append(f"{m} {path}")
        if m == "GET" and path == "/v1/pages/" + "a" * 32:
            return httpx.Response(200, json={"id": "a" * 32, "properties": {"title": {"type": "title", "title": [{"plain_text": "Reading"}]}}})
        if m == "POST" and path == "/v1/pages":
            body = json.loads(req.content)
            assert body["parent"] == {"page_id": "a" * 32}
            self.pages["b" * 32] = body["children"]
            return httpx.Response(200, json={"id": "b" * 32})
        if m == "GET" and path == "/v1/pages/" + "b" * 32:
            return httpx.Response(200, json={"id": "b" * 32, "archived": False})
        if m == "GET" and path.endswith("/children"):
            return httpx.Response(200, json={"results": [{"id": f"blk{i}"} for i in range(len(self.pages["b" * 32]))], "has_more": False})
        if m == "DELETE":
            self.pages["b" * 32].pop()
            return httpx.Response(200, json={})
        if m == "PATCH" and path.endswith("/children"):
            self.pages["b" * 32] += json.loads(req.content)["children"]
            return httpx.Response(200, json={})
        if m == "PATCH":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": "not found"})


def test_notion_page_made_once_then_replaced(client, monkeypatch):
    fake = FakeNotion()
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(fake)))
    pid = _page_with_notes()
    r = client.put("/api/reader/kb/notion", json={"token": "secret_test_token",
                                                  "page": "https://www.notion.so/Reading-" + "a" * 32})
    assert r.status_code == 200, r.text
    assert r.json()["notion"]["ready"] and r.json()["notion"]["where"] == "Reading"
    set_levels("local", {"notion.write": "ask"})
    r = client.post(f"/api/reader/papers/{pid}/kb", json={"target": "notion"})
    assert r.status_code == 200, r.text
    first = len(fake.pages["b" * 32])
    assert any(b["type"] == "quote" for b in fake.pages["b" * 32])
    annotations.add("local", pid, text="short and long term", comment="Two kinds of memory.", space="user:local")
    client.post(f"/api/reader/papers/{pid}/kb", json={"target": "notion"})
    assert sum(c == "POST /v1/pages" for c in fake.calls) == 1          # the same page, its content replaced
    assert len(fake.pages["b" * 32]) > first
    assert client.get("/api/reader/kb").json()["notion_settings"]["token"] is True


def test_notion_says_what_to_do_when_the_page_is_not_shared(client, monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(404, json={"message": "Could not find page"}))))
    r = client.put("/api/reader/kb/notion", json={"token": "secret_test_token", "page": "c" * 32})
    assert r.status_code == 400 and "Connections" in r.json()["detail"]

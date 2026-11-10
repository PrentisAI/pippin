"""The briefing: arXiv, HF and X as one pool, a paper seen in several places as one item, chosen and written up."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from aurora.brainstorm import agents as ag
from aurora.feed import brief
from aurora.feed import service as svc
from aurora.feed.sources import paper_ids_in
from aurora.feed.store import feed_store, merge_signals
from tests.test_feed import _project, client, sources  # noqa: F401  (fixtures)


def _paper(aid, title, **signals):
    return {"ukey": f"arxiv:{aid}", "kind": "paper", "src": "arxiv", "title": title, "url": f"https://arxiv.org/abs/{aid}",
            "summary": f"{title}. We study it carefully and find something.", "signals": signals}


def test_one_paper_seen_in_three_places_is_one_item_with_its_signals():
    a, new = feed_store.add_item("u_sig", _paper("2611.00001", "Agents that plan", arxiv="cs.AI"))
    assert new
    _, new = feed_store.add_item("u_sig", _paper("2611.00001", "Agents that plan", hf=40))
    assert not new
    feed_store.add_item("u_sig", {"ukey": "arxiv:2611.00001", "signals": {"x": [{"who": "Ada", "url": "https://x.com/a/status/1"}]}})
    feed_store.add_item("u_sig", {"ukey": "arxiv:2611.00001", "signals": {"x": [{"who": "Ada", "url": "https://x.com/a/status/1"},
                                                                              {"who": "Ben", "url": "https://x.com/b/status/2"}]}})
    row = feed_store.item(a["item_id"])
    assert row["signals"]["arxiv"] == "cs.AI" and row["signals"]["hf"] == 40
    assert [m["who"] for m in row["signals"]["x"]] == ["Ada", "Ben"]
    assert row["touched_at"] > 0 and row in feed_store.news("u_sig", time.time() - 5)
    assert merge_signals({"hf": 50}, {"hf": 3}) == {"hf": 50}
    assert brief.signals_line(row, "zh") == "arXiv cs.AI · HF 40 赞 · X 上：Ada、Ben"


def test_paper_links_in_posts():
    assert paper_ids_in(["https://huggingface.co/papers/2611.09124"]) == ("2611.09124", "")
    assert paper_ids_in(["https://www.alphaxiv.org/abs/2611.05791v2"]) == ("2611.05791", "")
    assert paper_ids_in(["https://arxiv.org/pdf/2611.10404v1"]) == ("2611.10404", "")
    assert paper_ids_in(["https://example.com/blog"]) == ("", "")


def test_names_match_strictly():
    assert svc.same_author("Maya Okafor", "M. Okafor") and svc.same_author("Maya J. Okafor", "Maya Okafor")
    assert not svc.same_author("Yu Su", "Yulin Su") and not svc.same_author("Chidi Okafor", "Maya Okafor")
    assert not svc.same_author("Yu Su", "Tongming Su") and svc.same_author("Yu Su", "Y. Su")


def test_x_posts_read_by_the_extension_become_paper_mentions(client, monkeypatch):  # noqa: F811
    src = svc.sources()

    async def resolve(urls):
        return ["https://arxiv.org/abs/2611.11929" if u.startswith("https://t.co/") else u for u in urls]

    async def arxiv_ids(ids):
        return [_paper(i, "Can LLM Agents Organize Their Own Memory?", arxiv="arXiv") for i in ids]
    monkeypatch.setattr(src, "resolve", resolve)
    monkeypatch.setattr(src, "arxiv_ids", arxiv_ids)
    assert client.get("/api/feed/x/due").json() == [{"url": "https://x.com/home", "platform": "x"}]
    posts = [{"id": "1", "url": "https://x.com/ada/status/1", "author": "Ada Lovelace",
              "text": "Agents that write their own memory as plain documents. Worth a read.", "links": ["https://t.co/abc"]},
             {"id": "2", "url": "https://x.com/ben/status/2", "author": "Ben", "text": "Lunch was great today, honestly the best",
              "links": []}]
    r = client.post("/api/feed/x/items", json={"page": "https://x.com/home", "items": posts}).json()
    assert r == {"posts": 2, "papers": 1}
    paper = next(i for i in feed_store.items("local", 0) if i["ukey"] == "arxiv:2611.11929")
    assert paper["signals"]["x"][0]["who"] == "Ada Lovelace" and "plain documents" in paper["signals"]["x"][0]["text"]
    assert client.get("/api/feed/x/due").json() == []                 # read just now
    assert client.get("/api/feed/following").json()["x_read"]["posts"] == 2
    # the same paper named again by someone else: one item, two voices
    posts[1] = {**posts[1], "id": "3", "url": "https://x.com/cy/status/3", "author": "Cy", "text": "see https://arxiv.org/abs/2611.11929 !!!"}
    asyncio.run(svc.take_x(svc_user(), posts))
    paper = feed_store.item(paper["item_id"])
    assert {m["who"] for m in paper["signals"]["x"]} == {"Ada Lovelace", "Cy"}


def svc_user():
    from aurora.auth.permissions import LOCAL_USER
    return LOCAL_USER


def test_settings_for_sources_x_pages_and_language(client):  # noqa: F811
    assert client.put("/api/feed/settings", json={"sources": ["arxiv", "tiktok"]}).status_code == 400
    assert client.put("/api/feed/settings", json={"x_pages": ["https://example.com/feed"]}).status_code == 400
    assert client.put("/api/feed/settings", json={"digest": {"lang": "fr"}}).status_code == 400
    f = client.put("/api/feed/settings", json={"sources": ["x", "arxiv"], "x_pages": ["https://x.com/i/lists/123"],
                                               "digest": {"lang": "zh"}}).json()
    assert f["sources"] == ["arxiv", "x"] and f["x_pages"] == ["https://x.com/i/lists/123"] and f["digest"]["lang"] == "zh"
    assert svc.DEFAULTS["sources"] == ["arxiv", "hf", "x"]


def test_examples_and_the_scripted_demo_are_not_what_you_work_on(client, monkeypatch):  # noqa: F811
    from aurora.api.routes.setup import DEMO_OBJECTIVE
    mine = _project(client)
    demo = client.post("/api/campaigns", json={"name": "Demo", "objective": DEMO_OBJECTIVE})
    ids = {p["campaign_id"] for p in svc.project_profiles(svc_user())}
    assert mine in ids and (demo.status_code >= 400 or demo.json().get("campaign_id") not in ids)
    assert not any(i.startswith("ex_") for i in ids)


class Writer:
    def __init__(self, answer):
        self.answer, self.prompts, self.models = answer, [], []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, *, model, messages, **kw):
        self.prompts.append(messages[0]["content"])
        self.models.append(model)
        body = self.answer(messages[0]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body, ensure_ascii=False)))])


@pytest.fixture()
def pool_of_three():
    uid = "local"
    ids = []
    for aid, title, sig in (("2611.10001", "Agents organise their own memory", {"arxiv": "cs.AI", "x": [{"who": "Ada", "url": "https://x.com/a/status/9", "text": "great"}]}),
                            ("2611.10002", "A world model infrastructure", {"hf": 34}),
                            ("2611.10003", "Gaussian splats for rooms", {"arxiv": "cs.CV"})):
        row, _ = feed_store.add_item(uid, _paper(aid, title, **sig))
        ids.append(row["item_id"])
    return ids


def test_the_model_chooses_groups_and_writes_each_with_its_links(client, monkeypatch, pool_of_three):  # noqa: F811
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    a, b, c = pool_of_three

    def answer(prompt):
        return {"headline": "两篇值得看。", "sections": [
            {"title": "Agent 记忆", "items": [{"id": a, "gist": "让 agent 用普通文档组织自己的记忆。", "why": "和你保存的记忆基准直接相关。"}]},
            {"title": "热门", "items": [{"id": b, "gist": "占位", "why": "HF 34 赞，近期讨论点。"},
                                       {"id": "f_made_up", "gist": "x", "why": "y"}]}]}
    w = Writer(answer)
    monkeypatch.setattr(ag, "client_factory", lambda access: w)
    client.put("/api/feed/settings", json={"topics": ["cs.AI"], "keywords": ["agent memory"], "digest": {"lang": "zh"}})
    got = asyncio.run(brief.compose(svc_user(), svc.settings("local"), time.time() - 600, chat=True, base="https://pippin.example"))
    title, text, numbered = got
    assert title.startswith("Pippin 简报 · ") and numbered == [a, b]
    assert "【Agent 记忆】" in text and "1. Agents organise their own memory" in text and "和你保存的记忆基准直接相关。" in text
    assert "X 上：Ada" in text and "https://x.com/a/status/9" in text and f"https://pippin.example/?feed={a}" in text
    assert "https://huggingface.co/papers/2611.10002" in text and "占位" not in text      # a garbled gist: the abstract
    assert "回复「读 2」" in text and "Gaussian splats" not in text
    assert w.models == [brief.MODEL] and "agent memory" in w.prompts[0] and "Simplified Chinese" in w.prompts[0]


def test_nothing_worth_it_means_no_briefing_and_no_model_means_the_most_talked_about(client, monkeypatch, pool_of_three):  # noqa: F811
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    monkeypatch.setattr(ag, "client_factory", lambda access: Writer(lambda p: {"headline": "", "sections": []}))
    assert asyncio.run(brief.compose(svc_user(), svc.settings("local"), time.time() - 600, chat=False, base="")) is None
    monkeypatch.delenv("OPENROUTER_API_KEY")
    title, text, numbered = asyncio.run(brief.compose(svc_user(), svc.settings("local"), time.time() - 600, chat=False, base=""))
    assert title.startswith("Pippin briefing · ") and numbered[-1] == pool_of_three[2]   # seen nowhere else: last

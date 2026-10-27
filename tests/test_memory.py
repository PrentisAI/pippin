"""The shared memory: saving things into idea units (merged across sources and languages, contradictions linked),
what surfaces for what the researcher is doing, asking instead of summarising, the week, and write permissions."""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm import agents as ag
from aurora.memory import ingest
from aurora.review import llm
from tests.test_brainstorm import _reply

ZH = "知乎上两位工程师在单卡上复现，发现稀疏注意力的加速在 batch=1 时几乎消失，主要来自大 batch 下的访存摊薄。"
EN = "Thread: in our reproduction, the sparse attention speedup vanishes at batch size 1; it comes from amortised memory reads."
AUTHOR = "Author reply: the v2 of the paper still shows a sparse attention speedup for single requests, smaller but real."
COLLAPSE = "Blog: model collapse only appears when synthetic data replaces real data instead of accumulating with it."

TOPICS = [("batch", "sparse", "稀疏", "注意力", "attention", "speedup", "加速", "single"),
          ("collapse", "塌缩", "synthetic", "合成")]


def fake_vec(text):
    t = text.lower()
    v = [float(sum(w in t for w in topic)) for topic in TOPICS] + [0.05]
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


class FakeModel:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.embeddings = SimpleNamespace(create=self.embed)
        self.tasks = []

    async def embed(self, *, model, input):
        return SimpleNamespace(data=[SimpleNamespace(embedding=fake_vec(t)) for t in input])

    async def create(self, *, model, messages, **kw):
        prompt = messages[-1]["content"]
        task = prompt.split("\n", 1)[0].removeprefix("TASK: ")
        self.tasks.append(task)
        if task == "memory_claims":
            if ZH in prompt:
                claims = [{"claim": "稀疏注意力的加速在 batch=1 时几乎消失", "gist": "两位工程师单卡复现。", "kind": "finding",
                           "quote": "稀疏注意力的加速在 batch=1 时几乎消失", "stance": "asserts"},
                          {"claim": "made up", "gist": "", "kind": "finding", "quote": "words the post never says",
                           "stance": "asserts"}]
                return _reply(json.dumps({"lang": "zh", "claims": claims}))
            if EN in prompt:
                return _reply(json.dumps({"lang": "en", "claims": [{"claim": "Sparse attention speedup vanishes at batch size 1",
                    "gist": "A reproduction.", "kind": "finding", "quote": "the sparse attention speedup vanishes at batch size 1",
                    "stance": "asserts"}]}))
            if AUTHOR in prompt:
                return _reply(json.dumps({"lang": "en", "claims": [{"claim": "Sparse attention still speeds up single requests in v2",
                    "gist": "The authors' reply.", "kind": "finding",
                    "quote": "the v2 of the paper still shows a sparse attention speedup for single requests", "stance": "asserts"}]}))
            return _reply(json.dumps({"lang": "en", "claims": [{"claim": "Collapse needs replacement, not accumulation",
                "gist": "A survey of results.", "kind": "opinion",
                "quote": "model collapse only appears when synthetic data replaces real data", "stance": "asserts"}]}))
        if task == "memory_align":
            ids = [line.split("]")[0].strip(" [") for line in prompt.splitlines() if line.strip().startswith("[u_")]
            if "vanishes" in prompt.split("Candidates:")[0]:
                return _reply(json.dumps({"verdicts": [{"n": 1, "same": ids[0], "contradicts": [], "refines": []}]}))
            if "still speeds up" in prompt:
                return _reply(json.dumps({"verdicts": [{"n": 1, "same": None, "contradicts": ids[:1], "refines": [],
                                                        "why": "One says the speedup vanishes, the other that it remains."}]}))
            return _reply(json.dumps({"verdicts": [{"n": 1, "same": None, "contradicts": [], "refines": []}]}))
        if task == "memory_surface":
            ids = [line.split("]")[0].strip(" [") for line in prompt.splitlines() if line.startswith("[u_")]
            return _reply(json.dumps({"units": [{"id": i, "relevance": 0.9, "new": True,
                                                 "stance": "challenges" if n == 0 else "supports",
                                                 "why": "It bears on the claim in section 3."} for n, i in enumerate(ids)]}))
        if task == "memory_ask":
            return _reply(json.dumps({"question": "One says the speedup vanishes at batch 1, the authors say it remains. "
                                                  "Which setting is yours, and which side do you lean to?"}))
        if task == "memory_synthesis":
            return _reply(json.dumps({"text": "The speedup is contested at batch 1 [U1] [U2]."}))
        return _reply("{}")


@pytest.fixture()
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    monkeypatch.setattr(llm, "client_factory", lambda access: fake)
    monkeypatch.setattr(llm, "RETRY_WAIT", 0)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    return fake


@pytest.fixture()
def client(model):
    from aurora.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


def save(c, **body):
    s = c.post("/api/memory/capture", json=body).json()
    end = time.time() + 20
    while time.time() < end:
        s = c.get(f"/api/memory/sources/{s['source_id']}").json()
        if s["state"] != "pending":
            return s
        time.sleep(0.05)
    raise AssertionError("saving did not finish")


def test_saving_merges_claims_across_sources_and_languages_and_links_contradictions(client, model):
    q = client.post("/api/memory/questions", json={"text": "稀疏注意力在长上下文下真的省算力吗？"}).json()
    assert q["label"] == "Q1"
    s1 = save(client, url="https://www.zhihu.com/question/1/answer/2", text=ZH, title="稀疏注意力复现")
    assert s1["state"] == "done" and s1["platform"] == "zhihu"
    (u1,) = s1["result"]["new"]                      # the made-up quote is not evidence: one claim only
    assert s1["result"]["questions"] == [q["question_id"]]
    # the same claim in English, from X: one unit, two sources
    s2 = save(client, url="https://x.com/someone/status/1", text=EN)
    assert s2["result"]["merged"] == [u1] and not s2["result"]["new"]
    assert "now 2 sources" in s2["summary"] and "bears on Q1" in s2["summary"]
    # the authors say the opposite: a unit of its own, linked as a contradiction
    s3 = save(client, url="https://x.com/author/status/2", text=AUTHOR)
    (u3,) = s3["result"]["new"]
    assert s3["result"]["conflicts"] == [[u3, u1]]
    unit = client.get(f"/api/memory/units/{u1}").json()
    assert [(e["platform"], e["lang"] or "?") for e in unit["evidence"]] == [("zhihu", "zh"), ("x", "en")]
    assert unit["links"][0]["rel"] == "contradicts" and unit["links"][0]["unit_id"] == u3
    assert unit["questions"][0]["label"] == "Q1"
    assert "batch=1" in client.get(f"/api/memory/sources/{s1['source_id']}/snapshot").json()["text"]
    # saved again: already there
    assert save(client, text=ZH)["result"]["duplicate_of"] == s1["source_id"]


def test_what_surfaces_for_what_you_are_reading(client, model):
    save(client, url="https://www.zhihu.com/q/1", text=ZH)
    save(client, url="https://x.com/author/status/2", text=AUTHOR)
    s = save(client, url="https://blog.example.com/collapse", text=COLLAPSE)
    collapse = s["result"]["new"][0]
    assert client.get("/api/memory/surface?ctx=current").status_code == 404      # nothing open yet
    client.post("/api/memory/context", json={"app": "Preview", "title": "sparse-attn-long-context.pdf", "page": "3",
                                             "selection": "significant wall-clock speedups at long context",
                                             "text": "Section 3: sparse attention speedup at long context."})
    r = client.get("/api/memory/surface?ctx=current").json()
    assert r["context"]["chip"] == "Reading" and "page 3" in r["context"]["sub"]
    assert len(r["surface"]) == 2 and r["conflicts"] == 1
    first = r["surface"][0]
    assert first["stance"] == "challenges" and first["clash_with"] and first["why"] == "It bears on the claim in section 3."
    assert [u["unit_id"] for u in r["sunk"]] == [collapse]
    assert "Unrelated to this context" in r["sunk"][0]["why"]
    # the researcher lets one sink: it stays down, and says why
    client.post(f"/api/memory/units/{first['unit_id']}/mark", json={"kind": "sunk"})
    r = client.get("/api/memory/surface?ctx=current").json()
    sunk = next(u for u in r["sunk"] if u["unit_id"] == first["unit_id"])
    assert sunk["sunk"] and "You let it sink" in sunk["why"] and len(r["surface"]) == 1
    client.post(f"/api/memory/units/{first['unit_id']}/mark", json={"kind": "raised"})
    # a question to them, from the conflict, instead of a summary; their answer comes back with the units
    a = client.post("/api/memory/ask", json={"ctx": "current"}).json()
    assert "which side" in a["question"] and len(a["basis"]) == 2
    client.post(f"/api/memory/asks/{a['ask_id']}/answer", json={"text": "Mine is batch 1, so I doubt it."})
    client.post(f"/api/memory/units/{first['unit_id']}/mark", json={"kind": "moved"})
    r = client.get("/api/memory/surface?ctx=current").json()
    again = next(u for u in r["surface"] if u["unit_id"] == first["unit_id"])
    assert again["answers"][0]["answer"] == "Mine is batch 1, so I doubt it." and again["moved"]
    # the week
    rep = client.get("/api/memory/report").json()
    assert rep["sources"] == 3 and rep["new_units"] == 3 and len(rep["conflicts"]) == 1
    assert rep["answers"][0]["answer"].startswith("Mine is batch 1") and rep["moved"][0]["unit_id"] == first["unit_id"]


def test_a_merge_can_be_undone_and_questions_come_from_the_mentor(client, model):
    s1 = save(client, text=ZH)
    save(client, text=EN)
    u1 = s1["result"]["new"][0]
    ev = client.get(f"/api/memory/units/{u1}").json()["evidence"]
    new = client.post(f"/api/memory/evidence/{ev[1]['evidence_id']}/split").json()
    assert len(new["evidence"]) == 1 and len(client.get(f"/api/memory/units/{u1}").json()["evidence"]) == 1
    # a research question settled with the Mentor is a memory question
    from aurora.brainstorm import mentor
    from aurora.brainstorm.store import bs_store
    ch = bs_store.new_chat("user:local", "local", "Robots")
    g = mentor.default_guide()
    g["rq"] = {"state": "settled", "question": "Do robots need time in memory?"}
    bs_store.set_guide(ch["chat_id"], g)
    qs = client.get("/api/memory/questions").json()
    assert [(q["label"], q["origin"]) for q in qs] == [("Q1", "mentor")]
    assert client.get(f"/api/memory/questions/{qs[0]['question_id']}/synthesis").json()["question"].startswith("Do robots")


def test_search_finds_units_by_meaning_and_by_word(client, model):
    save(client, text=ZH)
    save(client, text=COLLAPSE)
    hits = client.get("/api/memory/search", params={"q": "synthetic data collapse"}).json()
    assert hits[0]["claim"] == "Collapse needs replacement, not accumulation"


def test_writes_are_off_until_allowed(client):
    w = client.get("/api/me/writes").json()
    assert set(w["levels"].values()) == {"off"} and "zotero.save" in w["writes"]
    from aurora.auth import writes
    assert not writes.allowed("local", "zotero.save")
    w = client.put("/api/me/writes", json={"levels": {"zotero.save": "ask"}}).json()
    assert w["levels"]["zotero.save"] == "ask"
    assert writes.allowed("local", "zotero.save") and not writes.allowed("local", "zotero.save", background=True)
    assert client.put("/api/me/writes", json={"levels": {"manuscript.text": "auto"}}).status_code == 400


def test_mock_embeddings_are_stable():
    assert ingest._mock_vec("sparse attention") == ingest._mock_vec("sparse attention")
    assert ingest.relevance(0.2) == 0 and ingest.relevance(0.9) == 1


def test_two_units_the_memory_missed_can_be_merged(client, model):
    a = save(client, text=ZH)["result"]["new"][0]
    b = save(client, text=COLLAPSE)["result"]["new"][0]
    client.post(f"/api/memory/units/{a}/mark", json={"kind": "moved"})
    u = client.post(f"/api/memory/units/{a}/merge", json={"into": b}).json()
    assert u["unit_id"] == b and len(u["evidence"]) == 2 and u["moved"]
    assert client.get(f"/api/memory/units/{a}").status_code == 404


# ---------------------------------------------------------------------------
# Phase two: what the entry points use


def test_followed_pages_come_in_once_each(client, model):
    f = client.post("/api/memory/follows", json={"url": "https://x.com/someone", "label": "@someone"}).json()
    assert (f["platform"], f["via"]) == ("x", "extension")
    assert client.post("/api/memory/follows", json={"url": "https://www.reddit.com/r/LocalLLaMA/"}).json()["via"] == "server"
    assert [d["follow_id"] for d in client.get("/api/memory/follows/due?via=extension").json()] == [f["follow_id"]]
    items = [{"id": "111", "url": "https://x.com/someone/status/111", "text": EN},
             {"id": "112", "url": "https://x.com/someone/status/112", "text": "hi"}]          # too short to keep
    assert client.post(f"/api/memory/follows/{f['follow_id']}/items", json={"items": items}).json()["taken"] == 1
    end = time.time() + 10
    while time.time() < end and client.get("/api/memory/sources").json()[0]["state"] == "pending":
        time.sleep(0.05)
    s = client.get("/api/memory/sources").json()[0]
    assert s["via"] == "feed" and s["platform"] == "x" and s["state"] == "done"
    # seen once: not again, and not due until its time comes
    assert client.post(f"/api/memory/follows/{f['follow_id']}/items", json={"items": items}).json()["taken"] == 0
    assert client.get("/api/memory/follows/due?via=extension").json() == []
    d = client.get("/api/memory/digest").json()
    assert d["from_feed"] == 1 and d["groups"][0]["new"][0]["via"] == "feed"
    client.patch(f"/api/memory/follows/{f['follow_id']}", json={"paused": True})
    assert client.get("/api/memory/follows").json()[0]["paused"] == 1


def test_reddit_and_feeds_are_read_here():
    from aurora.memory import follow
    data = {"data": {"children": [{"data": {"name": "t3_a", "title": "Sparse attention at batch 1", "selftext": "No gain.",
                                            "permalink": "/r/LocalLLaMA/comments/a/x/", "author": "u1"}}]}}
    (post,) = follow.reddit_items(data)
    assert post["id"] == "t3_a" and post["url"].startswith("https://www.reddit.com/r/LocalLLaMA")
    assert follow._reddit_json("https://www.reddit.com/r/LocalLLaMA/") == "https://www.reddit.com/r/LocalLLaMA/new.json?limit=25"
    rss = "<rss><channel><item><title>A post</title><link>https://b.example/1</link><description>&lt;p&gt;Some text here&lt;/p&gt;</description></item></channel></rss>"
    (it,) = follow.feed_items(rss)
    assert it["url"] == "https://b.example/1" and it["text"] == "Some text here"
    atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>e1</id><title>T</title><link href="https://a.example/e1"/><summary>Words</summary></entry></feed>'
    assert follow.feed_items(atom)[0]["url"] == "https://a.example/e1"


def test_zotero_is_not_written_unless_allowed(client, model):
    from aurora.literature.library import library
    from aurora.literature.records import Paper
    p = library.upsert_paper(Paper(title="Sparse attention at scale", authors=["Ada Lovelace"], year=2024, doi="10.1/xyz"))
    r = client.post("/api/memory/zotero", json={"paper_id": p["paper_id"]}).json()
    assert r["saved"] is False and "off" in r["reason"] and "@" in r["bibtex"] and r["doi"] == "10.1/xyz"


def test_a_file_dropped_is_saved(client, model):
    r = client.post("/api/memory/capture/file?name=note.txt", content=ZH.encode())
    s = r.json()
    end = time.time() + 10
    while time.time() < end and s["state"] == "pending":
        s = client.get(f"/api/memory/sources/{s['source_id']}").json()
        time.sleep(0.05)
    assert s["state"] == "done" and s["title"] == "note.txt" and s["result"]["new"]
    assert client.post("/api/memory/capture/file?name=x.bin", content=b"\xff\xfe\x00").status_code == 400


def test_devices_connect_with_a_token_of_their_own(client):
    from aurora.auth.store import auth_store
    uid = auth_store.add_user("ada@lab.example", "Ada", "correct horse battery", "admin")["user_id"]
    token, device = auth_store.create_device_token(uid, "Chrome on my Mac")
    assert auth_store.user_for_session(token).user_id == uid
    assert auth_store.devices(uid)[0]["last_used"] is not None
    assert auth_store.revoke_device(uid, device["device_id"]) and auth_store.user_for_session(token) is None

"""Ideation: who leads (the researcher or the agents), friction that adapts, and tangents that branch off and return."""

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm import agents as ag
from aurora.brainstorm import conductor, lead
from aurora.brainstorm.store import bs_store
from tests.test_brainstorm import _call, _reply


class LeadModel:
    """Methods leads with drafts; the Skeptic questions; anyone else says ok; a tangent's summary is written."""

    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, *, model, messages, tools=None, **kw):
        self.calls.append(messages)
        if not tools:
            if "went off on a tangent" in messages[0]["content"]:
                return _reply("On the side I found that Zn only matters at the steps.")
            return _reply("Librarian")
        system = messages[0]["content"]
        asked = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        done = [m.get("tool_call_id") for m in messages if m["role"] == "tool"]
        if "the Methods in a research" in system and "you lead this conversation" in asked:
            if "call_draft_versions" not in done:
                return _reply(calls=[_call("draft_versions", drafts=["Test Zn at steps with DFT.", "Compare Cu(211) and Cu(111)."])])
            return _reply("We agree Zn matters; open is where. Next: test the site. Which draft do you take, 1 or 2?")
        if "the Skeptic in a research" in system:
            return _reply("What would show it is the steps and not the interface?")
        return _reply("ok")


@pytest.fixture()
def model(monkeypatch):
    fake = LeadModel()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    return fake


@pytest.fixture()
def client(model):
    from aurora.api.app import create_app
    return TestClient(create_app())


def settle(c, chat_id, cond=lambda r: True, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/brainstorm/chats/{chat_id}").json()
        if not r["pending"] and cond(r):
            time.sleep(0.15)                    # whatever runs after the answer (the conductor, friction)
            r = c.get(f"/api/brainstorm/chats/{chat_id}").json()
            if not r["pending"]:
                return r
        time.sleep(0.05)
    raise AssertionError("did not settle")


def _new(c):
    return c.post("/api/brainstorm/spaces/global/chats", json={}).json()["chat_id"]


def test_who_leads_switches_by_the_switch_and_by_words(client):
    cid = _new(client)
    assert client.get(f"/api/brainstorm/chats/{cid}").json()["chat"]["lead"] == "me"
    r = client.patch(f"/api/brainstorm/chats/{cid}", json={"lead": "pippin"})
    assert r.status_code == 200 and r.json()["lead"] == "pippin"
    done = settle(client, cid, lambda r: any(m["who"] == "Methods" for m in r["messages"]))
    assert any(m["kind"] == "system" and m["text"].startswith("Pippin leads now") for m in done["messages"])
    # in words, at the start of a message; the rest of it is said as usual
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "I'll lead. What is known about Zn on Cu steps?"})
    done = settle(client, cid, lambda r: r["messages"][-1]["who"] == "Librarian")
    assert done["chat"]["lead"] == "me" and any(m["text"].startswith("You lead now") for m in done["messages"])
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "你来带吧"})
    assert client.get(f"/api/brainstorm/chats/{cid}").json()["chat"]["lead"] == "pippin"
    assert lead.switch_word("我来说一下这个想法")[0] is None           # not a hand-over
    assert [m["data"]["by"] for m in bs_store.moves(chat_id=cid) if m["kind"] == "lead"] == ["switch", "words", "words"]
    assert client.patch(f"/api/brainstorm/chats/{cid}", json={"lead": "nobody"}).status_code == 400


def test_when_pippin_leads_it_proposes_drafts_and_a_choice(client):
    cid = _new(client)
    client.patch(f"/api/brainstorm/chats/{cid}", json={"lead": "pippin"})
    settle(client, cid, lambda r: any(m["who"] == "Methods" and m["text"] for m in r["messages"]))
    r = client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "Zn at Cu steps is the active site."}).json()
    assert r["agents"] == ["Methods"]
    done = settle(client, cid, lambda r: len([m for m in r["messages"] if m["who"] == "Methods" and m["kind"] == "text"]) == 2)
    drafts = [m for m in done["messages"] if (m.get("widget") or {}).get("type") == "drafts"]
    assert drafts and len(drafts[-1]["widget"]["drafts"]) == 2 and drafts[-1]["widget"]["state"] == "waiting"
    answer = [m for m in done["messages"] if m["who"] == "Methods" and m["kind"] == "text"][-1]
    assert answer["text"].endswith("Which draft do you take, 1 or 2?")       # the drafts follow it


SURE = "This will definitely work: Zn obviously sits at the Cu steps and nothing else explains the rate."


def test_friction_rises_when_sure_and_quiets_when_waved_off(client, monkeypatch):
    monkeypatch.setattr(conductor, "ENABLED", True)

    async def nobody(*a, **k):
        return None
    monkeypatch.setattr(conductor, "decide", nobody)
    client.put("/api/brainstorm/settings", json={"activity": "high"})
    cid = _new(client)
    # exploring: the first messages, however sure, are not questioned
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": SURE})
    done = settle(client, cid, lambda r: r["messages"][-1]["who"] == "Librarian")
    assert not any(m["who"] == "Skeptic" for m in done["messages"])
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "I have been reading about methanol synthesis over copper catalysts lately."})
    settle(client, cid, lambda r: r["messages"][-1]["who"] == "Librarian")
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": SURE})
    done = settle(client, cid, lambda r: r["messages"][-1]["who"] == "Skeptic")
    why = [m for m in done["messages"] if m["kind"] == "system" and m["who"] == "Skeptic"]
    assert why and "you sound very sure" in why[-1]["text"].lower()
    challenge = done["messages"][-1]
    assert challenge["widget"]["type"] == "friction" and challenge["widget"]["signals"]["sure"] == 2
    # "not now": no challenge for a while, however sure
    r = client.post(f"/api/brainstorm/messages/{challenge['msg_id']}/act", json={"choice": "later"})
    assert r.status_code == 200 and r.json()["widget"]["state"] == "waved"
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": SURE})
    done = settle(client, cid, lambda r: r["messages"][-1]["who"] == "Librarian")
    moves = [m["data"] for m in bs_store.moves(chat_id=cid) if m["kind"] == "friction"]
    assert [m["fired"] for m in moves] == [False, False, True, False] and moves[-1]["held"] == "waved off"
    assert any(m["kind"] == "friction_waved" for m in bs_store.moves(chat_id=cid))


def test_friction_rules():
    chat = bs_store.new_chat("user:local", "local", "t")
    chat = bs_store.chat(chat["chat_id"])

    def said(*texts):
        return [{"who": "local", "kind": "text", "text": t} for t in texts]
    long = "I have been thinking about how the copper surface binds the intermediates."
    assert lead.decide(said(long, long, SURE), chat, "local", "high", []) is not None
    assert lead.decide(said(long, long, SURE), chat, "local", "moderate", []) is None      # moderate wants more
    assert lead.decide(said(long, long, SURE), chat, "local", "off", []) is None
    assert lead.decide(said(long, "not sure", "maybe"), chat, "local", "high", []) is None  # tired
    assert lead.decide(said(long, long, SURE), chat, "local", "high", ["Skeptic"]) is None
    pip = {**chat, "lead": "pippin"}
    assert lead.decide(said(long, long, SURE), pip, "local", "high", []) is None


def test_a_tangent_sees_its_parent_up_to_where_it_branched_and_returns(client, model):
    cid = _new(client)
    a = bs_store.add_message(cid, "local", "Alpha: the main line about Zn.")
    b = bs_store.add_message(cid, "Librarian", "Beta: two papers on steps.")
    bs_store.add_message(cid, "local", "Gamma: said after the branch point.")
    r = client.post(f"/api/brainstorm/chats/{cid}/branch", json={"msg_id": b["msg_id"]})
    assert r.status_code == 200
    br = r.json()
    assert br["parent"] == cid and br["branch_at"] == b["msg_id"] and br["title"].startswith("↳")
    assert [m["text"][:5] for m in bs_store.context(br["chat_id"])] == ["Alpha", "Beta:"]
    client.post(f"/api/brainstorm/chats/{br['chat_id']}/messages", json={"text": "@Librarian what about the interface?"})
    settle(client, br["chat_id"], lambda r: any(m["who"] == "Librarian" for m in r["messages"]))
    seen = "\n".join(str(m.get("content")) for m in model.calls[-1])
    assert "Alpha" in seen and "Beta" in seen and "Gamma" not in seen
    # the parent links to it, the tangent back
    assert client.get(f"/api/brainstorm/chats/{cid}").json()["branches"][0]["branch_at"] == b["msg_id"]
    view = client.get(f"/api/brainstorm/chats/{br['chat_id']}").json()
    assert view["parent"]["chat_id"] == cid and view["parent"]["at"].startswith("Beta")
    # a tangent of the tangent sees all three levels
    inner = bs_store.messages(br["chat_id"])[0]
    br2 = client.post(f"/api/brainstorm/chats/{br['chat_id']}/branch", json={"msg_id": inner["msg_id"]}).json()
    assert [m["text"][:5] for m in bs_store.context(br2["chat_id"])][:3] == ["Alpha", "Beta:", "@Libr"]
    # back, with what it found (written, then changed by the researcher)
    s = client.post(f"/api/brainstorm/chats/{br['chat_id']}/return/summary").json()
    assert "Zn only matters" in s["text"]
    r = client.post(f"/api/brainstorm/chats/{br['chat_id']}/return", json={"text": s["text"] + " Worth a test."}).json()
    assert r == {"parent": cid, "posted": True}
    last = bs_store.messages(cid)[-1]
    assert last["who"] == "local" and last["text"].endswith("Worth a test.") and last["widget"]["type"] == "from_branch"
    assert client.post(f"/api/brainstorm/chats/{br['chat_id']}/return", json={}).json()["posted"] is False
    assert client.post(f"/api/brainstorm/chats/{cid}/return", json={}).status_code == 400   # not a tangent
    # deleting the parent keeps its tangents, as conversations of their own
    client.delete(f"/api/brainstorm/chats/{cid}")
    assert bs_store.chat(br["chat_id"])["parent"] == ""

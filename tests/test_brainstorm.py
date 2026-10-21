"""Brainstorms: the agents answer and record on the board; private stays private; ideas become proposals."""

import json
import re
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm import agents as ag
from aurora.brainstorm.store import bs_store
from aurora.literature import indexes as indexes_mod
from aurora.literature.library import library
from tests.test_literature import FakeIndexes


def _call(name, **args):
    return SimpleNamespace(id=f"call_{name}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _reply(content="", calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls or []))])


class FakeModel:
    """Scripted agents: each looks at what it was given and acts as its role would."""

    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, *, model, messages, tools=None, **kw):
        self.calls.append(messages)
        system = messages[0]["content"]
        if not tools:                                   # choosing who answers
            return _reply("Librarian")
        board = messages[1]["content"]
        focus = re.search(r"about the idea (c_\w+)", board)
        idea = focus.group(1) if focus else ""
        done = [m.get("tool_call_id") for m in messages if m["role"] == "tool"]
        names = {t["function"]["name"] for t in tools}
        if "You are the Librarian" in system or "the Librarian in a research" in system.split("\n")[0]:
            if "call_search_papers" not in done:
                return _reply(calls=[_call("search_papers", query="methanol synthesis")])
            if "call_add_evidence" not in done:
                return _reply(calls=[_call("add_evidence", card_id=idea, rel="supports", paper_key="Behrens12",
                                           text="Zn decorates the Cu steps.")])
            return _reply("One checked paper supports it: [Behrens12]. I added it as evidence.")
        if "the Skeptic in a research" in system:
            if "ask_to_share" in names and "call_ask_to_share" not in done:
                private = re.search(r"NOT visible to others here\).*?- (c_\w+):", board, re.S)
                return _reply(calls=[_call("ask_to_share", card_id=private.group(1), why="Your idea on steps bears on this.")])
            if idea and "call_add_question" not in done:
                return _reply(calls=[_call("add_question", card_id=idea, text="Is it the steps or the interface?")])
            return _reply("The weak point is the active site. I added a question.")
        if "the Methods in a research" in system:
            if "call_add_next_step" not in done:
                return _reply(calls=[_call("add_next_step", card_id=idea, text="Compute barriers at both sites with DFT.")])
            return _reply("A DFT comparison would settle it. I added it as a next step.")
        return _reply("ok")


@pytest.fixture()
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    return fake


@pytest.fixture()
def client(model):
    from aurora.api.app import create_app
    return TestClient(create_app())


def wait(c, chat_id, timeout=30.0):
    """The agents answer in the background: wait until nobody is still writing."""
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/brainstorm/chats/{chat_id}").json()
        if not r["pending"] and any(m["agent"] for m in r["messages"]):
            return r
        time.sleep(0.05)
    raise AssertionError("the agents did not finish")


def test_the_librarian_finds_evidence_and_records_it(client, model):
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol on Cu"}).json()["campaign_id"]
    chat = client.post(f"/api/brainstorm/spaces/{cid}/chats", json={}).json()
    assert chat["vis"] == "private" and chat["title"] == "New brainstorm"
    idea = client.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "Zn at Cu steps is the active site."}).json()["card"]
    r = client.post(f"/api/brainstorm/chats/{chat['chat_id']}/ask", json={"agent": "Librarian", "card_id": idea["card_id"]}).json()
    assert r["agents"] == ["Librarian"]
    done = wait(client, chat["chat_id"])
    (lib_msg,) = [m for m in done["messages"] if m["agent"]]
    assert lib_msg["steps"] == ["Thinking", "Searching the literature: “methanol synthesis”", "Reading [Behrens12]"]
    assert lib_msg["effects"] == [["Added evidence for", idea["card_id"]]]
    assert "[Behrens12]" in lib_msg["text"]
    space = client.get(f"/api/brainstorm/spaces/{cid}").json()
    (ev,) = [c for c in space["cards"] if c["type"] == "material"]
    assert ev["paper"]["key"] == "Behrens12" and ev["by"] == "Librarian" and ev["vis"] == "private"
    assert {"from_card": ev["card_id"], "to_card": idea["card_id"], "rel": "supports"} in space["links"]
    # the paper is now cited in the project's literature, and the search is kept
    assert "Behrens12" in [p["key"] for p in client.get(f"/api/campaigns/{cid}/literature").json()["papers"]]
    assert library.searches(cid)[0]["context"].startswith("Brainstorm")


def test_who_answers(client, model):
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    chat = client.post(f"/api/brainstorm/spaces/{cid}/chats", json={}).json()
    idea = client.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "Zn at steps."}).json()["card"]
    # @mentions name the agents
    r = client.post(f"/api/brainstorm/chats/{chat['chat_id']}/messages",
                    json={"text": "@Skeptic @Methods what do you think?", "focus": idea["card_id"]}).json()
    assert r["agents"] == ["Skeptic", "Methods"]
    done = wait(client, chat["chat_id"])
    assert [m["who"] for m in done["messages"]] == ["local", "Skeptic", "Methods"]
    assert done["chat"]["title"] == "@Skeptic @Methods what do you think?"         # named after its first message
    kinds = {c["type"] for c in client.get(f"/api/brainstorm/spaces/{cid}").json()["cards"]}
    assert kinds == {"idea", "question"}
    # otherwise the agents decide (moderately); "only when asked" means nobody
    assert client.post(f"/api/brainstorm/chats/{chat['chat_id']}/messages", json={"text": "Hmm, what next?"}).json()["agents"] == ["Librarian"]
    client.put("/api/brainstorm/settings", json={"activity": "off"})
    assert client.post(f"/api/brainstorm/chats/{chat['chat_id']}/messages", json={"text": "thinking aloud"}).json()["agents"] == []
    assert client.get(f"/api/brainstorm/spaces/{cid}").json()["activity"] == "off"


def test_without_a_key_the_agent_says_so(client, model, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    chat = client.post(f"/api/brainstorm/spaces/{cid}/chats", json={}).json()
    client.post(f"/api/brainstorm/chats/{chat['chat_id']}/messages", json={"text": "@Skeptic hi"})
    done = wait(client, chat["chat_id"])
    assert "No API key" in [m for m in done["messages"] if m["agent"]][0]["text"] and model.calls == []


def test_private_stays_private_and_agents_ask_before_using_it(model):
    from aurora.api.app import create_app
    from aurora.auth.store import auth_store
    from tests.test_accounts import PW, uid
    auth_store.add_user("wei@lab.example", "Wei Chen", PW, "admin")
    auth_store.add_user("linda@lab.example", "Linda Fei", PW, "member")
    auth_store.add_user("sara@lab.example", "Sara Novak", PW, "member")
    clients = {}
    for who in ("linda", "sara"):
        clients[who] = TestClient(create_app())
        clients[who].post("/api/auth/login", json={"email": f"{who}@lab.example", "password": PW})
    linda, sara = clients["linda"], clients["sara"]
    cid = linda.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    linda.put(f"/api/campaigns/{cid}/access/members/user:{uid('sara@lab.example')}", json={"role": "commenter"})
    mine = linda.post(f"/api/brainstorm/spaces/{cid}/chats", json={"title": "my thoughts"}).json()
    private_idea = linda.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "Steps matter most."}).json()["card"]
    shared_idea = linda.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "Interface matters.", "vis": "project"}).json()["card"]
    # Sara sees the shared idea, not the private one, not Linda's private brainstorm
    space = sara.get(f"/api/brainstorm/spaces/{cid}").json()
    assert [c["text"] for c in space["cards"]] == ["Interface matters."] and space["chats"] == []
    assert sara.get(f"/api/brainstorm/chats/{mine['chat_id']}").status_code == 404
    assert sara.get(f"/api/brainstorm/spaces/user:{uid('linda@lab.example')}").status_code == 404
    assert sara.patch(f"/api/brainstorm/cards/{private_idea['card_id']}", json={"text": "x"}).status_code == 404
    # Linda shares her brainstorm (and picks one idea); Sara can now read it
    linda.post(f"/api/brainstorm/chats/{mine['chat_id']}/share", json={"what": "all"})
    assert sara.get(f"/api/brainstorm/chats/{mine['chat_id']}").status_code == 200
    # in the shared brainstorm, the Skeptic does not use Linda's private idea: it asks her, privately
    linda.post(f"/api/brainstorm/chats/{mine['chat_id']}/messages", json={"text": "@Skeptic thoughts?", "focus": shared_idea["card_id"]})
    done = wait(linda, mine["chat_id"])
    (ask,) = [m for m in done["messages"] if m["kind"] == "consent"]
    assert ask["consent"] == private_idea["card_id"] and ask["only_for"] == uid("linda@lab.example")
    assert not [m for m in sara.get(f"/api/brainstorm/chats/{mine['chat_id']}").json()["messages"] if m["kind"] == "consent"]
    ctx = model.calls[0][1]["content"]
    assert "NOT visible to others" in ctx and "Steps matter most." in ctx.split("NOT visible")[1]
    assert "Steps matter most." not in ctx.split("NOT visible")[0]
    assert sara.post(f"/api/brainstorm/messages/{ask['msg_id']}/consent", json={"share": True}).status_code == 404
    linda.post(f"/api/brainstorm/messages/{ask['msg_id']}/consent", json={"share": True})
    assert "Steps matter most." in [c["text"] for c in sara.get(f"/api/brainstorm/spaces/{cid}").json()["cards"]]
    # a commenter may write; a viewer may not; nobody edits another's card
    assert sara.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "A question", "type": "question"}).status_code == 200
    assert sara.patch(f"/api/brainstorm/cards/{shared_idea['card_id']}", json={"text": "mine now"}).status_code == 403
    linda.put(f"/api/campaigns/{cid}/access/members/user:{uid('sara@lab.example')}", json={"role": "viewer"})
    assert sara.post(f"/api/brainstorm/chats/{mine['chat_id']}/messages", json={"text": "hi"}).status_code == 403
    # and the agents never touch a card they may not use
    board = ag.Board(space=cid, viewer="x", viewer_name="X", chat=bs_store.chat(mine["chat_id"]), cards=[])
    with pytest.raises(ValueError):
        ag.BoardTools(board, "Skeptic", ag.Turn("m"))._idea(private_idea["card_id"])


def test_an_idea_becomes_a_proposal(client, model):
    cid = client.post("/api/campaigns/quick", json={"prompt": "Methanol"}).json()["campaign_id"]
    chat = client.post(f"/api/brainstorm/spaces/{cid}/chats", json={}).json()
    idea = client.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "Zn at Cu steps is the active site.",
                                                                  "chat_id": chat["chat_id"]}).json()["card"]
    client.post(f"/api/brainstorm/chats/{chat['chat_id']}/ask", json={"agent": "Librarian", "card_id": idea["card_id"]})
    wait(client, chat["chat_id"])
    q = client.post(f"/api/brainstorm/spaces/{cid}/cards", json={"type": "question", "text": "Steps or interface?",
                                                               "links": [{"to": idea["card_id"], "rel": "asks about"}]}).json()["card"]
    client.patch(f"/api/brainstorm/cards/{q['card_id']}", json={"answer": "We test both."})
    r = client.post(f"/api/brainstorm/cards/{idea['card_id']}/promote", json={"ideation": "start", "add_found": "all"}).json()
    hist = client.get(f"/api/campaigns/{cid}/chat/history").json()
    (rec,) = [m for m in hist if m.get("proposal")]
    prop = rec["proposal"]
    assert prop["proposal_id"] == r["proposal_id"] and prop["skip_phases"] == ["ideation", "screening"]
    text = prop["params"]["instruction"]
    assert "Evidence for:" in text and "[Behrens12]" in text and "Steps or interface? (answered: We test both.)" in text
    assert prop["params"]["add_found"] == "all"
    assert "Turned" in client.get(f"/api/brainstorm/chats/{chat['chat_id']}").json()["messages"][-1]["text"]
    # your own space: the idea goes to a project you name
    gidea = client.post("/api/brainstorm/spaces/global/cards", json={"text": "A side idea."}).json()["card"]
    assert client.post(f"/api/brainstorm/cards/{gidea['card_id']}/promote", json={}).status_code == 400
    assert client.post(f"/api/brainstorm/cards/{gidea['card_id']}/promote", json={"campaign_id": cid}).status_code == 200


def test_every_brainstorm_route_has_a_rule():
    from aurora.api.app import create_app
    from aurora.auth.guard import RULES
    from tests.test_accounts import all_routes
    routes = [(m, r.path) for r in all_routes(create_app().routes) if getattr(r, "path", "").startswith("/api/brainstorm")
              for m in getattr(r, "methods", []) if m != "HEAD"]
    assert routes and all(x in RULES for x in routes)

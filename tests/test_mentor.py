"""The Mentor: guided questioning in a brainstorm, the research question written down and confirmed, the plan."""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm import agents as ag
from aurora.brainstorm import mentor
from aurora.literature import indexes as indexes_mod
from aurora.review import llm
from tests.test_brainstorm import _reply
from tests.test_literature import FakeIndexes

SUMMARY = {"question": "Does keeping time in a household robot's memory help it find moved objects?",
           "tightened": "", "self": {"feasible": "Simulated homes are enough.", "interesting": "", "novel": "",
                                      "ethical": "", "relevant": "Robots that tidy up."},
           "focus": ["household robots"], "excluded": ["industrial arms"], "to_confirm": ["which simulator"],
           "formal": {k: {"score": 4, "why": "..."} for k in mentor.FINER}, "quality": []}
CANDIDATES = {"candidates": [{"question": f"Candidate {i}?", "type": "causal",
                              "finer": {k: {"score": 3, "why": "."} for k in mentor.FINER}} for i in range(1, 4)],
              "focus": [], "excluded": [], "to_confirm": []}


class FakeModel:
    """Scripted: who answers, the Mentor's replies (with the protocol's tags), the Skeptic, and the JSON calls."""

    def __init__(self):
        self.mentor_prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, *, model, messages, tools=None, **kw):
        first = messages[0]["content"]
        last = messages[-1]["content"]
        if first.startswith("TASK: rq_summary"):
            return _reply(json.dumps(SUMMARY))
        if first.startswith("TASK: rq_candidates"):
            return _reply(json.dumps(CANDIDATES))
        if first.startswith("TASK: rq_word"):
            reply = first.split("Their reply:", 1)[1]
            return _reply(json.dumps({"action": "revise" if "household" in reply else "confirm" if "yes" in reply
                                      else "other", "pick": None}))
        if not tools:                                   # who answers a message nobody was named in
            return _reply("Mentor" if "not sure" in first else "Librarian")
        if first.startswith("The protocol below is from Academic Research Skills"):
            self.mentor_prompts.append(messages)
            if "not sure" in last:
                return _reply("You want to work on memory for robots. What do you really want to know?\n[Q:CLARIFY]\n"
                              "[LAYER: 1]\n[INTENT: exploratory]")
            if "lose track" in last:
                return _reply("So the robot loses track of objects people move. Obviously, you think time matters. "
                              "What would you predict?\n[INSIGHT: The researcher wants robots to track objects that "
                              "people move, and suspects time is the missing piece]\n[COMMITMENT: time stamps help]\n"
                              "[Q:PROBE]\n[RQ-READY]\n[LAYER: 1]\n[SIGNALS: S1]")
            if "Console: the researcher confirmed" in last:
                return _reply("Good. How would you answer it?\n[INSIGHT: not theirs]\n[LAYER: 2]")
            if "compare memories" in last:
                return _reply("Why that comparison over others?\n[INSIGHT: method | Compare memories with and without "
                              "time in simulated homes]\n[LAYER: 3]")
            if "candidate" in last:
                return _reply("You asked me to propose some myself.\n[SOCRATIC-NON-GENERATION-EXIT: "
                              "explicit_user_request]\nHere are three AI-generated starting points.\n[RQ-CANDIDATES]")
            if "stop" in last:
                return _reply("Here is where you landed: your question and your method.\n[END: user]")
            return _reply("Why does that matter to you?\n[Q:PROBE]\n[LAYER: 1]")
        if "the Skeptic in a research" in first:
            return _reply("Devil's advocate: are moved objects rare enough that time adds nothing?")
        return _reply("ok")


@pytest.fixture()
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    monkeypatch.setattr(llm, "client_factory", lambda access: fake)
    monkeypatch.setattr(llm, "RETRY_WAIT", 0)
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    return fake


@pytest.fixture()
def client(model):
    from aurora.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


def settle(c, chat_id, n, timeout=30.0):
    """Wait until the conversation has at least n messages and nobody is still writing."""
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/brainstorm/chats/{chat_id}").json()
        if len(r["messages"]) >= n and not r["pending"]:
            time.sleep(0.1)
            r2 = c.get(f"/api/brainstorm/chats/{chat_id}").json()
            if len(r2["messages"]) == len(r["messages"]) and not r2["pending"]:
                return r2
        time.sleep(0.05)
    raise AssertionError(f"the conversation did not reach {n} messages")


def say(c, chat_id, text):
    return c.post(f"/api/brainstorm/chats/{chat_id}/messages", json={"text": text}).json()


def test_the_protocol_is_the_ars_socratic_mentor():
    p = mentor.protocol()
    for heading in ("## Role Definition", "## Research-Question Authorship Boundary", "## Intent Detection Layer",
                    "## SCR Protocol", "## 5-Layer Questioning Model", "### Probe Wording", "### Banned Patterns",
                    "## INSIGHT Extraction Mechanism", "## Dialogue Health Indicator", "#### FINER Guiding Questions"):
        assert heading in p, heading
    # the reading probe stays dormant; the adjacent-framing probe is on (its activation gate is left out)
    assert "### Candidate Paper Tracking" not in p and "set to `\"1\"`" not in p
    assert "The adjacent-framing layer is ON" in mentor.system_prompt(None)
    from aurora.review.search import ARS
    assert "socratic_mentor_agent.md" in (ARS / "README.md").read_text()


def test_tags_are_taken_out_of_the_reply():
    text, tags = mentor.take_tags("What do you want to know?\n[Q:CLARIFY]\n[INSIGHT: method | Use sims]\n[LAYER: 2]")
    assert text == "What do you want to know?"
    assert tags == [("Q", "CLARIFY"), ("INSIGHT", "method | Use sims"), ("LAYER", "2")]


def test_a_guided_conversation_from_a_vague_interest_to_a_settled_question(client, model):
    chat = client.post("/api/brainstorm/spaces/global/chats", json={}).json()
    cid = chat["chat_id"]
    # a vague interest: the Mentor takes it, without being named, and asks
    r = say(client, cid, "I want to work on memory for robots but I'm not sure what exactly")
    assert r["agents"] == ["Mentor"]
    done = settle(client, cid, 2)
    reply = done["messages"][-1]
    assert reply["who"] == "Mentor" and reply["text"] == "You want to work on memory for robots. What do you really want to know?"
    assert done["chat"]["guided"] is True
    # the researcher's next message is for the Mentor; what they reach goes in their plan, from their own words
    r = say(client, cid, "Robots lose track of things people move around")
    assert r["agents"] == ["Mentor"]
    done = settle(client, cid, 5)
    mentor_msg = next(m for m in reversed(done["messages"]) if m["who"] == "Mentor" and m["kind"] == "text")
    assert "[" not in mentor_msg["text"] and mentor_msg["effects"] == [["Noted 1 point of yours in your research plan", ""]]
    plan = client.get(f"/api/brainstorm/chats/{cid}/plan").json()
    (ins,) = plan["insights"]
    assert ins["section"] == "question" and ins["origin"] == "you" and ins["quote"].startswith("Robots lose track")
    # the state note tells the Mentor where things stand (never shown); certainty is the researcher's, not its own
    assert "Layer 1" in model.mentor_prompts[-1][-2]["content"]
    assert "sounds certain" not in model.mentor_prompts[-1][-2]["content"]
    # the question converged: written down from the conversation, waiting for their word
    (q,) = [m for m in done["messages"] if (m.get("widget") or {}).get("type") == "question"]
    assert q["widget"]["state"] == "waiting" and q["widget"]["kind"] == "summary"
    assert SUMMARY["question"] in q["text"] and "Feasible: Simulated homes are enough." in q["text"]
    # they say what to change, in the conversation: a new version supersedes it
    say(client, cid, "Make it about household robots only")
    done = settle(client, cid, 7)
    qs = [m for m in done["messages"] if (m.get("widget") or {}).get("type") == "question"]
    assert [m["widget"]["state"] for m in qs] == ["superseded", "waiting"] and qs[-1]["widget"]["version"] == 2
    # they confirm it: on the board as theirs, in the plan, and the Mentor goes on to the method
    r = client.post(f"/api/brainstorm/messages/{qs[-1]['msg_id']}/act", json={"choice": "confirm"}).json()
    assert r["widget"]["state"] == "confirmed"
    done = settle(client, cid, 8)
    space = client.get("/api/brainstorm/spaces/global").json()
    (card,) = [c for c in space["cards"] if c["text"] == SUMMARY["question"]]
    assert card["by"] == "local" and done["chat"]["focus"] == card["card_id"]
    assert done["messages"][-1]["text"] == "Good. How would you answer it?" and not done["messages"][-1]["effects"]
    say(client, cid, "I would compare memories with and without time stamps")
    done = settle(client, cid, 11)
    whos = [m["who"] for m in done["messages"]]
    # the method settled (layer 2 passed): the Skeptic comes in as devil's advocate
    assert whos[-2:] == ["Mentor", "Skeptic"] and done["messages"][-1]["text"].startswith("Devil's advocate")
    plan = client.get(f"/api/brainstorm/chats/{cid}/plan").json()
    assert plan["question"]["state"] == "settled"
    assert plan["question"]["origin"] == "you"
    assert {(i["section"], i["origin"]) for i in plan["insights"]} == {("question", "you"), ("method", "you")}
    # the researcher corrects their plan
    method = next(i for i in plan["insights"] if i["section"] == "method")
    client.patch(f"/api/brainstorm/insights/{method['insight_id']}", json={"text": "Compare in real homes too"})
    client.delete(f"/api/brainstorm/insights/{ins['insight_id']}")
    plan = client.get(f"/api/brainstorm/chats/{cid}/plan").json()
    assert [i["text"] for i in plan["insights"] if i["section"] != "question"] == ["Compare in real homes too"]
    # they stop being guided: the question can go to the literature, a map that starts from it
    say(client, cid, "Let's stop here")
    done = settle(client, cid, 14)
    proposal = done["messages"][-1]
    assert proposal["who"] == "Librarian" and proposal["widget"]["action"] == "map_field"
    assert done["chat"]["guided"] is False


def test_candidates_only_when_asked_and_marked_as_the_ais(client, model, monkeypatch):
    chat = client.post("/api/brainstorm/spaces/global/chats", json={}).json()
    cid = chat["chat_id"]
    say(client, cid, "@Mentor please give me some candidate questions yourself")
    done = settle(client, cid, 3)
    reply = next(m for m in done["messages"] if m["who"] == "Mentor" and m["kind"] == "text")
    assert reply["effects"][0][0] == mentor.EXIT and "SOCRATIC" not in reply["text"]
    q = done["messages"][-1]
    assert q["widget"]["kind"] == "candidates" and "AI-generated starting points" in q["text"]
    client.post(f"/api/brainstorm/messages/{q['msg_id']}/act", json={"choice": "take:1"})
    settle(client, cid, 4)
    space = client.get("/api/brainstorm/spaces/global").json()
    (card,) = [c for c in space["cards"] if c["text"] == "Candidate 2?"]
    assert card["by"] == "Mentor"
    plan = client.get(f"/api/brainstorm/chats/{cid}/plan").json()
    assert plan["question"]["origin"] == "ai" and plan["question"]["question"] == "Candidate 2?"
    assert plan["insights"] == []          # nothing of the researcher's own came out of it
    # a map started from this conversation is scoped from the question, without asking again
    from aurora.api.routes import review as rv
    seen = {}
    monkeypatch.setattr(rv, "start_map", lambda *a, **kw: seen.update(kw) or {"map_id": "m_x"})
    from aurora.brainstorm.store import bs_store
    m = bs_store.add_message(cid, "Librarian", "Map it?", kind="action",
                             widget={"type": "proposal", "action": "map_field", "topic": "robot memory", "brief": "",
                                     "size": 30, "calls": 1, "state": "pending"})
    client.post(f"/api/brainstorm/messages/{m['msg_id']}/act", json={"choice": "start"})
    assert seen["question"]["question"] == "Candidate 2?" and seen["question"]["origin"] == "ai"


def test_others_answer_when_named_during_a_guided_conversation(client, model):
    chat = client.post("/api/brainstorm/spaces/global/chats", json={}).json()
    cid = chat["chat_id"]
    say(client, cid, "@Mentor I'm not sure where to start")
    settle(client, cid, 2)
    r = say(client, cid, "@Skeptic what is wrong with this?")
    assert r["agents"] == ["Skeptic"]
    settle(client, cid, 4)
    assert say(client, cid, "Fine, back to it")["agents"] == ["Mentor"]


def test_leaving_the_first_layer_writes_the_question_down(client, model):
    """The protocol's Layer 1 ends when the question can be said in a sentence: if the Mentor moves on without
    asking for it to be written down, it is written down anyway."""
    from aurora.brainstorm.store import bs_store
    chat = client.post("/api/brainstorm/spaces/global/chats", json={}).json()
    cid = chat["chat_id"]
    bs_store.add_message(cid, "local", "Robots lose track")
    mentor.start(cid)
    text, _ = mentor.after_turn(cid, "local", "How would you answer it?\n[LAYER: 2]")
    assert text == "How would you answer it?"
    assert mentor.take_pending(cid) == ["rq"]


def test_label_prefixes_are_taken_off_plain_fields():
    assert llm.unlabel("spec: 尚未指明数据来源") == "尚未指明数据来源"
    assert llm.unlabel("[inf] fits") == "fits" and llm.unlabel("a || b") == "a b"
    assert llm.unlabel("special cases stay") == "special cases stay"

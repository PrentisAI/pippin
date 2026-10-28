"""The conductor: another agent joins a brainstorm unasked when the conversation calls for it, within rules that keep
it quiet (once per turn, a cooldown, never when the researcher seems tired, costly moves only proposed)."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm import agents as ag
from aurora.brainstorm import conductor
from aurora.brainstorm.store import bs_store
from aurora.literature import indexes as indexes_mod
from tests.test_brainstorm import FakeModel, _reply
from tests.test_literature import FakeIndexes


class ConductedModel(FakeModel):
    """The scripted agents, and a conductor that answers with the move it is given."""

    def __init__(self, move):
        super().__init__()
        self.move = move
        self.asked = []

    async def create(self, *, model, messages, tools=None, **kw):
        if not tools and "should join a research brainstorm now" in messages[0]["content"]:
            self.asked.append(messages[0]["content"])
            return _reply(json.dumps(self.move))
        return await super().create(model=model, messages=messages, tools=tools, **kw)


@pytest.fixture()
def on(monkeypatch):
    monkeypatch.setattr(conductor, "ENABLED", True)


def _client(monkeypatch, move):
    fake = ConductedModel(move)
    monkeypatch.setattr(ag, "client_factory", lambda access: fake)
    monkeypatch.setattr(indexes_mod, "_shared", FakeIndexes())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123456")
    from aurora.api.app import create_app
    return TestClient(create_app()), fake


def _settle(c, chat_id, who, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/brainstorm/chats/{chat_id}").json()
        if not r["pending"] and any(m["who"] == who and m["kind"] == "text" and m["text"] for m in r["messages"]):
            return r
        time.sleep(0.05)
    raise AssertionError(f"{who} did not answer")


def _m(who, text, kind="text"):
    return {"who": who, "text": text, "kind": kind}


def test_the_rules_keep_it_quiet():
    long = "I am sure the annotators' labels are the right ground truth for what the wearer prefers"
    history = [_m("local", long), _m("Mentor", "Why?"), _m("local", long), _m("Mentor", "And then?"), _m("local", long)]
    assert conductor.may_join(history, "local", "moderate", ["Mentor"]) == ["Skeptic", "Librarian", "Methods"]
    assert conductor.may_join(history, "local", "off", ["Mentor"]) == []
    # the one who just answered does not join again
    assert "Librarian" not in conductor.may_join(history, "local", "moderate", ["Librarian"])
    # an agent that spoke two of the researcher's messages ago waits (moderately: three)
    spoke = [*history[:3], _m("Skeptic", "Is it, though?"), *history[3:]]
    assert "Skeptic" not in conductor.may_join(spoke, "local", "moderate", ["Mentor"])
    assert "Skeptic" in conductor.may_join(spoke, "local", "high", ["Mentor"])
    # short, unsure answers: nobody joins
    tired = [*history, _m("local", "i have no idea"), _m("Mentor", "Fine."), _m("local", "both")]
    assert conductor.tired(tired, "local") and conductor.may_join(tired, "local", "high", ["Mentor"]) == []


async def test_a_move_outside_the_rules_is_not_made():
    class Says:
        def __init__(self, move):
            self.chat = type("C", (), {"completions": type("X", (), {"create": self.create})()})()
            self.move = move

        async def create(self, **kw):
            return _reply("Sure: " + json.dumps(self.move))

    board = ag.Board(space="user:local", viewer="local", viewer_name="You", chat={"chat_id": "b1", "focus": ""}, cards=[])
    history = [_m("local", "I am certain that labels written by annotators are the truth about preferences")] * 3
    access = ag.ModelAccess(api_key="k", base_url="u", model="m")
    d = await conductor.decide(history, board, "local", "moderate", access, ["Mentor"],
                               client=Says({"move": "Skeptic", "why": "You said you are certain.", "about": "Challenge it."}))
    assert d == {"move": "Skeptic", "why": "You said you are certain.", "about": "Challenge it."}
    # the one who just answered may not be chosen again; unknown moves and "none" make nothing
    assert await conductor.decide(history, board, "local", "moderate", access, ["Skeptic"],
                                  client=Says({"move": "Skeptic", "why": "x", "about": "y"})) is None
    assert await conductor.decide(history, board, "local", "moderate", access, ["Mentor"],
                                  client=Says({"move": "Philosopher"})) is None
    assert await conductor.decide(history, board, "local", "moderate", access, ["Mentor"],
                                  client=Says({"move": "none"})) is None


def test_the_skeptic_joins_unasked_and_says_why(on, monkeypatch):
    c, fake = _client(monkeypatch, {"move": "Skeptic", "why": "You said the annotators are surely right.",
                                    "about": "Challenge that assumption."})
    chat = c.post("/api/brainstorm/spaces/global/chats", json={}).json()
    cid = chat["chat_id"]
    for _ in range(2):
        bs_store.add_message(cid, "local", "Earlier thoughts about memory for an assistant that sees my day")
    r = c.post(f"/api/brainstorm/chats/{cid}/messages",
               json={"text": "@Librarian surely the annotators' labels are the truth about what the wearer prefers"})
    assert r.json()["agents"] == ["Librarian"]
    done = _settle(c, cid, "Skeptic")
    whos = [(m["who"], m["kind"]) for m in done["messages"]][2:]
    assert whos[:2] == [("local", "text"), ("Librarian", "text")]
    assert ("Skeptic", "system") in whos and whos[-1] == ("Skeptic", "text")
    why = next(m for m in done["messages"] if m["who"] == "Skeptic" and m["kind"] == "system")
    assert why["text"] == "Skeptic joins in: You said the annotators are surely right."
    assert "Librarian" in fake.asked[0] and "May join now: Skeptic, Methods" in fake.asked[0]


def test_a_map_is_proposed_not_started(on, monkeypatch):
    c, _ = _client(monkeypatch, {"move": "map", "why": "You asked what the whole field has done.",
                                 "about": "Egocentric memory assistants"})
    chat = c.post("/api/brainstorm/spaces/global/chats", json={}).json()
    cid = chat["chat_id"]
    c.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "@Librarian how far has the field of egocentric memory come?"})
    end = time.time() + 20
    while time.time() < end:
        msgs = c.get(f"/api/brainstorm/chats/{cid}").json()["messages"]
        offer = [m for m in msgs if m["kind"] == "action" and (m.get("widget") or {}).get("action") == "map_field"]
        if offer:
            break
        time.sleep(0.05)
    (offer,) = offer
    assert offer["who"] == "Librarian" and offer["widget"]["state"] == "pending"
    assert offer["widget"]["topic"] == "Egocentric memory assistants"
    assert offer["text"].startswith("You asked what the whole field has done.")


def test_the_board_note_counts_what_is_linked_to_the_idea():
    idea = bs_store.add_card("user:local", "local", "idea", "Plant preferences, score recall", by="local")
    ev = bs_store.add_card("user:local", "local", "material", "APM-Bench", by="Librarian")
    q = bs_store.add_card("user:local", "local", "question", "Would they say it that day?", by="Methods")
    bs_store.link(ev["card_id"], idea["card_id"], "supports")
    bs_store.link(q["card_id"], idea["card_id"], "asks about")
    board = ag.Board(space="user:local", viewer="local", viewer_name="You",
                     chat={"chat_id": "b1", "focus": idea["card_id"]}, cards=[idea, ev, q])
    note = conductor._board_note(board)
    assert "Evidence on it: 1" in note and "Open questions on it: 1" in note

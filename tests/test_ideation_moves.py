"""The moves of ideation: drafts to edit and take further, the researcher's own versions, an idea's versions with
whose words they are, the round table starting from the researcher's idea and handing its decision back."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aurora.brainstorm import agents as ag
from aurora.brainstorm import ideas
from aurora.brainstorm.store import bs_store
from aurora.literature import indexes as indexes_mod
from aurora.review import llm
from tests.test_brainstorm import _call, _reply
from tests.test_literature import FakeIndexes


class FakeModel:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.embeddings = SimpleNamespace(create=self.embed)
        self.console = []

    async def embed(self, **kw):
        raise RuntimeError("no embeddings here")

    async def create(self, *, model, messages, tools=None, **kw):
        first, last = messages[0]["content"], messages[-1]["content"]
        done = [m.get("tool_call_id") for m in messages if m["role"] == "tool"]
        if first.startswith("TASK: own_versions"):
            return _reply(json.dumps({"versions": ["Robots predict where moved things went",
                                                   "Robots ask the family before searching",
                                                   "A version nobody wrote"]}))
        if first.startswith("TASK: elaboration_depth"):
            return _reply(json.dumps({"levels": [{"n": 1, "codes": ["mechanism"], "level": 3}]}))
        if not tools:
            return _reply("Methods")
        if "Console:" in last:
            self.console.append(last)
        if first.startswith("The protocol below is from Academic Research Skills"):
            if "stuck" in last:
                return _reply("Here is an analogy from logistics [Behrens12]. How could it inspire a new version?\n"
                              "[STIMULUS: analogy]\n[LAYER: 1]")
            if "Robots predict" in last:
                return _reply("You wrote two versions. Pick one?\n[VERSIONS]\n[LAYER: 1]")
            return _reply("What is most yours in it?\n[LAYER: 1]")
        if "the Methods in a research" in first:
            if "call_draft_versions" not in done:
                return _reply(calls=[_call("draft_versions", drafts=["Draft A: robots track objects over time.",
                                                                     "Draft B: robots ask people where things are."])])
            return _reply("Two drafts: one tracks objects, one asks people.")
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
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/brainstorm/chats/{chat_id}").json()
        if len(r["messages"]) >= n and not r["pending"]:
            time.sleep(0.15)
            r2 = c.get(f"/api/brainstorm/chats/{chat_id}").json()
            if len(r2["messages"]) == len(r["messages"]) and not r2["pending"]:
                return r2
        time.sleep(0.05)
    raise AssertionError(f"the conversation did not reach {n} messages")


def widgets(chat, kind):
    return [m for m in chat["messages"] if (m.get("widget") or {}).get("type") == kind]


def test_whose_words_a_version_is():
    assert ideas.share("", "anything") == 1.0
    assert ideas.share("robots track objects", "robots track objects") == 0.0
    assert 0 < ideas.share("robots track objects", "robots track objects that people move often") < 1
    assert ideas._origin("a draft", "a draft") == ("ai", 0.0)
    assert ideas._origin("", "mine")[0] == "you"


def test_drafts_go_round_after_round_and_what_is_taken_is_measured(client, model):
    chat = client.post("/api/brainstorm/spaces/global/chats", json={}).json()
    cid = chat["chat_id"]
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "@Methods write a few versions of my idea for me"})
    done = settle(client, cid, 3)
    (d,) = widgets(done, "drafts")
    assert [x["text"] for x in d["widget"]["drafts"]] == ["Draft A: robots track objects over time.",
                                                          "Draft B: robots ask people where things are."]
    assert d["who"] == "Methods" and d["widget"]["round"] == 1
    # the researcher edits draft A in place and asks for another round from it
    edited = "Draft A: robots track objects over time and learn who moves them."
    client.post(f"/api/brainstorm/messages/{d['msg_id']}/act", json={"choice": "again:0", "text": edited})
    done = settle(client, cid, 5)
    first, second = widgets(done, "drafts")
    assert first["widget"]["state"] == "taken" and first["widget"]["origin"] == "edited" and 0 < first["widget"]["share"] < 0.5
    card_id = first["widget"]["card_id"]
    assert "Console: the researcher took draft 1 and edited it" in model.console[-1]
    # round two: their version first, then the new drafts
    assert second["widget"]["round"] == 2 and second["widget"]["card_id"] == card_id
    assert second["widget"]["drafts"][0] == {"text": edited, "yours": True}
    # they take the AI's draft B as it is: the idea's next version is the AI's, and the Mentor asks what is theirs
    client.post(f"/api/brainstorm/messages/{second['msg_id']}/act", json={"choice": "take:2"})
    done = settle(client, cid, 6)
    assert done["messages"][-1]["who"] == "Mentor" and "without changing it" in model.console[-1]
    vs = client.get(f"/api/brainstorm/cards/{card_id}/versions").json()
    assert [(v["n"], v["origin"]) for v in vs] == [(1, "edited"), (2, "ai")]
    # they rewrite the idea themselves: measured against the AI's version before it
    client.patch(f"/api/brainstorm/cards/{card_id}", json={"text": "Robots ask people where things are, but only when unsure."})
    vs = client.get(f"/api/brainstorm/cards/{card_id}/versions").json()
    assert vs[-1]["origin"] == "edited" and vs[-1]["share"] > 0
    plan = client.get(f"/api/brainstorm/chats/{cid}/plan").json()
    assert plan["idea"]["card_id"] == card_id and len(plan["idea"]["versions"]) == 3
    # for evaluation: the moves, the researcher's share, the diversity of the space's ideas
    metrics = client.get("/api/brainstorm/spaces/global/ideation-metrics").json()
    assert metrics["moves"]["drafts"] == 2 and metrics["moves"]["taken"] == 2
    assert metrics["ownership"]["ideas"] == 1 and metrics["diversity"]["mean"] is None


def test_a_stimulus_then_the_researchers_own_versions_to_pick_from(client, model):
    chat = client.post("/api/brainstorm/spaces/global/chats", json={}).json()
    cid = chat["chat_id"]
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={"text": "@Mentor I am stuck on my robot memory idea"})
    settle(client, cid, 2)
    client.post(f"/api/brainstorm/chats/{cid}/messages", json={
        "text": "Robots predict where moved things went. Or: Robots ask the family before searching."})
    done = settle(client, cid, 5)
    (c,) = widgets(done, "choice")
    # only what they wrote, word for word
    assert c["widget"]["versions"] == ["Robots predict where moved things went", "Robots ask the family before searching"]
    client.post(f"/api/brainstorm/messages/{c['msg_id']}/act", json={"choice": "pick:1"})
    done = settle(client, cid, 6)
    space = client.get("/api/brainstorm/spaces/global").json()
    (card,) = [x for x in space["cards"] if x["text"] == "Robots ask the family before searching"]
    assert card["by"] == "local" and done["chat"]["focus"] == card["card_id"]
    assert client.get(f"/api/brainstorm/cards/{card['card_id']}/versions").json()[0]["origin"] == "you"
    assert "picked this version" in model.console[-1]
    kinds = [mv["kind"] for mv in bs_store.moves(chat_id=cid)]
    assert kinds == ["stimulus", "versions", "chosen"]
    # how far they elaborated, coded as in the paper (a few cents, on request)
    r = client.post(f"/api/brainstorm/chats/{cid}/depth").json()
    assert r["mean"] == 3


def test_a_round_table_starts_from_the_idea_and_hands_its_decision_back(client, model):
    cid = client.post("/api/campaigns/quick", json={"prompt": "Robot memory"}).json()["campaign_id"]
    idea = client.post(f"/api/brainstorm/spaces/{cid}/cards", json={"text": "Robots learn who moves things."}).json()["card"]
    r = client.post(f"/api/brainstorm/cards/{idea['card_id']}/promote", json={"ideation": "brainstorm"}).json()
    from aurora.api.routes.campaigns import StartExperimentRequest, _ensure_campaign, _experiment_config
    camp = _ensure_campaign(cid)
    p = camp.proposals[r["proposal_id"]].params
    assert p["ideation_develop"] is True and p["source_card"] == idea["card_id"]
    cfg = _experiment_config(camp, "run_x", StartExperimentRequest(instruction=p["instruction"], ideation_live=True,
                                                                   ideation_develop=True, source_card=idea["card_id"]))
    assert cfg.ideation.develop_objective and cfg.ideation.source_card == idea["card_id"] and cfg.ideation.live

    # the live meeting: the chair's decision comes back as drafts, the run waits at a gate
    from aurora.brainstorm.live import LiveMeeting
    from aurora.config import HumanConfig
    from aurora.core.human_gate import HumanGate
    meeting = LiveMeeting(cid, "run_x", "Robot memory", "local", source_card=idea["card_id"])
    events = []

    async def publish(name, **data):
        events.append(name)
        await meeting.on_event(name, data)

    async def scenario():
        await meeting.on_event("phase_started", {"phase": "ideation"})
        await meeting.on_event("ideation_turn", {"seat": "p01", "kind": "presentation", "headline": "Learn movers' habits",
                                                  "content": "Learn movers' habits. More."})
        await meeting.on_event("ideation_turn", {"seat": "p02", "kind": "presentation", "headline": "Ask before searching",
                                                  "content": "Ask before searching. More."})
        await meeting.on_event("ideation_progress", {"step": "decided", "chosen": "p01", "headline": "Learn movers' habits",
                                                      "rationale": "Most testable."})
        gate = HumanGate(HumanConfig(), publish)
        camp.experiments["run_x"] = SimpleNamespace(engine=SimpleNamespace(human_gate=gate))
        task = asyncio.create_task(gate.request("direction", "The round table decided", "...",
                                                options=["continue", "propose", "instruct", "stop"]))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if gate.pending:
                break
        return gate, task

    loop = asyncio.new_event_loop()
    gate, task = loop.run_until_complete(scenario())
    ch = bs_store.chat_for_run("run_x")
    msgs = bs_store.messages(ch["chat_id"])
    assert msgs[0]["effects"] == [["Starts from", idea["card_id"]]]
    (d,) = [m for m in msgs if (m.get("widget") or {}).get("purpose") == "direction"]
    items = d["widget"]["drafts"]
    assert items[0] == {"text": "Learn movers' habits", "seat": "p01", "chosen": True}
    assert items[1]["seat"] == "p02" and items[-1]["yours"] is True
    # the researcher goes with their own idea, as they rewrite it: the run takes it as their direction
    client.post(f"/api/brainstorm/messages/{d['msg_id']}/act",
                json={"choice": "take:2", "text": "Robots learn who moves things, and when."})
    decision = loop.run_until_complete(task)
    loop.close()
    assert decision.action == "propose" and "my own idea" in decision.message and "and when" in decision.message
    vs = client.get(f"/api/brainstorm/cards/{idea['card_id']}/versions").json()
    assert vs[-1]["text"] == "Robots learn who moves things, and when." and vs[-1]["origin"] == "you"
    w = bs_store.message(d["msg_id"])["widget"]
    assert w["state"] == "taken" and w["action"] == "propose"


def _live_engine(tmp_path, cid, monkeypatch):
    from aurora.agents.roundtable import roundtable as rt_mod
    from aurora.config import AuroraConfig
    from aurora.core import engine as engine_mod
    from aurora.core.engine import ResearchEngine
    from aurora.llm.router import LLMRouter

    real_gs = LLMRouter.generate_structured

    async def no_reading(self, stage, messages, schema, **kw):
        if schema is engine_mod.ResearcherDirection:
            raise RuntimeError("model unavailable")       # the message itself becomes the direction
        return await real_gs(self, stage, messages, schema, **kw)

    monkeypatch.setattr(LLMRouter, "generate_structured", no_reading)
    topics = []
    real_run = rt_mod.Roundtable.run

    async def run(self, ctx, topic=""):
        topics.append(topic)
        return await real_run(self, ctx, topic=topic)

    monkeypatch.setattr(rt_mod.Roundtable, "run", run)
    cfg = AuroraConfig(objective="Robots learn who moves things.", mock_mode=True, campaign_id=cid,
                       workspace={"root": str(tmp_path)}, human={"autonomy": "supervised"},
                       ideation={"live": True, "mode": "roundtable", "develop_objective": True})
    engine = ResearchEngine(cfg)
    screened, planned = [], []

    async def screen(proposal):
        screened.append(proposal)
        return proposal.model_copy(update={"selected": True})

    real_planning = engine._run_planning

    async def plan(proposal, guidance=""):
        planned.append(proposal)
        return await real_planning(proposal, guidance)

    engine._run_screening = screen
    engine._run_planning = plan
    return engine, topics, screened, planned


async def _gate(engine, task=None):
    for _ in range(400):
        await asyncio.sleep(0.05)
        if engine.human_gate.pending:
            break
    [gate] = engine.human_gate.pending
    return gate


async def test_the_seats_develop_the_researchers_idea_and_its_decision_waits_for_them(tmp_path, monkeypatch):
    engine, topics, screened, planned = _live_engine(tmp_path, "lv1", monkeypatch)
    task = asyncio.create_task(engine.run_campaign())
    gate = await _gate(engine)
    assert gate.source == "direction" and gate.options == ["continue", "propose", "instruct", "stop"]
    assert "do not propose a different direction" in topics[0] and "Robots learn who moves things." in topics[0]
    # another round, developing what the researcher says; then their own direction, as they wrote it
    engine.human_gate.resolve(gate.gate_id, "instruct", "Focus on shared objects like keys")
    gate = await _gate(engine)
    assert len(topics) == 2 and "Focus on shared objects like keys" in topics[1]
    engine.human_gate.resolve(gate.gate_id, "propose", "Robots that learn who moves shared objects")
    result = await asyncio.wait_for(task, timeout=60)
    assert result.status == "completed", result.summary
    assert not screened                                   # their direction is screened only if they ask
    assert planned[0].description == "Robots that learn who moves shared objects"


async def test_going_on_with_the_chairs_choice(tmp_path, monkeypatch):
    engine, topics, screened, planned = _live_engine(tmp_path, "lv2", monkeypatch)
    task = asyncio.create_task(engine.run_campaign())
    gate = await _gate(engine)
    engine.human_gate.resolve(gate.gate_id, "continue", "")
    result = await asyncio.wait_for(task, timeout=60)
    assert result.status == "completed", result.summary
    assert len(screened) == 1 and planned[0].proposal_id == screened[0].proposal_id

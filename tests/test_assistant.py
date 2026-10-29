"""Pippin at your side: one conversation from the orb or the side panel, answered with tools over the memory and the
projects, kept per person so it goes on where it stopped, with buttons that open the console."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aurora.human.frontdesk import FrontDeskAgent
from aurora.memory import assistant, voice


def _call(name, args, i=0):
    return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)),
                           model_dump=lambda: {"id": f"c{i}", "type": "function",
                                               "function": {"name": name, "arguments": json.dumps(args)}})


class Scripted:
    """A model that calls the tools it is given, in order, then answers; it records what it was sent."""

    def __init__(self, steps):
        self.steps, self.sent = list(steps), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.sent.append(kw["messages"])
        step = self.steps.pop(0)
        msg = SimpleNamespace(content=step if isinstance(step, str) else "",
                              tool_calls=None if isinstance(step, str) else [_call(n, a, i) for i, (n, a) in enumerate(step)])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    async def close(self):
        pass


@pytest.fixture()
def model(monkeypatch):
    holder = {}
    monkeypatch.setattr(FrontDeskAgent, "_get_client", lambda self: holder["m"])
    assistant._agents.clear()
    return holder


ACCESS = SimpleNamespace(model="m", api_key="k", base_url="u")


async def test_memory_answers_come_with_their_sources_and_the_conversation_goes_on(model, monkeypatch):
    async def hear(access, models, owner, said, *, screenshot=""):
        assert said == "what does the paper in front say about recall?" and screenshot == "data:image/png;base64,x"
        return {"intent": "ask", "speech": "It says recall drops.", "text": "Recall drops after a week (p. 4).",
                "items": [{"claim": "Recall drops after a week", "unit_id": "u1"}], "undo_id": ""}
    monkeypatch.setattr(voice, "hear", hear)
    model["m"] = Scripted([[("memory", {"request": "what does the paper in front say about recall?"})],
                           "It says recall drops after a week (p. 4)."])
    out = await assistant.ask("local", "what does this paper say about recall?", access=ACCESS, mem_access=None,
                              models={}, screenshot="data:image/png;base64,x", via="voice")
    assert out["speech"] == "It says recall drops after a week (p. 4)." and out["items"][0]["unit_id"] == "u1"
    assert out["text"]                                  # with sources: the panel shows them
    turns = assistant.history("local")
    assert [(t["role"], t.get("via")) for t in turns] == [("user", "voice"), ("assistant", None)]
    # the next turn sees the last one (also after a restart)
    assistant._agents.clear()
    model["m"] = Scripted(["Yes, on page 4."])
    await assistant.ask("local", "on which page?", access=ACCESS, mem_access=None, models={})
    sent = model["m"].sent[0]
    assert any("recall" in str(m.get("content")) for m in sent) and "on which page?" in sent[-1]["content"]
    assistant.clear("local")
    assert assistant.history("local") == []


async def test_projects_are_looked_up_and_the_console_opened(model, monkeypatch):
    from aurora.api.routes import campaigns

    async def list_campaigns():
        return [{"campaign_id": "p_1", "name": "Cu-Zn segregation", "experiment_count": 2, "active_experiments": 1,
                 "needs_attention": True, "updated_at": 0}]

    async def get_campaign(cid):
        return {"campaign_id": cid, "name": "Cu-Zn segregation", "objective": "Zn in Cu",
                "experiments": [{"run_id": "r1", "task_name": "DFT", "status": "awaiting_human", "phase": "execution",
                                 "cost_usd": 3.2, "pending_decision": {"question": "Run 12 more k-points?"},
                                 "started_at": 0}]}

    async def exp_chat_history(cid, rid, limit=100):
        return [{"role": "assistant", "text": "Waiting for your decision on k-points."}]
    monkeypatch.setattr(campaigns, "list_campaigns", list_campaigns)
    monkeypatch.setattr(campaigns, "get_campaign", get_campaign)
    monkeypatch.setattr(campaigns, "exp_chat_history", exp_chat_history)
    model["m"] = Scripted([[("project", {"project": "cu-zn"})],
                           [("open_in_console", {"page": "run", "project_id": "p_1", "run_id": "r1"})],
                           "The DFT run waits for you: run 12 more k-points?"])
    out = await assistant.ask("local", "how is the Cu-Zn project going?", access=ACCESS, mem_access=None, models={})
    tool_reply = json.loads(model["m"].sent[1][-1]["content"])
    assert tool_reply["runs"][0]["waiting_for_you"] == "Run 12 more k-points?"
    assert tool_reply["latest_run_conversation"][0]["text"].startswith("Waiting")
    assert out["links"] == [{"label": "Open the run", "path": "/campaigns/p_1/experiments/r1"}]


def test_the_api_keeps_the_conversation(tmp_path, monkeypatch, model):
    from aurora.api.app import create_app
    from aurora.api.routes import assistant as route
    monkeypatch.setattr(route, "_access", lambda: (ACCESS, None, {}))
    model["m"] = Scripted(["Hello! What are you working on?"])
    with TestClient(create_app()) as client:
        assert client.post("/api/assistant", json={"text": "  "}).status_code == 400
        r = client.post("/api/assistant", json={"text": "hi"}).json()
        assert r["reply"] == "Hello! What are you working on?" and r["text"] == ""      # short: only said
        assert [t["text"] for t in client.get("/api/assistant/history").json()] == ["hi", "Hello! What are you working on?"]
        assert client.delete("/api/assistant/history").status_code == 204
        assert client.get("/api/assistant/history").json() == []


async def test_what_they_look_at_reaches_the_model_not_their_words(model):
    model["m"] = Scripted(["Run 3 is waiting on the CO point."])
    await assistant.ask("local", "how is it going?", access=ACCESS, mem_access=None, models={},
                        context="the project “Cu-Zn”, its run “run 3”")
    sent = model["m"].sent[0][-1]["content"]
    assert "[They are looking at: the project “Cu-Zn”, its run “run 3”]" in sent and sent.endswith("how is it going?")
    assert assistant.history("local")[0]["text"] == "how is it going?"
    assistant.clear("local")


async def test_reading_a_run_report(model, monkeypatch, tmp_path):
    from aurora.api.routes import campaigns, run_results
    run_dir = tmp_path / "run"
    (run_dir / "phases" / "writing").mkdir(parents=True)
    (run_dir / "phases" / "writing" / "draft_1.md").write_text("# Old\n")
    (run_dir / "phases" / "writing" / "draft_2.md").write_text("# Zn at the surface\n\nZn segregates by 0.3 eV.\n")
    run = SimpleNamespace(campaign_dir=run_dir)

    async def list_campaigns():
        return [{"campaign_id": "p_1", "name": "Cu-Zn segregation"}]

    async def get_campaign(cid):
        return {"campaign_id": cid, "name": "Cu-Zn segregation",
                "experiments": [{"run_id": "r1", "task_name": "DFT", "status": "completed", "started_at": 1}]}
    monkeypatch.setattr(campaigns, "list_campaigns", list_campaigns)
    monkeypatch.setattr(campaigns, "get_campaign", get_campaign)
    monkeypatch.setattr(run_results, "_run", lambda cid, rid: run)
    monkeypatch.setattr(run_results, "overview", lambda r: {
        "name": "DFT", "status": "completed", "cost_usd": 1.5, "proposal": {"title": "Zn segregation"},
        "claims": [{"statement": "Zn goes to the surface", "status": "supported", "confidence": 0.8}],
        "results": [{"name": "E_seg", "value": -0.3, "unit": "eV", "command": "python x.py"}]})
    model["m"] = Scripted([[("report", {"project": "cu-zn"})], "Zn segregates to the surface by 0.3 eV."])
    await assistant.ask("local", "what did the DFT run find?", access=ACCESS, mem_access=None, models={})
    got = json.loads(model["m"].sent[1][-1]["content"])
    assert got["run_id"] == "r1" and "0.3 eV" in got["report"] and "Old" not in got["report"]    # the latest draft
    assert got["claims"][0]["status"] == "supported" and got["results"] == [{"name": "E_seg", "value": -0.3, "unit": "eV"}]
    assistant.clear("local")


async def test_a_proposal_is_done_only_when_confirmed(model, monkeypatch):
    from aurora.api.routes import campaigns
    from aurora.auth import guard
    decided, checked = [], []

    async def list_campaigns():
        return [{"campaign_id": "p_1", "name": "Cu-Zn segregation"}]

    async def get_campaign(cid):
        return {"campaign_id": cid, "name": "Cu-Zn segregation",
                "experiments": [{"run_id": "r1", "task_name": "DFT", "status": "awaiting_human", "started_at": 1,
                                 "pending_decision": {"decision_id": "d1", "options": ["continue", "instruct", "stop"],
                                                      "detail": {"title": "Run 12 more k-points?"}}}]}

    async def submit_decision(cid, rid, req):
        decided.append((cid, rid, req.action, req.message))
        return {"ok": True}
    monkeypatch.setattr(campaigns, "list_campaigns", list_campaigns)
    monkeypatch.setattr(campaigns, "get_campaign", get_campaign)
    monkeypatch.setattr(campaigns, "submit_decision", submit_decision)
    monkeypatch.setattr(guard, "require", lambda cid, perm, user=None: checked.append((cid, perm)))
    # a choice the gate does not offer is refused before anything is proposed
    model["m"] = Scripted([[("propose", {"action": "decide", "project": "cu-zn", "choice": "propose"})],
                           [("propose", {"action": "decide", "project": "cu-zn", "choice": "continue"})],
                           "I proposed letting the DFT run continue; confirm it to go ahead."])
    out = await assistant.ask("local", "let the DFT run continue", access=ACCESS, mem_access=None, models={})
    assert "error" in json.loads(model["m"].sent[1][-1]["content"])
    [a] = out["actions"]
    assert a["state"] == "pending" and a["run_id"] == "r1" and "Run 12 more k-points?" in a["label"]
    assert out["text"] and decided == []                       # the panel opens with it; nothing done yet
    assert assistant.pending("local")[0]["action_id"] == a["action_id"]
    done = await assistant.act("local", a["action_id"], True)
    assert done["state"] == "done" and decided == [("p_1", "r1", "continue", "")] and checked == [("p_1", "decide")]
    assert (await assistant.act("local", a["action_id"], True))["state"] == "done" and len(decided) == 1   # once
    assert assistant.pending("local") == []
    assert assistant.history("local")[-1]["actions"][0]["state"] == "done"
    # the next turn knows what came of it
    model["m"] = Scripted(["It continues."])
    await assistant.ask("local", "did it go?", access=ACCESS, mem_access=None, models={})
    assert "[Proposed: Let it continue" in str(model["m"].sent[0]) and "· done" in str(model["m"].sent[0])
    assistant.clear("local")


async def test_a_proposal_can_be_dropped(model, monkeypatch):
    from aurora.api.routes import campaigns

    async def list_campaigns():
        return [{"campaign_id": "p_1", "name": "Cu-Zn segregation"}]

    async def get_campaign(cid):
        return {"campaign_id": cid, "name": "Cu-Zn segregation",
                "experiments": [{"run_id": "r1", "task_name": "DFT", "status": "running", "started_at": 1}]}
    monkeypatch.setattr(campaigns, "list_campaigns", list_campaigns)
    monkeypatch.setattr(campaigns, "get_campaign", get_campaign)
    monkeypatch.setattr(campaigns, "stop_experiment", lambda *a: pytest.fail("stopped without a yes"))
    model["m"] = Scripted([[("propose", {"action": "stop", "project": "p_1", "run": "dft"})], "Shall I stop it?"])
    [a] = (await assistant.ask("local", "stop the DFT run", access=ACCESS, mem_access=None, models={}))["actions"]
    assert (await assistant.act("local", a["action_id"], False))["state"] == "declined"
    with pytest.raises(KeyError):
        await assistant.act("local", "nope", True)
    assistant.clear("local")

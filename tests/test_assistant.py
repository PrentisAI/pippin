"""AURORA at your side: one conversation from the orb or the side panel, answered with tools over the memory and the
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

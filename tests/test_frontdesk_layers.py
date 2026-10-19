"""The two layers of front desks: the project's and each run's. Conversations survive restarts,
a finished run can still be asked about (or its front desk switched off), and both layers can
look into the project's runs; a new run can build on earlier ones."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from aurora.api.app import create_app
from aurora.api.routes import campaigns as routes
from aurora.human.frontdesk import FrontDeskAgent


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


def finished_run(client, cid, **extra):
    eid = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "Zn segregation in Cu(111)", "mock_mode": True,
                            "skip_phases": ["ideation", "screening"], **extra}).json()["experiment_id"]
    for _ in range(600):
        exp = client.get(f"/api/campaigns/{cid}/experiments/{eid}").json()
        if exp["status"] not in ("starting", "running"):
            return eid
        time.sleep(0.1)
    raise AssertionError("did not finish")


def test_project_conversation_survives_a_restart(client):
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    camp = routes._ensure_campaign(cid)
    camp.chat_history.append({"role": "user", "text": "look at weekday peaks", "timestamp": 1.0})
    prop = routes.ExperimentProposal({"instruction": "weekday peaks", "based_on": []}, ["ideation"], "peaks")
    camp.proposals[prop.proposal_id] = prop
    camp.save()
    routes._campaigns.clear()                                         # as after a server restart
    history = client.get(f"/api/campaigns/{cid}/chat/history").json()
    assert any(m["text"] == "look at weekday peaks" for m in history)
    restored = routes._ensure_campaign(cid).proposals[prop.proposal_id]
    assert restored.params["instruction"] == "weekday peaks" and restored.suggested_skip == ["ideation"]


def test_restore_keeps_the_turns():
    desk = FrontDeskAgent(model="m", api_key="k", base_url="http://x")
    desk.restore([{"role": "assistant", "text": "hello"}, {"role": "system", "text": "started"},
                  {"role": "user", "text": "how is it going?"}, {"role": "assistant", "text": "fine"}])
    assert desk._messages == [{"role": "user", "content": "how is it going?"},
                              {"role": "assistant", "content": "fine"}]


def test_a_finished_run_can_be_asked_about_or_its_desk_switched_off(client, monkeypatch):
    seen = {}

    async def turn(self, content):
        seen["tools"] = sorted(self._tools)
        seen["memory"] = list(self._messages)
        await self._emit_text("It found Zn at the surface.")
        return "It found Zn at the surface."

    monkeypatch.setattr(FrontDeskAgent, "_turn", turn)
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    eid = finished_run(client, cid)
    # mock runs have no front desk (no real model calls); switch it on as a researcher would
    assert client.get(f"/api/campaigns/{cid}/experiments/{eid}").json()["frontdesk_enabled"] is False
    client.put(f"/api/campaigns/{cid}/experiments/{eid}/frontdesk", json={"enabled": True})
    r = client.post(f"/api/campaigns/{cid}/experiments/{eid}/chat/send", json={"text": "what did it find?"}).json()
    assert r["status"] == "sent" and r["response"] == "It found Zn at the surface."
    assert {"list_files", "read_file", "list_runs", "read_run"} <= set(seen["tools"])
    history = client.get(f"/api/campaigns/{cid}/experiments/{eid}/chat/history").json()
    assert [m["role"] for m in history][-2:] == ["user", "assistant"]

    assert client.put(f"/api/campaigns/{cid}/experiments/{eid}/frontdesk", json={"enabled": False}).json()["ok"]
    seen.clear()
    r = client.post(f"/api/campaigns/{cid}/experiments/{eid}/chat/send", json={"text": "and then?"}).json()
    assert r["status"] == "frontdesk_off" and seen == {}                # no model call
    assert routes._saved_request(eid).frontdesk is False                # a resume keeps it off
    assert client.get(f"/api/campaigns/{cid}/experiments/{eid}").json()["frontdesk_enabled"] is False


def test_front_desks_look_into_the_projects_runs(client):
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    eid = finished_run(client, cid)
    tools = {t.name: t for t in routes._run_tools(routes._ensure_campaign(cid))}
    runs = asyncio.run(tools["list_runs"].handler({}))
    assert runs[0]["run_id"] == eid and runs[0]["instruction"].startswith("Zn segregation")
    summary = asyncio.run(tools["read_run"].handler({"run_id": eid, "part": "summary"}))
    assert summary["status"] == "completed" and summary["report_start"]
    assert "error" in asyncio.run(tools["read_run"].handler({"run_id": eid, "part": "file", "path": "../../x"}))


def test_a_new_run_builds_on_earlier_ones(client):
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    first = finished_run(client, cid)
    second = finished_run(client, cid, based_on=[first])
    run = routes._get_run(cid, second)
    prior = run.campaign_dir / "prior_runs" / f"{first}.md"
    assert prior.exists() and "## Report" in prior.read_text()
    assert "Earlier runs this experiment builds on" in run.engine._config.objective


def test_two_runs_started_at_once_get_their_own_ids(client):
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    body = {"instruction": "same", "mock_mode": True, "skip_phases": ["ideation", "screening"]}
    a = client.post(f"/api/campaigns/{cid}/experiments", json=body).json()["experiment_id"]
    b = client.post(f"/api/campaigns/{cid}/experiments", json=body).json()["experiment_id"]
    assert a != b

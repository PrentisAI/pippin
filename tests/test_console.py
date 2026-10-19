"""Search, Analytics and Settings of the web console, and settings changed in conversation."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from aurora.api.app import create_app
from aurora.api.routes import campaigns as routes


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


def finished(client, cid, instruction="Zn segregation in Cu(111)", **extra):
    eid = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": instruction, "mock_mode": True,
                            "skip_phases": ["ideation", "screening"], **extra}).json()["experiment_id"]
    for _ in range(600):
        if client.get(f"/api/campaigns/{cid}/experiments/{eid}").json()["status"] not in ("starting", "running"):
            return eid
        time.sleep(0.1)
    raise AssertionError("did not finish")


def setting(client, key):
    return next(s for s in client.get("/api/settings").json()["editable"] if s["key"] == key)


def test_settings_change_the_defaults_and_can_be_reset(client):
    assert setting(client, "agent.max_parallel_jobs")["overridden"] is False
    r = client.put("/api/settings", json={"changes": {"agent.max_parallel_jobs": 3, "budget.max_cost_usd": 50,
                                                      "sandbox.cpus": 16, "skills.auto_accept": True}})
    assert r.status_code == 200, r.text
    s = setting(client, "agent.max_parallel_jobs")
    assert s["value"] == 3 and s["overridden"] and s["server_value"] == 1
    settings = client.get("/api/settings").json()
    assert settings["max_parallel_jobs"] == 3 and settings["max_cost_usd"] == 50   # what new cards start from
    cfg = routes._experiment_config(routes._ensure_campaign(
        client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]), "r1",
        routes.StartExperimentRequest(instruction="x"))
    assert cfg.sandbox.cpus == 16 and cfg.skills.auto_accept and cfg.budget.max_cost_usd == 50
    assert client.put("/api/settings", json={"reset": ["agent.max_parallel_jobs"]}).status_code == 200
    assert setting(client, "agent.max_parallel_jobs")["value"] == 1
    assert client.put("/api/settings", json={"changes": {"human.autonomy": "weird"}}).status_code == 400
    assert client.put("/api/settings", json={"changes": {"api_key": "sk-x"}}).status_code == 400   # not a console setting
    env = client.get("/api/settings").json()["environment"]
    assert {"api_key", "docker", "claude", "aurora_home"} <= set(env) and "sk-" not in str(env)


def test_search_finds_projects_runs_reports_and_skills(client):
    cid = client.post("/api/campaigns/quick", json={"prompt": "Copper alloys"}).json()["campaign_id"]
    finished(client, cid, instruction="Study zinc segregation at grain boundaries")
    client.post("/api/skills/", json={"name": "zinc-slab-setup", "rule": "## Purpose\nset up Zn slabs", "scope": "global"})
    res = client.get("/api/search", params={"q": "zinc"}).json()["results"]
    assert res["runs"][0]["campaign_id"] == cid and res["skills"][0]["title"] == "zinc-slab-setup"
    assert client.get("/api/search", params={"q": "x"}).json()["results"] == {}         # too short


def test_analytics_sums_cost_and_outcomes(client):
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    finished(client, cid)
    a = client.get("/api/analytics", params={"days": 7}).json()
    assert a["runs"] >= 1 and a["by_status"].get("completed", 0) >= 1
    assert len(a["cost_by_day"]) == 7 and any(p["campaign_id"] == cid for p in a["cost_by_project"])


def test_front_desk_changes_defaults_only_after_confirmation(client):
    preview = asyncio.run(routes._default_settings_tool({"changes": {"models.research": "anthropic/claude-opus-5.5"},
                                                         "confirmed": False}))
    assert preview["status"] == "preview" and preview["would_change"]["models.research"]["to"] == \
        "anthropic/claude-opus-5.5"
    assert setting(client, "models.research")["overridden"] is False                  # nothing saved yet
    saved = asyncio.run(routes._default_settings_tool({"changes": {"models.research": "anthropic/claude-opus-5.5"},
                                                       "confirmed": True}))
    assert saved["status"] == "saved" and setting(client, "models.research")["value"] == "anthropic/claude-opus-5.5"


def test_settings_from_the_conversation_reach_the_card_and_the_run(client, monkeypatch):
    started = {}

    async def spy(campaign, req, **kw):
        started["req"] = req
        return {"experiment_id": "e1", "skip_phases": req.skip_phases or [], "active_phases": []}

    monkeypatch.setattr(routes, "_start", spy)
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    camp = routes._ensure_campaign(cid)
    prop = routes.ExperimentProposal({"instruction": "x", "models": {"research": "anthropic/claude-opus-5.5"},
                                      "cpus": 8, "memory_gb": 32, "max_parallel_jobs": 2,
                                      "autonomy_mode": "autonomous"}, ["ideation", "screening"], "s")
    prop.max_cost_usd = 12
    camp.proposals[prop.proposal_id] = prop
    r = client.post(f"/api/campaigns/{cid}/proposals/{prop.proposal_id}/confirm",
                    json={"models": {"writing": "anthropic/claude-haiku-5.5"}, "cpus": 4})
    assert r.status_code == 200, r.text
    cfg = routes._experiment_config(camp, "e1", started["req"])
    assert cfg.models.research == "anthropic/claude-opus-5.5" and cfg.models.writing == "anthropic/claude-haiku-5.5"
    assert cfg.sandbox.cpus == 4 and cfg.sandbox.memory_mb == 32 * 1024 and cfg.agent.max_parallel_jobs == 2
    assert cfg.budget.max_cost_usd == 12 and cfg.human.autonomy.value == "autonomous"


def test_budget_and_mode_change_while_running(client, monkeypatch):
    from aurora.core.engine import ResearchEngine

    async def slow(self, *a, **k):
        await asyncio.sleep(30)

    monkeypatch.setattr(ResearchEngine, "_run_analysis", slow)
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    eid = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "x", "mock_mode": True, "skip_phases": ["ideation", "screening"]}
                      ).json()["experiment_id"]
    for _ in range(100):
        if client.get(f"/api/campaigns/{cid}/experiments/{eid}").json()["status"] == "running":
            break
        time.sleep(0.05)
    r = client.put(f"/api/campaigns/{cid}/experiments/{eid}/budget", json={"max_cost_usd": 7.5}).json()
    assert r["max_cost_usd"] == 7.5 and routes._get_run(cid, eid).engine._config.budget.max_cost_usd == 7.5
    assert client.get(f"/api/campaigns/{cid}/experiments/{eid}").json()["max_cost_usd"] == 7.5
    tools = {t.name: t for t in routes._run_control_tools(routes._get_run(cid, eid))}
    assert asyncio.run(tools["set_decision_mode"].handler({"mode": "autonomous"}))["to"] == "autonomous"
    assert routes._saved_request(eid).max_cost_usd == 7.5 and routes._saved_request(eid).autonomy_mode == "autonomous"
    client.post(f"/api/campaigns/{cid}/experiments/{eid}/stop")

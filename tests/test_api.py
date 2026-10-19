import time

import pytest
from fastapi.testclient import TestClient

from aurora.api.app import create_app


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


def wait_done(client, cid, eid, timeout=60):
    for _ in range(timeout * 10):
        exp = client.get(f"/api/campaigns/{cid}/experiments/{eid}").json()
        if exp["status"] not in ("starting", "running"):
            return exp
        time.sleep(0.1)
    raise AssertionError("experiment did not finish")


def test_mock_experiment_end_to_end(client):
    cid = client.post("/api/campaigns/quick", json={"prompt": "A project"}).json()["campaign_id"]
    r = client.post(f"/api/campaigns/{cid}/experiments",
                    json={"instruction": "Investigate Zn segregation", "mock_mode": True, "skip_phases": []})
    assert r.status_code == 200, r.text
    eid = r.json()["experiment_id"]
    exp = wait_done(client, cid, eid)
    assert exp["status"] == "completed", exp["error"]
    assert exp["report"] and exp["events"]

    files = client.get(f"/api/campaigns/{cid}/experiments/{eid}/workspace/tree").json()["files"]
    assert "phases" in {f["name"] for f in files}
    report = client.get(f"/api/campaigns/{cid}/experiments/{eid}/workspace/file", params={"path": exp["report"]})
    assert report.status_code == 200 and report.json()["content"]
    assert client.post(f"/api/campaigns/{cid}/experiments/{eid}/decide", json={"action": "continue"}).status_code == 409
    assert client.post(f"/api/campaigns/{cid}/experiments/{eid}/rerun").status_code == 200


@pytest.mark.parametrize("path", ["../../etc/passwd", "/etc/passwd", "a/../../b", "x;rm -rf /"])
def test_rejects_unsafe_paths(client, path):
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    eid = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "x", "mock_mode": True}).json()["experiment_id"]
    wait_done(client, cid, eid)
    r = client.get(f"/api/campaigns/{cid}/experiments/{eid}/workspace/file", params={"path": path})
    assert r.status_code in (400, 404)


def test_missing_requirements_fail_the_request(client):
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    r = client.post(f"/api/campaigns/{cid}/experiments", json={"instruction": "x"})
    assert r.status_code == 400 and "cannot start" in r.json()["detail"]


def test_unknown_project_is_404(client):
    assert client.get("/api/campaigns/nope").status_code == 404


def test_console_experiments_get_a_default_budget(client):
    from aurora.api.routes import campaigns
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    campaign = campaigns._ensure_campaign(cid)
    req = campaigns.StartExperimentRequest(instruction="x", mock_mode=True)
    assert campaigns._experiment_config(campaign, "r1", req).budget.max_cost_usd == campaigns.WEB_DEFAULT_MAX_COST_USD
    req = campaigns.StartExperimentRequest(instruction="x", mock_mode=True, max_cost_usd=None)
    assert campaigns._experiment_config(campaign, "r2", req).budget.max_cost_usd is None


def test_stop_and_resume_in_the_console(client, monkeypatch):
    import asyncio

    from aurora.core.engine import ResearchEngine
    original = ResearchEngine._run_analysis

    async def slow_analysis(self):
        if not getattr(self, "_resumed_once", False):
            await asyncio.sleep(60)
        return await original(self)

    monkeypatch.setattr(ResearchEngine, "_run_analysis", slow_analysis)
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    eid = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "Investigate Zn segregation", "mock_mode": True,
                            "skip_phases": ["ideation"]}).json()["experiment_id"]
    for _ in range(100):
        if client.get(f"/api/campaigns/{cid}/experiments/{eid}").json()["phase"] == "analysis":
            break
        time.sleep(0.1)
    client.delete(f"/api/campaigns/{cid}/experiments/{eid}")
    stopped = wait_done(client, cid, eid)
    assert stopped["status"] == "stopped"
    events_before = stopped["event_count"]

    monkeypatch.setattr(ResearchEngine, "_resumed_once", True, raising=False)
    r = client.post(f"/api/campaigns/{cid}/experiments/{eid}/resume")
    assert r.status_code == 200, r.text
    done = wait_done(client, cid, eid)
    assert done["status"] == "completed", done["error"]
    assert done["event_count"] > events_before and done["report"]


def test_confirm_uses_the_chosen_ideation_mode(client, monkeypatch):
    from aurora.api.routes import campaigns
    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    campaign = campaigns._ensure_campaign(cid)
    proposal = campaigns.ExperimentProposal({"instruction": "Investigate Zn segregation", "ideation_mode": "roundtable"},
                                            [], "s")
    campaign.proposals[proposal.proposal_id] = proposal
    seen = {}
    original = campaigns._experiment_config

    def spy(c, run_id, req):
        cfg = original(c, run_id, req.model_copy(update={"mock_mode": True}))
        seen["mode"] = cfg.ideation.mode
        return cfg

    monkeypatch.setattr(campaigns, "_experiment_config", spy)
    r = client.post(f"/api/campaigns/{cid}/proposals/{proposal.proposal_id}/confirm",
                    json={"skip_phases": [], "ideation_mode": "independent"})
    assert r.status_code == 200, r.text
    assert seen["mode"] == "independent"
    wait_done(client, cid, r.json()["experiment_id"])


def test_settings_and_per_role_overrides(client, monkeypatch):
    from aurora.api.routes import campaigns
    s = client.get("/api/settings").json()
    assert s["models"]["agent"] and s["models"]["research"] == "" and s["autonomy_mode"] == "supervised"
    assert "anthropic/claude-opus-5.5" in s["model_suggestions"]

    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    campaign = campaigns._ensure_campaign(cid)
    proposal = campaigns.ExperimentProposal({"instruction": "x"}, ["ideation", "screening"], "s")
    campaign.proposals[proposal.proposal_id] = proposal
    seen = {}
    original = campaigns._experiment_config

    def spy(c, run_id, req):
        cfg = original(c, run_id, req)
        seen["cfg"] = cfg
        return original(c, run_id, req.model_copy(update={"mock_mode": True}))

    monkeypatch.setattr(campaigns, "_experiment_config", spy)
    r = client.post(f"/api/campaigns/{cid}/proposals/{proposal.proposal_id}/confirm", json={
        "autonomy_mode": "autonomous",
        "models": {"research": "anthropic/claude-opus-5.5", "supervisor": "anthropic/claude-haiku-5.5",
                   "writing": ""}})
    assert r.status_code == 200, r.text
    cfg = seen["cfg"]
    assert cfg.human.autonomy.value == "autonomous"
    assert cfg.models.for_phase("research") == "anthropic/claude-opus-5.5"
    assert cfg.models.for_phase("writing") == cfg.models.agent
    assert cfg.models.supervisor == "anthropic/claude-haiku-5.5"
    wait_done(client, cid, r.json()["experiment_id"])
    bad = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "x", "mock_mode": True, "models": {"reserch": "a/b"}})
    assert bad.status_code == 400 and "reserch" in bad.json()["detail"]

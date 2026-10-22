"""Forking a run from a phase or a job: the new run starts there, with what was made and spent until then."""

import json

from tests.test_api import client, wait_done  # noqa: F401


def test_fork_after_planning_with_something_done_differently(client):  # noqa: F811
    cid = client.post("/api/campaigns/quick", json={"prompt": "A project"}).json()["campaign_id"]
    eid = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "Investigate Zn segregation", "mock_mode": True, "skip_phases": []}).json()["experiment_id"]
    assert wait_done(client, cid, eid)["status"] == "completed"
    fp = client.get(f"/api/campaigns/{cid}/experiments/{eid}/forkpoints").json()
    labels = [p["label"] for p in fp["points"]]
    assert labels[:3] == ["after ideation", "after screening", "after planning"]
    assert any(label.startswith("after job") for label in labels) or "after preflight" in labels
    after_planning = next(p for p in fp["points"] if p["label"] == "after planning")
    assert client.post(f"/api/campaigns/{cid}/experiments/{eid}/fork", json={"point": after_planning["id"]}).status_code == 400
    r = client.post(f"/api/campaigns/{cid}/experiments/{eid}/fork",
                    json={"point": after_planning["id"], "name": "smaller cells", "guidance": "Use 3x3 cells only."})
    assert r.status_code == 200, r.text
    new = r.json()["experiment_id"]
    assert r.json()["forked_from"]["point"] == "after planning"
    exp = wait_done(client, cid, new)
    assert exp["status"] == "completed", exp["error"]
    types = [e["event_type"] for e in exp["events"]]
    phases = [e["data"]["phase"] for e in exp["events"] if e["event_type"] == "phase_started"]
    assert "ideation" not in phases and "planning" not in phases and phases[0] in ("preflight", "research")
    assert "campaign_started" in types
    from aurora.api.routes.campaigns import _get_run
    run = _get_run(cid, new)
    fork = json.loads((run.campaign_dir / "fork.json").read_text())
    assert fork["run_id"] == eid and fork["guidance"] == "Use 3x3 cells only."
    assert (run.campaign_dir / "phases" / "planning").is_dir()
    cmp = client.get(f"/api/campaigns/{cid}/compare?runs={eid},{new}").json()
    assert cmp["runs"][1]["forked_from"]["point"] == "after planning"


def test_a_fork_takes_another_idea(client, monkeypatch):  # noqa: F811
    from aurora.core.engine import ResearchEngine
    seen = []

    async def adopt(self, text, rerun=None):
        seen.append(text)
        p = self._objective_proposal()
        p.title = "The second idea"
        return p
    monkeypatch.setattr(ResearchEngine, "_adopt_researcher_proposal", adopt)
    cid = client.post("/api/campaigns/quick", json={"prompt": "A project"}).json()["campaign_id"]
    eid = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "Investigate Zn segregation", "mock_mode": True, "skip_phases": []}).json()["experiment_id"]
    wait_done(client, cid, eid)
    point = next(p for p in client.get(f"/api/campaigns/{cid}/experiments/{eid}/forkpoints").json()["points"]
                 if p["label"] == "after ideation")
    r = client.post(f"/api/campaigns/{cid}/experiments/{eid}/fork",
                    json={"point": point["id"], "direction": "Pursue idea_p02 instead"})
    exp = wait_done(client, cid, r.json()["experiment_id"])
    assert seen == ["Pursue idea_p02 instead"]
    assert any(e["event_type"] == "fork_direction" and e["data"]["title"] == "The second idea" for e in exp["events"])

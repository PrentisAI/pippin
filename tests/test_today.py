"""Today: what waits for a person from every project (a run's decision, what the memory flagged), what changed
since yesterday, in one call; examples are left out, and nothing is answered here."""

import time

from fastapi.testclient import TestClient


def _client(monkeypatch, campaigns, details):
    from aurora.api.app import create_app
    from aurora.api.routes import campaigns as cr

    async def list_campaigns():
        return campaigns

    async def get_campaign(cid):
        return details[cid]
    monkeypatch.setattr(cr, "list_campaigns", list_campaigns)
    monkeypatch.setattr(cr, "get_campaign", get_campaign)
    return TestClient(create_app())


def test_decisions_alerts_and_what_changed(monkeypatch):
    from aurora.memory.store import mem_store
    now = time.time()
    campaigns = [
        {"campaign_id": "p1", "name": "Cu-Zn", "needs_attention": True, "active_experiments": 1, "updated_at": now},
        {"campaign_id": "p2", "name": "Bikes", "needs_attention": False, "active_experiments": 0, "updated_at": now - 600},
        {"campaign_id": "ex", "name": "Example", "example": True, "needs_attention": True, "updated_at": now},
    ]
    gate = {"decision_id": "g1", "source": "milestone_audit", "context": "Rerun?\n\nmore", "options": ["continue", "instruct", "stop"],
            "created_at": now - 3600, "detail": {"title": "CO on Cu(211) has not converged", "detail": "Rerun finer or drop it?"}}
    details = {
        "p1": {"experiments": [{"run_id": "r3", "task_name": "run 3", "status": "awaiting_human", "started_at": now - 7200,
                                "finished_at": None, "pending_decision": gate}]},
        "p2": {"experiments": [{"run_id": "r2", "task_name": "run 2", "status": "completed", "started_at": now - 9000,
                                "finished_at": now - 1800, "cost_usd": 9.38, "pending_decision": None},
                               {"run_id": "r1", "task_name": "run 1", "status": "failed", "started_at": now - 5 * 86400,
                                "finished_at": now - 4 * 86400, "pending_decision": None}]},
    }
    mem_store.add_alert("local", "retraction", "now", "doi:10.1/x", "A paper you rely on was retracted", "Smith 2020", {"doi": "10.1/x"})
    c = _client(monkeypatch, campaigns, details)
    d = c.get("/api/today").json()
    kinds = [(n["kind"], n["title"]) for n in d["needs"]]
    assert kinds == [("decision", "CO on Cu(211) has not converged"), ("alert", "A paper you rely on was retracted")] or \
        kinds == [("alert", "A paper you rely on was retracted"), ("decision", "CO on Cu(211) has not converged")]
    dec = next(n for n in d["needs"] if n["kind"] == "decision")
    assert (dec["campaign_id"], dec["run_id"], dec["options"]) == ("p1", "r3", ["continue", "instruct", "stop"])
    texts = [x["text"] for x in d["changed"]]
    assert "Bikes · run 2 finished, $9.38" in texts and "Cu-Zn · run 3 started" in texts
    assert not any("run 1" in t for t in texts)                 # four days ago: not "since yesterday"
    assert d["projects"] == 2 and d["running"] == 1             # the example is left out
    # seen alerts leave the page
    mem_store.update_alert(next(n for n in d["needs"] if n["kind"] == "alert")["id"], seen_at=time.time())
    assert [n["kind"] for n in c.get("/api/today").json()["needs"]] == ["decision"]

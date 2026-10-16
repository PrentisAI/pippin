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

"""The first minutes: what is missing and how to fix it, the key without accounts, and the demo."""

import os

import httpx

from tests.test_api import client, wait_done  # noqa: F401


def test_the_checklist_says_what_is_missing(client, monkeypatch):  # noqa: F811
    r = client.get("/api/setup").json()
    key = next(c for c in r["checks"] if c["key"] == "model_key")
    assert key["required"] and not key["ok"] and key["action"] == "key" and r["local"]
    assert not r["ready"]
    assert {c["key"] for c in r["checks"]} >= {"model_key", "claude", "docker", "slurm", "notify", "openalex"}
    client.post("/api/setup/dismiss")
    assert client.get("/api/setup").json()["dismissed"]


def test_the_server_key_from_the_console(client, monkeypatch, tmp_path):  # noqa: F811
    from aurora.api.routes import setup
    real = httpx.AsyncClient
    monkeypatch.setattr(setup.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(401 if "bad" in req.headers["authorization"] else 200, json={}))))
    assert client.put("/api/setup/key", json={"key": "not-a-key"}).status_code == 400
    assert client.put("/api/setup/key", json={"key": "sk-or-v1-bad" + "x" * 30}).status_code == 400
    r = client.put("/api/setup/key", json={"key": "sk-or-v1-" + "a" * 40})
    assert r.status_code == 200 and next(c for c in r.json()["checks"] if c["key"] == "model_key")["ok"]
    env = setup._env_path()
    assert env.read_text().strip() == "OPENROUTER_API_KEY=sk-or-v1-" + "a" * 40 and oct(env.stat().st_mode)[-3:] == "600"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert os.environ.get("OPENROUTER_API_KEY") is None


def test_the_demo_needs_no_key_and_no_docker(client):  # noqa: F811
    r = client.post("/api/demo")
    assert r.status_code == 200, r.text
    exp = wait_done(client, r.json()["campaign_id"], r.json()["experiment_id"])
    assert exp["status"] == "completed", exp["error"]
    assert exp["report"]
    names = [c["name"] for c in client.get("/api/campaigns/").json()]
    assert any(n.startswith("Demo: Zn segregation") for n in names)

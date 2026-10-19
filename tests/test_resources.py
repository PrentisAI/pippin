"""Container CPU and memory limits: from the config, on the proposal card, and changed while running."""

import time

import pytest
from fastapi.testclient import TestClient

from aurora.api.app import create_app
from aurora.config import AuroraConfig
from aurora.core import docker_backend
from aurora.core.docker_backend import DockerBackend
from aurora.core.engine import ResearchEngine


async def test_running_container_is_updated_in_place(monkeypatch):
    calls = []

    async def fake_run(*args, timeout=0):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(docker_backend, "_run", fake_run)
    backend = DockerBackend(cpus=4, memory_mb=8192)
    backend.container_id = "abc123"
    await backend.update_resources(32, 65536)
    assert calls == [("docker", "update", "--cpus", "32", "--memory", "65536m", "--memory-swap", "131072m",
                      "abc123")]
    assert backend.resources == (32, 65536)


async def test_engine_update_tells_the_agent_and_publishes(tmp_path):
    engine = ResearchEngine(AuroraConfig(objective="x", mock_mode=True, campaign_id="r1",
                                         workspace={"root": str(tmp_path)}))
    events, told = [], []

    async def record(event):
        events.append((event.name, event.data))

    class Runner:
        async def inject_message(self, text):
            told.append(text)

    engine.event_bus.subscribe_all(record)
    engine.current_iteration_runner = Runner()
    result = await engine.update_resources(16, 32768)
    assert (result["cpus"], result["memory_mb"]) == (16, 32768)
    assert engine._config.sandbox.cpus == 16
    assert events[-1][0] == "resources_updated" and events[-1][1]["old_memory_mb"] == 8192
    assert told and "16 CPUs" in told[0] and "32 GB" in told[0]


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


def test_console_resources_are_checked_and_kept_for_a_resume(client, monkeypatch):
    async def limits():
        return 64, 256 * 1024

    monkeypatch.setattr(docker_backend, "host_limits", limits)
    settings = client.get("/api/settings").json()["sandbox"]
    assert settings["host_cpus"] == 64 and settings["cpus"] > 0

    cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    eid = client.post(f"/api/campaigns/{cid}/experiments",
                      json={"instruction": "x", "mock_mode": True, "skip_phases": ["ideation", "screening"],
                            "cpus": 8, "memory_mb": 16384}).json()["experiment_id"]
    for _ in range(600):
        exp = client.get(f"/api/campaigns/{cid}/experiments/{eid}").json()
        if exp["status"] not in ("starting", "running"):
            break
        time.sleep(0.1)
    assert exp["resources"]["cpus"] == 8 and exp["resources"]["memory_mb"] == 16384

    url = f"/api/campaigns/{cid}/experiments/{eid}/resources"
    assert client.put(url, json={"cpus": 128}).status_code == 400          # more than the host has
    assert client.put(url, json={}).status_code == 400
    r = client.put(url, json={"cpus": 24, "memory_mb": 49152})
    assert r.status_code == 200 and r.json()["live"] is False
    rerun = client.post(f"/api/campaigns/{cid}/experiments/{eid}/rerun").json()
    new = client.get(f"/api/campaigns/{cid}/experiments/{rerun['experiment_id']}").json()
    assert (new["resources"]["cpus"], new["resources"]["memory_mb"]) == (24, 49152)


async def test_workspace_is_mounted_as_app(monkeypatch, tmp_path):
    calls = []

    async def fake_run(*args, timeout=0):
        calls.append(args)
        return 0, "abc123def4567\n", ""

    monkeypatch.setattr(docker_backend, "_run", fake_run)
    backend = DockerBackend(campaign_dir=str(tmp_path), workspace_dir=str(tmp_path / "workspace"))
    await backend.setup()
    run = next(c for c in calls if c[:2] == ("docker", "run"))
    assert f"{(tmp_path / 'workspace').resolve()}:/app" in run and backend.mounted
    assert await backend.extract_workspace(tmp_path / "elsewhere")          # nothing to copy
    assert not any(c[:2] == ("docker", "cp") for c in calls)
    await backend.teardown()
    chown = next(c for c in calls if "chown -R" in " ".join(c))
    assert "/app" in chown[-1]                                             # files handed back first
    assert calls.index(chown) < next(i for i, c in enumerate(calls) if c[:2] == ("docker", "rm"))

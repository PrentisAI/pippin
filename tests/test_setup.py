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
    from aurora.paths import aurora_home
    monkeypatch.setattr(httpx, "get", lambda url, headers, timeout: httpx.Response(
        401 if "bad" in headers["Authorization"] else 200, json={}))
    assert client.put("/api/setup/key", json={"key": "not-a-key"}).status_code == 400
    assert client.put("/api/setup/key", json={"key": "sk-or-v1-bad" + "x" * 30}).status_code == 400
    r = client.put("/api/setup/key", json={"key": "sk-or-v1-" + "a" * 40})
    assert r.status_code == 200 and next(c for c in r.json()["checks"] if c["key"] == "model_key")["ok"]
    env = aurora_home() / ".env"
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


def _fake_installs(monkeypatch, setup, code=None):
    """Installs that are started but not run: each Popen is recorded, with the exit code to report."""
    started = []

    class Proc:
        def __init__(self, cmd, **kw):
            started.append(tuple(cmd))
            self.code = code

        def poll(self):
            return self.code

    monkeypatch.setattr(setup.subprocess, "Popen", Proc)
    monkeypatch.setattr(setup, "_installs", {})
    return started


def test_claude_code_is_installed_in_the_background(client, monkeypatch):  # noqa: F811
    from aurora.api.routes import setup
    ran = []
    monkeypatch.setattr(setup, "_run", lambda cmd, timeout=15: (ran.append(tuple(cmd)), (1, ""))[1])
    started = _fake_installs(monkeypatch, setup)
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    r = client.post("/api/setup/claude")
    assert r.status_code == 200, r.text
    assert started == [("/usr/bin/npm", "install", "-g", "@anthropic-ai/claude-code")]
    claude = next(c for c in r.json()["checks"] if c["key"] == "claude")
    assert claude["action"] == "installing"
    # a second click while it runs starts nothing more
    client.post("/api/setup/claude")
    assert len(started) == 1
    # it failed: what went wrong is shown, and it can be started again
    setup._installs["claude"]["proc"].code = 1
    with open(setup._installs["claude"]["log"], "w") as f:
        f.write("npm ERR! network")
    claude = next(c for c in client.get("/api/setup").json()["checks"] if c["key"] == "claude")
    assert claude["action"] == "install" and "npm ERR! network" in claude["error"]
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    assert client.post("/api/setup/claude").status_code == 400


def test_an_install_from_before_a_restart_is_not_started_twice(client, monkeypatch):  # noqa: F811
    from aurora.api.routes import setup
    started = _fake_installs(monkeypatch, setup)
    monkeypatch.setattr(setup, "_run", lambda cmd, timeout=15: (0, "") if cmd[0] == "pgrep" else (1, ""))
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    r = client.post("/api/setup/claude")
    assert started == [] and next(c for c in r.json()["checks"] if c["key"] == "claude")["action"] == "installing"


def test_docker_desktop_is_left_to_the_person(client, monkeypatch):  # noqa: F811
    import sys

    from aurora.api.routes import setup
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(setup.os.path, "exists", lambda p: False)
    monkeypatch.setattr(setup, "_run", lambda cmd, timeout=15: (1, ""))
    docker = next(c for c in client.get("/api/setup").json()["checks"] if c["key"] == "docker")
    assert not docker["ok"] and docker["action"] == "download"
    assert client.post("/api/setup/docker/install").status_code in (404, 405)


def test_a_docker_desktop_installed_by_hand_is_linked(client, monkeypatch, tmp_path):  # noqa: F811
    import sys

    from aurora.api.routes import setup
    app_bin, brew_bin = tmp_path / "Docker.app" / "Contents" / "Resources" / "bin", tmp_path / "brew" / "bin"
    app_bin.mkdir(parents=True)
    brew_bin.mkdir(parents=True)
    for name in ("docker", "docker-credential-desktop", "kubectl"):
        (app_bin / name).write_text("")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(setup, "DOCKER_APP", str(tmp_path / "Docker.app"))
    monkeypatch.setattr(setup, "DOCKER_BIN", str(app_bin))
    monkeypatch.setattr(setup, "_run", lambda cmd, timeout=15: (1, "") if cmd[0] == "pgrep" else (0, ""))
    monkeypatch.setattr(setup, "_installs", {})
    monkeypatch.setattr(setup.shutil, "which", lambda name: str(brew_bin / "brew") if name == "brew" else None)
    docker = next(c for c in client.get("/api/setup").json()["checks"] if c["key"] == "docker")
    assert docker["action"] == "link" and docker["link_dir"] == str(brew_bin)
    r = client.post("/api/setup/docker/link")
    assert r.status_code == 200, r.text
    # the CLI and its helpers, not everything else Docker Desktop ships
    assert sorted(p.name for p in brew_bin.iterdir()) == ["docker", "docker-credential-desktop"]
    assert (brew_bin / "docker").resolve() == (app_bin / "docker").resolve()
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    r = client.post("/api/setup/docker/link")
    assert r.status_code == 400 and "sudo ln -s" in r.json()["detail"]


def test_the_person_says_where_docker_is(client, monkeypatch, tmp_path):  # noqa: F811
    import os

    from aurora.api.routes import setup
    from aurora.paths import aurora_home
    bin_dir = tmp_path / ".docker" / "bin"
    bin_dir.mkdir(parents=True)
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\necho 'Docker version 29.9.2, build x'\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("AURORA_DOCKER_DIR", raising=False)      # put back after the test
    monkeypatch.setattr(setup, "_run", lambda cmd, timeout=15: (0, "Docker version 29.9.2") if cmd[-1] == "--version" else (1, ""))
    assert client.put("/api/setup/docker/path", json={"path": str(tmp_path / "nothing")}).status_code == 400
    # the directory or the command itself, as `which docker` prints it
    r = client.put("/api/setup/docker/path", json={"path": f" '{bin_dir}' "})
    assert r.status_code == 200, r.text
    assert os.environ["PATH"].split(os.pathsep)[0] == str(bin_dir)
    assert f"AURORA_DOCKER_DIR={bin_dir}" in (aurora_home() / ".env").read_text()
    monkeypatch.setattr(setup, "_run", lambda cmd, timeout=15: (0, "not it"))
    assert client.put("/api/setup/docker/path", json={"path": str(docker)}).status_code == 400


def test_a_docker_directory_from_env_goes_on_path(tmp_path, monkeypatch):
    import os

    from aurora.env import load_dotenv
    env = tmp_path / ".env"
    env.write_text("AURORA_DOCKER_DIR=/opt/docker/bin\n")
    monkeypatch.delenv("AURORA_DOCKER_DIR", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")
    load_dotenv(env)
    assert os.environ["PATH"].split(os.pathsep)[0] == "/opt/docker/bin"

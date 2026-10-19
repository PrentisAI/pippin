"""Research agents inside the container: the key stays in the proxy, the agent gets a token."""

import asyncio
import importlib
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aurora.core import docker_backend
from aurora.core.docker_backend import DockerBackend
from aurora.llm.agent_loops import ClaudeCodeLoop


@pytest.fixture()
def proxy(monkeypatch):
    """The key proxy in front of a fake upstream that reports the key it was given."""
    seen = {}

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["auth"] = self.headers.get("authorization")
            seen["path"] = self.path
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self.send_response(200)
            self.end_headers()
            for part in (b"data: one\n\n", b"data: two\n\n"):    # a streamed answer
                self.wfile.write(part)
                self.wfile.flush()

        def log_message(self, *a):
            pass

    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    monkeypatch.setenv("AURORA_UPSTREAM", f"http://127.0.0.1:{up.server_address[1]}/api")
    monkeypatch.setenv("AURORA_KEY", "sk-real")
    monkeypatch.setenv("AURORA_TOKEN", "tok123")
    from aurora.core import key_proxy
    importlib.reload(key_proxy)
    px = ThreadingHTTPServer(("127.0.0.1", 0), key_proxy.Handler)
    threading.Thread(target=px.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{px.server_address[1]}", seen
    px.shutdown()
    up.shutdown()


def post(url, headers):
    req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


def test_proxy_adds_the_key_only_for_the_token(proxy):
    base, seen = proxy
    status, body = post(f"{base}/v1/messages", {"Authorization": "Bearer tok123"})
    assert status == 200 and body == b"data: one\n\ndata: two\n\n"
    assert seen == {"auth": "Bearer sk-real", "path": "/api/v1/messages"}
    for url, headers in ((f"{base}/v1/messages", {"Authorization": "Bearer wrong"}),
                         (f"{base}/v1/messages", {}),
                         (f"{base}/admin", {"Authorization": "Bearer tok123"})):
        with pytest.raises(urllib.error.HTTPError) as err:
            post(url, headers)
        assert err.value.code == 403


async def test_backend_keeps_the_key_out_of_the_campaign_container(monkeypatch, tmp_path):
    calls = []

    async def fake_run(*args, timeout=0):
        calls.append(args)
        if args[:3] == ("docker", "run", "-d") and "--env-file" in args:
            env_file = args[args.index("--env-file") + 1]
            calls.append(("env-file", open(env_file).read()))
        return 0, "c0ffee1234567\n", ""

    monkeypatch.setattr(docker_backend, "_run", fake_run)
    backend = DockerBackend(campaign_dir=str(tmp_path), workspace_dir=str(tmp_path / "workspace"), allow_network=False,
                            agent={"claude": "/usr/bin/claude", "upstream": "https://up/api", "key": "sk-real"})
    await backend.setup()
    assert "sk-real" in next(c[1] for c in calls if c[0] == "env-file")       # only in the proxy's env file
    assert not any("sk-real" in " ".join(map(str, c)) for c in calls if c[0] != "env-file")
    net = next(c for c in calls if c[:3] == ("docker", "network", "create"))
    assert "--internal" in net
    agent_run = [c for c in calls if c[:3] == ("docker", "run", "-d")][-1]
    assert agent_run[agent_run.index("--network") + 1] == net[-1]               # no internet: the proxy only
    assert "/usr/bin/claude:/aurora/bin/claude:ro" in agent_run
    assert f"{tmp_path.resolve()}:{tmp_path.resolve()}:ro" in agent_run
    assert not any(c[:4] == ("docker", "network", "connect", "bridge") for c in calls)
    assert backend.agent_env["ANTHROPIC_AUTH_TOKEN"] and backend.agent_env["ANTHROPIC_API_KEY"] == ""
    await backend.teardown()
    assert any(c[:3] == ("docker", "network", "rm") for c in calls)


async def test_loop_starts_claude_in_the_container(monkeypatch, tmp_path):
    captured = {}

    async def fake_exec(*cmd, env=None, **kw):
        captured["cmd"], captured["env"] = cmd, env
        raise RuntimeError("stop here")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    loop = ClaudeCodeLoop(model="m", api_key="sk-real", base_url="https://openrouter.ai/api", cwd=str(tmp_path),
                          docker_container="c1", container_agent={"container": "c1", "ANTHROPIC_AUTH_TOKEN": "tok",
                                                                  "HOME": "/aurora/home"})
    with pytest.raises(RuntimeError):
        await loop.run("sys", "task")
    cmd = captured["cmd"]
    assert cmd[:3] == ("docker", "exec", "-i") and "c1" in cmd and "ANTHROPIC_AUTH_TOKEN=tok" in cmd
    assert "Bash" in cmd and not any(str(x).startswith("Bash(docker exec") for x in cmd)
    assert "sk-real" not in " ".join(cmd) and not any("sk-real" in v for v in captured["env"].values())

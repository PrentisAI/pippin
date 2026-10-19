"""Notifications: what each event sends, the channel formats, delivery, and the console settings."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from aurora.api.app import create_app
from aurora.config import AuroraConfig, NotificationsConfig, NotifyChannel
from aurora.core.engine import ResearchEngine
from aurora.core.events import Event
from aurora.notify import Notifier, _request, _serverchan_url, deliver, describe


class _Hook(BaseHTTPRequestHandler):
    received: list = []
    reply = b'{"errcode": 0}'

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Hook.received.append((self.path, json.loads(body)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_Hook.reply)

    def log_message(self, *args):
        pass


@pytest.fixture()
def hook():
    _Hook.received = []
    _Hook.reply = b'{"errcode": 0}'
    server = HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_events_become_notifications():
    gate = describe(Event("human_gate_opened", {"title": "Pre-flight failed", "detail": "the data URL is 404"}), "Citi")
    assert gate == ("decision_needed", "Decision needed: Citi", "Pre-flight failed\n\nthe data URL is 404")
    done = describe(Event("campaign_completed", {"summary": "done", "cost_usd": 3.2, "wall_time_seconds": 4000}), "Citi")
    assert done[0] == "run_finished" and "$3.20" in done[2] and "1 h 6 min" in done[2]
    # a run the researcher stopped needs no message; one a budget stopped does
    assert describe(Event("campaign_stopped", {"by_user": True}), "x") is None
    assert describe(Event("campaign_stopped", {"by_user": False, "summary": "cost budget used up"}), "x")[0] == "run_failed"
    assert describe(Event("tool_call_start", {}), "x") is None


def test_channel_formats():
    wecom = NotifyChannel(type="wecom", target="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k")
    url, body = _request(wecom, "run_finished", "Finished: x", "text", "http://c/e", {})
    assert body["msgtype"] == "markdown" and "[Open in AURORA](http://c/e)" in body["markdown"]["content"]
    feishu = NotifyChannel(type="feishu", target="https://open.feishu.cn/hook/x")
    assert _request(feishu, "k", "t", "x", "http://c", {})[1]["card"]["elements"][1]["actions"][0]["url"] == "http://c"
    sc = NotifyChannel(type="serverchan", target="SCT123abc")
    url, body = _request(sc, "k", "A very long title that goes on and on and on", "x", "http://c", {})
    assert url == "https://sctapi.ftqq.com/SCT123abc.send" and len(body["title"]) <= 32
    assert _serverchan_url("sctp4242tABC") == "https://4242.push.ft07.com/send/sctp4242tABC.send"
    with pytest.raises(ValueError):
        NotifyChannel(type="slack", target="x", events=["everything"])


async def test_delivery_and_errors(hook):
    channel = NotifyChannel(type="webhook", target=f"{hook}/aurora")
    assert await deliver(channel, "run_finished", "Finished: x", "ok", "http://c/e", {"experiment_id": "e1"}) == ""
    path, body = _Hook.received[-1]
    assert path == "/aurora" and body["event"] == "run_finished" and body["experiment_id"] == "e1"
    _Hook.reply = b'{"errcode": 93000, "errmsg": "invalid webhook url"}'     # 200 with an error code
    error = await deliver(NotifyChannel(type="wecom", target=hook), "k", "t", "x")
    assert "93000" in error and "invalid webhook" in error
    assert await deliver(NotifyChannel(type="webhook", target="http://127.0.0.1:1/x"), "k", "t", "x")


async def test_notifier_filters_and_dedupes(hook):
    config = NotificationsConfig(channels=[
        NotifyChannel(type="webhook", target=f"{hook}/a", events=["decision_needed"]),
        NotifyChannel(type="webhook", target=f"{hook}/b", events=["run_finished"], enabled=False),
    ])
    n = Notifier(lambda: config, run_name="Citi", link="http://c/e")
    gate = Event("human_gate_opened", {"title": "t", "detail": "d"})
    await n.on_event(gate)
    await n.on_event(gate)                                   # the same again at once: dropped
    await n.on_event(Event("campaign_completed", {"summary": "s"}))   # its channel is off
    await n.drain()
    assert [p for p, _ in _Hook.received] == ["/a"]
    assert _Hook.received[0][1]["url"] == "http://c/e"


async def test_a_campaign_notifies_when_it_ends(tmp_path, hook):
    cfg = AuroraConfig(objective="x", mock_mode=True, campaign_id="n1", workspace={"root": str(tmp_path)},
                       notifications={"channels": [{"type": "webhook", "target": f"{hook}/done",
                                                    "events": ["run_finished", "run_failed"]}]})
    engine = ResearchEngine(cfg)
    result = await engine.run_campaign()
    assert result.status == "completed"
    assert [b["event"] for _, b in _Hook.received] == ["run_finished"]
    saved = (tmp_path / "n1" / "config.yaml").read_text()
    assert "/done" not in saved                    # webhook URLs are not left where agents read


async def test_budget_warning_once(tmp_path):
    cfg = AuroraConfig(objective="x", mock_mode=True, campaign_id="n2", workspace={"root": str(tmp_path)},
                       budget={"max_hours": 1, "max_cost_usd": 10})
    engine = ResearchEngine(cfg)
    seen = []

    async def grab(event):
        seen.append(event)
    engine._bus.subscribe("budget_warning", grab)
    import time
    engine._started_at = time.time() - 0.85 * 3600
    await engine._warn_budget()
    await engine._warn_budget()
    assert len(seen) == 1 and seen[0].data["share"] >= 0.8 and "hours" in seen[0].data["detail"]


def test_console_settings_keep_secrets(hook):
    with TestClient(create_app()) as client:
        r = client.put("/api/settings/notifications", json={
            "console_url": "http://localhost:8100/",
            "channels": [{"type": "wecom", "name": "phone", "target": "https://qyapi.weixin.qq.com/send?key=SECRET1234"}]})
        assert r.status_code == 200, r.text
        ch = r.json()["channels"][0]
        assert "SECRET" not in json.dumps(r.json()) and ch["target_hint"].endswith("1234") and ch["id"]
        assert r.json()["console_url"] == "http://localhost:8100"
        # saved again without the target (as the console does): the key stays
        r = client.put("/api/settings/notifications", json={
            "console_url": "", "channels": [{**ch, "target": "", "events": ["run_finished"]}]})
        from aurora.api.routes.campaigns import live_notifications
        saved = live_notifications().channels[0]
        assert saved.target.endswith("SECRET1234") and saved.events == ["run_finished"]
        assert client.put("/api/settings/notifications", json={
            "channels": [{"type": "slack", "target": ""}]}).status_code == 400
        t = client.post("/api/settings/notifications/test", json={"type": "webhook", "target": f"{hook}/t"}).json()
        assert t == {"ok": True, "error": ""} and _Hook.received[-1][1]["event"] == "test"


def test_web_runs_link_to_the_experiment(monkeypatch):
    from aurora.api.routes import campaigns as routes
    monkeypatch.setenv("AURORA_CONSOLE_URL", "http://localhost:8100")
    with TestClient(create_app()) as client:
        cid = client.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
        campaign = routes._ensure_campaign(cid)
        run = routes.ExperimentRunState("e1", cid, "My run", "m")
        n = routes._notifier(campaign, run)
        assert n.link == f"http://localhost:8100/campaigns/{cid}/experiments/e1" and n.run_name == "My run"

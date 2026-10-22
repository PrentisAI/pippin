"""Answering a decision from a notification, and quiet hours."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aurora.config import NotificationsConfig, NotifyChannel
from aurora.core.events import Event
from aurora.human import actions
from aurora.notify import Notifier, _request, held_digest, in_quiet_hours


def test_quiet_hours_across_midnight():
    cfg = NotificationsConfig(quiet_start="22:00", quiet_end="07:30")
    assert in_quiet_hours(cfg, datetime(2026, 10, 22, 23, 5)) and in_quiet_hours(cfg, datetime(2026, 10, 22, 6, 0))
    assert not in_quiet_hours(cfg, datetime(2026, 10, 22, 12, 0))
    assert not in_quiet_hours(NotificationsConfig(), datetime(2026, 10, 22, 23, 0))
    day = NotificationsConfig(quiet_start="12:00", quiet_end="13:00")
    assert in_quiet_hours(day, datetime(2026, 10, 22, 12, 30)) and not in_quiet_hours(day, datetime(2026, 10, 22, 13, 0))
    title, text = held_digest([{"kind": "run_finished", "title": "Finished: a", "text": "done\nmore", "url": "http://x"}])
    assert "1" in title and "Finished: a: done (http://x)" in text


def test_decision_links_in_each_channel():
    links = [("Approve and continue", "http://c/a/t1"), ("Stop the experiment", "http://c/a/t2")]
    for kind in ("wecom", "feishu", "slack", "serverchan", "webhook"):
        _, body = _request(NotifyChannel(type=kind, target="https://hook/x"), "decision_needed", "T", "detail", "http://c/run", {}, links)
        flat = str(body)
        assert "http://c/a/t1" in flat and "http://c/a/t2" in flat, kind
    _, card = _request(NotifyChannel(type="feishu", target="https://hook/x"), "decision_needed", "T", "d", "http://c/run", {}, links)
    assert [b["text"]["content"] for b in card["card"]["elements"][1]["actions"]] == ["Open in AURORA", "Approve and continue", "Stop the experiment"]


async def test_held_in_quiet_hours_and_links_added():
    cfg = NotificationsConfig(quiet_start="00:00", quiet_end="23:59", quiet_except=["decision_needed"],
                              channels=[NotifyChannel(type="webhook", target="https://hook/x",
                                                      events=["decision_needed", "run_finished"])])
    held, sent = [], []
    n = Notifier(lambda: cfg, run_name="r", actions=lambda e: [("Approve and continue", "http://c/a/x")], hold=held.append)

    async def fake_send(channels, kind, title, text, acts=None):
        sent.append((kind, acts))
    n._send_all = fake_send
    await n.on_event(Event(name="campaign_completed", data={"summary": "s"}, source="e"))
    await n.on_event(Event(name="human_gate_opened", data={"gate_id": "g1", "title": "Go on?", "detail": "d"}, source="e"))
    await asyncio.sleep(0)
    assert [h["kind"] for h in held] == ["run_finished"]
    assert sent == [("decision_needed", [("Approve and continue", "http://c/a/x")])]


def test_a_decision_link_asks_then_acts(monkeypatch):
    from aurora.api.app import create_app
    c = TestClient(create_app())
    cid = c.post("/api/campaigns/quick", json={"prompt": "p"}).json()["campaign_id"]
    from aurora.api.routes.campaigns import _ensure_campaign
    resolved = []

    class Gate:
        pending = [SimpleNamespace(gate_id="g1", title="Screening is done: start planning?", detail="score 7.1")]

        def resolve(self, gate_id, action, message="", *, decided_by="human", rerun=None, by=""):
            resolved.append((gate_id, action))
            self.pending = []
            return True
    run = SimpleNamespace(task_name="weekend demand", engine=SimpleNamespace(human_gate=Gate()), started_by="local")
    _ensure_campaign(cid).experiments["r1"] = run
    token = actions.make(cid, "r1", "g1", "continue", "local")
    page = c.get(f"/a/{token}")
    assert page.status_code == 200 and "Screening is done" in page.text and "Approve and continue" in page.text
    assert resolved == []                            # opening never acts
    done = c.post(f"/a/{token}")
    assert done.status_code == 200 and "Approved" in done.text and resolved == [("g1", "continue")]
    assert "already made" in c.get(f"/a/{token}").text
    assert c.get(f"/a/{token[:-3]}xyz").status_code == 410
    expired = actions.make(cid, "r1", "g1", "stop", "local", ttl=-1)
    assert "expired" in c.get(f"/a/{expired}").text

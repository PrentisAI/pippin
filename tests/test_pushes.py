"""Pippin speaks first: how a running experiment is getting on, and the feed twice a day, in your chat apps."""

import asyncio
import time
from datetime import datetime

from aurora.config import NotificationsConfig
from aurora.core.events import Event
from aurora.feed import service as svc
from aurora.notify import Notifier
from tests.test_feed import _project, _refresh, client, sources  # noqa: F401  (fixtures)


def _ev(name, **data):
    return Event(name=name, data=data, source="engine")


async def test_progress_at_each_phase_and_every_few_hours():
    cfg = NotificationsConfig(progress_every_hours=3)
    said = []

    async def direct(kind, title, text):
        said.append((kind, title, text))
    n = Notifier(lambda: cfg, run_name="Weekend demand", direct=direct)
    await n.on_event(_ev("phase_started", phase="planning"))       # the first phase: the start was announced already
    await n.on_event(_ev("phase_started", phase="research"))
    await n.on_event(_ev("job_started", job_id="j1", title="clean trips", index=1, total=2))
    await n.on_event(_ev("job_started", job_id="j2", title="fit model", index=2, total=2))
    await n.on_event(_ev("iteration_complete", job_id="j1", decision="done", cost_usd=0.5))
    await asyncio.sleep(0)
    assert [s[1] for s in said] == ["Weekend demand: now research"]
    assert "the research agent works on the jobs" in said[0][2]

    assert not n.progress_due(time.time() + 3600)                  # not yet
    assert n.progress_due(time.time() + 4 * 3600)
    await asyncio.sleep(0)
    kind, title, text = said[-1]
    assert kind == "progress" and title == "Progress: Weekend demand"
    assert "Jobs: 1 of 2 done; running fit model" in text and "job clean trips done" in text and "$0.50" in text
    assert not n.progress_due(time.time() + 8 * 3600)              # nothing happened since
    await n.on_event(_ev("campaign_completed", summary="ok"))
    assert n.progress.ended and not n.progress_due(time.time() + 99 * 3600)


async def test_progress_can_be_turned_off():
    cfg = NotificationsConfig(progress_every_hours=0, progress_phases=False)
    said = []

    async def direct(kind, title, text):
        said.append(kind)
    n = Notifier(lambda: cfg, run_name="r", direct=direct)
    for p in ("planning", "research"):
        await n.on_event(_ev("phase_started", phase=p))
    await n.on_event(_ev("iteration_complete", job_id="j1", decision="done"))
    await asyncio.sleep(0)
    assert not n.progress_due(time.time() + 99 * 3600) and said == []


def test_the_digest_twice_a_day_in_the_chat_app_and_read_2(client, monkeypatch):  # noqa: F811
    from aurora import im
    from aurora.auth.store import auth_store
    cid = _project(client)
    _refresh(client)
    pushed = []

    async def push(uid, title, text, url=""):
        pushed.append((title, text))
        return True
    monkeypatch.setattr(im, "push", push)
    monkeypatch.setattr(im, "links", lambda uid: [{"app": "feishu", "name": "", "at": 0}])
    monkeypatch.setattr(im, "ready", lambda app: True)
    r = client.put("/api/feed/settings", json={"digest": {"at": "8:00", "at2": "18:30", "skip": False}})
    assert r.status_code == 200, r.text
    d = r.json()["digest"]
    assert (d["at"], d["at2"], d["chat"], d["linked"]) == ("08:00", "18:30", True, ["feishu"])
    assert client.put("/api/feed/settings", json={"digest": {"at2": "6pm"}}).status_code == 400

    morning, evening = datetime.now().replace(hour=9, minute=0), datetime.now().replace(hour=19, minute=0)
    assert svc._due("local", morning).endswith("08:00") and svc._due("local", evening).endswith("18:30")
    assert svc._due("local", datetime.now().replace(hour=7, minute=0)) == ""
    auth_store.set_pref("local", "feed_digest_sent", svc._due("local", morning))
    assert svc._due("local", morning.replace(hour=11)) == ""           # this half's was sent
    assert svc._due("local", evening)                                   # the evening's is still due

    from aurora.auth.permissions import LOCAL_USER
    assert asyncio.run(svc.send_digest(LOCAL_USER)) == ""
    title, text = pushed[-1]
    assert title.startswith("Pippin briefing · ") and "1. " in text and "read 2" in text
    numbered = auth_store.get_pref("local", "feed_digest_items")
    assert numbered

    from aurora.api.routes import reader
    monkeypatch.setattr(reader, "companion_read", _fake_companion)
    from aurora.im import _as
    said = asyncio.run(_as("local", lambda: svc.digest_word("local", "读 1")))
    assert said.startswith("已保存「") and "Pippin 正在读它" in said
    assert numbered[0] in client.get("/api/feed/saved").text or client.get("/api/feed/saved").json()["items"]
    assert asyncio.run(_as("local", lambda: svc.digest_word("local", "read 99"))) == f"The digest had {len(numbered)} items."
    assert asyncio.run(svc.digest_word("local", "what about the second one?")) is None
    r = client.post(f"/api/feed/items/{numbered[0]}/read")
    assert r.status_code == 200 and r.json()["paper_id"]
    assert cid


async def _fake_companion(ident, body):
    return {"started": True}

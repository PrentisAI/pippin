"""Pippin in Slack and WhatsApp, and voice messages: each app's requests are checked by its signature, a voice message
is heard and answered in writing (and aloud when a voice is set up), and notifications reach Slack and WhatsApp."""

import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from aurora import im
from aurora.memory import voice


@pytest.fixture()
def client():
    from aurora.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def later(monkeypatch):
    jobs = []
    monkeypatch.setattr(im, "_later", lambda coro: jobs.append(coro))
    return jobs


@pytest.fixture()
def turn(monkeypatch):
    asked = []

    async def fake(uid, text, app):
        asked.append((uid, text, app))
        return "Two runs are going."
    monkeypatch.setattr(im, "_turn", fake)
    return asked


@pytest.fixture()
def quiet(monkeypatch):
    """No voice answers (the written ones are what is checked)."""
    async def none(*a, **k):
        return None
    monkeypatch.setattr(im, "_say_aloud", none)


def _slack(client, body: dict, secret="ss", ts=None):
    raw = json.dumps(body).encode()
    ts = str(int(ts if ts is not None else time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
    return client.post("/im/slack", content=raw, headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
                                                          "Content-Type": "application/json"})


def _link(app, account):
    im._bind(app, account, im.new_code("local"))


async def test_slack_checks_its_signature_and_answers_a_direct_message(client, later, turn, quiet, monkeypatch):
    im.save_settings("slack", {"bot_token": "xoxb-1", "signing_secret": "ss"})
    assert im.ready("slack")
    assert _slack(client, {"type": "url_verification", "challenge": "c1"}).json() == {"challenge": "c1"}
    assert _slack(client, {"type": "url_verification", "challenge": "c1"}, secret="wrong").status_code == 403
    assert _slack(client, {"type": "url_verification", "challenge": "c1"}, ts=time.time() - 600).status_code == 403
    calls = []

    async def call(method, **body):
        calls.append((method, body))
        return {"ok": True}
    monkeypatch.setattr(im, "_slack_call", call)
    _link("slack", "U1")
    dm = {"type": "event_callback", "event_id": "Ev1",
          "event": {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1", "text": "how are my runs?", "ts": "1.1"}}
    _slack(client, dm)
    await later.pop()
    assert turn == [("local", "how are my runs?", "slack")]
    assert calls[-1] == ("chat.postMessage", {"channel": "D1", "text": "Two runs are going."})
    _slack(client, dm)                                    # Slack retries: answered once
    assert later == []
    # its own messages are not answered
    _slack(client, {**dm, "event_id": "Ev2", "event": {**dm["event"], "bot_id": "B1"}})
    assert later == []
    # a mention in a channel is answered in its thread
    _slack(client, {"type": "event_callback", "event_id": "Ev3",
                    "event": {"type": "app_mention", "channel": "C1", "user": "U1", "text": "<@B> status?", "ts": "5.5"}})
    await later.pop()
    assert calls[-1][1]["thread_ts"] == "5.5" and calls[-1][1]["channel"] == "C1"


def _wa(client, body: dict, secret="as"):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/im/whatsapp", content=raw, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})


def _wa_message(m: dict) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "8613800000000", "profile": {"name": "Linda"}}], "messages": [m]}}]}]}


async def test_whatsapp_checks_the_webhook_and_answers_a_message(client, later, turn, quiet, monkeypatch):
    im.save_settings("whatsapp", {"phone_number_id": "111", "access_token": "EAA", "app_secret": "as", "verify_token": "vt"})
    r = client.get("/im/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"
    assert client.get("/im/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "42"}).status_code == 403
    sent = []

    def graph(req: httpx.Request) -> httpx.Response:
        sent.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(graph)))
    msg = _wa_message({"from": "8613800000000", "id": "wamid.1", "type": "text", "text": {"body": "hi"}})
    assert _wa(client, msg, secret="wrong").status_code == 403
    _wa(client, msg)
    await later.pop()
    assert sent[-1][0] == "/v21.0/111/messages" and "bind" in sent[-1][1]["text"]["body"]     # not linked yet
    _link("whatsapp", "8613800000000")
    _wa(client, _wa_message({"from": "8613800000000", "id": "wamid.2", "type": "text", "text": {"body": "status?"}}))
    await later.pop()
    assert turn == [("local", "status?", "whatsapp")] and sent[-1][1]["text"]["body"] == "Two runs are going."
    assert sent[-1][1]["to"] == "8613800000000" and sent[-1][1]["messaging_product"] == "whatsapp"


async def test_a_whatsapp_voice_message_is_heard_and_answered_aloud(client, later, turn, monkeypatch):
    im.save_settings("whatsapp", {"phone_number_id": "111", "access_token": "EAA", "app_secret": "as", "verify_token": "vt"})
    _link("whatsapp", "861")
    heard, spoken = [], []
    monkeypatch.setattr(voice, "can_hear", lambda: True)

    async def transcribe(data, suffix=".wav", **kw):
        heard.append((data, suffix))
        return "我的项目怎么样了"
    monkeypatch.setattr(voice, "transcribe", transcribe)

    async def media(mid):
        return b"OGGDATA-" + mid.encode()
    monkeypatch.setattr(im, "_whatsapp_media", media)
    sent = []

    async def send(to, text):
        sent.append(text)
        return ""
    monkeypatch.setattr(im, "_whatsapp_send", send)

    async def speech(text):
        return b"MP3", ".mp3"
    monkeypatch.setattr(im, "_speech", speech)

    async def wa_voice(to, audio, suffix):
        spoken.append((to, audio, suffix))
        return ""
    monkeypatch.setattr(im, "_whatsapp_voice", wa_voice)
    _wa(client, _wa_message({"from": "861", "id": "wamid.v", "type": "audio", "audio": {"id": "M1", "mime_type": "audio/ogg"}}))
    await later.pop()
    assert heard == [(b"OGGDATA-M1", ".ogg")] and turn == [("local", "我的项目怎么样了", "whatsapp")]
    assert sent[-1].startswith("🎙 “我的项目怎么样了”") and sent[-1].endswith("Two runs are going.")
    assert spoken == [("861", b"MP3", ".mp3")]


async def test_a_feishu_voice_message_without_speech_recognition_says_how(client, later, monkeypatch):
    im.save_settings("feishu", {"app_id": "a", "app_secret": "s"})
    _link("feishu", "ou_1")
    monkeypatch.setattr(voice, "can_hear", lambda: False)
    sent = []

    async def send(open_id, text, reply_to=""):
        sent.append((open_id, text, reply_to))
        return ""
    monkeypatch.setattr(im, "_feishu_send", send)
    body = {"schema": "2.0", "header": {"event_id": "fe1", "event_type": "im.message.receive_v1"},
            "event": {"sender": {"sender_id": {"open_id": "ou_1"}},
                      "message": {"message_id": "m1", "message_type": "audio", "content": json.dumps({"file_key": "fk", "duration": 2000})}}}
    client.post("/im/feishu", json=body)
    await later.pop()
    assert "AURORA_WHISPER" in sent[-1][1] and sent[-1][2] == "m1"
    # with recognition: the file is fetched and heard
    monkeypatch.setattr(voice, "can_hear", lambda: True)

    async def transcribe(data, suffix=".wav", **kw):
        return f"heard {data.decode()}"
    monkeypatch.setattr(voice, "transcribe", transcribe)

    async def fetch(mid, key):
        return f"{mid}/{key}".encode()
    monkeypatch.setattr(im, "_feishu_file", fetch)

    async def turn(uid, text, app):
        return f"you said: {text}"
    monkeypatch.setattr(im, "_turn", turn)

    async def none(*a, **k):
        return None
    monkeypatch.setattr(im, "_say_aloud", none)
    client.post("/im/feishu", json={**body, "header": {**body["header"], "event_id": "fe2"}})
    await later.pop()
    assert sent[-1][1].endswith("you said: heard m1/fk")


def test_what_is_said_aloud_is_short_and_without_links():
    assert im.spoken("Two runs are going.\n\n▶ Stop run 3\nReply yes") == "Two runs are going."
    long = "First sentence here. " + "More words. " * 40
    assert im.spoken(long).startswith("First sentence here.") and len(im.spoken(long)) < 120
    assert "http" not in im.spoken("See https://example.org now.")


async def test_notifications_reach_slack_and_whatsapp(monkeypatch):
    im.save_settings("slack", {"bot_token": "xoxb-1", "signing_secret": "ss"})
    im.save_settings("whatsapp", {"phone_number_id": "111", "access_token": "EAA", "app_secret": "as", "verify_token": "vt"})
    _link("slack", "U1")
    _link("whatsapp", "861")
    calls = []

    def api(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append((req.url.host, req.url.path, body))
        if req.url.path.endswith("conversations.open"):
            return httpx.Response(200, json={"ok": True, "channel": {"id": "D9"}})
        if req.url.host == "slack.com":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(400, json={"error": {"code": 131047, "message": "Re-engagement message"}})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(api)))
    # Slack gets it; WhatsApp refuses (more than 24 hours since they wrote), which is logged, not raised
    assert await im.push("local", "Finished: DFT", "Converged.", "http://x/c/1")
    assert ("slack.com", "/api/conversations.open", {"users": "U1"}) in calls
    post = next(c for c in calls if c[1].endswith("chat.postMessage"))
    assert post[2] == {"channel": "D9", "text": "Finished: DFT\nConverged.\nhttp://x/c/1"}
    assert any(c[0] == "graph.facebook.com" for c in calls)
    assert "24 hours" in await im._whatsapp_send("861", "x")


async def test_audio_is_converted_for_the_apps():
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    import struct
    pcm = b"\x00\x10" * 24000
    wav = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
           + b"data" + struct.pack("<I", len(pcm)) + pcm)
    ogg, ms = await im._convert(wav, ".wav", "ogg")
    assert ogg[:4] == b"OggS" and 900 <= ms <= 1100

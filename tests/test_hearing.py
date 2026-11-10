"""Hearing what is said: a model that listens, told the person's names and terms; nothing sent for silence."""

import asyncio
import base64
import json
import math
import struct

import httpx

from aurora.memory import voice
from tests.test_memory import client, model  # noqa: F401  (fixtures)


def _wav(pcm: bytes, rate: int = 16000) -> bytes:
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def _quiet(seconds: float, amp: int = 0) -> bytes:
    return b"".join(struct.pack("<h", int(amp * math.sin(i * 1.3))) for i in range(int(16000 * seconds)))


def _said(seconds: float = 1.0) -> bytes:
    return b"".join(struct.pack("<h", int(9000 * math.sin(i / 6) * (0.6 + 0.4 * math.sin(i / 900))))
                    for i in range(int(16000 * seconds)))


SPEECH = _wav(_quiet(0.5, 30) + _said(1.0) + _quiet(0.5, 30))


def test_silence_and_the_hum_of_a_room_are_not_speech():
    assert not voice.has_speech(_wav(_quiet(3)))
    assert not voice.has_speech(_wav(_quiet(3, 150)))           # a room: about -47 dB
    assert not voice.has_speech(_wav(_quiet(1, 30) + _said(0.1) + _quiet(1, 30)))   # a click
    assert voice.has_speech(SPEECH)
    assert voice.has_speech(b"not a wav at all")                 # what cannot be read is given the benefit of the doubt


class _Fake:
    def __init__(self, answer="Grinsztajn 的结论在 TabPFN 上还成立吗？", status=200):
        self.answer, self.status, self.sent = answer, status, []

    def install(self, monkeypatch):
        fake = self

        async def post(self, url, headers=None, json=None, **kw):
            fake.sent.append((url, headers, json))
            body = {"choices": [{"message": {"content": fake.answer}}]} if fake.status == 200 else {"error": "no"}
            return httpx.Response(fake.status, json=body, request=httpx.Request("POST", url))
        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        return self


def test_a_model_hears_it_told_the_names_and_terms_of_the_projects(client, monkeypatch):  # noqa: F811
    from aurora.feed import service as feed
    monkeypatch.setattr(feed, "project_profiles", lambda user: [
        {"name": "Tabular scaling", "objective": "Does the advantage of GBDT over MLPs shrink? Revisit Grinsztajn et al.",
         "papers": ["TabPFN: a transformer that solves small tabular classification problems in a second"]}])
    voice._hints.clear()
    fake = _Fake().install(monkeypatch)
    r = client.post("/api/memory/voice/transcribe", json={"audio": base64.b64encode(SPEECH).decode(), "audio_type": "wav"})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "Grinsztajn 的结论在 TabPFN 上还成立吗？"
    url, headers, body = fake.sent[-1]
    assert url.endswith("/chat/completions") and body["model"] == voice.HEARING_MODEL
    system = body["messages"][0]["content"]
    for w in ("Grinsztajn", "TabPFN", "GBDT", "MLPs", "Pippin"):
        assert w in system
    assert "Does" not in system.split("often says")[1]           # common words are not hints
    audio = body["messages"][1]["content"][0]["input_audio"]
    assert audio["format"] == "wav" and base64.b64decode(audio["data"])[:4] == b"RIFF"


def test_silence_is_not_sent_and_no_words_means_nothing(client, monkeypatch):  # noqa: F811
    fake = _Fake().install(monkeypatch)
    silent = base64.b64encode(_wav(_quiet(2, 100))).decode()
    assert client.post("/api/memory/voice/transcribe", json={"audio": silent, "audio_type": "wav"}).json() == {"text": ""}
    assert fake.sent == []
    fake.answer = "(No words are spoken.)"
    said = base64.b64encode(SPEECH).decode()
    assert client.post("/api/memory/voice/transcribe", json={"audio": said, "audio_type": "wav"}).json() == {"text": ""}


def test_whisper_hears_when_the_model_cannot_and_when_asked_to(client, monkeypatch, tmp_path):  # noqa: F811
    import stat
    w = tmp_path / "whisper-cli"
    w.write_text("#!/bin/sh\necho 'read the second one'\n")
    w.chmod(w.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("AURORA_WHISPER", f"{w} -f {{audio}}")
    fake = _Fake(status=502).install(monkeypatch)
    said = base64.b64encode(SPEECH).decode()
    assert client.post("/api/memory/voice/transcribe", json={"audio": said, "audio_type": "wav"}).json()["text"] == "read the second one"
    assert len(fake.sent) == 1
    client.put("/api/settings", json={"changes": {"voice.hearing": "local"}})
    client.post("/api/memory/voice/transcribe", json={"audio": said, "audio_type": "wav"})
    assert len(fake.sent) == 1                                    # local: nothing leaves the machine
    client.put("/api/settings", json={"reset": ["voice.hearing"]})


def test_a_chat_app_voice_message_is_heard_as_the_person(monkeypatch):
    from aurora import im
    seen = []

    async def transcribe(data, suffix=".wav", **kw):
        from aurora.auth.guard import current_user
        seen.append(current_user().user_id)
        return "读第二篇"
    monkeypatch.setattr(voice, "transcribe", transcribe)
    monkeypatch.setattr(voice, "can_hear", lambda: True)
    monkeypatch.setattr(im, "_who", lambda app, account: "local")
    replies = []

    async def reply(app, account, text, ctx, **kw):
        replies.append(text)
    monkeypatch.setattr(im, "_reply", reply)

    async def fetch():
        return b"ogg"
    asyncio.run(im._reply_heard("feishu", "ou_1", fetch, ".ogg", {}))
    assert seen == ["local"] and replies == ["读第二篇"]
    assert json.dumps(replies, ensure_ascii=False)

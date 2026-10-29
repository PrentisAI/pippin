"""Pippin in Feishu and WeCom: the apps' callbacks are checked by their token or signature, a chat account is linked
to a person with a code, what they write goes to Pippin as them, and 确认 / yes does what Pippin proposed."""

import base64
import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient

from aurora import im

AES = base64.b64encode(os.urandom(32)).decode().rstrip("=")      # a WeCom EncodingAESKey: 43 characters


@pytest.fixture()
def client():
    from aurora.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def later(monkeypatch):
    """Run what would be answered in the background, so the test can look at it."""
    jobs = []
    monkeypatch.setattr(im, "_later", lambda coro: jobs.append(coro))
    return jobs


def _feishu_encrypt(body: dict, key: str) -> str:
    raw = json.dumps(body).encode()
    pad = 16 - len(raw) % 16
    iv = os.urandom(16)
    e = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + e.update(raw + bytes([pad]) * pad) + e.finalize()).decode()


def test_settings_keep_secrets_and_show_only_whether_they_are_set(client):
    r = client.put("/api/im/settings/feishu", json={"app_id": "cli_1", "app_secret": "s3cret", "verification_token": "vt"})
    assert r.status_code == 200 and r.json()["ready"] and r.json()["app_secret"] == "" and r.json()["set"]["app_secret"]
    # left empty: the saved secret stays
    client.put("/api/im/settings/feishu", json={"app_id": "cli_2", "app_secret": ""})
    assert im.settings("feishu")["app_secret"] == "s3cret" and im.settings("feishu")["app_id"] == "cli_2"
    got = client.get("/api/im/settings").json()
    assert got["apps"]["feishu"]["app_id"] == "cli_2" and got["callbacks"]["wecom"].endswith("/im/wecom")
    assert "s3cret" not in json.dumps(got)
    assert client.put("/api/im/settings/slack", json={}).status_code == 404


async def test_feishu_checks_the_callback_and_links_an_account(client, later, monkeypatch):
    im.save_settings("feishu", {"app_id": "a", "app_secret": "s", "verification_token": "vt", "encrypt_key": "ek"})
    check = {"type": "url_verification", "challenge": "c1", "token": "vt"}
    assert client.post("/im/feishu", json={"encrypt": _feishu_encrypt(check, "ek")}).json() == {"challenge": "c1"}
    assert client.post("/im/feishu", json={"encrypt": _feishu_encrypt({**check, "token": "no"}, "ek")}).status_code == 403
    sent = []

    async def send(open_id, text, reply_to=""):
        sent.append((open_id, text, reply_to))
        return ""
    monkeypatch.setattr(im, "_feishu_send", send)

    def message(text, n):
        return {"encrypt": _feishu_encrypt({"schema": "2.0", "header": {"event_id": f"e{n}", "event_type": "im.message.receive_v1", "token": "vt"},
                                            "event": {"sender": {"sender_id": {"open_id": "ou_1"}},
                                                      "message": {"message_id": f"m{n}", "message_type": "text",
                                                                  "content": json.dumps({"text": text})}}}, "ek")}
    # not linked yet: how to link
    client.post("/im/feishu", json=message("hi", 1))
    await later.pop()
    assert sent[-1][0] == "ou_1" and "绑定" in sent[-1][1] and sent[-1][2] == "m1"
    # the console gives a code; sending it links the account to the person
    code = client.post("/api/im/code").json()["code"]
    client.post("/im/feishu", json=message(f"绑定 {code}", 2))
    await later.pop()
    assert "已绑定" in sent[-1][1] and client.get("/api/im/me").json()["links"][0]["app"] == "feishu"
    # the same event again (Feishu resends) is answered once
    client.post("/im/feishu", json=message(f"绑定 {code}", 2))
    assert later == []
    # a used code does not link again
    assert "无效" in await im.answer("feishu", "ou_2", f"绑定 {code}")
    # linked: what they write goes to Pippin as them
    asked = []

    async def turn(uid, text, app):
        asked.append((uid, text, app))
        return "Two runs are going."
    monkeypatch.setattr(im, "_turn", turn)
    client.post("/im/feishu", json=message("@_user_1 我的项目怎么样了？", 3))
    await later.pop()
    assert asked == [("local", "我的项目怎么样了？", "feishu")] and sent[-1][1] == "Two runs are going."
    assert client.delete("/api/im/links/feishu").status_code == 204 and client.get("/api/im/me").json()["links"] == []


def test_wecom_checks_its_signature(client):
    im.save_settings("wecom", {"corp_id": "ww1", "agent_id": "1000002", "secret": "s", "token": "tk", "aes_key": AES})
    echo = im.wecom_encrypt("hello-echo")
    sig = im.wecom_signature("tk", "123", "n", echo)
    r = client.get("/im/wecom", params={"msg_signature": sig, "timestamp": "123", "nonce": "n", "echostr": echo})
    assert r.status_code == 200 and r.text == "hello-echo"
    assert client.get("/im/wecom", params={"msg_signature": "bad", "timestamp": "123", "nonce": "n", "echostr": echo}).status_code == 403


async def test_wecom_message_is_answered(client, later, monkeypatch):
    im.save_settings("wecom", {"corp_id": "ww1", "agent_id": "1000002", "secret": "s", "token": "tk", "aes_key": AES})
    sent = []

    async def send(user, text):
        sent.append((user, text))
        return ""
    monkeypatch.setattr(im, "_wecom_send", send)
    inner = "<xml><FromUserName>zhangsan</FromUserName><MsgType>text</MsgType><Content>hi</Content><MsgId>7</MsgId></xml>"
    enc = im.wecom_encrypt(inner)
    body = f"<xml><ToUserName>ww1</ToUserName><Encrypt>{enc}</Encrypt><AgentID>1000002</AgentID></xml>"
    sig = im.wecom_signature("tk", "1", "n", enc)
    r = client.post("/im/wecom", params={"msg_signature": sig, "timestamp": "1", "nonce": "n"}, content=body)
    assert r.status_code == 200 and r.text == "success"
    await later.pop()
    assert sent[0][0] == "zhangsan" and "绑定" in sent[0][1]
    bad = client.post("/im/wecom", params={"msg_signature": "x", "timestamp": "1", "nonce": "n"}, content=body)
    assert bad.status_code == 403


async def test_yes_does_what_pippin_proposed(monkeypatch):
    from aurora.memory import assistant
    did = []
    monkeypatch.setattr(assistant, "pending", lambda uid: [{"action_id": "a1", "label": "Let DFT continue"}])

    async def act(uid, aid, confirm):
        did.append((uid, aid, confirm))
        return {"label": "Let DFT continue", "state": "done" if confirm else "declined", "result": "It continues."}
    monkeypatch.setattr(assistant, "act", act)
    assert (await im._turn("local", "确认", "feishu")).startswith("已完成：Let DFT continue")
    assert (await im._turn("local", "No.", "wecom")).startswith("好的，不做了") and did == [("local", "a1", True), ("local", "a1", False)]


async def test_notifications_reach_a_linked_chat_and_join_the_conversation(monkeypatch):
    from aurora.memory import assistant
    assert await im.push("local", "Decision needed", "Run 12 more k-points?") is False      # nothing linked
    im.save_settings("wecom", {"corp_id": "ww1", "agent_id": "1", "secret": "s", "token": "tk", "aes_key": AES})
    code = im.new_code("local")
    im._bind("wecom", "zhangsan", code)
    sent = []

    async def send(user, text):
        sent.append((user, text))
        return ""
    monkeypatch.setattr(im, "_wecom_send", send)
    assert await im.push("local", "Decision needed: DFT", "Run 12 more k-points?", "http://x/c/1")
    assert sent == [("zhangsan", "Decision needed: DFT\nRun 12 more k-points?\nhttp://x/c/1")]
    assert assistant.history("local")[-1]["text"].startswith("Decision needed: DFT")


async def test_a_notifier_tells_the_linked_chat_too():
    from aurora.config import NotificationsConfig
    from aurora.notify import Notifier
    got = []

    async def direct(kind, title, text):
        got.append((kind, title))
    n = Notifier(lambda: NotificationsConfig(), direct=direct)
    n.send("decision_needed", "Decision needed: DFT", "k-points?")
    n.send("budget_warning", "Budget 80% used", "")         # not for the chat
    n.send("decision_needed", "Decision needed: DFT", "k-points?")      # the same again: once
    for t in list(n._tasks):
        await t
    assert got == [("decision_needed", "Decision needed: DFT")]

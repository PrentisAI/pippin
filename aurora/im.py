"""Talking to Pippin from Feishu/Lark (飞书), WeCom (企业微信), Slack or WhatsApp: the same conversation as the
orb's, in a chat app, typed or spoken.

A team admin connects the team's own app (a Feishu custom app with the bot ability, a WeCom self-built app, a Slack
app with a bot, or a WhatsApp Business number in a Meta app) and points its message callback at this server
(``/im/feishu``, ``/im/wecom``, ``/im/slack`` or ``/im/whatsapp``; the server must be reachable from the internet).  Each person links their chat account once: the console gives them a code, they send ``绑定 <code>``
(or ``bind <code>``) to the bot.  From then on what they write to the bot goes to Pippin as them (their projects,
their rights), the answer comes back in the chat, and Pippin's proposals are done when they reply ``确认`` / ``yes``
(``取消`` / ``no`` drops them).  Their decision and run notifications come to the same chat, so they can answer them.

A voice message is heard with the console's speech recognition (``AURORA_WHISPER``, see ``aurora.memory.voice``):
what was heard is shown above the answer, and the answer comes back in writing and, short, as a voice message in the
voice the orb uses (where the app takes one, and when a voice is set up; otherwise in writing only).

The group robots under Settings › Notifications only send; they cannot hear a reply, which is why this is an app.
WhatsApp lets a business write to someone only within 24 hours of their last message (later, only with a template
Meta approved), so a notification to WhatsApp reaches someone who wrote to Pippin that day.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import shutil
import struct
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

APPS = ("feishu", "wecom", "slack", "whatsapp")
FIELDS = {
    # domain: open.feishu.cn, or open.larksuite.com for Lark
    "feishu": ("app_id", "app_secret", "verification_token", "encrypt_key", "domain"),
    "wecom": ("corp_id", "agent_id", "secret", "token", "aes_key"),
    "slack": ("bot_token", "signing_secret"),
    # version: the Graph API's (v21.0 when empty)
    "whatsapp": ("phone_number_id", "access_token", "app_secret", "verify_token", "version"),
}
NEED = {"feishu": ("app_id", "app_secret"), "wecom": ("corp_id", "agent_id", "secret", "token", "aes_key"),
        "slack": ("bot_token", "signing_secret"),
        "whatsapp": ("phone_number_id", "access_token", "app_secret", "verify_token")}
SECRET = {"app_secret", "encrypt_key", "secret", "aes_key", "verification_token", "token", "bot_token",
          "signing_secret", "access_token", "verify_token"}
CODE_TTL = 15 * 60
YES = {"确认", "确定", "好", "好的", "可以", "是", "执行", "yes", "y", "ok", "okay", "confirm", "do it", "go"}
NO = {"取消", "不", "不要", "算了", "否", "no", "n", "cancel", "stop it"}
_TIMEOUT = 15.0


# ---------------------------------------------------------------- settings (the team's apps), kept encrypted

def settings(app: str) -> dict[str, str]:
    from aurora.auth.secrets import _fernet
    from aurora.auth.store import auth_store
    row = auth_store.get_secret("team", f"im_{app}")
    if row is None:
        return {}
    try:
        return json.loads(_fernet().decrypt(row["ciphertext"]).decode())
    except Exception:
        return {}


def save_settings(app: str, values: dict[str, Any]) -> dict[str, Any]:
    """Change an app's settings; a secret left empty keeps the one saved.  ``enabled`` off stops listening."""
    from aurora.auth.secrets import _fernet
    from aurora.auth.store import auth_store
    have = settings(app)
    new = {k: str(values.get(k) or "").strip() for k in FIELDS[app]}
    for k in SECRET & set(FIELDS[app]):
        if not new[k]:
            new[k] = have.get(k, "")
    new["enabled"] = "1" if values.get("enabled", True) else ""
    auth_store.put_secret("team", f"im_{app}", _fernet().encrypt(json.dumps(new).encode()), app)
    _tokens.pop(app, None)
    return view(app)


def view(app: str) -> dict[str, Any]:
    """The settings as the console shows them: secrets only as whether they are set."""
    s = settings(app)
    return {"app": app, "enabled": bool(s.get("enabled")), "ready": ready(app),
            **{k: ("" if k in SECRET else s.get(k, "")) for k in FIELDS[app]},
            "set": {k: bool(s.get(k)) for k in FIELDS[app] if k in SECRET}}


def ready(app: str) -> bool:
    s = settings(app)
    return bool(s.get("enabled")) and all(s.get(k) for k in NEED[app])


# ---------------------------------------------------------------- who is who: a chat account linked to a person

def _bindings_file() -> Path:
    from aurora.paths import aurora_home
    d = aurora_home() / "im"
    d.mkdir(parents=True, exist_ok=True)
    return d / "bindings.json"


def _bindings() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(_bindings_file().read_text())
    except (OSError, ValueError):
        return {}


def _save_bindings(b: dict[str, dict[str, Any]]) -> None:
    _bindings_file().write_text(json.dumps(b, ensure_ascii=False))


_codes: dict[str, tuple[str, float]] = {}


def new_code(user_id: str) -> str:
    """A code to send to the bot, valid for a quarter of an hour."""
    for c, (_, at) in list(_codes.items()):
        if time.time() - at > CODE_TTL:
            _codes.pop(c, None)
    code = f"{secrets.randbelow(10**6):06d}"
    _codes[code] = (user_id, time.time())
    return code


def links(user_id: str) -> list[dict[str, Any]]:
    """Their linked chat accounts."""
    return [{"app": k.split(":", 1)[0], "name": v.get("name", ""), "at": v.get("at", 0)}
            for k, v in _bindings().items() if v.get("user_id") == user_id]


def unlink(user_id: str, app: str) -> None:
    b = _bindings()
    _save_bindings({k: v for k, v in b.items() if not (k.startswith(app + ":") and v.get("user_id") == user_id)})


def _who(app: str, account: str) -> str | None:
    return (_bindings().get(f"{app}:{account}") or {}).get("user_id")


def _bind(app: str, account: str, code: str, name: str = "") -> str:
    hit = _codes.pop(code, None)
    if hit is None or time.time() - hit[1] > CODE_TTL:
        return "这个绑定码无效或已过期。请在 Pippin 的设置里重新获取。 (That code is not valid or has expired.)"
    b = _bindings()
    b[f"{app}:{account}"] = {"user_id": hit[0], "name": name, "at": time.time()}
    _save_bindings(b)
    return "已绑定。现在可以直接在这里和 Pippin 说话，比如「我的项目现在怎么样了？」 (Linked: talk to Pippin here.)"


# ---------------------------------------------------------------- what someone wrote, and Pippin's answer

_seen: dict[str, float] = {}


def _first(key: str) -> bool:
    """Whether a message is new (the apps resend a message they think was not received)."""
    now = time.time()
    for k, at in list(_seen.items()):
        if now - at > 3600:
            _seen.pop(k, None)
    if key in _seen:
        return False
    _seen[key] = now
    return True


async def answer(app: str, account: str, text: str, name: str = "") -> str:
    """What Pippin says back to *account*'s message *text*."""
    text = re.sub(r"@\S+\s*", "", text).strip()       # mentions of the bot in a group
    m = re.match(r"^(?:绑定|bind)\s*(\d{6})$", text, re.I)
    if m:
        return _bind(app, account, m.group(1), name)
    uid = _who(app, account)
    if uid is None:
        return ("你好，我是 Pippin。先在 Pippin 的设置 › 聊天应用 里获取绑定码，然后发「绑定 123456」给我。\n"
                "Hello, I am Pippin. Get a code under Settings › Chat apps in Pippin, then send me “bind 123456”.")
    return await _as(uid, lambda: _turn(uid, text, app))


async def _as(user_id: str, fn: Any) -> Any:
    """Run *fn* as the person (their rights, their keys)."""
    from aurora.auth.guard import CURRENT
    from aurora.auth.permissions import LOCAL_USER
    from aurora.auth.store import auth_store
    if user_id in ("", "local"):
        user = LOCAL_USER
    else:
        row = auth_store.get_user(user_id)
        if row is None:
            return "这个账号在 Pippin 里已不存在。 (This account no longer exists in Pippin.)"
        user = auth_store.as_user(row)
    token = CURRENT.set(user)
    try:
        return await fn()
    finally:
        CURRENT.reset(token)


async def _turn(uid: str, text: str, app: str) -> str:
    from aurora.api.routes.assistant import _access
    from aurora.memory import assistant
    word = text.strip().lower().rstrip("。.!！")
    waiting = assistant.pending(uid)
    if waiting and (word in YES or word in NO):
        a = await assistant.act(uid, waiting[0]["action_id"], word in YES)
        head = {"done": "已完成", "declined": "好的，不做了", "failed": "没有成功", "expired": "已过期"}.get(a["state"], a["state"])
        return f"{head}：{a['label']}" + (f"\n{a['result']}" if a.get("result") and a["state"] != "declined" else "")
    try:                     # "read 2" / "save 3" about the feed digest last pushed
        from aurora.feed.service import digest_word
    except ImportError:
        digest_word = None
    if digest_word is not None:
        said = await digest_word(uid, text)
        if said is not None:
            return said
    try:
        access, mem, models = _access()
    except Exception as exc:
        return f"Pippin 现在无法回答：{getattr(exc, 'detail', exc)}"
    turn = await assistant.ask(uid, text[:4000], access=access, mem_access=mem, models=models, via=app)
    out = turn.get("reply") or "…"
    for a in turn.get("actions") or []:
        out += f"\n\n▶ {a['label']}\n回复「确认」执行，「取消」放弃。 (Reply “yes” to do it, “no” to drop it.)"
    for link in turn.get("links") or []:
        base = _console()
        if base:
            out += f"\n{link['label']}: {base}{link['path']}"
    return out


def _console() -> str:
    try:
        from aurora.api.routes.campaigns import console_url
        return console_url()
    except Exception:
        return ""


# ---------------------------------------------------------------- telling someone something (a notification)

async def push(user_id: str, title: str, text: str, url: str = "") -> bool:
    """Send a person a message in each chat app they linked; whether one went.  It joins their conversation with
    Pippin, so "continue" or "stop it" in reply is understood."""
    sent = False
    body = f"{title}\n{text}".strip() + (f"\n{url}" if url else "")
    for key, v in _bindings().items():
        app, account = key.split(":", 1)
        if v.get("user_id") != user_id or not ready(app):
            continue
        try:
            error = await _send(app, account, body, {})
        except httpx.HTTPError as exc:
            error = str(exc)
        if error:
            logger.warning("Pippin could not message %s on %s: %s", user_id, app, error)
        else:
            sent = True
    if sent:
        from aurora.memory import assistant
        assistant.note(user_id, f"{title}\n{text}".strip())
    return sent


# ---------------------------------------------------------------- Feishu / Lark

_tokens: dict[str, tuple[str, float]] = {}


def _feishu_base() -> str:
    d = settings("feishu").get("domain") or "open.feishu.cn"
    return f"https://{re.sub(r'^https?://', '', d).rstrip('/')}/open-apis"


async def _feishu_token() -> str:
    have = _tokens.get("feishu")
    if have and have[1] > time.time() + 60:
        return have[0]
    s = settings("feishu")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = (await c.post(f"{_feishu_base()}/auth/v3/tenant_access_token/internal",
                          json={"app_id": s.get("app_id"), "app_secret": s.get("app_secret")})).json()
    if r.get("code") != 0:
        raise httpx.HTTPError(f"feishu token: {r.get('msg')}")
    _tokens["feishu"] = (r["tenant_access_token"], time.time() + int(r.get("expire", 3600)))
    return r["tenant_access_token"]


async def _feishu_send(open_id: str, text: str, reply_to: str = "", *, msg: tuple[str, dict[str, Any]] | None = None) -> str:
    token = await _feishu_token()
    kind, value = msg or ("text", {"text": text[:8000]})
    content = json.dumps(value, ensure_ascii=False)
    url = (f"{_feishu_base()}/im/v1/messages/{reply_to}/reply" if reply_to
           else f"{_feishu_base()}/im/v1/messages?receive_id_type=open_id")
    body = {"msg_type": kind, "content": content, **({} if reply_to else {"receive_id": open_id})}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = (await c.post(url, json=body, headers={"Authorization": f"Bearer {token}"})).json()
    return "" if r.get("code") == 0 else f"feishu {r.get('code')}: {r.get('msg')}"


def feishu_decrypt(encrypted: str, key: str) -> dict[str, Any]:
    """An encrypted Feishu event (AES-256-CBC, the key the SHA-256 of the app's Encrypt Key)."""
    raw = base64.b64decode(encrypted)
    d = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()), modes.CBC(raw[:16])).decryptor()
    plain = d.update(raw[16:]) + d.finalize()
    return json.loads(plain[: -plain[-1]].decode())


async def feishu_event(body: dict[str, Any]) -> dict[str, Any]:
    """One callback from Feishu: the URL check, or a message to answer (answered in the background: Feishu waits
    three seconds at most)."""
    s = settings("feishu")
    if "encrypt" in body:
        if not s.get("encrypt_key"):
            raise PermissionError("an encrypted event, but no Encrypt Key is set")
        body = feishu_decrypt(body["encrypt"], s["encrypt_key"])
    token = body.get("token") or (body.get("header") or {}).get("token") or ""
    if s.get("verification_token") and token != s["verification_token"]:
        raise PermissionError("the verification token does not match")
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}
    if not s.get("enabled"):
        return {}
    head, ev = body.get("header") or {}, body.get("event") or {}
    if head.get("event_type") != "im.message.receive_v1" or not _first("feishu:" + str(head.get("event_id"))):
        return {}
    msg = ev.get("message") or {}
    account = ((ev.get("sender") or {}).get("sender_id") or {}).get("open_id", "")
    if not account:
        return {}
    ctx = {"reply_to": msg.get("message_id", "")}
    try:
        content = json.loads(msg.get("content") or "{}")
    except ValueError:
        content = {}
    kind = msg.get("message_type")
    if kind == "text":
        _later(_reply("feishu", account, content.get("text", ""), ctx))
    elif kind == "audio" and content.get("file_key"):
        _later(_reply_heard("feishu", account, lambda: _feishu_file(msg.get("message_id", ""), content["file_key"]),
                            ".ogg", ctx))
    else:
        _later(_send("feishu", account, ONLY_WORDS, ctx))
    return {}


async def _feishu_file(message_id: str, file_key: str) -> bytes:
    """A file someone sent the bot (their voice message: Opus)."""
    token = await _feishu_token()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{_feishu_base()}/im/v1/messages/{message_id}/resources/{file_key}", params={"type": "file"},
                        headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.content


async def _feishu_voice(open_id: str, audio: bytes, suffix: str, ctx: dict[str, Any]) -> str:
    """Pippin's answer as a Feishu voice message (uploaded as Opus, with its length)."""
    ogg, ms = await _convert(audio, suffix, "ogg")
    token = await _feishu_token()
    async with httpx.AsyncClient(timeout=60) as c:
        up = (await c.post(f"{_feishu_base()}/im/v1/files", headers={"Authorization": f"Bearer {token}"},
                           data={"file_type": "opus", "file_name": "pippin.opus", "duration": str(ms)},
                           files={"file": ("pippin.opus", ogg, "audio/ogg")})).json()
    if up.get("code") != 0:
        return f"feishu upload {up.get('code')}: {up.get('msg')}"
    return await _feishu_send(open_id, "", ctx.get("reply_to", ""),
                              msg=("audio", {"file_key": (up.get("data") or {}).get("file_key", "")}))


# ---------------------------------------------------------------- WeCom (企业微信)

def _wecom_key() -> bytes:
    return base64.b64decode(settings("wecom").get("aes_key", "") + "=")


def wecom_signature(token: str, timestamp: str, nonce: str, data: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce, data])).encode()).hexdigest()


def wecom_decrypt(data: str) -> str:
    """A WeCom message (AES-256-CBC, padded to 32 bytes): 16 random bytes, the length, the message, the corp ID."""
    key = _wecom_key()
    d = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    plain = d.update(base64.b64decode(data)) + d.finalize()
    plain = plain[: -plain[-1]]
    n = struct.unpack(">I", plain[16:20])[0]
    if plain[20 + n:].decode() != settings("wecom").get("corp_id", ""):
        raise PermissionError("the message is for another corp")
    return plain[20:20 + n].decode()


def wecom_encrypt(text: str) -> str:
    """The reverse of :func:`wecom_decrypt` (for the tests, and for a passive reply)."""
    key = _wecom_key()
    raw = secrets.token_bytes(16) + struct.pack(">I", len(text.encode())) + text.encode() + \
        settings("wecom").get("corp_id", "").encode()
    pad = 32 - len(raw) % 32
    raw += bytes([pad]) * pad
    e = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(e.update(raw) + e.finalize()).decode()


def wecom_verify(signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    """The URL check when the callback is saved in WeCom: the decrypted echo."""
    s = settings("wecom")
    if wecom_signature(s.get("token", ""), timestamp, nonce, echostr) != signature:
        raise PermissionError("the signature does not match")
    return wecom_decrypt(echostr)


async def wecom_event(signature: str, timestamp: str, nonce: str, body: bytes) -> None:
    """One message from WeCom: checked, decrypted and answered in the background (WeCom waits five seconds)."""
    s = settings("wecom")
    enc = ET.fromstring(body).findtext("Encrypt") or ""
    if wecom_signature(s.get("token", ""), timestamp, nonce, enc) != signature:
        raise PermissionError("the signature does not match")
    if not s.get("enabled"):
        return
    msg = ET.fromstring(wecom_decrypt(enc))
    account = msg.findtext("FromUserName") or ""
    kind = msg.findtext("MsgType")
    if kind not in ("text", "voice") or not account:
        return
    if not _first("wecom:" + (msg.findtext("MsgId") or f"{account}:{msg.findtext('CreateTime')}")):
        return
    if kind == "text":
        _later(_reply("wecom", account, msg.findtext("Content") or "", {}))
    elif (msg.findtext("Recognition") or "").strip():        # WeCom's own recognition, when the app has it on
        _later(_reply("wecom", account, msg.findtext("Recognition") or "", {}, heard=True))
    else:
        media = msg.findtext("MediaId") or ""
        _later(_reply_heard("wecom", account, lambda: _wecom_media(media), "." + (msg.findtext("Format") or "amr").lower(), {}))


async def _wecom_media(media_id: str) -> bytes:
    token = await _wecom_token()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get("https://qyapi.weixin.qq.com/cgi-bin/media/get", params={"access_token": token, "media_id": media_id})
    r.raise_for_status()
    if r.headers.get("content-type", "").startswith(("application/json", "text/plain")):
        raise httpx.HTTPError(f"wecom media: {r.text[:200]}")
    return r.content


async def _wecom_voice(user: str, audio: bytes, suffix: str) -> str:
    """A WeCom voice message: AMR, at most 60 seconds (when this ffmpeg can write AMR)."""
    amr, ms = await _convert(audio, suffix, "amr")
    if ms > 60_000:
        return "too long for a WeCom voice message"
    token = await _wecom_token()
    async with httpx.AsyncClient(timeout=60) as c:
        up = (await c.post("https://qyapi.weixin.qq.com/cgi-bin/media/upload", params={"access_token": token, "type": "voice"},
                           files={"media": ("pippin.amr", amr, "audio/amr")})).json()
    if up.get("errcode", 0) != 0:
        return f"wecom upload {up.get('errcode')}: {up.get('errmsg')}"
    return await _wecom_send(user, "", msg=("voice", {"media_id": up.get("media_id", "")}))


async def _wecom_token() -> str:
    have = _tokens.get("wecom")
    if have and have[1] > time.time() + 60:
        return have[0]
    s = settings("wecom")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = (await c.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                         params={"corpid": s.get("corp_id"), "corpsecret": s.get("secret")})).json()
    if r.get("errcode") != 0:
        raise httpx.HTTPError(f"wecom token: {r.get('errmsg')}")
    _tokens["wecom"] = (r["access_token"], time.time() + int(r.get("expires_in", 7200)))
    return r["access_token"]


async def _wecom_send(user: str, text: str, *, msg: tuple[str, dict[str, Any]] | None = None) -> str:
    s = settings("wecom")
    token = await _wecom_token()
    kind, value = msg or ("text", {"content": text[:2000]})
    body = {"touser": user, "msgtype": kind, "agentid": int(s.get("agent_id") or 0), kind: value}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = (await c.post("https://qyapi.weixin.qq.com/cgi-bin/message/send", params={"access_token": token}, json=body)).json()
    return "" if r.get("errcode") == 0 else f"wecom {r.get('errcode')}: {r.get('errmsg')}"


# ---------------------------------------------------------------- Slack

def slack_verify(timestamp: str, signature: str, body: bytes) -> None:
    """Slack signs each request: HMAC-SHA256 of ``v0:<timestamp>:<body>`` with the app's signing secret; a request
    older than five minutes is refused (a replay)."""
    secret = settings("slack").get("signing_secret", "")
    if not secret:
        raise PermissionError("no signing secret is set")
    try:
        if abs(time.time() - int(timestamp)) > 300:
            raise PermissionError("the request is too old")
    except ValueError:
        raise PermissionError("no timestamp") from None
    mine = "v0=" + hmac.new(secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mine, signature or ""):
        raise PermissionError("the signature does not match")


async def slack_event(timestamp: str, signature: str, raw: bytes) -> dict[str, Any]:
    """One request from Slack's Events API: the URL check, or a message to the bot (a direct message, or a mention in
    a channel, answered in its thread), answered in the background (Slack waits three seconds)."""
    slack_verify(timestamp, signature, raw)
    try:
        body = json.loads(raw)
    except ValueError:
        raise PermissionError("not JSON") from None
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}
    if body.get("type") != "event_callback" or not settings("slack").get("enabled"):
        return {}
    ev = body.get("event") or {}
    if not _first("slack:" + str(body.get("event_id") or ev.get("client_msg_id") or ev.get("ts"))):
        return {}
    if ev.get("bot_id") or ev.get("subtype") not in (None, "file_share"):
        return {}                                   # the bot's own messages, edits, joins
    if ev.get("type") == "message" and ev.get("channel_type") != "im":
        return {}
    if ev.get("type") not in ("message", "app_mention") or not ev.get("user"):
        return {}
    account = ev["user"]
    ctx = {"channel": ev.get("channel", "")}
    if ev.get("type") == "app_mention":
        ctx["thread_ts"] = ev.get("thread_ts") or ev.get("ts", "")
    audio = next((f for f in ev.get("files") or [] if str(f.get("mimetype", "")).startswith(("audio/", "video/"))
                  or f.get("subtype") == "slack_audio"), None)
    if audio and not (ev.get("text") or "").strip():
        url = audio.get("url_private_download") or audio.get("url_private") or ""
        _later(_reply_heard("slack", account, lambda: _slack_file(url), "." + (audio.get("filetype") or "m4a"), ctx))
    else:
        _later(_reply("slack", account, ev.get("text") or "", ctx))
    return {}


def _slack_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings('slack').get('bot_token', '')}"}


async def _slack_call(method: str, **body: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = (await c.post(f"https://slack.com/api/{method}", json=body, headers=_slack_headers())).json()
    return r


async def _slack_file(url: str) -> bytes:
    if not url.startswith("https://"):
        raise httpx.HTTPError("no file address")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(url, headers=_slack_headers())
    r.raise_for_status()
    if r.headers.get("content-type", "").startswith("text/html"):
        raise httpx.HTTPError("Slack gave a page, not the file: the app needs the files:read scope")
    return r.content


async def _slack_channel(user: str, ctx: dict[str, Any]) -> str:
    """Where to write: the conversation the message came from, or a direct message opened with the person."""
    if ctx.get("channel"):
        return ctx["channel"]
    r = await _slack_call("conversations.open", users=user)
    if not r.get("ok"):
        raise httpx.HTTPError(f"slack conversations.open: {r.get('error')}")
    ctx["channel"] = (r.get("channel") or {}).get("id", "")
    return ctx["channel"]


async def _slack_send(user: str, text: str, ctx: dict[str, Any]) -> str:
    channel = await _slack_channel(user, ctx)
    body: dict[str, Any] = {"channel": channel, "text": text[:39000]}
    if ctx.get("thread_ts"):
        body["thread_ts"] = ctx["thread_ts"]
    r = await _slack_call("chat.postMessage", **body)
    return "" if r.get("ok") else f"slack: {r.get('error')}"


async def _slack_voice(user: str, audio: bytes, suffix: str, ctx: dict[str, Any]) -> str:
    """Pippin's answer as an audio file in the conversation (Slack's two-step upload)."""
    channel = await _slack_channel(user, ctx)
    name = "pippin" + suffix
    async with httpx.AsyncClient(timeout=60) as c:
        got = (await c.post("https://slack.com/api/files.getUploadURLExternal", headers=_slack_headers(),
                            data={"filename": name, "length": str(len(audio))})).json()
        if not got.get("ok"):
            return f"slack upload: {got.get('error')}"
        up = await c.post(got["upload_url"], files={"file": (name, audio)})
        if up.status_code >= 300:
            return f"slack upload: {up.status_code}"
    body: dict[str, Any] = {"files": [{"id": got["file_id"], "title": "Pippin"}], "channel_id": channel}
    if ctx.get("thread_ts"):
        body["thread_ts"] = ctx["thread_ts"]
    r = await _slack_call("files.completeUploadExternal", **body)
    return "" if r.get("ok") else f"slack: {r.get('error')}"


# ---------------------------------------------------------------- WhatsApp (the Cloud API)

def _graph() -> str:
    v = settings("whatsapp").get("version") or "v21.0"
    return f"https://graph.facebook.com/{v if v.startswith('v') else 'v' + v}"


def whatsapp_check(mode: str, token: str, challenge: str) -> str:
    """The webhook check when it is saved in Meta's app dashboard."""
    want = settings("whatsapp").get("verify_token", "")
    if mode != "subscribe" or not want or not hmac.compare_digest(token, want):
        raise PermissionError("the verify token does not match")
    return challenge


def whatsapp_verify(signature: str, body: bytes) -> None:
    secret = settings("whatsapp").get("app_secret", "")
    if not secret:
        raise PermissionError("no app secret is set")
    mine = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mine, signature or ""):
        raise PermissionError("the signature does not match")


async def whatsapp_event(signature: str, raw: bytes) -> None:
    """Messages from WhatsApp (text and voice), checked by Meta's signature and answered in the background."""
    whatsapp_verify(signature, raw)
    if not settings("whatsapp").get("enabled"):
        return
    try:
        body = json.loads(raw)
    except ValueError:
        raise PermissionError("not JSON") from None
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
            for m in value.get("messages") or []:
                account = m.get("from", "")
                if not account or not _first("whatsapp:" + str(m.get("id"))):
                    continue
                name = names.get(account, "")
                if m.get("type") == "text":
                    _later(_reply("whatsapp", account, (m.get("text") or {}).get("body", ""), {}, name=name))
                elif m.get("type") == "audio" and (m.get("audio") or {}).get("id"):
                    media = m["audio"]["id"]
                    _later(_reply_heard("whatsapp", account, lambda media=media: _whatsapp_media(media), ".ogg", {}, name=name))
                else:
                    _later(_send("whatsapp", account, ONLY_WORDS, {}))


def _wa_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings('whatsapp').get('access_token', '')}"}


async def _whatsapp_media(media_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as c:
        meta = (await c.get(f"{_graph()}/{media_id}", headers=_wa_headers())).json()
        if not meta.get("url"):
            raise httpx.HTTPError(f"whatsapp media: {(meta.get('error') or {}).get('message', meta)}")
        r = await c.get(meta["url"], headers=_wa_headers())
    r.raise_for_status()
    return r.content


async def _whatsapp_post(body: dict[str, Any]) -> str:
    phone = settings("whatsapp").get("phone_number_id", "")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(f"{_graph()}/{phone}/messages", headers=_wa_headers(),
                         json={"messaging_product": "whatsapp", **body})
    if r.status_code < 300:
        return ""
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    if err.get("code") == 131047:
        return ("whatsapp: more than 24 hours since they last wrote; WhatsApp lets Pippin write first only with an "
                "approved template")
    return f"whatsapp {err.get('code', r.status_code)}: {err.get('message', r.text[:200])}"


async def _whatsapp_send(to: str, text: str) -> str:
    return await _whatsapp_post({"to": to, "type": "text", "text": {"body": text[:4096]}})


async def _whatsapp_voice(to: str, audio: bytes, suffix: str) -> str:
    """A voice note: Opus in Ogg, uploaded to the number's media first."""
    ogg, _ = await _convert(audio, suffix, "ogg")
    phone = settings("whatsapp").get("phone_number_id", "")
    async with httpx.AsyncClient(timeout=60) as c:
        up = (await c.post(f"{_graph()}/{phone}/media", headers=_wa_headers(),
                           data={"messaging_product": "whatsapp", "type": "audio/ogg"},
                           files={"file": ("pippin.ogg", ogg, "audio/ogg")})).json()
    if not up.get("id"):
        return f"whatsapp upload: {(up.get('error') or {}).get('message', up)}"
    return await _whatsapp_post({"to": to, "type": "audio", "audio": {"id": up["id"]}})


# ---------------------------------------------------------------- answering, in writing and aloud

ONLY_WORDS = "目前我只读文字和语音消息。 (I read text and voice messages only, for now.)"
NOT_HEARING = ("这个 Pippin 还听不懂语音：管理员需要在服务器上设置 AURORA_WHISPER（whisper.cpp 命令）。请先打字。\n"
               "This Pippin cannot hear voice messages yet: an admin sets AURORA_WHISPER (a whisper.cpp command) on the "
               "server. Please type for now.")


async def _send(app: str, account: str, text: str, ctx: dict[str, Any]) -> str:
    """Write to *account* in *app*: in reply to their message, or in their conversation with the bot."""
    if app == "feishu":
        return await _feishu_send(account, text, ctx.get("reply_to", ""))
    if app == "wecom":
        return await _wecom_send(account, text)
    if app == "slack":
        return await _slack_send(account, text, ctx)
    return await _whatsapp_send(account, text)


async def _reply(app: str, account: str, text: str, ctx: dict[str, Any], *, heard: bool = False, name: str = "") -> None:
    """Answer what *account* wrote (or said: *heard*): Pippin's answer in writing, then, when they spoke, aloud."""
    try:
        out = await answer(app, account, text, name)
    except Exception:
        logger.exception("Pippin could not answer a %s message", app)
        out = "出错了，请稍后再试。 (Something went wrong; try again in a moment.)"
    shown = f"🎙 “{text.strip()}”\n\n{out}" if heard else out
    try:
        error = await _send(app, account, shown, ctx)
    except httpx.HTTPError as exc:
        error = str(exc)
    if error:
        logger.warning("%s reply failed: %s", app, error)
        return
    uid = _who(app, account)
    if heard and uid is not None:
        await _as(uid, lambda: _say_aloud(app, account, out, ctx))


async def _reply_heard(app: str, account: str, fetch: Any, suffix: str, ctx: dict[str, Any], *, name: str = "") -> None:
    """A voice message: heard by the console's speech recognition, then answered like a written one."""
    from aurora.memory import voice
    if _who(app, account) is None:              # not linked yet: how to link (nothing to hear)
        await _reply(app, account, "", ctx, name=name)
        return
    if not voice.can_hear():
        await _send(app, account, NOT_HEARING, ctx)
        return
    try:
        said = await voice.transcribe(await fetch(), suffix)
    except Exception as exc:
        logger.warning("a %s voice message could not be heard: %s", app, exc)
        await _send(app, account, "没听清，请再说一次或打字。 (I could not make that out; say it again or type it.)", ctx)
        return
    if not said.strip():
        await _send(app, account, "没听到内容。 (I heard nothing in that message.)", ctx)
        return
    await _reply(app, account, said, ctx, heard=True, name=name)


def spoken(reply: str) -> str:
    """What of an answer is said aloud: the answer without what is only for reading (the proposals to confirm and the
    links), and a long one cut to its first sentence."""
    from aurora.memory.assistant import SPOKEN
    text = reply.split("\n\n▶", 1)[0].strip()
    text = re.sub(r"https?://\S+", "", text).strip()
    if len(text) <= SPOKEN:
        return text
    first = re.split(r"(?<=[.!?。！？])\s*", text, maxsplit=1)[0][:SPOKEN]
    zh = bool(re.search(r"[一-鿿]", text))
    return first + (" 其余的写在上面。" if zh else " The rest is in the message above.")


async def _speech(text: str) -> tuple[bytes, str]:
    """Speech for *text* in the voice the orb uses: the cloud voice when one is set up, else the model that talks."""
    import os

    from aurora.api.routes.campaigns import base_config
    from aurora.api.routes.memory import _speech_key
    from aurora.memory import voice
    cfg = base_config()
    v = cfg.get("voice") or {}
    if os.environ.get("AURORA_TTS_BASE_URL") or (v.get("speech") or "model") != "model":
        return await voice.cloud_speech(text), ".mp3"
    key = _speech_key()
    if not key:
        raise ValueError("no voice is set up")
    return await voice.model_speech(text, api_key=key, base_url=cfg.get("api_base_url") or "https://openrouter.ai/api/v1",
                                    model=v.get("speech_model") or "openai/gpt-audio-mini",
                                    voice=v.get("speech_voice") or "alloy"), ".wav"


async def _say_aloud(app: str, account: str, reply: str, ctx: dict[str, Any]) -> None:
    """The answer as a voice message too; when anything fails, the written answer already sent is enough."""
    words = spoken(reply)
    if not words:
        return
    try:
        audio, suffix = await _speech(words)
        if app == "feishu":
            error = await _feishu_voice(account, audio, suffix, ctx)
        elif app == "wecom":
            error = await _wecom_voice(account, audio, suffix)
        elif app == "slack":
            error = await _slack_voice(account, audio, suffix, ctx)
        else:
            error = await _whatsapp_voice(account, audio, suffix)
    except Exception as exc:
        error = str(exc) or type(exc).__name__
    if error:
        logger.info("no voice answer on %s (the written one was sent): %s", app, error)


_FORMATS = {"ogg": ["-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1"],
            "amr": ["-c:a", "libopencore_amrnb", "-ar", "8000", "-ac", "1", "-b:a", "12.2k"]}


async def _convert(data: bytes, suffix: str, fmt: str) -> tuple[bytes, int]:
    """*data* (a recording ending in *suffix*) as *fmt* (``ogg``: Opus; ``amr``), and its length in milliseconds."""
    if not shutil.which("ffmpeg"):
        raise ValueError("ffmpeg is not installed")
    with tempfile.TemporaryDirectory(prefix="aurora-im-") as tmp:
        src, out = Path(tmp) / f"in{suffix}", Path(tmp) / f"out.{fmt}"
        src.write_bytes(data)
        p = await asyncio.create_subprocess_exec("ffmpeg", "-y", "-loglevel", "error", "-i", str(src), *_FORMATS[fmt],
                                                 str(out), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(p.communicate(), 60)
        if p.returncode != 0 or not out.is_file():
            raise ValueError(f"ffmpeg: {err.decode(errors='replace')[-200:]}")
        ms = 0
        if shutil.which("ffprobe"):
            q = await asyncio.create_subprocess_exec("ffprobe", "-v", "error", "-show_entries", "format=duration",
                                                     "-of", "csv=p=0", str(out), stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.DEVNULL)
            got, _ = await asyncio.wait_for(q.communicate(), 30)
            try:
                ms = int(float(got.decode().strip()) * 1000)
            except ValueError:
                ms = 0
        return out.read_bytes(), ms


_tasks: set[asyncio.Task] = set()


def _later(coro: Any) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)

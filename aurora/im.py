"""Talking to Pippin from Feishu/Lark (飞书) or WeCom (企业微信): the same conversation as the orb's, in a chat app.

A team admin connects the team's own app (a Feishu custom app with the bot ability, or a WeCom self-built app) and
points its message callback at this server (``/im/feishu`` or ``/im/wecom``; the server must be reachable from the
internet).  Each person links their chat account once: the console gives them a code, they send ``绑定 <code>``
(or ``bind <code>``) to the bot.  From then on what they write to the bot goes to Pippin as them (their projects,
their rights), the answer comes back in the chat, and Pippin's proposals are done when they reply ``确认`` / ``yes``
(``取消`` / ``no`` drops them).  Their decision and run notifications come to the same chat, so they can answer them.

The group robots under Settings › Notifications only send; they cannot hear a reply, which is why this is an app.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import struct
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

APPS = ("feishu", "wecom")
FIELDS = {
    # domain: open.feishu.cn, or open.larksuite.com for Lark
    "feishu": ("app_id", "app_secret", "verification_token", "encrypt_key", "domain"),
    "wecom": ("corp_id", "agent_id", "secret", "token", "aes_key"),
}
SECRET = {"app_secret", "encrypt_key", "secret", "aes_key", "verification_token", "token"}
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
    need = {"feishu": ("app_id", "app_secret"), "wecom": ("corp_id", "agent_id", "secret", "token", "aes_key")}[app]
    return bool(s.get("enabled")) and all(s.get(k) for k in need)


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
            error = await (_feishu_send(account, body) if app == "feishu" else _wecom_send(account, body))
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


async def _feishu_send(open_id: str, text: str, reply_to: str = "") -> str:
    token = await _feishu_token()
    content = json.dumps({"text": text[:8000]}, ensure_ascii=False)
    url = (f"{_feishu_base()}/im/v1/messages/{reply_to}/reply" if reply_to
           else f"{_feishu_base()}/im/v1/messages?receive_id_type=open_id")
    body = {"msg_type": "text", "content": content, **({} if reply_to else {"receive_id": open_id})}
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
    if msg.get("message_type") != "text" or not account:
        if account:
            _later(_feishu_send(account, "目前我只读文字消息。 (I read text messages only, for now.)", msg.get("message_id", "")))
        return {}
    try:
        text = json.loads(msg.get("content") or "{}").get("text", "")
    except ValueError:
        text = ""

    async def go() -> None:
        try:
            out = await answer("feishu", account, text)
        except Exception:
            logger.exception("Pippin could not answer a Feishu message")
            out = "出错了，请稍后再试。 (Something went wrong; try again in a moment.)"
        error = await _feishu_send(account, out, msg.get("message_id", ""))
        if error:
            logger.warning("Feishu reply failed: %s", error)
    _later(go())
    return {}


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
    if msg.findtext("MsgType") != "text" or not account:
        return
    if not _first("wecom:" + (msg.findtext("MsgId") or f"{account}:{msg.findtext('CreateTime')}")):
        return
    text = msg.findtext("Content") or ""

    async def go() -> None:
        try:
            out = await answer("wecom", account, text)
        except Exception:
            logger.exception("Pippin could not answer a WeCom message")
            out = "出错了，请稍后再试。 (Something went wrong; try again in a moment.)"
        error = await _wecom_send(account, out)
        if error:
            logger.warning("WeCom reply failed: %s", error)
    _later(go())


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


async def _wecom_send(user: str, text: str) -> str:
    s = settings("wecom")
    token = await _wecom_token()
    body = {"touser": user, "msgtype": "text", "agentid": int(s.get("agent_id") or 0), "text": {"content": text[:2000]}}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = (await c.post("https://qyapi.weixin.qq.com/cgi-bin/message/send", params={"access_token": token}, json=body)).json()
    return "" if r.get("errcode") == 0 else f"wecom {r.get('errcode')}: {r.get('errmsg')}"


_tasks: set[asyncio.Task] = set()


def _later(coro: Any) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)

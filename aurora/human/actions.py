"""Signed links that answer a decision from a notification ("Approve", "Stop").

A link carries the run, the gate, the action and the person it was sent to, signed with a key only
the server has, and expires after a day.  Opening it shows what it would do and asks to confirm
(link previews in chat apps open links too, so a plain open never acts).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

TTL = 24 * 3600
LABELS = {"continue": "Approve and continue", "stop": "Stop the experiment"}


def _key() -> bytes:
    from aurora.paths import aurora_home
    path = Path(aurora_home()) / "action.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
        os.chmod(path, 0o600)
    return path.read_bytes()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make(campaign_id: str, run_id: str, gate_id: str, action: str, user_id: str, ttl: int = TTL) -> str:
    payload = _b64(json.dumps({"c": campaign_id, "r": run_id, "g": gate_id, "a": action, "u": user_id,
                               "e": int(time.time()) + ttl}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest()[:18])
    return f"{payload}.{sig}"


def read(token: str) -> dict[str, Any]:
    """The link's content; raises ValueError when it was tampered with or has expired."""
    try:
        payload, sig = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("not a decision link") from exc
    want = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest()[:18])
    if not hmac.compare_digest(want, sig):
        raise ValueError("this link is not valid")
    data = json.loads(_unb64(payload))
    if data["e"] < time.time():
        raise ValueError("this link has expired: decide in the console")
    if data["a"] not in LABELS:
        raise ValueError("unknown action")
    return data

"""API keys kept encrypted at rest; nobody can read one back through the console.

The encryption key is ``AURORA_HOME/secret.key`` (created on first use, readable by the server's
user only).  A key is only ever decrypted to start that person's (or the team's) model calls.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from aurora.auth.permissions import User
from aurora.auth.store import auth_store

PROVIDERS = ("openrouter",)
# other services a person connects (not model providers): their keys are kept the same way
SERVICES = ("zotero",)


def _fernet() -> Fernet:
    from aurora.paths import aurora_home
    path = Path(aurora_home()) / "secret.key"
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
        os.chmod(path, 0o600)
    return Fernet(path.read_bytes())


def _hint(value: str) -> str:
    return f"{value[:6]}…{value[-4:]}" if len(value) > 12 else "…"


def put_key(owner: str, provider: str, value: str) -> str:
    value = value.strip()
    if provider not in PROVIDERS + SERVICES:
        raise ValueError(f"provider must be one of {PROVIDERS + SERVICES}")
    if len(value) < 8:
        raise ValueError("That does not look like an API key")
    auth_store.put_secret(owner, provider, _fernet().encrypt(value.encode()), _hint(value))
    return _hint(value)


def get_key(owner: str, provider: str = "openrouter") -> str | None:
    row = auth_store.get_secret(owner, provider)
    if row is None:
        return None
    try:
        return _fernet().decrypt(row["ciphertext"]).decode()
    except InvalidToken:
        return None


def key_status(owner: str, provider: str = "openrouter") -> dict[str, object]:
    row = auth_store.get_secret(owner, provider)
    return {"provider": provider, "set": row is not None, "hint": row["hint"] if row else "",
            "updated_at": row["updated_at"] if row else None}


def key_for(user: User, provider: str = "openrouter") -> tuple[str, str]:
    """The key a user's model calls use and where it comes from: theirs, the team's, or none.

    Without accounts (or for the local owner) this is the server's environment key, as before.
    """
    if user.is_local:
        return os.environ.get("OPENROUTER_API_KEY", ""), "server"
    own = get_key(f"user:{user.user_id}", provider)
    if own:
        return own, "own"
    if auth_store.rules().get("team_key_for_members") and not user.is_guest:
        team = get_key("team", provider) or os.environ.get("OPENROUTER_API_KEY", "")
        if team:
            return team, "team"
    return "", "none"

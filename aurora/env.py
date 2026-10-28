"""Load API keys from a ``.env`` file (shared by the CLI and the web app)."""

from __future__ import annotations

import os
from pathlib import Path

# A source checkout keeps .env at the repository root
_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def _candidates() -> list[Path]:
    paths = [Path.cwd() / ".env"]
    home = os.environ.get("AURORA_HOME")
    paths.append(Path(home).expanduser() / ".env" if home else Path.home() / ".aurora" / ".env")
    paths.append(_SOURCE_ROOT / ".env")
    return paths


def load_dotenv(path: str | Path | None = None) -> Path | None:
    """Set ``KEY=value`` lines from the first ``.env`` found as environment variables.

    Looks in the current directory, then ``$AURORA_HOME`` (default
    ``~/.aurora``), then the source checkout.  Variables already set in the
    environment win, so a shell ``export`` overrides the file.  Returns the
    file that was loaded, if any.
    """
    for env_file in ([Path(path)] if path else _candidates()):
        if not env_file.is_file():
            continue
        with open(env_file) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):]
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip().strip("'\""))
        add_to_path(os.environ.get("AURORA_DOCKER_DIR", ""))
        return env_file
    return None


def add_to_path(directory: str) -> None:
    """Put *directory* first on this process's PATH (a command the person told the console where to find)."""
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if directory and directory not in parts:
        os.environ["PATH"] = os.pathsep.join([directory, *parts])


KEY_PATTERN = r"sk-or-[A-Za-z0-9_\-]{20,}"


def check_openrouter_key(key: str, timeout: float = 15) -> bool | None:
    """Whether OpenRouter accepts *key*: True, False, or None when it could not be asked (offline)."""
    import httpx
    try:
        r = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    except httpx.HTTPError:
        return None
    return r.status_code not in (401, 403)


def save_env_value(name: str, value: str) -> Path:
    """Write ``name=value`` into ``AURORA_HOME/.env`` (readable only by this user) and set it for this process."""
    from aurora.paths import aurora_home
    path = aurora_home() / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [ln for ln in (path.read_text().splitlines() if path.exists() else [])
             if not ln.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    os.environ[name] = value
    return path


def save_openrouter_key(key: str) -> Path:
    """Write the key into ``AURORA_HOME/.env`` (readable only by this user) and set it for this process."""
    return save_env_value("OPENROUTER_API_KEY", key)

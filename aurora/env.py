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
        return env_file
    return None

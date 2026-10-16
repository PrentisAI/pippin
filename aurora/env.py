"""Load API keys from the project's ``.env`` file (shared by the CLI and the web app)."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: str | Path | None = None) -> None:
    """Set ``KEY=value`` lines from ``.env`` as environment variables.

    Variables already set in the environment win, so a shell ``export``
    overrides the file.
    """
    env_file = Path(path) if path else PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    with open(env_file) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

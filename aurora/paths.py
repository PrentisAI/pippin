"""Where Pippin keeps its state on disk.

Everything the web console stores (project database, event logs, experiment
workspaces) lives under one directory, ``$AURORA_HOME`` (default ``~/.aurora``).
"""

from __future__ import annotations

import os
from pathlib import Path


def aurora_home() -> Path:
    home = Path(os.environ.get("AURORA_HOME") or Path.home() / ".aurora").expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return home

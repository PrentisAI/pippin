"""Which Claude Code session did which piece of work, so an interrupted piece can be continued.

Every agent session (a phase, an audit, a research job) is recorded under a key
in ``<campaign>/sessions.json`` as soon as Claude Code reports its session id,
and marked done when it finishes.  When a stopped campaign is resumed, a piece
of work whose session is recorded but not done is continued in that same
session (``claude --resume``), with everything it had already read, thought and
written, instead of starting over.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CONTINUE_NOTE = (
    "[Resumed] This session was interrupted because the campaign was stopped, and it now "
    "continues. Commands now run in the container named in the system prompt (a new container; "
    "the files from before were put back). First check what you had already produced, then "
    "continue the task from where you stopped. Do not start over."
)

RESTART_NOTE = (
    "[Restarted] An earlier attempt at this task was interrupted. Its partial outputs may already "
    "be in the workspace (and in /app in the container): look at them first and build on them."
)


class SessionRegistry:
    """Persistent map from a piece of work to its Claude Code session."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        try:
            self._data: dict[str, dict] = json.loads(self._path.read_text()) if self._path.exists() else {}
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read %s; interrupted sessions will start over", self._path)
            self._data = {}

    def open_session(self, key: str) -> str | None:
        """The session of *key* if it was started but not finished."""
        entry = self._data.get(key)
        return entry["session_id"] if entry and not entry.get("done") else None

    def system_prompt(self, key: str) -> str:
        """The system prompt the session of *key* was started with ('' if unknown)."""
        return (self._data.get(key) or {}).get("system_prompt", "")

    def record(self, key: str, session_id: str, system_prompt: str = "") -> None:
        self._data[key] = {"session_id": session_id, "done": False, "updated_at": time.time(),
                           "system_prompt": system_prompt or self.system_prompt(key)}
        self._save()

    def done(self, key: str) -> None:
        if key in self._data:
            self._data[key]["done"] = True
            self._data[key]["updated_at"] = time.time()
            self._save()

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1))
        tmp.replace(self._path)

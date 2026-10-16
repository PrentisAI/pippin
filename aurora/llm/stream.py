"""Live events emitted by agent sessions for the web console and the event log."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class StreamEvent:
    """One event of a running agent session (tool call, text, result, ...)."""

    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"event_type": self.event_type, "data": self.data, "timestamp": self.timestamp}


StreamCallback = Callable[[StreamEvent], Awaitable[None]]

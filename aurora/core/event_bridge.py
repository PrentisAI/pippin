"""Event bridge — translates core engine events to WebSocket format.

Subscribes to the ``EventBus`` and converts typed ``Event`` objects
into the flat dict format the frontend expects, then broadcasts via
the run state and persists to JSONL.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aurora.core.events import Event, EventBus

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]
EventHookFn = Callable[[dict[str, Any]], Awaitable[None]]


class EventBridge:
    """Bridge between the core engine EventBus and the WebSocket layer.

    Converts typed ``Event`` objects into the flat ``lhtb_event`` dict
    format the frontend ``AgentStreamView`` expects.
    """

    def __init__(
        self,
        bus: EventBus,
        run_id: str,
        *,
        broadcast: BroadcastFn | None = None,
        on_event_hook: EventHookFn | None = None,
    ) -> None:
        self._bus = bus
        self._run_id = run_id
        self._broadcast = broadcast
        self._on_event_hook = on_event_hook
        bus.subscribe_all(self._on_engine_event)

    async def _on_engine_event(self, event: Event) -> None:
        """Convert an engine Event to a WebSocket dict and broadcast."""
        ws_event: dict[str, Any] = {
            "type": "lhtb_event",
            "run_id": self._run_id,
            "event_type": event.name,
            "data": event.data,
            "timestamp": time.time(),
        }

        if self._broadcast is not None:
            try:
                await self._broadcast(ws_event)
            except Exception:
                logger.debug("Broadcast failed for event %s", event.name)

        if self._on_event_hook is not None:
            try:
                await self._on_event_hook(ws_event)
            except Exception:
                logger.debug("Event hook failed for event %s", event.name)

    def detach(self) -> None:
        """Unsubscribe from the event bus."""
        self._bus.unsubscribe("*", self._on_engine_event)

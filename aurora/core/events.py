"""Asynchronous event bus for intra-campaign communication.

Events are typed, named messages that decouple components — an agent
producing an observation does not need to know which hooks, auditors,
or UI elements consume it.  Handlers are async callables registered
per event name.

Usage::

    bus = EventBus()

    async def on_experiment_complete(event: Event) -> None:
        print(event.data["job_id"])

    bus.subscribe("experiment_complete", on_experiment_complete)
    await bus.publish(Event(name="experiment_complete", data={"job_id": "j1"}))
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Callable[["Event"], Awaitable[None]]


@dataclass
class Event:
    """A single event flowing through the bus."""

    name: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class EventBus:
    """Simple async publish-subscribe event bus.

    All handlers for a given event name run concurrently via
    ``asyncio.gather``.  A handler that raises is logged but does not
    prevent other handlers from running.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._history: list[Event] = []
        self._max_history: int = 1000

    # -- subscription --------------------------------------------------------

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """Register *handler* for events named *event_name*."""
        self._handlers.setdefault(event_name, []).append(handler)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        """Remove *handler* from *event_name* (no-op if not found)."""
        handlers = self._handlers.get(event_name, [])
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    def subscribe_all(self, handler: EventHandler) -> None:
        """Register *handler* to receive **every** event (wildcard ``*``)."""
        self._handlers.setdefault("*", []).append(handler)

    # -- publishing ----------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """Publish *event* to all matching handlers.

        Handlers registered for the event's name **and** wildcard handlers
        (``*``) are invoked concurrently.  Exceptions in individual handlers
        are logged and suppressed.
        """
        # Record in history (ring buffer)
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        handlers: list[EventHandler] = []
        handlers.extend(self._handlers.get(event.name, []))
        handlers.extend(self._handlers.get("*", []))

        if not handlers:
            return

        results = await asyncio.gather(
            *(self._safe_call(h, event) for h in handlers),
            return_exceptions=True,
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Event handler %s raised for event %r: %s",
                    handlers[i].__name__,
                    event.name,
                    result,
                )

    async def _safe_call(self, handler: EventHandler, event: Event) -> None:
        """Call *handler* with *event*, catching and re-raising as needed."""
        try:
            await handler(event)
        except Exception:
            logger.exception(
                "Handler %s failed for event %r",
                handler.__name__,
                event.name,
            )
            raise

    # -- introspection -------------------------------------------------------

    @property
    def history(self) -> list[Event]:
        """Return a copy of the event history."""
        return list(self._history)

    def clear_history(self) -> None:
        """Purge the event history buffer."""
        self._history.clear()

    def handler_count(self, event_name: str | None = None) -> int:
        """Return the number of handlers for *event_name* (or all events)."""
        if event_name is not None:
            return len(self._handlers.get(event_name, []))
        return sum(len(hs) for hs in self._handlers.values())

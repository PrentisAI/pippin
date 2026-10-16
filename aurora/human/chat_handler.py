"""Bidirectional chat handler for agentic research loops.

Bridges the frontend user and the running agentic researcher:

- **User → Agent**: messages queued via :meth:`send_user_message` are
  picked up by the :class:`AgenticLoop` between tool calls (cooperative
  interrupt) and injected into Claude's conversation (chat steering).

- **Agent → User**: stream events from the agentic loop are forwarded
  to all connected WebSocket clients for live rendering.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ChatMessage:
    """A single message in the chat history."""
    role: str  # "user" | "agent" | "system"
    text: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class AgentChatHandler:
    """Manages bidirectional chat between a human user and an agentic loop.

    Lifecycle
    ---------
    1. ``StateManager`` creates one per running campaign.
    2. The ``AgenticResearchIteration`` passes ``self.user_queue`` and
       ``self.stream_event`` to the :class:`AgenticLoop`.
    3. The frontend sends messages via ``POST /chat`` → :meth:`send_user_message`.
    4. The agentic loop drains the queue between tool calls and injects
       messages into the Claude conversation.
    5. Stream events (reasoning, tool calls, results) are broadcast to
       all connected WebSocket clients.
    """

    def __init__(
        self,
        campaign_id: str,
        broadcast_fn: BroadcastFn | None = None,
    ) -> None:
        self._campaign_id = campaign_id
        self._broadcast = broadcast_fn
        self._user_queue: asyncio.Queue[str] = asyncio.Queue()
        self._runtime_user_queue: asyncio.Queue[str] | None = None
        self._history: list[ChatMessage] = []
        self._active = False

    @property
    def user_queue(self) -> asyncio.Queue[str]:
        """Queue for the agentic loop to read user messages from."""
        return self._user_queue

    @property
    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        self._active = active

    # ------------------------------------------------------------------
    # User → Agent
    # ------------------------------------------------------------------

    async def send_user_message(self, text: str) -> dict[str, Any]:
        """Queue a user message for the agentic loop.

        Called by the REST API when the user sends a chat message.
        Returns a confirmation dict.
        """
        msg = ChatMessage(role="user", text=text)
        self._history.append(msg)

        target_queue = self._runtime_user_queue or self._user_queue
        target_queue.put_nowait(text)

        if self._broadcast:
            await self._broadcast({
                "type": "chat_message",
                "campaign_id": self._campaign_id,
                "data": msg.to_dict(),
            })

        logger.info(
            "User message queued for campaign %s: %s",
            self._campaign_id, text[:80],
        )

        return {
            "status": "queued",
            "queue_size": self._user_queue.qsize(),
            "active": self._active,
        }

    # ------------------------------------------------------------------
    # Agent → User (stream events)
    # ------------------------------------------------------------------

    async def stream_event(self, event: Any) -> None:
        """Forward an agentic loop stream event to the frontend.

        This is passed as the ``stream_callback`` to :class:`AgenticLoop`.

        Parameters
        ----------
        event :
            A :class:`StreamEvent` from the tool runner.
        """
        event_dict = event.to_dict() if hasattr(event, "to_dict") else {
            "event_type": str(event),
            "data": {},
            "timestamp": time.time(),
        }

        # Record agent-facing events in chat history
        event_type = event_dict.get("event_type", "")
        data = event_dict.get("data", {})

        if event_type in ("reasoning_delta", "brain_reasoning_delta"):
            pass
        elif event_type == "reasoning":
            self._history.append(ChatMessage(
                role="agent",
                text=data.get("text", "")[:500],
                metadata={"type": "reasoning", "turn": data.get("turn")},
            ))
        elif event_type == "tool_call_start":
            self._history.append(ChatMessage(
                role="system",
                text=f"Calling tool: {data.get('tool_name', '?')}",
                metadata={"type": "tool_call", "tool_name": data.get("tool_name")},
            ))
        elif event_type == "tool_call_result":
            self._history.append(ChatMessage(
                role="system",
                text=(
                    f"Tool {data.get('tool_name', '?')}: "
                    f"{'success' if data.get('success') else 'failed'} "
                    f"({data.get('duration_seconds', 0):.1f}s)"
                ),
                metadata={
                    "type": "tool_result",
                    "tool_name": data.get("tool_name"),
                    "success": data.get("success"),
                },
            ))
        elif event_type == "user_message_injected":
            self._history.append(ChatMessage(
                role="system",
                text=f"Message delivered to agent: {data.get('text', '')[:100]}",
                metadata={"type": "user_message_injected"},
            ))
        elif event_type == "agent_done":
            self._history.append(ChatMessage(
                role="agent",
                text=data.get("text", "")[:500],
                metadata={"type": "final_response"},
            ))
        # -- Duplex mode events --
        elif event_type == "cerebellum_response":
            self._history.append(ChatMessage(
                role="agent",
                text=data.get("text", "")[:500],
                metadata={"type": "cerebellum_response"},
            ))
        elif event_type == "brain_task_started":
            self._history.append(ChatMessage(
                role="system",
                text=f"Brain task started: {data.get('description', data.get('task_id', '?'))[:150]}",
                metadata={
                    "type": "brain_task_started",
                    "task_id": data.get("task_id"),
                    "task_type": data.get("task_type"),
                },
            ))
        elif event_type == "brain_tool_call_start":
            self._history.append(ChatMessage(
                role="system",
                text=f"[Brain] Calling tool: {data.get('tool_name', '?')}",
                metadata={
                    "type": "brain_tool_call",
                    "tool_name": data.get("tool_name"),
                    "task_id": data.get("task_id"),
                },
            ))
        elif event_type == "brain_tool_call_result":
            self._history.append(ChatMessage(
                role="system",
                text=(
                    f"[Brain] Tool {data.get('tool_name', '?')}: "
                    f"{'success' if data.get('success') else 'failed'}"
                ),
                metadata={
                    "type": "brain_tool_result",
                    "tool_name": data.get("tool_name"),
                    "success": data.get("success"),
                    "task_id": data.get("task_id"),
                },
            ))
        elif event_type == "brain_reasoning":
            self._history.append(ChatMessage(
                role="agent",
                text=f"[Brain] {data.get('text', '')[:400]}",
                metadata={
                    "type": "brain_reasoning",
                    "task_id": data.get("task_id"),
                },
            ))
        elif event_type == "brain_agent_done":
            self._history.append(ChatMessage(
                role="system",
                text=(
                    f"Brain task {data.get('task_id', '?')} complete "
                    f"({data.get('turns', '?')} turns, "
                    f"{data.get('tool_calls', '?')} tool calls)"
                ),
                metadata={
                    "type": "brain_agent_done",
                    "task_id": data.get("task_id"),
                },
            ))
        elif event_type == "brain_progress":
            self._history.append(ChatMessage(
                role="agent",
                text=f"[Brain] {data.get('result_preview', '')[:300]}",
                metadata={
                    "type": "brain_progress",
                    "task_id": data.get("task_id"),
                },
            ))
        elif event_type == "user_message_received":
            self._history.append(ChatMessage(
                role="system",
                text=f"User message received: {data.get('text', '')[:100]}",
                metadata={"type": "user_message_received"},
            ))
        elif event_type == "error":
            self._history.append(ChatMessage(
                role="system",
                text=f"Error ({data.get('source', '?')}): {data.get('error', '')[:200]}",
                metadata={"type": "error", "source": data.get("source")},
            ))

        # Broadcast to WebSocket clients
        if self._broadcast:
            await self._broadcast({
                "type": "agent_stream",
                "campaign_id": self._campaign_id,
                "event": event_dict,
            })

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent chat history."""
        return [m.to_dict() for m in self._history[-limit:]]

    def clear_history(self) -> None:
        self._history.clear()

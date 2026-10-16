"""Web-based human checkpoint handler (async queue transport).

Replaces terminal-based stdin prompts with an async queue pattern so
that the web frontend can submit checkpoint responses via the REST API
while the engine coroutine awaits a decision.

Flow
----
1. The engine calls :meth:`request_approval` when it hits a checkpoint.
2. A :class:`CheckpointRequest` is placed into the pending map.
3. A WebSocket notification is broadcast to connected frontends.
4. The engine coroutine awaits the per-checkpoint response queue.
5. The frontend user reviews the request and POSTs a response.
6. The API route calls :meth:`submit_response`, which puts the
   response onto the queue.
7. ``request_approval`` resolves and returns the response.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aurora.config import AutonomyMode
from aurora.human.checkpoint import (
    CheckpointDecision,
    CheckpointRequest,
    CheckpointResponse,
)
from aurora.models.artifacts import ResearchArtifacts

logger = logging.getLogger(__name__)

# Type alias for the broadcast callback
BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]

# Risk levels that require human approval in supervised mode
_HIGH_RISK = {"high", "critical"}


class WebCheckpointHandler:
    """Web-based checkpoint handler using async queues.

    When the engine hits a checkpoint:
    1. It calls ``request_approval()`` which puts a
       :class:`CheckpointRequest` into the pending map.
    2. It broadcasts a WebSocket notification to the frontend.
    3. It awaits the response (blocks the engine coroutine, **not** the
       event loop).
    4. The frontend user sees the alert, reviews, and submits a response.
    5. The response is put into the per-checkpoint response queue.
    6. ``request_approval()`` resolves and returns the
       :class:`CheckpointResponse`.

    Parameters
    ----------
    broadcast_fn : BroadcastFn | None
        Async callable used to push WebSocket messages to all connected
        frontends.  May be ``None`` during testing.
    auto_approve_timeout : float | None
        If set, automatically approve checkpoints after this many
        seconds when no human responds.
    autonomy_mode : AutonomyMode
        Initial autonomy mode.  Can be changed at runtime via
        :meth:`set_autonomy_mode`.
    """

    def __init__(
        self,
        broadcast_fn: BroadcastFn | None = None,
        auto_approve_timeout: float | None = None,
        autonomy_mode: AutonomyMode = AutonomyMode.SUPERVISED,
    ) -> None:
        self._pending: dict[str, CheckpointRequest] = {}
        self._response_queues: dict[str, asyncio.Queue[CheckpointResponse]] = {}
        self._broadcast = broadcast_fn
        self._auto_approve_timeout = auto_approve_timeout
        self._autonomy_mode = autonomy_mode
        self._history: list[tuple[CheckpointRequest, CheckpointResponse]] = []

    # ------------------------------------------------------------------
    # Autonomy mode
    # ------------------------------------------------------------------

    @property
    def autonomy_mode(self) -> AutonomyMode:
        return self._autonomy_mode

    def set_autonomy_mode(self, mode: AutonomyMode) -> int:
        """Change the autonomy mode and release pending checkpoints if needed.

        Returns the number of checkpoints that were auto-resolved.
        """
        old = self._autonomy_mode
        self._autonomy_mode = mode
        logger.info("Autonomy mode changed: %s -> %s", old.value, mode.value)

        if mode == AutonomyMode.AUTONOMOUS:
            return self._release_all_pending("Mode switched to autonomous")
        if mode == AutonomyMode.SUPERVISED:
            return self._release_low_risk_pending()
        return 0

    def _should_auto_approve(self, request: CheckpointRequest) -> bool:
        """Decide whether a checkpoint should be auto-approved under the current mode."""
        if self._autonomy_mode == AutonomyMode.AUTONOMOUS:
            return True
        if self._autonomy_mode == AutonomyMode.SUPERVISED:
            return request.risk_assessment not in _HIGH_RISK
        return False

    def _release_all_pending(self, reason: str) -> int:
        """Auto-approve all pending checkpoints."""
        count = 0
        for cp_id, queue in list(self._response_queues.items()):
            response = CheckpointResponse(
                checkpoint_id=cp_id,
                decision=CheckpointDecision.APPROVE.value,
                comments=reason,
                timestamp=datetime.now(timezone.utc),
            )
            try:
                queue.put_nowait(response)
                count += 1
            except asyncio.QueueFull:
                pass
        return count

    def _release_low_risk_pending(self) -> int:
        """Auto-approve pending checkpoints that are not high risk."""
        count = 0
        for cp_id, request in list(self._pending.items()):
            if request.risk_assessment not in _HIGH_RISK:
                queue = self._response_queues.get(cp_id)
                if queue is not None:
                    response = CheckpointResponse(
                        checkpoint_id=cp_id,
                        decision=CheckpointDecision.APPROVE.value,
                        comments="Auto-approved (supervised mode, low risk)",
                        timestamp=datetime.now(timezone.utc),
                    )
                    try:
                        queue.put_nowait(response)
                        count += 1
                    except asyncio.QueueFull:
                        pass
        return count

    # ------------------------------------------------------------------
    # Engine-facing API
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        request: CheckpointRequest,
    ) -> CheckpointResponse:
        """Submit a checkpoint and wait for a human response via the web UI.

        This method blocks the calling coroutine until a response arrives
        (or the auto-approve timeout fires).  It does **not** block the
        event loop, so other tasks can run concurrently.

        Parameters
        ----------
        request : CheckpointRequest
            The checkpoint data to present to the human.

        Returns
        -------
        CheckpointResponse
            The human's decision (or an auto-approved fallback).
        """
        # Check autonomy mode before blocking
        if self._should_auto_approve(request):
            logger.info(
                "Checkpoint %s auto-approved (mode=%s, risk=%s)",
                request.checkpoint_id,
                self._autonomy_mode.value,
                request.risk_assessment,
            )
            response = CheckpointResponse(
                checkpoint_id=request.checkpoint_id,
                decision=CheckpointDecision.APPROVE.value,
                comments=f"Auto-approved ({self._autonomy_mode.value} mode)",
                timestamp=datetime.now(timezone.utc),
            )
            self._history.append((request, response))
            return response

        self._pending[request.checkpoint_id] = request
        queue: asyncio.Queue[CheckpointResponse] = asyncio.Queue(maxsize=1)
        self._response_queues[request.checkpoint_id] = queue

        logger.info(
            "Checkpoint %s awaiting human response (event=%s, mode=%s)",
            request.checkpoint_id,
            request.event_type,
            self._autonomy_mode.value,
        )

        # Broadcast to frontend via WebSocket
        if self._broadcast:
            await self._broadcast({
                "type": "checkpoint_request",
                "data": request.model_dump(mode="json"),
            })

        # Wait for response (with optional timeout -> auto-approve)
        try:
            if self._auto_approve_timeout:
                response = await asyncio.wait_for(
                    queue.get(),
                    timeout=self._auto_approve_timeout,
                )
            else:
                response = await queue.get()
        except asyncio.TimeoutError:
            logger.info(
                "Checkpoint %s auto-approved after %.0fs timeout",
                request.checkpoint_id,
                self._auto_approve_timeout,
            )
            response = CheckpointResponse(
                checkpoint_id=request.checkpoint_id,
                decision=CheckpointDecision.APPROVE.value,
                comments="Auto-approved after web timeout",
                timestamp=datetime.now(timezone.utc),
            )

        # Clean up
        self._pending.pop(request.checkpoint_id, None)
        self._response_queues.pop(request.checkpoint_id, None)
        self._history.append((request, response))

        # Broadcast resolution
        if self._broadcast:
            await self._broadcast({
                "type": "checkpoint_resolved",
                "data": {
                    "checkpoint_id": request.checkpoint_id,
                    "decision": response.decision,
                },
            })

        logger.info(
            "Checkpoint %s resolved: %s",
            request.checkpoint_id,
            response.decision,
        )
        return response

    # ------------------------------------------------------------------
    # API-facing methods
    # ------------------------------------------------------------------

    def submit_response(
        self,
        checkpoint_id: str,
        response: CheckpointResponse,
    ) -> bool:
        """Submit a human response to a pending checkpoint.

        Called by the REST API endpoint when the user responds.

        Parameters
        ----------
        checkpoint_id : str
            The checkpoint to respond to.
        response : CheckpointResponse
            The human's decision.

        Returns
        -------
        bool
            ``True`` if the response was delivered, ``False`` if no
            pending checkpoint with that ID exists.
        """
        queue = self._response_queues.get(checkpoint_id)
        if queue is None:
            logger.warning(
                "No pending checkpoint %s — response ignored", checkpoint_id
            )
            return False
        queue.put_nowait(response)
        return True

    def get_pending(self) -> list[CheckpointRequest]:
        """Return all checkpoint requests waiting for a human response."""
        return list(self._pending.values())

    def get_history(self) -> list[tuple[CheckpointRequest, CheckpointResponse]]:
        """Return the full history of resolved checkpoints."""
        return list(self._history)

    def has_pending(self, checkpoint_id: str) -> bool:
        """Check whether a specific checkpoint is still pending."""
        return checkpoint_id in self._pending

    # ------------------------------------------------------------------
    # Convenience factory
    # ------------------------------------------------------------------

    @staticmethod
    def build_request(
        event_type: str,
        summary: str,
        *,
        proposed_action: str = "",
        risk_assessment: str = "low",
        recommendation: str = "approve",
        current_state: str = "",
        artifacts: ResearchArtifacts | None = None,
    ) -> CheckpointRequest:
        """Build a :class:`CheckpointRequest` with a fresh ID.

        This is a convenience helper that mirrors the parameter list of
        :meth:`HumanCheckpoint.request_approval` but returns a request
        object rather than calling the handler directly.
        """
        state_str = current_state
        if not state_str and artifacts:
            state_str = (
                f"Goal: {artifacts.current_goal}; "
                f"Experiments: {len(artifacts.experiments)} completed, "
                f"{len(artifacts.failed_experiments)} failed; "
                f"Claims: {len(artifacts.claims)}; "
                f"Observations: {len(artifacts.observations)}; "
                f"Interventions: {len(artifacts.human_interventions)}"
            )

        return CheckpointRequest(
            checkpoint_id=f"cp_{uuid.uuid4().hex[:12]}",
            event_type=event_type,
            summary=summary,
            current_state=state_str,
            proposed_action=proposed_action,
            risk_assessment=risk_assessment,
            recommendation=recommendation,
            timestamp=datetime.now(timezone.utc),
        )

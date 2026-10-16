"""Structured human checkpoint approval (Section G.2).

When the harness encounters a decision that requires human judgement —
initial research specification, goal steering, auditor escalation, or
termination — it pauses and presents a structured summary to the human
operator.

Interaction modes
-----------------
* **stdin** (default): prompt on the terminal and wait for input.
* **file-poll**: write a request JSON and poll for a response file.
* **auto-approve**: automatically approve after a configurable timeout
  (for fully unattended runs).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from aurora.config import HumanCheckpointConfig
from aurora.models.artifacts import ResearchArtifacts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Checkpoint decision
# ---------------------------------------------------------------------------

class CheckpointDecision(str, Enum):
    """Human's response to a checkpoint prompt."""
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"       # approve with modifications
    ESCALATE = "escalate"   # request further review
    TERMINATE = "terminate"  # stop the campaign


class CheckpointRequest(BaseModel):
    """Data presented to the human at a checkpoint."""
    checkpoint_id: str
    event_type: str
    summary: str
    current_state: str
    proposed_action: str
    risk_assessment: str
    recommendation: str
    artifacts_snapshot: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class CheckpointResponse(BaseModel):
    """Human's response to a checkpoint."""
    checkpoint_id: str
    decision: str  # CheckpointDecision value
    comments: str = ""
    modifications: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


# ---------------------------------------------------------------------------
# Human checkpoint handler
# ---------------------------------------------------------------------------

class HumanCheckpoint:
    """Manages human-in-the-loop checkpoint interactions.

    Parameters
    ----------
    config : HumanCheckpointConfig
        Controls which events require approval and timeout behaviour.
    mode : str
        Interaction mode: ``"stdin"`` (default), ``"file_poll"``, or
        ``"auto_approve"``.
    poll_dir : Path | None
        Directory for file-poll mode request/response files.
    """

    def __init__(
        self,
        config: HumanCheckpointConfig,
        mode: str = "stdin",
        poll_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._mode = mode
        self._poll_dir = poll_dir

    def requires_approval(self, event_type: str) -> bool:
        """Check whether *event_type* needs human approval."""
        return event_type in self._config.require_approval_for

    async def request_approval(
        self,
        checkpoint_id: str,
        event_type: str,
        summary: str,
        artifacts: ResearchArtifacts | None = None,
        proposed_action: str = "",
        risk_assessment: str = "low",
        recommendation: str = "approve",
    ) -> CheckpointResponse:
        """Present a checkpoint to the human and wait for a decision.

        Parameters
        ----------
        checkpoint_id : str
            Unique identifier for this checkpoint.
        event_type : str
            The type of event requiring approval (must be in
            ``require_approval_for``).
        summary : str
            Human-readable summary of the current research state.
        artifacts : ResearchArtifacts | None
            Current artifacts for context.
        proposed_action : str
            What the system intends to do next.
        risk_assessment : str
            Risk level: "low", "medium", "high".
        recommendation : str
            System's recommendation: "approve", "reject", etc.

        Returns
        -------
        CheckpointResponse
            The human's decision.
        """
        request = CheckpointRequest(
            checkpoint_id=checkpoint_id,
            event_type=event_type,
            summary=summary,
            current_state=self._format_state_summary(artifacts) if artifacts else "",
            proposed_action=proposed_action,
            risk_assessment=risk_assessment,
            recommendation=recommendation,
            timestamp=datetime.now(timezone.utc),
        )

        if self._mode == "auto_approve":
            return await self._auto_approve(request)
        elif self._mode == "file_poll":
            return await self._file_poll(request)
        else:
            return await self._stdin_prompt(request)

    # ------------------------------------------------------------------
    # Interaction modes
    # ------------------------------------------------------------------

    async def _stdin_prompt(self, request: CheckpointRequest) -> CheckpointResponse:
        """Present a checkpoint on stdin and wait for user input."""
        banner = "\n" + "=" * 60
        print(banner)
        print(f"  🔔 HUMAN CHECKPOINT: {request.event_type}")
        print(banner)
        print(f"\n{request.summary}\n")

        if request.current_state:
            print(f"Current state:\n{request.current_state}\n")

        if request.proposed_action:
            print(f"Proposed action: {request.proposed_action}")
        print(f"Risk assessment: {request.risk_assessment}")
        print(f"Recommendation: {request.recommendation}")

        print(f"\nOptions: [a]pprove  [r]eject  [m]odify  [t]erminate")

        # Auto-approve with timeout if configured
        timeout = self._config.auto_approve_timeout_seconds
        if timeout is not None:
            print(f"(Auto-approving in {timeout}s if no response)")

        decision_str = await self._read_input_with_timeout(
            prompt="> Decision: ",
            timeout=timeout,
            default="a",
        )

        decision_map = {
            "a": CheckpointDecision.APPROVE,
            "approve": CheckpointDecision.APPROVE,
            "r": CheckpointDecision.REJECT,
            "reject": CheckpointDecision.REJECT,
            "m": CheckpointDecision.MODIFY,
            "modify": CheckpointDecision.MODIFY,
            "t": CheckpointDecision.TERMINATE,
            "terminate": CheckpointDecision.TERMINATE,
        }

        decision = decision_map.get(
            decision_str.strip().lower(),
            CheckpointDecision.APPROVE,
        )

        comments = ""
        if decision in (CheckpointDecision.MODIFY, CheckpointDecision.REJECT):
            comments = await self._read_input_with_timeout(
                prompt="> Comments (optional): ",
                timeout=60,
                default="",
            )

        return CheckpointResponse(
            checkpoint_id=request.checkpoint_id,
            decision=decision.value,
            comments=comments,
            timestamp=datetime.now(timezone.utc),
        )

    async def _file_poll(self, request: CheckpointRequest) -> CheckpointResponse:
        """Write a request file and poll for a response file.

        Request:  ``{poll_dir}/{checkpoint_id}_request.json``
        Response: ``{poll_dir}/{checkpoint_id}_response.json``
        """
        if self._poll_dir is None:
            raise ValueError("poll_dir must be set for file_poll mode")

        self._poll_dir.mkdir(parents=True, exist_ok=True)
        req_path = self._poll_dir / f"{request.checkpoint_id}_request.json"
        resp_path = self._poll_dir / f"{request.checkpoint_id}_response.json"

        # Write request
        with open(req_path, "w", encoding="utf-8") as f:
            f.write(request.model_dump_json(indent=2))

        logger.info("Checkpoint request written to %s — waiting for response at %s", req_path, resp_path)

        # Poll for response
        timeout = self._config.auto_approve_timeout_seconds
        elapsed = 0
        poll_interval = 2  # seconds

        while not resp_path.exists():
            if timeout and elapsed >= timeout:
                logger.info("Auto-approving after %ds timeout", timeout)
                return CheckpointResponse(
                    checkpoint_id=request.checkpoint_id,
                    decision=CheckpointDecision.APPROVE.value,
                    comments="Auto-approved after timeout",
                    timestamp=datetime.now(timezone.utc),
                )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # Read response
        raw = resp_path.read_text(encoding="utf-8")
        return CheckpointResponse.model_validate_json(raw)

    async def _auto_approve(self, request: CheckpointRequest) -> CheckpointResponse:
        """Auto-approve after the configured timeout."""
        timeout = self._config.auto_approve_timeout_seconds or 0
        if timeout > 0:
            logger.info("Auto-approving checkpoint %s in %ds", request.checkpoint_id, timeout)
            await asyncio.sleep(timeout)
        else:
            logger.info("Auto-approving checkpoint %s immediately", request.checkpoint_id)

        return CheckpointResponse(
            checkpoint_id=request.checkpoint_id,
            decision=CheckpointDecision.APPROVE.value,
            comments="Auto-approved",
            timestamp=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _read_input_with_timeout(
        prompt: str,
        timeout: float | None,
        default: str,
    ) -> str:
        """Read a line from stdin with an optional timeout."""
        loop = asyncio.get_event_loop()

        if timeout is None:
            # No timeout — block until input
            return await loop.run_in_executor(None, lambda: input(prompt))

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, lambda: input(prompt)),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            print(f"\n(Timed out — using default: {default!r})")
            return default

    @staticmethod
    def _format_state_summary(artifacts: ResearchArtifacts) -> str:
        """Format a compact state summary for the checkpoint display."""
        lines = [
            f"  Goal: {artifacts.current_goal}",
            f"  Experiments: {len(artifacts.experiments)} completed, {len(artifacts.failed_experiments)} failed",
            f"  Observations: {len(artifacts.observations)}",
            f"  Claims: {len(artifacts.claims)} ({sum(1 for c in artifacts.claims if c.status == 'accepted')} accepted)",
            f"  Open questions: {len(artifacts.open_questions)}",
            f"  Audit findings: {len(artifacts.auditor_findings)}",
            f"  Interventions: {len(artifacts.human_interventions)}",
        ]
        return "\n".join(lines)

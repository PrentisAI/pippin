"""Human checkpoint hook (Section G).

:class:`HumanCheckpointHook` pauses the pipeline when the current event
matches one of the approval gates configured in
:class:`~aurora.config.HumanCheckpointConfig`.  It returns a
:class:`HookResult` with ``human_intervention_required=True`` and a
structured summary suitable for presenting to a human operator.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aurora.config import HumanCheckpointConfig
from aurora.hooks.base import Hook, HookContext, HookPoint, HookResult

logger = logging.getLogger(__name__)

# Mapping from HumanCheckpointConfig.require_approval_for strings to
# the hook points and context data keys that signal each event type.
_EVENT_TO_HOOK_POINTS: dict[str, set[HookPoint]] = {
    "proposal_approval": {HookPoint.PRE_EXPERIMENT},
    "goal_change": {HookPoint.MENTAL_MODEL_UPDATED},
    "termination": {HookPoint.ITERATION_COMPLETE},
    "validation_sub_campaign": {HookPoint.POST_EXPERIMENT, HookPoint.ITERATION_COMPLETE},
}


class HumanCheckpointHook(Hook):
    """Pauses the pipeline when a configured approval gate is reached.

    The hook subscribes to all :class:`HookPoint` values that could
    correspond to any approval event listed in the
    :class:`HumanCheckpointConfig`.  At each firing it checks whether
    the context data contains an ``event_type`` matching one of the
    configured approval gates.

    Parameters
    ----------
    config:
        Human checkpoint configuration specifying which events require
        human approval and optional auto-approve timeout.

    Expected ``context.data`` keys:

    * ``event_type`` — string identifying the event (e.g.
      ``"proposal_approval"``, ``"goal_change"``).
    * ``summary`` — human-readable description of the state that
      triggered the checkpoint (optional).
    * ``proposal`` — proposal data if the event is a proposal approval
      (optional).
    * ``old_goal`` / ``new_goal`` — goal change details (optional).
    """

    def __init__(
        self,
        config: HumanCheckpointConfig,
        *,
        priority: int = 0,
    ) -> None:
        self.name = "human_checkpoint"
        self.priority = priority
        self.enabled = True
        self._config = config

        # Subscribe to all hook points that any configured event maps to
        points: set[HookPoint] = set()
        for event in config.require_approval_for:
            points.update(_EVENT_TO_HOOK_POINTS.get(event, set()))
        # Always listen on ITERATION_COMPLETE as a catch-all
        points.add(HookPoint.ITERATION_COMPLETE)
        self.hook_points = points

    async def execute(self, context: HookContext) -> HookResult:
        event_type: str = context.data.get("event_type", "")

        if event_type not in self._config.require_approval_for:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message=f"Event '{event_type}' does not require human approval.",
                severity="info",
            )

        # Build a structured summary for the human operator
        summary_parts: list[str] = [
            f"Human approval required for: {event_type}",
            f"Iteration: {context.iteration}",
            f"Hook point: {context.hook_point.value}",
        ]

        if context.data.get("summary"):
            summary_parts.append(f"Context: {context.data['summary']}")

        if event_type == "proposal_approval" and context.data.get("proposal"):
            proposal = context.data["proposal"]
            if isinstance(proposal, dict):
                summary_parts.append(f"Proposal: {proposal.get('title', 'N/A')}")
                summary_parts.append(
                    f"Description: {str(proposal.get('description', ''))[:200]}"
                )
            else:
                summary_parts.append(f"Proposal: {str(proposal)[:200]}")

        if event_type == "goal_change":
            old = context.data.get("old_goal", "N/A")
            new = context.data.get("new_goal", "N/A")
            summary_parts.append(f"Old goal: {old}")
            summary_parts.append(f"New goal: {new}")

        if event_type == "termination":
            summary_parts.append(
                f"Reason: {context.data.get('termination_reason', 'N/A')}"
            )

        checkpoint_summary = "\n".join(summary_parts)

        logger.info(
            "Human checkpoint triggered: event_type=%s, iteration=%d",
            event_type, context.iteration,
        )

        result_data: dict[str, Any] = {
            "event_type": event_type,
            "checkpoint_summary": checkpoint_summary,
            "iteration": context.iteration,
            "hook_point": context.hook_point.value,
        }

        if self._config.auto_approve_timeout_seconds is not None:
            result_data["auto_approve_timeout_seconds"] = (
                self._config.auto_approve_timeout_seconds
            )

        return HookResult(
            hook_name=self.name,
            passed=False,
            message=checkpoint_summary,
            severity="warning",
            human_intervention_required=True,
            data=result_data,
        )

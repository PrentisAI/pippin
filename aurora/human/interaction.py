"""Intervention logging — H_cap vs H_run accounting (Section G, Table 2).

Every human intervention is recorded as a first-class event in the
research trajectory.  The :class:`InteractionLogger` classifies each
intervention into:

* **H_cap** (capability-building): one-time fixes that amortise to zero
  once codified into a reusable skill or execution runbook.
* **H_run** (per-run irreducible): overhead that recurs in every campaign
  (initial specification, strategic steering gates, termination decisions).

The decomposition H = H_cap + H_run is the paper's metric for measuring
how autonomous the system is becoming over successive campaigns.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from aurora.models.artifacts import ResearchArtifacts
from aurora.models.human import (
    HumanIntervention,
    InterventionCategory,
    InterventionType,
)
from aurora.state.artifacts import ArtifactStore
from aurora.state.workspace import WorkspaceManager

logger = logging.getLogger(__name__)

# Categories that are inherently per-run (H_run)
_PER_RUN_CATEGORIES = {
    InterventionCategory.INITIAL_SPECIFICATION,
    InterventionCategory.GOAL_STEERING,
    InterventionCategory.TERMINATION_DECISION,
}

# Categories that are capability-building (H_cap) by default
_CAPABILITY_CATEGORIES = {
    InterventionCategory.DOMAIN_CLARIFICATION,
    InterventionCategory.LOCAL_CORRECTION,
    InterventionCategory.METHOD_CONSTRAINT,
    InterventionCategory.AUDITOR_ESCALATION,
}


class InteractionLogger:
    """Records and classifies human interventions.

    Parameters
    ----------
    workspace : WorkspaceManager
        For writing trajectory events.
    artifact_store : ArtifactStore
        For updating the persistent artifact record.
    """

    def __init__(
        self,
        workspace: WorkspaceManager,
        artifact_store: ArtifactStore,
    ) -> None:
        self._ws = workspace
        self._store = artifact_store
        self._session_interventions: list[HumanIntervention] = []

    def log_intervention(
        self,
        artifacts: ResearchArtifacts,
        category: InterventionCategory,
        description: str,
        directive: str,
        *,
        intervention_type: InterventionType | None = None,
        state_before: str = "",
        state_after: str = "",
    ) -> ResearchArtifacts:
        """Record a human intervention and return updated artifacts.

        Parameters
        ----------
        artifacts : ResearchArtifacts
            Current artifact record.
        category : InterventionCategory
            Category from Table 2.
        description : str
            Free-text description of what happened.
        directive : str
            What the human instructed the system to do.
        intervention_type : InterventionType | None
            Explicit H_cap/H_run classification.  If ``None`` the type
            is inferred from the category.
        state_before : str
            Summary of research state before the intervention.
        state_after : str
            Summary of research state after the intervention.

        Returns
        -------
        ResearchArtifacts
            Updated artifacts with the intervention recorded.
        """
        if intervention_type is None:
            intervention_type = self._classify(category)

        intervention = HumanIntervention(
            intervention_id=f"intv_{uuid.uuid4().hex[:8]}",
            category=category,
            intervention_type=intervention_type,
            description=description,
            directive=directive,
            state_before=state_before,
            state_after=state_after,
            timestamp=datetime.now(timezone.utc),
        )

        self._session_interventions.append(intervention)
        updated = self._store.record_human_intervention(artifacts, intervention)

        logger.info(
            "Recorded %s intervention [%s]: %s",
            intervention_type.value,
            category.value,
            description[:80],
        )

        return updated

    def mark_converted_to_skill(
        self,
        intervention_id: str,
    ) -> None:
        """Mark an H_cap intervention as having been codified into a skill.

        Once a capability-building intervention is converted into a reusable
        skill, it no longer counts toward the intervention total for future
        campaigns.
        """
        for intv in self._session_interventions:
            if intv.intervention_id == intervention_id:
                intv.converted_to_skill = True
                logger.info(
                    "Intervention %s converted to skill (H_cap amortised)",
                    intervention_id,
                )
                return
        logger.warning("Intervention %s not found in session", intervention_id)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_statistics(self, artifacts: ResearchArtifacts | None = None) -> dict[str, Any]:
        """Compute H = H_cap + H_run statistics.

        Parameters
        ----------
        artifacts : ResearchArtifacts | None
            If given, statistics cover the full campaign history;
            otherwise only the current session.

        Returns
        -------
        dict
            Keys: ``total``, ``h_cap``, ``h_run``, ``h_cap_converted``,
            ``categories``, ``h_cap_ratio``, ``h_run_ratio``.
        """
        interventions = (
            artifacts.human_interventions if artifacts
            else self._session_interventions
        )

        h_cap = [i for i in interventions if i.intervention_type == InterventionType.CAPABILITY_BUILDING]
        h_run = [i for i in interventions if i.intervention_type == InterventionType.PER_RUN]
        h_cap_converted = [i for i in h_cap if i.converted_to_skill]

        total = len(interventions)
        category_counts: dict[str, int] = {}
        for intv in interventions:
            key = intv.category.value
            category_counts[key] = category_counts.get(key, 0) + 1

        return {
            "total": total,
            "h_cap": len(h_cap),
            "h_run": len(h_run),
            "h_cap_converted": len(h_cap_converted),
            "h_cap_ratio": len(h_cap) / total if total > 0 else 0.0,
            "h_run_ratio": len(h_run) / total if total > 0 else 0.0,
            "categories": category_counts,
        }

    def format_summary(self, artifacts: ResearchArtifacts | None = None) -> str:
        """Format a human-readable intervention summary.

        Returns a Markdown table showing the H = H_cap + H_run
        decomposition.
        """
        stats = self.get_statistics(artifacts)
        lines = [
            "## Human Intervention Summary (H = H_cap + H_run)",
            "",
            f"| Metric | Count |",
            f"|--------|-------|",
            f"| Total interventions (H) | {stats['total']} |",
            f"| Capability-building (H_cap) | {stats['h_cap']} |",
            f"| Per-run irreducible (H_run) | {stats['h_run']} |",
            f"| H_cap converted to skills | {stats['h_cap_converted']} |",
            "",
            "### By Category",
            "",
            "| Category | Count |",
            "|----------|-------|",
        ]
        for cat, count in sorted(stats["categories"].items()):
            lines.append(f"| {cat} | {count} |")

        if stats["total"] > 0:
            lines.extend([
                "",
                f"H_cap ratio: {stats['h_cap_ratio']:.1%}",
                f"H_run ratio: {stats['h_run_ratio']:.1%}",
            ])

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _classify(category: InterventionCategory) -> InterventionType:
        """Auto-classify a category as H_cap or H_run."""
        if category in _PER_RUN_CATEGORIES:
            return InterventionType.PER_RUN
        return InterventionType.CAPABILITY_BUILDING

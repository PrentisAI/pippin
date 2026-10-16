"""Persistent research artifact store (Section E.1).

:class:`ArtifactStore` is the single gateway for reading and mutating the
``ResearchArtifacts`` record.  Every mutation:

1. Operates on a **deep copy** of the artifacts (immutable-style).
2. Updates the ``updated_at`` timestamp.
3. Appends a typed event to the trajectory JSONL log.
4. Atomically writes the updated record to disk.
5. Returns the new ``ResearchArtifacts`` instance.

This ensures that a crash at any point leaves the on-disk state either
fully before or fully after the change — never in between.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from aurora.models.artifacts import (
    ExperimentRecord,
    Observation,
    ResearchArtifacts,
)
from aurora.models.audit import AuditRecord
from aurora.models.claims import ScientificClaim
from aurora.models.human import HumanIntervention
from aurora.state.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class ArtifactStore:
    """Manages persistent research artifacts with append-only semantics."""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self._ws = workspace

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def load(self) -> ResearchArtifacts:
        """Load artifacts from the workspace.

        Returns a default-initialised instance (with empty collections) if
        the file does not yet exist.
        """
        path = self._ws.artifacts_path
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            return ResearchArtifacts.model_validate_json(raw)
        logger.info("No artifacts file found — returning empty artifacts")
        now = _now()
        return ResearchArtifacts(
            initial_objective="",
            current_goal="",
            created_at=now,
            updated_at=now,
        )

    def save(self, artifacts: ResearchArtifacts) -> None:
        """Atomically persist the artifacts record."""
        self._ws.atomic_write_json(self._ws.artifacts_path, artifacts)

    def initialize(
        self,
        objective: str,
        goal: str | None = None,
    ) -> ResearchArtifacts:
        """Create a fresh artifacts record for a new campaign.

        Parameters
        ----------
        objective:
            The initial research objective (G₀).
        goal:
            The initial active goal (defaults to *objective*).
        """
        now = _now()
        artifacts = ResearchArtifacts(
            initial_objective=objective,
            current_goal=goal or objective,
            created_at=now,
            updated_at=now,
        )
        self.save(artifacts)
        self._emit("artifacts_initialized", {"objective": objective})
        return artifacts

    # ------------------------------------------------------------------
    # Experiments
    # ------------------------------------------------------------------

    def record_experiment(
        self,
        artifacts: ResearchArtifacts,
        record: ExperimentRecord,
    ) -> ResearchArtifacts:
        """Add or update an experiment record.

        If a record with the same ``job_id`` already exists it is replaced
        in-place; otherwise the record is appended.
        """
        updated = artifacts.model_copy(deep=True)
        idx = self._find_experiment_index(updated.experiments, record.job_id)
        if idx is not None:
            updated.experiments[idx] = record
        else:
            updated.experiments.append(record)
        return self._commit(updated, "experiment_recorded", {
            "job_id": record.job_id,
            "status": record.status,
        })

    def record_failure(
        self,
        artifacts: ResearchArtifacts,
        record: ExperimentRecord,
        diagnosis: str,
    ) -> ResearchArtifacts:
        """Record a failed experiment with diagnosis.

        The record is moved from ``experiments`` to ``failed_experiments``
        with an updated status and failure diagnosis.
        """
        updated = artifacts.model_copy(deep=True)
        failed = record.model_copy(
            update={"status": "failed", "failure_diagnosis": diagnosis},
        )

        # Remove from active experiments if present
        idx = self._find_experiment_index(updated.experiments, record.job_id)
        if idx is not None:
            updated.experiments.pop(idx)

        # Add to failed experiments (replace if already there)
        fidx = self._find_experiment_index(updated.failed_experiments, record.job_id)
        if fidx is not None:
            updated.failed_experiments[fidx] = failed
        else:
            updated.failed_experiments.append(failed)

        return self._commit(updated, "experiment_failed", {
            "job_id": record.job_id,
            "diagnosis": diagnosis,
        })

    # ------------------------------------------------------------------
    # Observations / evidence
    # ------------------------------------------------------------------

    def record_observation(
        self,
        artifacts: ResearchArtifacts,
        obs: Observation,
    ) -> ResearchArtifacts:
        """Append a new observation from an experiment."""
        updated = artifacts.model_copy(deep=True)
        updated.observations.append(obs)

        # Auto-populate experiment → observation map
        updated.experiment_observation_map.setdefault(obs.source_job_id, [])
        if obs.observation_id not in updated.experiment_observation_map[obs.source_job_id]:
            updated.experiment_observation_map[obs.source_job_id].append(obs.observation_id)

        return self._commit(updated, "observation_recorded", {
            "observation_id": obs.observation_id,
            "source_job_id": obs.source_job_id,
        })

    def validate_evidence(
        self,
        artifacts: ResearchArtifacts,
        observation_id: str,
    ) -> ResearchArtifacts:
        """Mark an observation as independently validated evidence."""
        updated = artifacts.model_copy(deep=True)

        # Set validated flag on the observation
        for obs in updated.observations:
            if obs.observation_id == observation_id:
                obs.validated = True
                break
        else:
            raise ValueError(f"Observation {observation_id!r} not found")

        if observation_id not in updated.validated_evidence:
            updated.validated_evidence.append(observation_id)

        return self._commit(updated, "evidence_validated", {
            "observation_id": observation_id,
        })

    # ------------------------------------------------------------------
    # Claims
    # ------------------------------------------------------------------

    def add_claim(
        self,
        artifacts: ResearchArtifacts,
        claim: ScientificClaim,
    ) -> ResearchArtifacts:
        """Add a new scientific claim (tentative by default)."""
        updated = artifacts.model_copy(deep=True)
        updated.claims.append(claim)

        # Auto-create provenance links from supporting evidence
        for evidence_id in claim.supporting_evidence:
            updated.provenance_links.setdefault(claim.claim_id, [])
            if evidence_id not in updated.provenance_links[claim.claim_id]:
                updated.provenance_links[claim.claim_id].append(evidence_id)

        return self._commit(updated, "claim_added", {
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "status": claim.status,
        })

    def promote_claim(
        self,
        artifacts: ResearchArtifacts,
        claim_id: str,
        status: Literal["accepted", "rejected"],
        reason: str = "",
    ) -> ResearchArtifacts:
        """Promote or reject a scientific claim."""
        updated = artifacts.model_copy(deep=True)
        now = _now()

        for claim in updated.claims:
            if claim.claim_id == claim_id:
                claim.status = status
                if status == "accepted":
                    claim.promoted_at = now
                    # Ensure provenance links exist for accepted claims
                    for evidence_id in claim.supporting_evidence:
                        updated.provenance_links.setdefault(claim.claim_id, [])
                        if evidence_id not in updated.provenance_links[claim.claim_id]:
                            updated.provenance_links[claim.claim_id].append(evidence_id)
                elif status == "rejected":
                    claim.rejected_at = now
                    claim.rejection_reason = reason
                break
        else:
            raise ValueError(f"Claim {claim_id!r} not found")

        return self._commit(updated, "claim_promoted", {
            "claim_id": claim_id,
            "new_status": status,
            "reason": reason,
        })

    # ------------------------------------------------------------------
    # Goals and plans
    # ------------------------------------------------------------------

    def update_goal(
        self,
        artifacts: ResearchArtifacts,
        new_goal: str,
        reason: str,
    ) -> ResearchArtifacts:
        """Update the active research goal (Gₜ)."""
        updated = artifacts.model_copy(deep=True)
        old_goal = updated.current_goal
        updated.current_goal = new_goal
        return self._commit(updated, "goal_updated", {
            "old_goal": old_goal,
            "new_goal": new_goal,
            "reason": reason,
        })

    def update_plan(
        self,
        artifacts: ResearchArtifacts,
        plan_summary: str,
    ) -> ResearchArtifacts:
        """Update the current plan summary."""
        updated = artifacts.model_copy(deep=True)
        updated.current_plan_summary = plan_summary
        return self._commit(updated, "plan_updated", {
            "plan_summary": plan_summary[:200],
        })

    def add_open_question(
        self,
        artifacts: ResearchArtifacts,
        question: str,
    ) -> ResearchArtifacts:
        """Record an open scientific question."""
        updated = artifacts.model_copy(deep=True)
        updated.open_questions.append(question)
        return self._commit(updated, "question_added", {"question": question})

    def resolve_question(
        self,
        artifacts: ResearchArtifacts,
        question: str,
    ) -> ResearchArtifacts:
        """Remove a question that has been resolved."""
        updated = artifacts.model_copy(deep=True)
        try:
            updated.open_questions.remove(question)
        except ValueError:
            logger.warning("Question not found, cannot resolve: %s", question[:80])
            return artifacts
        return self._commit(updated, "question_resolved", {"question": question})

    # ------------------------------------------------------------------
    # Governance
    # ------------------------------------------------------------------

    def record_human_intervention(
        self,
        artifacts: ResearchArtifacts,
        intervention: HumanIntervention,
    ) -> ResearchArtifacts:
        """Log a human intervention event."""
        updated = artifacts.model_copy(deep=True)
        updated.human_interventions.append(intervention)
        return self._commit(updated, "human_intervention", {
            "intervention_id": intervention.intervention_id,
            "category": intervention.category.value,
            "type": intervention.intervention_type.value,
        })

    def record_audit_finding(
        self,
        artifacts: ResearchArtifacts,
        record: AuditRecord,
    ) -> ResearchArtifacts:
        """Record an auditor finding."""
        updated = artifacts.model_copy(deep=True)
        updated.auditor_findings.append(record)
        return self._commit(updated, "audit_recorded", {
            "audit_id": record.audit_id,
            "trigger": record.trigger.value,
            "outcome": record.outcome.value,
            "severity": record.severity,
        })

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def add_provenance_link(
        self,
        artifacts: ResearchArtifacts,
        claim_id: str,
        evidence_id: str,
    ) -> ResearchArtifacts:
        """Link a claim to a piece of supporting evidence."""
        updated = artifacts.model_copy(deep=True)
        updated.provenance_links.setdefault(claim_id, [])
        if evidence_id not in updated.provenance_links[claim_id]:
            updated.provenance_links[claim_id].append(evidence_id)
        return self._commit(updated, "provenance_linked", {
            "claim_id": claim_id,
            "evidence_id": evidence_id,
        })

    # ------------------------------------------------------------------
    # Milestones
    # ------------------------------------------------------------------

    def update_milestone(
        self,
        artifacts: ResearchArtifacts,
        milestone_id: str,
        status: str,
    ) -> ResearchArtifacts:
        """Update the status of a research milestone."""
        updated = artifacts.model_copy(deep=True)
        updated.milestone_status[milestone_id] = status
        return self._commit(updated, "milestone_updated", {
            "milestone_id": milestone_id,
            "status": status,
        })

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _commit(
        self,
        artifacts: ResearchArtifacts,
        event_type: str,
        event_data: dict,
    ) -> ResearchArtifacts:
        """Timestamp, persist, and log an artifacts mutation."""
        artifacts.updated_at = _now()
        self.save(artifacts)
        self._emit(event_type, event_data)
        return artifacts

    def _emit(self, event_type: str, data: dict) -> None:
        """Write a structured event to the trajectory log."""
        self._ws.append_event({
            "event": event_type,
            "source": "artifact_store",
            **data,
        })

    @staticmethod
    def _find_experiment_index(
        records: list[ExperimentRecord],
        job_id: str,
    ) -> int | None:
        for i, rec in enumerate(records):
            if rec.job_id == job_id:
                return i
        return None


class ProvenanceTracker:
    """Tracks and validates claim-evidence provenance chains."""

    def __init__(self, artifacts: ResearchArtifacts) -> None:
        self._artifacts = artifacts

    def get_claim_evidence(self, claim_id: str) -> list[str]:
        """Forward lookup: claim → supporting evidence IDs."""
        return list(self._artifacts.provenance_links.get(claim_id, []))

    def get_evidence_claims(self, evidence_id: str) -> list[str]:
        """Reverse lookup: evidence → claim IDs that cite it."""
        return [
            cid for cid, eids in self._artifacts.provenance_links.items()
            if evidence_id in eids
        ]

    def get_experiment_observations(self, job_id: str) -> list[str]:
        """Get observation IDs produced by an experiment."""
        return list(self._artifacts.experiment_observation_map.get(job_id, []))

    def validate_all_claims(self) -> list[str]:
        """Return claim IDs that have no supporting evidence linked."""
        ungrounded = []
        for claim in self._artifacts.claims:
            if claim.status in ("accepted", "tentative"):
                evidence = self.get_claim_evidence(claim.claim_id)
                if not evidence:
                    ungrounded.append(claim.claim_id)
        return ungrounded

    def generate_provenance_report(self) -> str:
        """Generate a human-readable traceability table."""
        lines = ["# Provenance Report", ""]
        lines.append("## Claim → Evidence Traceability")
        lines.append("")
        for claim in self._artifacts.claims:
            evidence = self.get_claim_evidence(claim.claim_id)
            status_marker = "✓" if evidence else "✗"
            lines.append(f"- [{status_marker}] **{claim.claim_id}** [{claim.status}]: {claim.statement[:100]}")
            if evidence:
                for eid in evidence:
                    lines.append(f"  - Evidence: {eid}")
            else:
                lines.append("  - ⚠ NO SUPPORTING EVIDENCE LINKED")

        lines.append("")
        lines.append("## Experiment → Observation Map")
        lines.append("")
        for job_id, obs_ids in self._artifacts.experiment_observation_map.items():
            lines.append(f"- **{job_id}**: {len(obs_ids)} observation(s)")
            for oid in obs_ids:
                lines.append(f"  - {oid}")

        ungrounded = self.validate_all_claims()
        if ungrounded:
            lines.append("")
            lines.append(f"## ⚠ {len(ungrounded)} Ungrounded Claim(s)")
            for cid in ungrounded:
                lines.append(f"- {cid}")

        return "\n".join(lines)

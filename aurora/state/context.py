"""On-demand context reconstruction (Section E.2).

Instead of replaying the full research trajectory into every model context
window, :class:`ContextReconstructor` selectively assembles only the
information relevant to the current task — within a configurable token
budget.

Priority order (highest → lowest):

1. Current goal and active plan
2. Mental model summary
3. Unresolved contradictions and open questions
4. Recent experiments (results + metrics)
5. Historical failures relevant to proposed action
6. Current scientific claims with evidential status
7. Active auditor findings
8. Artifact references (file paths for large outputs)

Each section is appended only while the cumulative token estimate stays
within the budget.
"""

from __future__ import annotations

import logging
from textwrap import dedent

from aurora.models.artifacts import (
    ExperimentRecord,
    ResearchArtifacts,
)
from aurora.models.mental_model import ScientificMentalModel
from aurora.state.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class ContextReconstructor:
    """Builds a task-relevant context string within a token budget."""

    def __init__(
        self,
        workspace: WorkspaceManager,
        max_tokens: int = 50_000,
    ) -> None:
        self._ws = workspace
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reconstruct(
        self,
        task_description: str,
        artifacts: ResearchArtifacts,
        mental_model: ScientificMentalModel | None = None,
        relevant_job_ids: list[str] | None = None,
    ) -> str:
        """Build a context string for a specific task.

        Parameters
        ----------
        task_description:
            One-sentence description of what the agent is about to do.
        artifacts:
            Current persistent research artifacts.
        mental_model:
            Current scientific mental model (if available).
        relevant_job_ids:
            Specific experiment IDs whose details should be included.

        Returns
        -------
        str
            A Markdown-formatted context string fitting within the token
            budget.
        """
        sections: list[str] = []
        budget = self.max_tokens

        # Header: always included
        header = self._format_header(task_description, artifacts)
        budget -= self._estimate_tokens(header)
        sections.append(header)

        # Priority 1: Goal and plan (always included)
        goal_section = self._format_goal_section(artifacts)
        cost = self._estimate_tokens(goal_section)
        if cost <= budget:
            sections.append(goal_section)
            budget -= cost

        # Priority 2: Mental model (if available)
        if mental_model is not None:
            mm_section = self._format_mental_model_section(mental_model)
            cost = self._estimate_tokens(mm_section)
            if cost <= budget:
                sections.append(mm_section)
                budget -= cost

        # Priority 3: Contradictions and open questions
        contra_section = self._format_contradictions_section(artifacts)
        cost = self._estimate_tokens(contra_section)
        if cost <= budget and contra_section:
            sections.append(contra_section)
            budget -= cost

        # Priority 4: Recent / relevant experiments
        exp_section = self._format_experiments_section(
            artifacts,
            relevant_job_ids=relevant_job_ids,
            max_recent=10,
        )
        cost = self._estimate_tokens(exp_section)
        if cost <= budget and exp_section:
            sections.append(exp_section)
            budget -= cost

        # Priority 5: Historical failures
        fail_section = self._format_failures_section(artifacts)
        cost = self._estimate_tokens(fail_section)
        if cost <= budget and fail_section:
            sections.append(fail_section)
            budget -= cost

        # Priority 6: Claims
        claims_section = self._format_claims_section(artifacts)
        cost = self._estimate_tokens(claims_section)
        if cost <= budget and claims_section:
            sections.append(claims_section)
            budget -= cost

        # Priority 7: Auditor findings
        audit_section = self._format_audit_findings_section(artifacts)
        cost = self._estimate_tokens(audit_section)
        if cost <= budget and audit_section:
            sections.append(audit_section)
            budget -= cost

        # Priority 8: Artifact references
        refs_section = self._format_artifact_refs_section(artifacts)
        cost = self._estimate_tokens(refs_section)
        if cost <= budget and refs_section:
            sections.append(refs_section)
            budget -= cost

        context = "\n\n---\n\n".join(sections)
        logger.debug(
            "Context reconstructed: ~%d tokens (%d sections), budget remaining ~%d",
            self.max_tokens - budget,
            len(sections),
            budget,
        )
        return context

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token count — 1 token ≈ 4 characters."""
        return len(text) // 4

    # ------------------------------------------------------------------
    # Section formatters
    # ------------------------------------------------------------------

    @staticmethod
    def _format_header(task_description: str, artifacts: ResearchArtifacts) -> str:
        n_exp = len(artifacts.experiments)
        n_obs = len(artifacts.observations)
        n_claims = len(artifacts.claims)
        n_failures = len(artifacts.failed_experiments)
        return dedent(f"""\
            # Research Context

            **Current task:** {task_description}

            **Campaign stats:** {n_exp} experiments, {n_obs} observations, \
            {n_claims} claims, {n_failures} failures
        """).strip()

    @staticmethod
    def _format_goal_section(artifacts: ResearchArtifacts) -> str:
        lines = [
            "## Goal and Plan",
            f"**Initial objective (G₀):** {artifacts.initial_objective}",
            f"**Current goal (Gₜ):** {artifacts.current_goal}",
        ]
        if artifacts.current_plan_summary:
            lines.append(f"**Active plan:** {artifacts.current_plan_summary}")
        if artifacts.milestone_status:
            lines.append("**Milestones:**")
            for mid, status in artifacts.milestone_status.items():
                lines.append(f"  - {mid}: {status}")
        return "\n".join(lines)

    @staticmethod
    def _format_mental_model_section(model: ScientificMentalModel) -> str:
        lines = ["## Scientific Mental Model"]

        if model.active_hypotheses:
            lines.append("**Active hypotheses:**")
            for h in model.active_hypotheses:
                lines.append(f"  - [{h.hypothesis_id}] {h.statement} "
                             f"(confidence={h.confidence:.2f})")
        if model.competing_hypotheses:
            lines.append("**Competing hypotheses:**")
            for h in model.competing_hypotheses:
                lines.append(f"  - [{h.hypothesis_id}] {h.statement} "
                             f"(confidence={h.confidence:.2f})")
        if model.inferred_mechanisms:
            lines.append("**Inferred mechanisms:**")
            for m in model.inferred_mechanisms:
                lines.append(f"  - {m}")
        if model.causal_relationships:
            lines.append("**Causal relationships:**")
            for cr in model.causal_relationships:
                lines.append(f"  - {cr.cause} → {cr.effect}: {cr.mechanism} "
                             f"(confidence={cr.confidence:.2f})")
        if model.current_rationale:
            lines.append(f"**Rationale:** {model.current_rationale}")
        if model.unresolved_ambiguity:
            lines.append("**Unresolved ambiguities:**")
            for a in model.unresolved_ambiguity:
                lines.append(f"  - {a}")

        return "\n".join(lines)

    @staticmethod
    def _format_contradictions_section(artifacts: ResearchArtifacts) -> str:
        items: list[str] = []

        # Claims contradicted by evidence
        for claim in artifacts.claims:
            if claim.status == "rejected" and claim.rejection_reason:
                items.append(f"[REJECTED CLAIM] {claim.statement}: {claim.rejection_reason}")

        # Open questions flag unresolved tensions
        for q in artifacts.open_questions:
            items.append(f"[OPEN QUESTION] {q}")

        if not items:
            return ""

        lines = ["## Contradictions & Open Questions"]
        lines.extend(f"- {item}" for item in items)
        return "\n".join(lines)

    @staticmethod
    def _format_experiments_section(
        artifacts: ResearchArtifacts,
        relevant_job_ids: list[str] | None = None,
        max_recent: int = 10,
    ) -> str:
        # Collect relevant experiments
        experiments: list[ExperimentRecord] = []
        if relevant_job_ids:
            by_id = {e.job_id: e for e in artifacts.experiments}
            for jid in relevant_job_ids:
                if jid in by_id:
                    experiments.append(by_id[jid])
        # Also include most recent
        recent = artifacts.experiments[-max_recent:]
        seen = {e.job_id for e in experiments}
        for e in recent:
            if e.job_id not in seen:
                experiments.append(e)

        if not experiments:
            return ""

        lines = ["## Recent Experiments"]
        for exp in experiments:
            status_icon = {
                "completed": "✓",
                "active": "⟳",
                "proposed": "○",
                "failed": "✗",
            }.get(exp.status, "?")
            lines.append(
                f"- {status_icon} **{exp.job_id}** ({exp.skill_used}) — {exp.status}"
            )
            if exp.result_path:
                lines.append(f"  Result: `{exp.result_path}`")
        return "\n".join(lines)

    @staticmethod
    def _format_failures_section(artifacts: ResearchArtifacts) -> str:
        if not artifacts.failed_experiments:
            return ""
        lines = ["## Historical Failures"]
        for exp in artifacts.failed_experiments[-10:]:
            lines.append(f"- **{exp.job_id}** ({exp.skill_used}): "
                         f"{exp.failure_diagnosis or 'no diagnosis'}")
        return "\n".join(lines)

    @staticmethod
    def _format_claims_section(artifacts: ResearchArtifacts) -> str:
        if not artifacts.claims:
            return ""
        lines = ["## Scientific Claims"]
        for c in artifacts.claims:
            icon = {"tentative": "?", "accepted": "✓", "rejected": "✗"}.get(c.status, "?")
            evidence_count = len(c.supporting_evidence)
            lines.append(
                f"- {icon} **[{c.status.upper()}]** {c.statement} "
                f"(confidence={c.confidence:.2f}, evidence={evidence_count} items)"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_audit_findings_section(artifacts: ResearchArtifacts) -> str:
        findings = [f for f in artifacts.auditor_findings if f.severity != "info"]
        if not findings:
            return ""
        lines = ["## Active Auditor Findings"]
        for af in findings[-5:]:
            lines.append(
                f"- [{af.severity.upper()}] {af.finding} → **{af.outcome.value}**"
            )
            if af.corrective_action:
                lines.append(f"  Action required: {af.corrective_action}")
        return "\n".join(lines)

    @staticmethod
    def _format_artifact_refs_section(artifacts: ResearchArtifacts) -> str:
        refs: list[str] = []
        for exp in artifacts.experiments:
            if exp.result_path and exp.status == "completed":
                refs.append(f"- `{exp.result_path}` ({exp.job_id})")
        if not refs:
            return ""
        lines = ["## Artifact References (file paths)"]
        lines.extend(refs[-20:])  # cap at 20
        if len(refs) > 20:
            lines.append(f"  ... and {len(refs) - 20} more")
        return "\n".join(lines)

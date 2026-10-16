"""JIT audit hooks implementing the eight trigger policies from Section F.3.

Each hook extends :class:`Hook` and uses the :class:`LLMRouter` at
:data:`PipelineStage.AUDITOR_TRIAGE` for lightweight LLM-based assessment.
Hooks that detect an anomaly return a failing :class:`HookResult` with
structured data suitable for constructing an :class:`AuditRecord`.

Hook inventory
--------------
1. :class:`EnergyAnomalyHook` — |ΔE| > 0.5 eV threshold
2. :class:`ConvergenceHook` — SCF / ionic convergence metrics
3. :class:`ConsistencyHook` — five-category consistency check
4. :class:`PeriodicAuditHook` — comprehensive audit every *N* iterations
5. :class:`GoalDriftHook` — current goal vs. original objective
6. :class:`ResourceExhaustionHook` — resource usage > 80 %
7. :class:`RepeatedFailureHook` — *N* consecutive failures
8. :class:`NovelResultHook` — unexpectedly novel result detection
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from aurora.config import PipelineStage
from aurora.hooks.base import Hook, HookContext, HookPoint, HookResult
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter
from aurora.models.audit import AuditOutcome, ConsistencyCategory

logger = logging.getLogger(__name__)

_STAGE = PipelineStage.AUDITOR_TRIAGE


# ---------------------------------------------------------------------------
# Structured response models used only within this module
# ---------------------------------------------------------------------------

class _EnergyTriageResponse(BaseModel):
    """LLM triage response for an energy anomaly."""

    is_anomaly: bool = Field(description="Whether the deviation is a genuine anomaly.")
    explanation: str = Field(description="Brief explanation of the assessment.")
    recommended_outcome: str = Field(
        default="continue",
        description="One of: continue, validate, replan, escalate.",
    )


class _ConsistencyCheckResponse(BaseModel):
    """LLM response for a multi-category consistency check."""

    issues_found: bool = Field(default=False)
    categories_with_issues: list[str] = Field(default_factory=list)
    explanation: str = Field(default="")
    recommended_outcome: str = Field(default="continue")


class _GoalDriftResponse(BaseModel):
    """LLM assessment of goal drift."""

    drift_detected: bool = Field(default=False)
    drift_severity: str = Field(default="none", description="none | mild | severe")
    explanation: str = Field(default="")
    recommended_outcome: str = Field(default="continue")


class _PeriodicAuditResponse(BaseModel):
    """LLM comprehensive periodic audit result."""

    issues_found: bool = Field(default=False)
    findings: list[str] = Field(default_factory=list)
    recommended_outcome: str = Field(default="continue")
    explanation: str = Field(default="")


class _NoveltyAssessmentResponse(BaseModel):
    """LLM assessment of result novelty."""

    is_novel: bool = Field(default=False)
    novelty_description: str = Field(default="")
    recommended_action: str = Field(
        default="continue",
        description="continue | validate | escalate",
    )


# ===================================================================
# 1. EnergyAnomalyHook
# ===================================================================

class EnergyAnomalyHook(Hook):
    """Detects energy deviations exceeding |ΔE| > threshold (default 0.5 eV).

    Fires at :data:`HookPoint.ENERGY_COMPUTED`.  When the threshold is
    breached, an LLM triage call determines whether the anomaly is
    genuine and recommends a corrective action.

    Expected ``context.data`` keys:

    * ``delta_e`` — energy difference in eV.
    * ``reference_energy`` — reference energy value (optional, for context).
    * ``computed_energy`` — newly computed energy (optional).
    """

    def __init__(
        self,
        router: LLMRouter,
        *,
        threshold_ev: float = 0.5,
        priority: int = 10,
    ) -> None:
        self.name = "energy_anomaly"
        self.hook_points = {HookPoint.ENERGY_COMPUTED}
        self.priority = priority
        self.enabled = True
        self._router = router
        self._threshold_ev = threshold_ev

    async def execute(self, context: HookContext) -> HookResult:
        delta_e: float = context.data.get("delta_e", 0.0)

        if abs(delta_e) <= self._threshold_ev:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message=f"|ΔE| = {abs(delta_e):.4f} eV within threshold ({self._threshold_ev} eV).",
                severity="info",
            )

        # Threshold breached — ask LLM for triage
        logger.warning(
            "Energy anomaly detected: |ΔE| = %.4f eV (threshold %.4f eV)",
            abs(delta_e), self._threshold_ev,
        )

        prompt = (
            f"An energy computation produced |ΔE| = {abs(delta_e):.4f} eV, "
            f"exceeding the anomaly threshold of {self._threshold_ev} eV.\n"
            f"Context data: {json.dumps(context.data, default=str)}\n\n"
            "Assess whether this is a genuine anomaly or an expected large "
            "deviation (e.g. phase transition, surface reconstruction).  "
            "Recommend one of: continue, validate, replan, escalate."
        )

        triage = await self._router.generate_structured(
            _STAGE,
            [Message.user(prompt)],
            _EnergyTriageResponse,
            temperature=0.2,
        )

        severity = "critical" if triage.is_anomaly else "warning"
        return HookResult(
            hook_name=self.name,
            passed=not triage.is_anomaly,
            message=(
                f"|ΔE| = {abs(delta_e):.4f} eV — "
                f"{'anomaly confirmed' if triage.is_anomaly else 'large but expected'}. "
                f"{triage.explanation}"
            ),
            severity=severity,
            data={
                "delta_e": delta_e,
                "threshold_ev": self._threshold_ev,
                "is_anomaly": triage.is_anomaly,
                "recommended_outcome": triage.recommended_outcome,
                "explanation": triage.explanation,
            },
        )


# ===================================================================
# 2. ConvergenceHook
# ===================================================================

class ConvergenceHook(Hook):
    """Checks SCF and ionic convergence metrics after an experiment.

    Fires at :data:`HookPoint.POST_EXPERIMENT`.

    Expected ``context.data`` keys:

    * ``converged`` — boolean overall convergence flag.
    * ``scf_converged`` — SCF convergence (optional).
    * ``ionic_converged`` — ionic relaxation convergence (optional).
    * ``convergence_metrics`` — dict of metric name → value (optional).
    """

    def __init__(
        self,
        router: LLMRouter,
        *,
        priority: int = 20,
    ) -> None:
        self.name = "convergence_check"
        self.hook_points = {HookPoint.POST_EXPERIMENT}
        self.priority = priority
        self.enabled = True
        self._router = router

    async def execute(self, context: HookContext) -> HookResult:
        converged: bool = context.data.get("converged", True)
        scf_converged: bool = context.data.get("scf_converged", True)
        ionic_converged: bool = context.data.get("ionic_converged", True)
        metrics: dict[str, Any] = context.data.get("convergence_metrics", {})

        issues: list[str] = []
        if not converged:
            issues.append("Overall convergence flag is False")
        if not scf_converged:
            issues.append("SCF did not converge")
        if not ionic_converged:
            issues.append("Ionic relaxation did not converge")

        if not issues:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message="All convergence checks passed.",
                severity="info",
                data={"convergence_metrics": metrics},
            )

        # Use LLM to assess severity of convergence failure
        prompt = (
            f"A DFT/simulation experiment reported convergence issues:\n"
            f"- Issues: {'; '.join(issues)}\n"
            f"- Metrics: {json.dumps(metrics, default=str)}\n\n"
            "Assess the severity (info/warning/critical) and recommend "
            "an action: continue, validate, or replan."
        )

        response = await self._router.generate(
            _STAGE,
            [Message.user(prompt)],
            temperature=0.2,
            max_tokens=512,
        )

        # Parse severity from response text (simple heuristic)
        text_lower = response.content.lower()
        if "critical" in text_lower:
            severity = "critical"
        elif "warning" in text_lower:
            severity = "warning"
        else:
            severity = "warning"

        return HookResult(
            hook_name=self.name,
            passed=False,
            message=f"Convergence issues detected: {'; '.join(issues)}. {response.content[:200]}",
            severity=severity,
            data={
                "issues": issues,
                "convergence_metrics": metrics,
                "llm_assessment": response.content,
            },
        )


# ===================================================================
# 3. ConsistencyHook
# ===================================================================

class ConsistencyHook(Hook):
    """Five-category consistency check at iteration boundaries.

    Fires at :data:`HookPoint.ITERATION_COMPLETE`.  Uses the LLM to
    evaluate consistency across the five categories defined in
    :class:`ConsistencyCategory`: GOAL, PLAN, EXECUTION, EVIDENCE,
    EPISTEMIC.

    Expected ``context.data`` keys:

    * ``current_goal`` — the active research goal.
    * ``current_plan`` — summary of the current experiment plan.
    * ``recent_results`` — list of recent result summaries.
    * ``claims`` — list of current scientific claims.
    * ``observations`` — list of recent observations.
    """

    def __init__(
        self,
        router: LLMRouter,
        *,
        priority: int = 30,
    ) -> None:
        self.name = "consistency_check"
        self.hook_points = {HookPoint.ITERATION_COMPLETE}
        self.priority = priority
        self.enabled = True
        self._router = router

    async def execute(self, context: HookContext) -> HookResult:
        categories = [c.value for c in ConsistencyCategory]
        prompt = (
            "Evaluate the internal consistency of the current research state "
            f"across these five categories: {', '.join(categories)}.\n\n"
            f"Current goal: {context.data.get('current_goal', 'N/A')}\n"
            f"Current plan: {context.data.get('current_plan', 'N/A')}\n"
            f"Recent results: {json.dumps(context.data.get('recent_results', []), default=str)}\n"
            f"Claims: {json.dumps(context.data.get('claims', []), default=str)}\n"
            f"Observations: {json.dumps(context.data.get('observations', []), default=str)}\n\n"
            "Identify any inconsistencies.  For each issue state which "
            "category it falls under.  Recommend: continue, validate, "
            "replan, reframe, reject, or escalate."
        )

        result = await self._router.generate_structured(
            _STAGE,
            [Message.user(prompt)],
            _ConsistencyCheckResponse,
            temperature=0.2,
        )

        if not result.issues_found:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message="No consistency issues detected across all five categories.",
                severity="info",
                data={"categories_checked": categories},
            )

        severity = "critical" if result.recommended_outcome in ("reframe", "reject", "escalate") else "warning"
        return HookResult(
            hook_name=self.name,
            passed=False,
            message=(
                f"Consistency issues in: {', '.join(result.categories_with_issues)}. "
                f"{result.explanation}"
            ),
            severity=severity,
            data={
                "categories_checked": categories,
                "categories_with_issues": result.categories_with_issues,
                "recommended_outcome": result.recommended_outcome,
                "explanation": result.explanation,
            },
        )


# ===================================================================
# 4. PeriodicAuditHook
# ===================================================================

class PeriodicAuditHook(Hook):
    """Comprehensive audit triggered every *N* iterations.

    Fires at :data:`HookPoint.PERIODIC`.  The hook tracks the iteration
    count internally and only performs the (relatively expensive) LLM
    call when ``context.iteration`` is a multiple of *audit_interval*.

    Expected ``context.data`` keys:

    * ``state_summary`` — high-level campaign state summary.
    * ``recent_events`` — list of recent trajectory events.
    """

    def __init__(
        self,
        router: LLMRouter,
        *,
        audit_interval: int = 5,
        priority: int = 50,
    ) -> None:
        self.name = "periodic_audit"
        self.hook_points = {HookPoint.PERIODIC}
        self.priority = priority
        self.enabled = True
        self._router = router
        self._audit_interval = audit_interval

    async def execute(self, context: HookContext) -> HookResult:
        # Only fire every N iterations
        if context.iteration % self._audit_interval != 0:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message=(
                    f"Skipped — iteration {context.iteration} is not a "
                    f"multiple of audit interval {self._audit_interval}."
                ),
                severity="info",
            )

        logger.info(
            "Periodic audit triggered at iteration %d (interval=%d)",
            context.iteration, self._audit_interval,
        )

        prompt = (
            f"Perform a comprehensive audit at iteration {context.iteration}.\n\n"
            f"State summary: {context.data.get('state_summary', 'N/A')}\n"
            f"Recent events: {json.dumps(context.data.get('recent_events', []), default=str)}\n\n"
            "Check for: goal drift, plan staleness, evidence gaps, "
            "resource waste, epistemic overconfidence, and any other "
            "anomalies.  List findings and recommend: continue, validate, "
            "replan, reframe, reject, recover, or escalate."
        )

        result = await self._router.generate_structured(
            _STAGE,
            [Message.user(prompt)],
            _PeriodicAuditResponse,
            temperature=0.2,
        )

        if not result.issues_found:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message=f"Periodic audit at iteration {context.iteration}: no issues.",
                severity="info",
                data={"iteration": context.iteration},
            )

        severity = "critical" if result.recommended_outcome in ("reframe", "reject", "escalate") else "warning"
        return HookResult(
            hook_name=self.name,
            passed=False,
            message=(
                f"Periodic audit at iteration {context.iteration}: "
                f"{len(result.findings)} finding(s). {result.explanation}"
            ),
            severity=severity,
            data={
                "iteration": context.iteration,
                "findings": result.findings,
                "recommended_outcome": result.recommended_outcome,
                "explanation": result.explanation,
            },
        )


# ===================================================================
# 5. GoalDriftHook
# ===================================================================

class GoalDriftHook(Hook):
    """Detects drift between the current research goal and the original objective.

    Fires at :data:`HookPoint.MENTAL_MODEL_UPDATED`.

    Expected ``context.data`` keys:

    * ``original_objective`` — the campaign's initial objective (G₀).
    * ``current_goal`` — the currently active goal (Gₜ).
    * ``goal_history`` — list of prior goal strings (optional).
    """

    def __init__(
        self,
        router: LLMRouter,
        *,
        priority: int = 25,
    ) -> None:
        self.name = "goal_drift"
        self.hook_points = {HookPoint.MENTAL_MODEL_UPDATED}
        self.priority = priority
        self.enabled = True
        self._router = router

    async def execute(self, context: HookContext) -> HookResult:
        original = context.data.get("original_objective", "")
        current = context.data.get("current_goal", "")

        if not original or not current:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message="Insufficient data for goal drift assessment.",
                severity="info",
            )

        goal_history = context.data.get("goal_history", [])
        prompt = (
            "Compare the current research goal with the original objective "
            "and assess whether the campaign has drifted.\n\n"
            f"Original objective (G₀): {original}\n"
            f"Current goal (Gₜ):       {current}\n"
            f"Goal history: {json.dumps(goal_history, default=str)}\n\n"
            "Determine if drift occurred, its severity (none/mild/severe), "
            "and recommend: continue, reframe, or escalate."
        )

        result = await self._router.generate_structured(
            _STAGE,
            [Message.user(prompt)],
            _GoalDriftResponse,
            temperature=0.2,
        )

        if not result.drift_detected:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message="No significant goal drift detected.",
                severity="info",
                data={"drift_severity": "none"},
            )

        severity = "critical" if result.drift_severity == "severe" else "warning"
        return HookResult(
            hook_name=self.name,
            passed=False,
            message=f"Goal drift detected ({result.drift_severity}): {result.explanation}",
            severity=severity,
            data={
                "drift_severity": result.drift_severity,
                "recommended_outcome": result.recommended_outcome,
                "explanation": result.explanation,
                "original_objective": original,
                "current_goal": current,
            },
        )


# ===================================================================
# 6. ResourceExhaustionHook
# ===================================================================



# ===================================================================
# 7. RepeatedFailureHook
# ===================================================================

class RepeatedFailureHook(Hook):
    """Triggers when *N* consecutive experiment failures occur.

    Fires at :data:`HookPoint.POST_EXPERIMENT`.  Tracks success/failure
    internally; the counter resets on any success.

    Expected ``context.data`` keys:

    * ``success`` — boolean indicating whether the last job succeeded.
    * ``job_id`` — identifier of the completed job (optional).
    * ``errors`` — list of error strings (optional).
    """

    def __init__(
        self,
        router: LLMRouter,
        *,
        failure_threshold: int = 3,
        priority: int = 12,
    ) -> None:
        self.name = "repeated_failure"
        self.hook_points = {HookPoint.POST_EXPERIMENT}
        self.priority = priority
        self.enabled = True
        self._router = router
        self._failure_threshold = failure_threshold
        self._consecutive_failures: int = 0
        self._failure_log: list[dict[str, Any]] = []

    async def execute(self, context: HookContext) -> HookResult:
        success: bool = context.data.get("success", True)

        if success:
            self._consecutive_failures = 0
            self._failure_log.clear()
            return HookResult(
                hook_name=self.name,
                passed=True,
                message="Experiment succeeded — failure counter reset.",
                severity="info",
            )

        # Record failure
        self._consecutive_failures += 1
        self._failure_log.append({
            "job_id": context.data.get("job_id", "unknown"),
            "errors": context.data.get("errors", []),
            "iteration": context.iteration,
        })

        if self._consecutive_failures < self._failure_threshold:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message=(
                    f"Failure {self._consecutive_failures}/{self._failure_threshold} — "
                    f"below threshold."
                ),
                severity="info",
                data={"consecutive_failures": self._consecutive_failures},
            )

        # Threshold reached — triage via LLM
        logger.warning(
            "Repeated failure threshold reached: %d consecutive failures",
            self._consecutive_failures,
        )

        prompt = (
            f"{self._consecutive_failures} consecutive experiment failures have occurred.\n"
            f"Failure log: {json.dumps(self._failure_log[-self._failure_threshold:], default=str)}\n\n"
            "Analyse the pattern and recommend: replan, recover, or escalate."
        )

        response = await self._router.generate(
            _STAGE,
            [Message.user(prompt)],
            temperature=0.2,
            max_tokens=512,
        )

        return HookResult(
            hook_name=self.name,
            passed=False,
            message=(
                f"{self._consecutive_failures} consecutive failures reached threshold "
                f"({self._failure_threshold}). {response.content[:200]}"
            ),
            severity="critical",
            human_intervention_required=True,
            data={
                "consecutive_failures": self._consecutive_failures,
                "failure_threshold": self._failure_threshold,
                "failure_log": self._failure_log[-self._failure_threshold:],
                "llm_assessment": response.content,
                "recommended_outcome": AuditOutcome.REPLAN.value,
            },
        )


# ===================================================================
# 8. NovelResultHook
# ===================================================================

class NovelResultHook(Hook):
    """Flags unexpectedly novel experimental results for validation.

    Fires at :data:`HookPoint.POST_EXPERIMENT`.  Uses the LLM to assess
    whether a result deviates significantly from expectations.

    Expected ``context.data`` keys:

    * ``result_summary`` — textual summary of the result.
    * ``expected_outcomes`` — list of expected outcome descriptions.
    * ``metrics`` — dict of key numerical results (optional).
    """

    def __init__(
        self,
        router: LLMRouter,
        *,
        priority: int = 40,
    ) -> None:
        self.name = "novel_result"
        self.hook_points = {HookPoint.POST_EXPERIMENT}
        self.priority = priority
        self.enabled = True
        self._router = router

    async def execute(self, context: HookContext) -> HookResult:
        result_summary: str = context.data.get("result_summary", "")
        expected: list[str] = context.data.get("expected_outcomes", [])
        metrics: dict[str, Any] = context.data.get("metrics", {})

        if not result_summary:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message="No result summary provided — skipping novelty check.",
                severity="info",
            )

        prompt = (
            "Assess whether the following experimental result is unexpectedly "
            "novel — i.e. it significantly differs from what was predicted or "
            "is unusual for this domain.\n\n"
            f"Result summary: {result_summary}\n"
            f"Expected outcomes: {json.dumps(expected, default=str)}\n"
            f"Metrics: {json.dumps(metrics, default=str)}\n\n"
            "If novel, describe the novelty and recommend: continue (with "
            "note), validate (independent confirmation), or escalate (human "
            "review)."
        )

        assessment = await self._router.generate_structured(
            _STAGE,
            [Message.user(prompt)],
            _NoveltyAssessmentResponse,
            temperature=0.3,
        )

        if not assessment.is_novel:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message="Result is consistent with expectations — no unexpected novelty.",
                severity="info",
            )

        return HookResult(
            hook_name=self.name,
            passed=True,  # Novel results are not failures — they are noteworthy
            message=f"Novel result detected: {assessment.novelty_description}",
            severity="warning",
            data={
                "is_novel": True,
                "novelty_description": assessment.novelty_description,
                "recommended_action": assessment.recommended_action,
            },
        )


# ===================================================================
# 9. ClaimPromotionHook
# ===================================================================

class _ClaimPromotionResponse(BaseModel):
    """LLM assessment of whether a promoted claim has sufficient evidence."""

    evidence_sufficient: bool = Field(
        default=True,
        description="Whether the supporting evidence is sufficient for promotion.",
    )
    explanation: str = Field(default="", description="Brief explanation of the assessment.")
    recommended_outcome: str = Field(
        default="continue",
        description="One of: continue, validate, replan, escalate.",
    )


class ClaimPromotionHook(Hook):
    """Checks whether a promoted claim has sufficient supporting evidence.

    Fires at :data:`HookPoint.CLAIM_PROMOTED`.  Uses the LLM to evaluate
    whether the evidence base justifies the promotion.

    Expected ``context.data`` keys:

    * ``claim_statement`` — the text of the claim being promoted.
    * ``claim_status`` — the new status of the claim.
    * ``supporting_evidence`` — list of evidence descriptions.
    * ``evidence_count`` — number of supporting evidence items.
    """

    def __init__(
        self,
        router: LLMRouter,
        *,
        priority: int = 35,
    ) -> None:
        self.name = "claim_promotion"
        self.hook_points = {HookPoint.CLAIM_PROMOTED}
        self.priority = priority
        self.enabled = True
        self._router = router

    async def execute(self, context: HookContext) -> HookResult:
        claim_statement: str = context.data.get("claim_statement", "")
        claim_status: str = context.data.get("claim_status", "")
        supporting_evidence: list[str] = context.data.get("supporting_evidence", [])
        evidence_count: int = context.data.get("evidence_count", len(supporting_evidence))

        if not claim_statement:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message="No claim statement provided — skipping promotion check.",
                severity="info",
            )

        prompt = (
            "A scientific claim is being promoted to a higher confidence level.  "
            "Assess whether the supporting evidence is sufficient.\n\n"
            f"Claim: {claim_statement}\n"
            f"New status: {claim_status}\n"
            f"Number of supporting evidence items: {evidence_count}\n"
            f"Evidence: {json.dumps(supporting_evidence, default=str)}\n\n"
            "Determine whether the evidence is sufficient for promotion.  "
            "Consider quantity, quality, and independence of evidence.  "
            "Recommend: continue, validate, replan, or escalate."
        )

        result = await self._router.generate_structured(
            _STAGE,
            [Message.user(prompt)],
            _ClaimPromotionResponse,
            temperature=0.2,
        )

        if result.evidence_sufficient:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message=f"Claim promotion approved: {result.explanation}",
                severity="info",
                data={
                    "claim_statement": claim_statement,
                    "claim_status": claim_status,
                    "evidence_count": evidence_count,
                },
            )

        severity = "critical" if result.recommended_outcome in ("escalate", "replan") else "warning"
        return HookResult(
            hook_name=self.name,
            passed=False,
            message=(
                f"Claim promotion flagged — insufficient evidence: "
                f"{result.explanation}"
            ),
            severity=severity,
            data={
                "claim_statement": claim_statement,
                "claim_status": claim_status,
                "evidence_count": evidence_count,
                "evidence_sufficient": False,
                "recommended_outcome": result.recommended_outcome,
                "explanation": result.explanation,
            },
        )


# ===================================================================
# 10. TerminationGuardHook
# ===================================================================

class _TerminationGuardResponse(BaseModel):
    """LLM assessment of whether termination is premature."""

    premature: bool = Field(
        default=False,
        description="Whether termination appears premature.",
    )
    explanation: str = Field(default="", description="Brief explanation of the assessment.")
    recommended_outcome: str = Field(
        default="continue",
        description="One of: continue, replan, escalate.",
    )


class TerminationGuardHook(Hook):
    """Guards against premature campaign termination.

    Fires at :data:`HookPoint.TERMINATION_ATTEMPT`.  Uses the LLM to
    check whether open questions remain, claims are unvalidated, or
    the experiment plan is incomplete.

    Expected ``context.data`` keys:

    * ``open_questions`` — list of unresolved research questions.
    * ``unvalidated_claims`` — list of claims not yet validated.
    * ``completed_experiments`` — number of experiments completed.
    * ``total_experiments`` — total number of planned experiments.
    """

    def __init__(
        self,
        router: LLMRouter,
        *,
        priority: int = 5,
    ) -> None:
        self.name = "termination_guard"
        self.hook_points = {HookPoint.TERMINATION_ATTEMPT}
        self.priority = priority
        self.enabled = True
        self._router = router

    async def execute(self, context: HookContext) -> HookResult:
        open_questions: list[str] = context.data.get("open_questions", [])
        unvalidated_claims: list[str] = context.data.get("unvalidated_claims", [])
        completed: int = context.data.get("completed_experiments", 0)
        total: int = context.data.get("total_experiments", 0)

        prompt = (
            "The research campaign is about to terminate.  Assess whether "
            "termination is premature.\n\n"
            f"Open questions: {json.dumps(open_questions, default=str)}\n"
            f"Unvalidated claims: {json.dumps(unvalidated_claims, default=str)}\n"
            f"Experiments completed: {completed} / {total}\n\n"
            "Determine whether termination is premature — i.e. significant "
            "open questions remain, claims are unvalidated, or the experiment "
            "plan is far from complete.  Recommend: continue, replan, or "
            "escalate."
        )

        result = await self._router.generate_structured(
            _STAGE,
            [Message.user(prompt)],
            _TerminationGuardResponse,
            temperature=0.2,
        )

        if not result.premature:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message="Termination approved — no premature exit detected.",
                severity="info",
                data={
                    "completed_experiments": completed,
                    "total_experiments": total,
                },
            )

        return HookResult(
            hook_name=self.name,
            passed=False,
            message=(
                f"Premature termination detected: {result.explanation}"
            ),
            severity="critical",
            human_intervention_required=True,
            data={
                "premature": True,
                "open_questions": open_questions,
                "unvalidated_claims": unvalidated_claims,
                "completed_experiments": completed,
                "total_experiments": total,
                "recommended_outcome": result.recommended_outcome,
                "explanation": result.explanation,
            },
        )


# ===================================================================
# 11. MilestoneAuditHook
# ===================================================================

class _MilestoneAuditResponse(BaseModel):
    """LLM review of progress at a phase boundary."""

    issues_detected: bool = Field(
        default=False,
        description="Whether significant issues were found.",
    )
    findings: list[str] = Field(default_factory=list)
    explanation: str = Field(default="", description="Brief explanation of the assessment.")
    recommended_outcome: str = Field(
        default="continue",
        description="One of: continue, validate, replan, escalate.",
    )


class MilestoneAuditHook(Hook):
    """Reviews progress at phase boundaries (milestones).

    Fires at :data:`HookPoint.MILESTONE_COMPLETE`.  Uses the LLM to
    evaluate whether the completed phase met expectations and whether
    the campaign is ready to proceed to the next phase.

    Expected ``context.data`` keys:

    * ``completed_phase`` — name of the phase that just completed.
    * ``next_phase`` — name of the phase about to start.
    * ``state_summary`` — high-level summary of the current state.
    * ``iteration_count`` — number of iterations completed so far.
    """

    def __init__(
        self,
        router: LLMRouter,
        *,
        priority: int = 45,
    ) -> None:
        self.name = "milestone_audit"
        self.hook_points = {HookPoint.MILESTONE_COMPLETE}
        self.priority = priority
        self.enabled = True
        self._router = router

    async def execute(self, context: HookContext) -> HookResult:
        completed_phase: str = context.data.get("completed_phase", "")
        next_phase: str = context.data.get("next_phase", "")
        state_summary: str = context.data.get("state_summary", "")
        iteration_count: int = context.data.get("iteration_count", 0)

        if not completed_phase:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message="No phase information provided — skipping milestone audit.",
                severity="info",
            )

        prompt = (
            "A research campaign has completed a major phase.  Review the "
            "progress and assess readiness to proceed.\n\n"
            f"Completed phase: {completed_phase}\n"
            f"Next phase: {next_phase}\n"
            f"Iterations so far: {iteration_count}\n"
            f"State summary: {state_summary}\n\n"
            "Identify any issues that should be addressed before moving "
            "to the next phase.  Consider: data quality, completeness, "
            "goal alignment, and resource usage.  "
            "Recommend: continue, validate, replan, or escalate."
        )

        result = await self._router.generate_structured(
            _STAGE,
            [Message.user(prompt)],
            _MilestoneAuditResponse,
            temperature=0.2,
        )

        if not result.issues_detected:
            return HookResult(
                hook_name=self.name,
                passed=True,
                message=(
                    f"Milestone audit passed: {completed_phase} → {next_phase}. "
                    f"{result.explanation}"
                ),
                severity="info",
                data={
                    "completed_phase": completed_phase,
                    "next_phase": next_phase,
                    "iteration_count": iteration_count,
                },
            )

        severity = "critical" if result.recommended_outcome in ("escalate", "replan") else "warning"
        return HookResult(
            hook_name=self.name,
            passed=False,
            message=(
                f"Milestone audit flagged {len(result.findings)} issue(s) at "
                f"{completed_phase} → {next_phase}: {result.explanation}"
            ),
            severity=severity,
            data={
                "completed_phase": completed_phase,
                "next_phase": next_phase,
                "iteration_count": iteration_count,
                "findings": result.findings,
                "recommended_outcome": result.recommended_outcome,
                "explanation": result.explanation,
            },
        )

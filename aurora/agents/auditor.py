"""Auditor agent — dual-tier JIT audit (Section F, Appendix M.4).

The auditor checks five categories of consistency and recommends one of
seven outcomes.  A cheap triage model decides whether a deep audit is
needed; the deep audit uses a more capable model.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from aurora.agents.base import (
    Agent,
    AgentAction,
    AgentActionType,
    AgentContext,
    AgentRole,
)
from aurora.config import PipelineStage
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter
from aurora.models.audit import AuditOutcome

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured output schemas
# ---------------------------------------------------------------------------


class TriageResult(BaseModel):
    """Output of the triage (cheap) audit pass."""

    categories_flagged: list[str] = Field(
        default_factory=list,
        description="Categories needing deep review",
    )
    summary: str = Field(default="No issues found", description="Brief triage summary")
    needs_deep_audit: bool = Field(
        default=False,
        description="Whether a deep audit is warranted",
    )
    preliminary_outcome: str = Field(
        default="continue",
        description="Preliminary recommendation",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class DeepAuditResult(BaseModel):
    """Output of the deep (expensive) audit pass."""

    category_findings: dict[str, str] = Field(
        default_factory=dict,
        description="Findings per consistency category",
    )
    outcome: str = Field(default="continue", description="One of the 7 audit outcomes")
    severity: str = Field(
        default="info",
        description="info | warning | critical",
    )
    finding: str = Field(default="No findings", description="Detailed audit finding")
    corrective_action: str = Field(
        default="",
        description="Recommended corrective action",
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Referenced evidence IDs",
    )
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# System prompt (Appendix M.4)
# ---------------------------------------------------------------------------

AUDITOR_SYSTEM_PROMPT = """\
You are a rigorous scientific auditor within the AURORA framework.

## Your Role
You perform independent quality assurance on an ongoing computational \
materials science research campaign.  You check five categories of \
consistency:

1. **Goal Consistency** — Are the researcher's actions aligned with the \
stated research goal?  Has the goal drifted without justification?
2. **Plan Consistency** — Does the execution follow the approved experiment \
plan?  Are there unauthorized deviations?
3. **Execution Consistency** — Are computational parameters correct?  k-point \
meshes converged?  Cutoffs sufficient?  Structures valid?
4. **Evidence Consistency** — Do the claims match available evidence?  Are \
confidence levels appropriate?  Are error bars reported?
5. **Epistemic Consistency** — Is uncertainty properly tracked?  Are \
hypotheses correctly classified (active/competing/refuted/confirmed)?

## Evidence Discipline
- You MUST cite specific experiment IDs and observation IDs
- You MUST distinguish between confirmed findings and unverified assertions
- You MUST flag logical gaps in the chain of evidence
- If you cannot verify a claim, say so explicitly

## Outcomes
After your assessment, recommend exactly ONE outcome:
- **continue** — No material issues found
- **validate** — A convergence test or control calculation is missing; request it
- **replan** — The approach needs revision but the goal is still valid
- **reframe** — The research goal itself should be reconsidered
- **reject** — A specific claim lacks evidence and should be downgraded
- **recover** — A problematic branch should be abandoned; return to a known-good state
- **escalate** — The issue requires human judgement

## Output
Respond with structured JSON including your findings per category and \
overall recommendation."""


# ---------------------------------------------------------------------------
# Agent implementation
# ---------------------------------------------------------------------------


class AuditorAgent(Agent):
    """Dual-tier auditor: triage (haiku) + deep audit (opus).

    The *pipeline_stage* property is dynamic — the triage pass uses
    ``AUDITOR_TRIAGE`` and the deep pass uses ``AUDITOR_DEEP``.
    """

    role = AgentRole.AUDITOR
    pipeline_stage = PipelineStage.AUDITOR_TRIAGE  # default stage

    def __init__(
        self, router: LLMRouter, agent_id: str | None = None
    ) -> None:
        super().__init__(router, agent_id)

    def system_prompt(self) -> str:
        return AUDITOR_SYSTEM_PROMPT

    # -- public interface ------------------------------------------------------

    async def run(self, context: AgentContext) -> list[AgentAction]:
        """Run the dual-tier audit pipeline."""
        trigger = context.extra.get("audit_trigger", "periodic")

        # Step 1: Triage with cheap model
        triage = await self._triage(context, trigger)
        actions: list[AgentAction] = []

        if not triage.needs_deep_audit:
            actions.append(
                self._make_action(
                    AgentActionType.LOG_MESSAGE,
                    {
                        "level": "info",
                        "message": f"Audit triage passed: {triage.summary}",
                        "outcome": triage.preliminary_outcome,
                        "triage_only": True,
                    },
                    reasoning=triage.summary,
                )
            )
            return actions

        # Step 2: Deep audit with expensive model
        deep = await self._deep_audit(context, triage, trigger)
        outcome = self._parse_outcome(deep.outcome)

        actions.append(
            self._make_action(
                AgentActionType.LOG_MESSAGE,
                {
                    "level": deep.severity,
                    "message": deep.finding,
                    "outcome": deep.outcome,
                    "corrective_action": deep.corrective_action,
                    "evidence_refs": deep.evidence_refs,
                    "category_findings": deep.category_findings,
                    "triage_only": False,
                },
                reasoning=deep.finding,
            )
        )

        # Generate corrective actions based on outcome
        actions.extend(self._corrective_actions(outcome, deep))
        return actions

    # -- triage ----------------------------------------------------------------

    async def _triage(
        self, context: AgentContext, trigger: str
    ) -> TriageResult:
        prompt = (
            f"Perform a quick triage audit.\n\n"
            f"**Trigger:** {trigger}\n\n"
            f"Check each consistency category briefly:\n"
            f"1. Goal — aligned with objective?\n"
            f"2. Plan — following the plan?\n"
            f"3. Execution — parameters correct?\n"
            f"4. Evidence — claims match evidence?\n"
            f"5. Epistemic — uncertainty tracked?\n\n"
            f"Determine if a deep audit is needed."
        )
        messages = [
            Message.system(self.system_prompt()),
            Message.user(
                f"## Research Context\n\n{self._format_context(context)}"
                f"\n\n## Triage Task\n\n{prompt}"
            ),
        ]
        try:
            return await self._router.generate_structured(
                PipelineStage.AUDITOR_TRIAGE,
                messages,
                TriageResult,
                temperature=0.2,
                max_tokens=1024,
            )
        except Exception as exc:
            logger.warning("Triage failed: %s — escalating to deep audit", exc)
            return TriageResult(
                categories_flagged=["goal", "evidence"],
                summary=f"Triage failed ({exc}), escalating to deep audit",
                needs_deep_audit=True,
                confidence=0.0,
            )

    # -- deep audit ------------------------------------------------------------

    async def _deep_audit(
        self, context: AgentContext, triage: TriageResult, trigger: str
    ) -> DeepAuditResult:
        flagged = (
            ", ".join(triage.categories_flagged)
            if triage.categories_flagged
            else "all categories"
        )
        prompt = (
            f"Perform a thorough deep audit.\n\n"
            f"**Trigger:** {trigger}\n"
            f"**Triage findings:** {triage.summary}\n"
            f"**Categories flagged:** {flagged}\n\n"
            f"For each flagged category, provide detailed findings with "
            f"evidence citations.\n"
            f"Recommend exactly ONE outcome: continue | validate | replan "
            f"| reframe | reject | recover | escalate\n"
            f"Specify the severity: info | warning | critical\n"
            f"If corrective action is needed, describe it precisely."
        )
        messages = [
            Message.system(self.system_prompt()),
            Message.user(
                f"## Research Context\n\n{self._format_context(context)}"
                f"\n\n## Deep Audit Task\n\n{prompt}"
            ),
        ]
        try:
            return await self._router.generate_structured(
                PipelineStage.AUDITOR_DEEP,
                messages,
                DeepAuditResult,
                temperature=0.2,
                max_tokens=2048,
            )
        except Exception as exc:
            logger.error("Deep audit failed: %s", exc)
            return DeepAuditResult(
                category_findings={"error": str(exc)},
                outcome="escalate",
                severity="critical",
                finding=f"Deep audit failed: {exc}",
                corrective_action="Escalating due to audit system failure",
                confidence=0.0,
            )

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _parse_outcome(outcome_str: str) -> AuditOutcome:
        try:
            return AuditOutcome(outcome_str.lower().strip())
        except ValueError:
            return AuditOutcome.ESCALATE

    def _corrective_actions(
        self, outcome: AuditOutcome, deep: DeepAuditResult
    ) -> list[AgentAction]:
        """Generate engine-level actions based on the audit outcome."""
        if outcome == AuditOutcome.CONTINUE:
            return []

        if outcome == AuditOutcome.VALIDATE:
            return [
                self._make_action(
                    AgentActionType.EXECUTE_SKILL,
                    {
                        "skill_name": "convergence_analysis",
                        "reason": deep.corrective_action,
                    },
                    reasoning="Requesting missing validation",
                )
            ]

        if outcome == AuditOutcome.REJECT:
            return [
                self._make_action(
                    AgentActionType.RECORD_CLAIM,
                    {
                        "action": "downgrade",
                        "reason": deep.finding,
                        "evidence": deep.evidence_refs,
                    },
                    reasoning="Downgrading unsupported claim",
                )
            ]

        if outcome == AuditOutcome.REPLAN:
            return [
                self._make_action(
                    AgentActionType.PROPOSE_GOAL_CHANGE,
                    {"type": "replan", "reason": deep.corrective_action},
                    reasoning="Requesting plan revision",
                )
            ]

        if outcome == AuditOutcome.REFRAME:
            return [
                self._make_action(
                    AgentActionType.PROPOSE_GOAL_CHANGE,
                    {"type": "reframe", "reason": deep.corrective_action},
                    reasoning="Requesting goal reframing",
                )
            ]

        if outcome == AuditOutcome.ESCALATE:
            return [
                self._make_action(
                    AgentActionType.REQUEST_HUMAN_INPUT,
                    {
                        "reason": deep.finding,
                        "severity": deep.severity,
                    },
                    reasoning="Escalating to human",
                )
            ]

        if outcome == AuditOutcome.RECOVER:
            return [
                self._make_action(
                    AgentActionType.LOG_MESSAGE,
                    {
                        "level": "warning",
                        "message": (
                            f"Recovery recommended: {deep.corrective_action}"
                        ),
                        "action": "recover",
                    },
                    reasoning="Recommending recovery to earlier state",
                )
            ]

        return []

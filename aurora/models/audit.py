"""Audit models (Section F).

The auditor evaluates five categories of consistency and produces one of
seven possible outcomes.  Audits are triggered by a hybrid policy: event-
driven hooks plus periodic passes.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class AuditTrigger(str, Enum):
    """Events that can trigger an audit (Section F.3)."""

    MILESTONE_COMPLETION = "milestone_completion"
    GOAL_CHANGE = "goal_change"
    REPEATED_FAILURE = "repeated_failure"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    CLAIM_PROMOTION = "claim_promotion"
    COMPUTE_SPEND = "compute_spend"
    TERMINATION = "termination"
    HUMAN_REQUEST = "human_request"
    PERIODIC = "periodic"


class AuditOutcome(str, Enum):
    """Verdicts of the live auditor (supervisor) about the research agent.

    - continue  — the agent makes reasonable progress; nothing to do
    - advise    — a suggestion is sent to the agent, which keeps working
    - interrupt — the agent's current action is stopped and the correction sent
    - escalate  — a human gate opens: a decision only the human should make
    - withdraw  — the auditor takes back its escalation: no human decision is needed after all
    """

    CONTINUE = "continue"
    ADVISE = "advise"
    INTERRUPT = "interrupt"
    ESCALATE = "escalate"
    WITHDRAW = "withdraw"

    # Legacy values kept for backward compatibility with old AuditorAgent
    VALIDATE = "validate"
    REPLAN = "replan"
    REFRAME = "reframe"
    REJECT = "reject"
    RECOVER = "recover"


class ConsistencyCategory(str, Enum):
    """Categories of consistency checked by the auditor (Section F.1)."""

    GOAL = "goal"
    PLAN = "plan"
    EXECUTION = "execution"
    EVIDENCE = "evidence"
    EPISTEMIC = "epistemic"


class AuditRecord(BaseModel):
    """A single audit record written to the workspace."""

    schema_version: str = "aurora_audit_v0"
    audit_id: str
    trigger: AuditTrigger
    categories_checked: list[ConsistencyCategory] = Field(default_factory=list)
    outcome: AuditOutcome
    severity: Literal["info", "warning", "critical"] = "info"
    finding: str = Field(description="What the auditor found.")
    corrective_action: str = Field(
        default="",
        description="Recommended or required corrective action.",
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="IDs of evidence items referenced by this audit.",
    )
    triage_model: str = Field(
        default="",
        description="Model used for the triage pass.",
    )
    deep_audit_model: str | None = Field(
        default=None,
        description="Model used for the deep audit (None if triage sufficed).",
    )
    timestamp: datetime

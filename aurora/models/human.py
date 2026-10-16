"""Human intervention models (Section G, Table 2).

Every human interaction is a first-class event in the research trace,
categorised by its role and tagged as either capability-building (H_cap)
or per-run irreducible (H_run).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class InterventionCategory(str, Enum):
    """Categories of human intervention (Table 2)."""

    INITIAL_SPECIFICATION = "initial_specification"
    DOMAIN_CLARIFICATION = "domain_clarification"
    LOCAL_CORRECTION = "local_correction"
    GOAL_STEERING = "goal_steering"
    METHOD_CONSTRAINT = "method_constraint"
    AUDITOR_ESCALATION = "auditor_escalation"
    TERMINATION_DECISION = "termination_decision"


class InterventionType(str, Enum):
    """Distinguishes one-time fixes from irreducible per-run oversight.

    * ``CAPABILITY_BUILDING`` (H_cap): amortises to zero over subsequent
      campaigns once codified into a reusable skill or execution runbook.
    * ``PER_RUN`` (H_run): irreducible per-run overhead such as the initial
      research specification and strategic steering gates.
    """

    CAPABILITY_BUILDING = "capability_building"
    PER_RUN = "per_run"


class HumanIntervention(BaseModel):
    """A single logged human intervention event."""

    schema_version: str = "aurora_intervention_v0"
    intervention_id: str
    category: InterventionCategory
    intervention_type: InterventionType
    description: str = Field(description="Free-text description of what happened.")
    directive: str = Field(description="What the human instructed the system to do.")
    state_before: str = Field(
        default="",
        description="Summary of the research state before the intervention.",
    )
    state_after: str = Field(
        default="",
        description="Summary of the research state after the intervention.",
    )
    converted_to_skill: bool = Field(
        default=False,
        description="Whether this H_cap intervention was codified into a reusable skill.",
    )
    timestamp: datetime

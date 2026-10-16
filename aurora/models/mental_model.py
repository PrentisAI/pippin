"""Scientific mental model (Section E.3).

The mental model is *separate* from the persistent research artifacts.  It
represents the agent's current cognitive state — active hypotheses, inferred
mechanisms, confidence levels — and is periodically **reconstructed from the
persistent evidence** rather than carried forward from previous model
reasoning.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Hypothesis(BaseModel):
    """A scientific hypothesis held in the mental model."""

    hypothesis_id: str
    statement: str
    status: Literal["active", "competing", "refuted", "confirmed"] = "active"
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence score in [0, 1].",
    )
    supporting_evidence: list[str] = Field(
        default_factory=list,
        description="Observation IDs that support this hypothesis.",
    )
    contradicting_evidence: list[str] = Field(
        default_factory=list,
        description="Observation IDs that contradict this hypothesis.",
    )


class CausalRelationship(BaseModel):
    """An inferred causal or explanatory relationship."""

    cause: str
    effect: str
    mechanism: str = Field(description="Proposed physical or chemical mechanism.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)


class ScientificMentalModel(BaseModel):
    """The agent's evolving scientific belief state.

    Unlike ``ResearchArtifacts`` which is the durable project record, the
    mental model holds the agent's *interpretation* of that record.  Two
    agents with access to the same artifact history may construct different
    mental models.
    """

    schema_version: str = "aurora_mental_model_v0"
    active_hypotheses: list[Hypothesis] = Field(default_factory=list)
    competing_hypotheses: list[Hypothesis] = Field(default_factory=list)
    inferred_mechanisms: list[str] = Field(default_factory=list)
    causal_relationships: list[CausalRelationship] = Field(default_factory=list)
    unresolved_ambiguity: list[str] = Field(default_factory=list)
    expected_outcomes: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping from experiment description to expected result.",
    )
    current_rationale: str = Field(
        default="",
        description="Free-text explanation of why the current goal is worth pursuing.",
    )
    reconstructed_from_evidence_at: datetime | None = Field(
        default=None,
        description="Timestamp of the last evidence-based reconstruction.",
    )

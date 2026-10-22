"""Multi-agent roundtable models (paper: "Multi-agent ideation").

The roundtable convenes heterogeneous foundation models for multi-turn
deliberative debate.  A ``RoundtableChair`` monitors convergence and routes
speakers; each ``SeatContribution`` is one turn from one seat.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SeatContribution(BaseModel):
    """A single contribution from a roundtable seat in a given round."""

    seat_id: str
    model_id: str
    round_number: int
    content: str
    referenced_evidence: list[str] = Field(
        default_factory=list,
        description="Evidence IDs cited in this contribution.",
    )
    unverified_claims: list[str] = Field(
        default_factory=list,
        description="Claims explicitly flagged as '(my claim, unverified)'.",
    )
    kind: str = Field(default="", description="Turn type, e.g. presentation, remarks, response, open.")
    agenda_item: str = Field(default="", description="Seat ID whose presentation is being discussed, if any.")
    citations: list[str] = Field(default_factory=list, description="URLs returned by web search for this turn.")
    reasoning: str = Field(default="", description="Reasoning text returned by the API for this turn, if any.")


class ConvergenceJudgment(BaseModel):
    """The chair's assessment of whether the roundtable has converged."""

    converged: bool
    reason: str
    next_speakers: list[str] = Field(
        default_factory=list,
        description="Seat IDs that should speak next (empty if converged).",
    )


class IdeaScore(BaseModel):
    """Novelty × Feasibility × Significance score for a proposal."""

    novelty: float = Field(default=7.0, ge=0.0, le=10.0)
    feasibility: float = Field(default=7.0, ge=0.0, le=10.0)
    significance: float = Field(default=7.0, ge=0.0, le=10.0)
    composite: float = Field(
        default=7.0,
        description="Weighted combination of the three axes.",
    )
    explanation: str = Field(
        default="",
        description="Rationale for the scores.",
    )
    closest: list[dict[str, str]] = Field(
        default_factory=list,
        description="The closest prior work, at most three: [{key, differs}], each with one sentence on how "
                    "the proposal differs from it (keys from the prior work given).",
    )


class RoundtableProposal(BaseModel):
    """A research proposal produced by the roundtable deliberation."""

    schema_version: str = "aurora_proposal_v0"
    proposal_id: str
    title: str
    description: str
    research_questions: list[str] = Field(default_factory=list)
    proposed_methods: list[str] = Field(default_factory=list)
    expected_outcomes: list[str] = Field(default_factory=list)
    hypothesis: str = ""
    alternatives_considered: str = ""
    falsification_check: str = ""
    unresolved_disagreements: list[str] = Field(default_factory=list)
    meeting_notes: list[dict] = Field(default_factory=list)
    # library keys of the real papers the proposal cites (aurora.literature)
    references: list[str] = Field(default_factory=list)
    decision: dict = Field(default_factory=dict)
    contributions: list[SeatContribution] = Field(
        default_factory=list,
        description="Full deliberation history.",
    )
    score: IdeaScore | None = Field(
        default=None,
        description="Set by the idea screener after deliberation.",
    )
    selected: bool = Field(
        default=False,
        description="Whether this proposal was selected for execution.",
    )
    created_at: datetime

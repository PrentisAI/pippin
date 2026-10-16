"""Scientific claim model.

A claim progresses through tentative → accepted or tentative → rejected,
with supporting evidence and validation metadata tracked at each transition.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ScientificClaim(BaseModel):
    """A scientific claim produced during the research trajectory.

    Claims start as *tentative* and are promoted to *accepted* only after
    validation (converged-k, independent MLIP cross-check, etc.) or demoted
    to *rejected* when the auditor or new evidence contradicts them.
    """

    claim_id: str
    statement: str
    status: Literal["tentative", "accepted", "rejected"] = "tentative"
    supporting_evidence: list[str] = Field(
        default_factory=list,
        description="Observation IDs that support this claim.",
    )
    validation_method: str = Field(
        default="",
        description="How the claim was validated (e.g. converged-k, MLIP cross-check).",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score in [0, 1].",
    )
    promoted_at: datetime | None = Field(
        default=None,
        description="Timestamp when the claim was promoted to accepted.",
    )
    rejected_at: datetime | None = Field(
        default=None,
        description="Timestamp when the claim was rejected.",
    )
    rejection_reason: str = Field(
        default="",
        description="Explanation for rejection.",
    )

"""Pydantic data models for Pippin.

Every persisted record carries a ``schema_version`` field so that future
migrations can be handled gracefully.
"""

from __future__ import annotations

from aurora.models.artifacts import (
    ExperimentRecord,
    Observation,
    ResearchArtifacts,
)
from aurora.models.audit import (
    AuditOutcome,
    AuditRecord,
    AuditTrigger,
    ConsistencyCategory,
)
from aurora.models.claims import ScientificClaim
from aurora.models.experiment import (
    DependencyEdge,
    ExperimentJob,
    ExperimentPlan,
    JobResult,
    PreflightResult,
)
from aurora.models.human import (
    HumanIntervention,
    InterventionCategory,
    InterventionType,
)
from aurora.models.mental_model import (
    CausalRelationship,
    Hypothesis,
    ScientificMentalModel,
)
from aurora.models.roundtable import (
    ConvergenceJudgment,
    IdeaScore,
    RoundtableProposal,
    SeatContribution,
)

__all__ = [
    # artifacts
    "ExperimentRecord",
    "Observation",
    "ResearchArtifacts",
    # audit
    "AuditOutcome",
    "AuditRecord",
    "AuditTrigger",
    "ConsistencyCategory",
    # claims
    "ScientificClaim",
    # experiment
    "DependencyEdge",
    "ExperimentJob",
    "ExperimentPlan",
    "JobResult",
    "PreflightResult",
    # human
    "HumanIntervention",
    "InterventionCategory",
    "InterventionType",
    # mental_model
    "CausalRelationship",
    "Hypothesis",
    "ScientificMentalModel",
    # roundtable
    "ConvergenceJudgment",
    "IdeaScore",
    "RoundtableProposal",
    "SeatContribution",
]

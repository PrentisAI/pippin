"""Persistent research artifacts (Section E.1).

The ``ResearchArtifacts`` record is the durable project record, containing
the full history of experiments, observations, claims, human interventions,
and auditor findings.  It is persisted as ``artifacts.json`` in the
workspace and supports append-only operations so that no evidence is
silently dropped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from aurora.models.audit import AuditRecord
from aurora.models.claims import ScientificClaim
from aurora.models.human import HumanIntervention


class ExperimentRecord(BaseModel):
    """Record of a single experiment within the research trajectory."""

    job_id: str
    skill_used: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: Literal["proposed", "active", "completed", "failed"] = "proposed"
    result_path: str | None = Field(
        default=None,
        description="Workspace-relative path to the result JSON.",
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_diagnosis: str | None = None


class Observation(BaseModel):
    """A scientific observation extracted from experiment results."""

    observation_id: str
    source_job_id: str = Field(description="Job ID that produced this observation.")
    description: str
    numerical_values: dict[str, float] = Field(
        default_factory=dict,
        description="Key-value pairs of numerical measurements.",
    )
    validated: bool = Field(
        default=False,
        description="Whether this observation has been independently validated.",
    )
    timestamp: datetime


class ResearchArtifacts(BaseModel):
    """The complete persistent research artifact record.

    This is the single source of truth for the research trajectory,
    stored as ``artifacts.json`` in the workspace root.  It tracks
    everything from the initial objective through all experiments,
    observations, claims, interventions, and auditor findings.
    """

    schema_version: str = "aurora_artifacts_v0"

    # Goals
    initial_objective: str = Field(description="The original research question (G₀).")
    current_goal: str = Field(description="The currently active research goal (Gₜ).")
    current_plan_summary: str = Field(
        default="",
        description="Human-readable summary of the active experiment plan.",
    )

    # Milestones
    milestone_status: dict[str, str] = Field(
        default_factory=dict,
        description="Milestone ID → status string.",
    )

    # Experiments
    experiments: list[ExperimentRecord] = Field(default_factory=list)
    failed_experiments: list[ExperimentRecord] = Field(default_factory=list)

    # Evidence
    observations: list[Observation] = Field(default_factory=list)
    validated_evidence: list[str] = Field(
        default_factory=list,
        description="Observation IDs that passed independent validation.",
    )

    # Scientific state
    open_questions: list[str] = Field(default_factory=list)
    claims: list[ScientificClaim] = Field(default_factory=list)

    # Governance
    human_interventions: list[HumanIntervention] = Field(default_factory=list)
    auditor_findings: list[AuditRecord] = Field(default_factory=list)

    # Provenance
    provenance_links: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Claim ID → list of supporting evidence IDs.",
    )
    experiment_observation_map: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Job ID → list of observation IDs produced by that experiment.",
    )

    # Timestamps
    created_at: datetime
    updated_at: datetime

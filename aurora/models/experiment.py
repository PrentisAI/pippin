"""Experiment planning and execution models.

An ``ExperimentPlan`` is a DAG of ``ExperimentJob`` nodes connected by
``DependencyEdge`` entries.  Each job tracks its lifecycle from *pending*
through *running* to *completed* or *failed*, with a ``JobResult`` recording
outputs and resource consumption.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class DependencyEdge(BaseModel):
    """A directed edge in the experiment dependency graph."""

    from_job_id: str = Field(description="Job that must complete first.")
    to_job_id: str = Field(description="Job that depends on the source.")


class ExperimentJob(BaseModel):
    """A single computational job within an experiment plan."""

    job_id: str
    skill_name: str = Field(description="Name of the skill to invoke.")
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_outputs: list[str] = Field(
        default_factory=list,
        description="Filenames or artifact keys expected on success.",
    )
    resource_requirements: dict[str, Any] = Field(
        default_factory=dict,
        description="E.g. {'gpu': 'H100', 'num_gpus': 2}.",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="Job IDs this job depends on.",
    )
    status: Literal[
        "pending", "ready", "running", "completed", "failed", "blocked"
    ] = "pending"


class PreflightResult(BaseModel):
    """Result of a pre-flight verification for a single job."""

    job_id: str
    passed: bool
    checks: dict[str, bool] = Field(
        default_factory=dict,
        description="Named check → pass/fail.",
    )
    diagnostics: list[str] = Field(
        default_factory=list,
        description="Human-readable diagnostic messages.",
    )


class JobResult(BaseModel):
    """Outcome of executing a single experiment job."""

    job_id: str
    success: bool
    output_files: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(
        default_factory=dict,
        description="Key numerical results (energies, errors, …).",
    )
    errors: list[str] = Field(default_factory=list)
    wall_time_seconds: float = 0.0
    gpu_hours: float = 0.0


class ExperimentPlan(BaseModel):
    """A complete experiment plan derived from a screened proposal.

    The plan is stored as ``experiment_plan.json`` in the workspace and
    can be revised (incrementing ``revision``) when the auditor or the
    researcher decides to replan.
    """

    schema_version: str = "aurora_experiment_plan_v0"
    plan_id: str
    proposal_id: str = Field(description="ID of the roundtable proposal this plan implements.")
    jobs: list[ExperimentJob] = Field(default_factory=list)
    dependency_edges: list[DependencyEdge] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revision: int = Field(default=0, description="Monotonically increasing revision counter.")
    revision_reason: str = Field(default="", description="Reason for the latest revision.")

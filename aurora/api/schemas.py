"""Pydantic response schemas for the AURORA REST API.

These schemas decouple the API layer from the internal data models,
providing stable, versioned response shapes for the frontend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Campaign schemas
# ---------------------------------------------------------------------------

class CampaignSummary(BaseModel):
    """Lightweight campaign listing entry."""

    campaign_id: str
    objective: str
    domain: str = "computational_materials_science"
    phase: str = "initializing"
    autonomy_mode: str = "supervised"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PipelinePhaseStatus(BaseModel):
    """Status of a single pipeline phase."""

    phase: str
    status: Literal["done", "active", "pending", "skipped"] = "pending"
    start_time: datetime | None = None
    end_time: datetime | None = None


class CampaignProgress(BaseModel):
    """Pipeline progress breakdown for a campaign."""

    campaign_id: str
    current_phase: str
    research_cycle: int = 0
    phases: list[PipelinePhaseStatus] = Field(default_factory=list)
    iteration_count: int = 0
    completed_jobs: int = 0
    total_jobs: int = 0


class CampaignDetail(BaseModel):
    """Full campaign detail including config summary and status."""

    campaign_id: str
    objective: str
    domain: str = "computational_materials_science"
    phase: str = "initializing"
    autonomy_mode: str = "supervised"
    research_cycle: int = 0
    config: dict[str, Any] = Field(default_factory=dict)
    progress: CampaignProgress | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Experiment schemas
# ---------------------------------------------------------------------------

class ExperimentSummary(BaseModel):
    """Experiment entry for listing views."""

    job_id: str
    skill_used: str
    status: str = "proposed"
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ExperimentDetail(BaseModel):
    """Full experiment detail including parameters and results."""

    job_id: str
    skill_used: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: str = "proposed"
    result_path: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_diagnosis: str | None = None


# ---------------------------------------------------------------------------
# Claim schemas
# ---------------------------------------------------------------------------

class ClaimSummary(BaseModel):
    """Claim entry for listing views."""

    claim_id: str
    statement: str
    status: Literal["tentative", "accepted", "rejected"] = "tentative"
    confidence: float = 0.0
    evidence_count: int = 0


class ClaimDetail(BaseModel):
    """Full claim detail with linked evidence."""

    claim_id: str
    statement: str
    status: Literal["tentative", "accepted", "rejected"] = "tentative"
    confidence: float = 0.0
    supporting_evidence: list[str] = Field(default_factory=list)
    validation_method: str = ""
    promoted_at: datetime | None = None
    rejected_at: datetime | None = None
    rejection_reason: str = ""


# ---------------------------------------------------------------------------
# Provenance schemas
# ---------------------------------------------------------------------------

class ProvenanceNode(BaseModel):
    """A single node in the provenance graph."""

    id: str
    type: Literal["experiment", "observation", "claim"]
    label: str
    status: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProvenanceEdge(BaseModel):
    """A directed edge in the provenance graph."""

    source: str
    target: str
    relation: str  # "produced" | "supports"


class ProvenanceGraph(BaseModel):
    """Complete provenance graph for visualization."""

    nodes: list[ProvenanceNode] = Field(default_factory=list)
    edges: list[ProvenanceEdge] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Activity / event schemas
# ---------------------------------------------------------------------------

class ActivityEvent(BaseModel):
    """A single event from the event bus history."""

    name: str
    data: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    timestamp: datetime


class AuditSummary(BaseModel):
    """Audit record summary for listing views."""

    audit_id: str
    trigger: str
    outcome: str
    severity: str = "info"
    finding: str
    timestamp: datetime


class TimelineEntry(BaseModel):
    """A single entry in the campaign timeline view."""

    timestamp: datetime
    event_type: str
    phase: str = ""
    description: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------

class DashboardStats(BaseModel):
    """Aggregate statistics for the campaign dashboard."""

    total_experiments: int = 0
    completed_experiments: int = 0
    failed_experiments: int = 0
    success_rate: float = 0.0
    total_observations: int = 0
    validated_observations: int = 0
    total_claims: int = 0
    tentative_claims: int = 0
    accepted_claims: int = 0
    rejected_claims: int = 0
    ungrounded_claims: int = 0
    open_questions: int = 0
    iteration_count: int = 0
    research_cycles: int = 0
    audit_count: int = 0
    human_interventions: int = 0

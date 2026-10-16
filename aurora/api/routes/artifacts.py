"""Research artifact endpoints.

Provides access to experiments, observations, claims, and aggregate
research summary statistics.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from aurora.api.schemas import (
    ClaimDetail,
    ClaimSummary,
    DashboardStats,
    ExperimentDetail,
    ExperimentSummary,
)
from aurora.api.state import get_state_manager

router = APIRouter(
    prefix="/api/campaigns/{campaign_id}/artifacts",
    tags=["artifacts"],
)


def _load_artifacts_or_404(campaign_id: str):
    """Load artifacts for a campaign, raising 404 if not found."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    artifacts = state.load_artifacts(campaign_id)
    return artifacts


@router.get("/experiments", response_model=list[ExperimentSummary])
async def list_experiments(
    campaign_id: str,
    status: str | None = Query(None, description="Filter by status"),
    skill: str | None = Query(None, description="Filter by skill_used"),
) -> list[ExperimentSummary]:
    """List experiments with optional status and skill filtering."""
    artifacts = _load_artifacts_or_404(campaign_id)
    if artifacts is None:
        return []

    all_experiments = artifacts.experiments + artifacts.failed_experiments
    results: list[ExperimentSummary] = []

    for exp in all_experiments:
        if status and exp.status != status:
            continue
        if skill and exp.skill_used != skill:
            continue
        results.append(ExperimentSummary(
            job_id=exp.job_id,
            skill_used=exp.skill_used,
            status=exp.status,
            started_at=exp.started_at,
            completed_at=exp.completed_at,
        ))

    return results


@router.get("/experiments/{job_id}", response_model=ExperimentDetail)
async def get_experiment(campaign_id: str, job_id: str) -> ExperimentDetail:
    """Get detailed information about a specific experiment."""
    artifacts = _load_artifacts_or_404(campaign_id)
    if artifacts is None:
        raise HTTPException(status_code=404, detail="No artifacts found.")

    all_experiments = artifacts.experiments + artifacts.failed_experiments
    for exp in all_experiments:
        if exp.job_id == job_id:
            return ExperimentDetail(
                job_id=exp.job_id,
                skill_used=exp.skill_used,
                parameters=exp.parameters,
                status=exp.status,
                result_path=exp.result_path,
                started_at=exp.started_at,
                completed_at=exp.completed_at,
                failure_diagnosis=exp.failure_diagnosis,
            )

    raise HTTPException(status_code=404, detail=f"Experiment '{job_id}' not found.")


@router.get("/observations")
async def list_observations(
    campaign_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List observations with pagination."""
    artifacts = _load_artifacts_or_404(campaign_id)
    if artifacts is None:
        return {"observations": [], "total": 0, "limit": limit, "offset": offset}

    total = len(artifacts.observations)
    page = artifacts.observations[offset : offset + limit]

    return {
        "observations": [
            {
                "observation_id": obs.observation_id,
                "source_job_id": obs.source_job_id,
                "description": obs.description,
                "numerical_values": obs.numerical_values,
                "validated": obs.validated,
                "timestamp": obs.timestamp.isoformat(),
            }
            for obs in page
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/claims", response_model=list[ClaimSummary])
async def list_claims(
    campaign_id: str,
    status: str | None = Query(
        None,
        description="Filter by status: tentative, accepted, rejected",
    ),
) -> list[ClaimSummary]:
    """List scientific claims with optional status filtering."""
    artifacts = _load_artifacts_or_404(campaign_id)
    if artifacts is None:
        return []

    results: list[ClaimSummary] = []
    for claim in artifacts.claims:
        if status and claim.status != status:
            continue
        evidence_count = len(
            artifacts.provenance_links.get(claim.claim_id, [])
        )
        results.append(ClaimSummary(
            claim_id=claim.claim_id,
            statement=claim.statement,
            status=claim.status,
            confidence=claim.confidence,
            evidence_count=evidence_count,
        ))

    return results


@router.get("/claims/{claim_id}", response_model=ClaimDetail)
async def get_claim(campaign_id: str, claim_id: str) -> ClaimDetail:
    """Get claim detail with linked evidence."""
    artifacts = _load_artifacts_or_404(campaign_id)
    if artifacts is None:
        raise HTTPException(status_code=404, detail="No artifacts found.")

    for claim in artifacts.claims:
        if claim.claim_id == claim_id:
            return ClaimDetail(
                claim_id=claim.claim_id,
                statement=claim.statement,
                status=claim.status,
                confidence=claim.confidence,
                supporting_evidence=claim.supporting_evidence,
                validation_method=claim.validation_method,
                promoted_at=claim.promoted_at,
                rejected_at=claim.rejected_at,
                rejection_reason=claim.rejection_reason,
            )

    raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")


@router.get("/summary", response_model=DashboardStats)
async def get_summary(campaign_id: str) -> DashboardStats:
    """Get overall research summary statistics."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    return state.get_dashboard_stats(campaign_id)

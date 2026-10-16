"""Provenance graph and traceability endpoints.

Serves the experiment-observation-claim provenance graph as nodes and
edges for frontend visualization, along with textual reports and
ungrounded-claim detection.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from aurora.api.schemas import (
    ProvenanceEdge,
    ProvenanceGraph,
    ProvenanceNode,
)
from aurora.api.state import get_state_manager
from aurora.state.artifacts import ProvenanceTracker

router = APIRouter(
    prefix="/api/campaigns/{campaign_id}/provenance",
    tags=["provenance"],
)


def _load_artifacts_or_404(campaign_id: str):
    """Load artifacts, raising 404 if the campaign does not exist."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    artifacts = state.load_artifacts(campaign_id)
    return artifacts


@router.get("/graph", response_model=ProvenanceGraph)
async def get_provenance_graph(campaign_id: str) -> ProvenanceGraph:
    """Get the full provenance graph as nodes and edges.

    Node types:
    - ``experiment``: an experiment job
    - ``observation``: a scientific observation extracted from results
    - ``claim``: a scientific claim

    Edge relations:
    - ``produced``: experiment -> observation
    - ``supports``: observation -> claim
    """
    artifacts = _load_artifacts_or_404(campaign_id)
    if artifacts is None:
        return ProvenanceGraph()

    nodes: list[ProvenanceNode] = []
    edges: list[ProvenanceEdge] = []

    # -- Experiment nodes --
    all_experiments = artifacts.experiments + artifacts.failed_experiments
    for exp in all_experiments:
        nodes.append(ProvenanceNode(
            id=exp.job_id,
            type="experiment",
            label=f"{exp.skill_used} ({exp.job_id})",
            status=exp.status,
            metadata={
                "skill_used": exp.skill_used,
                "started_at": exp.started_at.isoformat() if exp.started_at else None,
                "completed_at": exp.completed_at.isoformat() if exp.completed_at else None,
            },
        ))

    # -- Observation nodes --
    for obs in artifacts.observations:
        nodes.append(ProvenanceNode(
            id=obs.observation_id,
            type="observation",
            label=obs.description[:80] if obs.description else obs.observation_id,
            status="validated" if obs.validated else "unvalidated",
            metadata={
                "source_job_id": obs.source_job_id,
                "validated": obs.validated,
                "numerical_values": obs.numerical_values,
                "timestamp": obs.timestamp.isoformat(),
            },
        ))

    # -- Claim nodes --
    for claim in artifacts.claims:
        nodes.append(ProvenanceNode(
            id=claim.claim_id,
            type="claim",
            label=claim.statement[:80] if claim.statement else claim.claim_id,
            status=claim.status,
            metadata={
                "confidence": claim.confidence,
                "validation_method": claim.validation_method,
            },
        ))

    # -- Edges: experiment -> observation (produced) --
    for job_id, obs_ids in artifacts.experiment_observation_map.items():
        for obs_id in obs_ids:
            edges.append(ProvenanceEdge(
                source=job_id,
                target=obs_id,
                relation="produced",
            ))

    # -- Edges: observation -> claim (supports) --
    for claim_id, evidence_ids in artifacts.provenance_links.items():
        for evidence_id in evidence_ids:
            edges.append(ProvenanceEdge(
                source=evidence_id,
                target=claim_id,
                relation="supports",
            ))

    return ProvenanceGraph(nodes=nodes, edges=edges)


@router.get("/report")
async def get_provenance_report(campaign_id: str) -> dict[str, str]:
    """Get the provenance report as human-readable text."""
    artifacts = _load_artifacts_or_404(campaign_id)
    if artifacts is None:
        return {"report": "No artifacts found for this campaign."}

    tracker = ProvenanceTracker(artifacts)
    report = tracker.generate_provenance_report()
    return {"report": report}


@router.get("/ungrounded")
async def get_ungrounded_claims(campaign_id: str) -> dict[str, list[str]]:
    """Get claim IDs that have no supporting evidence linked."""
    artifacts = _load_artifacts_or_404(campaign_id)
    if artifacts is None:
        return {"ungrounded_claim_ids": []}

    tracker = ProvenanceTracker(artifacts)
    ungrounded = tracker.validate_all_claims()
    return {"ungrounded_claim_ids": ungrounded}

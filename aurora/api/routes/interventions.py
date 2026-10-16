"""Human-in-the-loop intervention endpoints.

Provides REST endpoints for:
* Listing and responding to pending checkpoint requests.
* Proactively injecting an intervention into a running campaign.
* Pausing and resuming a campaign.
* Querying intervention history and H_cap / H_run statistics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aurora.api.state import get_state_manager
from aurora.human.checkpoint import CheckpointDecision, CheckpointResponse
from aurora.models.human import InterventionCategory

router = APIRouter(
    prefix="/api/campaigns/{campaign_id}/interventions",
    tags=["interventions"],
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class CheckpointResponseBody(BaseModel):
    """Body for the POST /respond endpoint."""

    checkpoint_id: str
    decision: str  # CheckpointDecision value
    comments: str = ""
    modifications: dict[str, Any] = Field(default_factory=dict)


class ProactiveInterventionBody(BaseModel):
    """Body for the POST /intervene endpoint."""

    category: str  # InterventionCategory value
    description: str
    directive: str


class InterventionHistoryEntry(BaseModel):
    """A single entry in the intervention history list."""

    checkpoint_id: str
    event_type: str
    summary: str
    decision: str
    comments: str = ""
    request_timestamp: datetime
    response_timestamp: datetime


class InterventionStatsResponse(BaseModel):
    """H_cap / H_run statistics for a campaign."""

    total: int = 0
    h_cap: int = 0
    h_run: int = 0
    h_cap_converted: int = 0
    h_cap_ratio: float = 0.0
    h_run_ratio: float = 0.0
    categories: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/pending")
async def get_pending_checkpoints(campaign_id: str) -> list[dict[str, Any]]:
    """Return all pending checkpoint requests awaiting a human response."""
    state = get_state_manager()
    handler = state.get_checkpoint_handler(campaign_id)
    if handler is None:
        raise HTTPException(
            status_code=404,
            detail=f"No checkpoint handler for campaign '{campaign_id}'.",
        )

    pending = handler.get_pending()
    return [req.model_dump(mode="json") for req in pending]


@router.post("/respond")
async def respond_to_checkpoint(
    campaign_id: str,
    body: CheckpointResponseBody,
) -> dict[str, Any]:
    """Submit a human response to a pending checkpoint.

    This resolves the pending checkpoint and unblocks the engine.
    """
    state = get_state_manager()
    handler = state.get_checkpoint_handler(campaign_id)
    if handler is None:
        raise HTTPException(
            status_code=404,
            detail=f"No checkpoint handler for campaign '{campaign_id}'.",
        )

    # Validate decision value
    valid_decisions = {d.value for d in CheckpointDecision}
    if body.decision not in valid_decisions:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid decision '{body.decision}'. "
                f"Valid values: {sorted(valid_decisions)}"
            ),
        )

    response = CheckpointResponse(
        checkpoint_id=body.checkpoint_id,
        decision=body.decision,
        comments=body.comments,
        modifications=body.modifications,
        timestamp=datetime.now(timezone.utc),
    )

    delivered = handler.submit_response(body.checkpoint_id, response)
    if not delivered:
        raise HTTPException(
            status_code=404,
            detail=f"No pending checkpoint with id '{body.checkpoint_id}'.",
        )

    return {
        "status": "delivered",
        "checkpoint_id": body.checkpoint_id,
        "decision": body.decision,
    }


@router.post("/intervene")
async def proactive_intervention(
    campaign_id: str,
    body: ProactiveInterventionBody,
) -> dict[str, Any]:
    """Proactively submit an intervention (not in response to a checkpoint).

    This creates an intervention event **and** pauses the engine to
    apply the directive.
    """
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    # Validate category
    valid_categories = {c.value for c in InterventionCategory}
    if body.category not in valid_categories:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid category '{body.category}'. "
                f"Valid values: {sorted(valid_categories)}"
            ),
        )

    # Record the proactive intervention
    intervention_record = state.record_proactive_intervention(
        campaign_id=campaign_id,
        category=body.category,
        description=body.description,
        directive=body.directive,
    )

    # Pause the engine so the directive can take effect
    paused = state.pause_campaign(campaign_id)

    # Broadcast the intervention to WebSocket clients
    await state.broadcast({
        "type": "proactive_intervention",
        "campaign_id": campaign_id,
        "data": intervention_record,
    })

    return {
        "status": "intervention_recorded",
        "campaign_id": campaign_id,
        "paused": paused,
        "intervention": intervention_record,
    }


@router.post("/pause")
async def pause_campaign(campaign_id: str) -> dict[str, Any]:
    """Pause a running campaign.

    The engine stops after the current iteration completes.
    """
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    if not campaign.running:
        raise HTTPException(
            status_code=409, detail="Campaign is not running."
        )

    paused = state.pause_campaign(campaign_id)
    if not paused:
        raise HTTPException(
            status_code=409, detail="Campaign could not be paused."
        )

    await state.broadcast({
        "type": "campaign_paused",
        "campaign_id": campaign_id,
    })

    return {"status": "paused", "campaign_id": campaign_id}


@router.post("/resume")
async def resume_campaign(campaign_id: str) -> dict[str, Any]:
    """Resume a paused campaign."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    resumed = state.resume_campaign(campaign_id)
    if not resumed:
        raise HTTPException(
            status_code=409, detail="Campaign is not paused."
        )

    await state.broadcast({
        "type": "campaign_resumed",
        "campaign_id": campaign_id,
    })

    return {"status": "resumed", "campaign_id": campaign_id}


@router.get("/history")
async def get_intervention_history(
    campaign_id: str,
) -> list[InterventionHistoryEntry]:
    """Return all past interventions and checkpoint responses."""
    state = get_state_manager()
    handler = state.get_checkpoint_handler(campaign_id)

    entries: list[InterventionHistoryEntry] = []

    # Include resolved checkpoints from the handler
    if handler is not None:
        for req, resp in handler.get_history():
            entries.append(InterventionHistoryEntry(
                checkpoint_id=req.checkpoint_id,
                event_type=req.event_type,
                summary=req.summary,
                decision=resp.decision,
                comments=resp.comments,
                request_timestamp=req.timestamp,
                response_timestamp=resp.timestamp,
            ))

    # Include proactive interventions from the state manager
    proactive = state.get_proactive_interventions(campaign_id)
    for intv in proactive:
        entries.append(InterventionHistoryEntry(
            checkpoint_id=intv.get("intervention_id", ""),
            event_type="proactive_intervention",
            summary=intv.get("description", ""),
            decision="applied",
            comments=intv.get("directive", ""),
            request_timestamp=intv.get("timestamp", datetime.now(timezone.utc)),
            response_timestamp=intv.get("timestamp", datetime.now(timezone.utc)),
        ))

    # Sort by request timestamp (newest first)
    entries.sort(key=lambda e: e.request_timestamp, reverse=True)
    return entries


@router.get("/stats")
async def get_intervention_stats(
    campaign_id: str,
) -> InterventionStatsResponse:
    """Return H_cap / H_run statistics for the campaign."""
    state = get_state_manager()
    artifacts = state.load_artifacts(campaign_id)

    if artifacts is None:
        return InterventionStatsResponse()

    interventions = artifacts.human_interventions
    total = len(interventions)

    from aurora.models.human import InterventionType

    h_cap = [
        i for i in interventions
        if i.intervention_type == InterventionType.CAPABILITY_BUILDING
    ]
    h_run = [
        i for i in interventions
        if i.intervention_type == InterventionType.PER_RUN
    ]
    h_cap_converted = [i for i in h_cap if i.converted_to_skill]

    category_counts: dict[str, int] = {}
    for intv in interventions:
        key = intv.category.value
        category_counts[key] = category_counts.get(key, 0) + 1

    return InterventionStatsResponse(
        total=total,
        h_cap=len(h_cap),
        h_run=len(h_run),
        h_cap_converted=len(h_cap_converted),
        h_cap_ratio=len(h_cap) / total if total > 0 else 0.0,
        h_run_ratio=len(h_run) / total if total > 0 else 0.0,
        categories=category_counts,
    )

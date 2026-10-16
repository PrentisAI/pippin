"""Campaign management endpoints.

Handles campaign CRUD, lifecycle control (start/stop), and
progress queries.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from pydantic import BaseModel as PydanticBaseModel

from aurora.api.schemas import (
    CampaignDetail,
    CampaignProgress,
    CampaignSummary,
)
from aurora.api.state import get_state_manager
from aurora.config import AutonomyMode, AuroraConfig


class AutonomyModeRequest(PydanticBaseModel):
    mode: AutonomyMode


class QuickCreateRequest(PydanticBaseModel):
    """Minimal request to create and start a campaign from a single prompt."""
    prompt: str
    auto_start: bool = True

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


@router.get("/", response_model=list[CampaignSummary])
async def list_campaigns() -> list[CampaignSummary]:
    """List available campaigns by scanning workspace directories."""
    state = get_state_manager()
    return state.scan_workspace()


@router.post("/", response_model=CampaignSummary, status_code=201)
async def create_campaign(config: AuroraConfig) -> CampaignSummary:
    """Create a new campaign from configuration."""
    state = get_state_manager()

    # Check for duplicate
    existing = state.get_campaign_state(config.campaign_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Campaign '{config.campaign_id}' already exists.",
        )

    campaign_state = state.register_campaign(config)

    return CampaignSummary(
        campaign_id=config.campaign_id,
        objective=config.objective,
        domain=config.domain,
        phase=campaign_state.phase.value,
        autonomy_mode=campaign_state.autonomy_mode.value,
        created_at=campaign_state.created_at,
        updated_at=campaign_state.updated_at,
    )


@router.post("/quick", status_code=201)
async def quick_create_campaign(body: QuickCreateRequest) -> dict[str, Any]:
    """Create and optionally start a campaign from a single prompt.

    Generates a campaign ID from the prompt, sets the prompt as objective,
    and starts the campaign immediately if ``auto_start`` is true.
    """
    state = get_state_manager()

    slug = hashlib.sha256(body.prompt.encode()).hexdigest()[:8]
    campaign_id = f"campaign_{slug}_{int(time.time()) % 100000}"

    config = AuroraConfig(
        campaign_id=campaign_id,
        objective=body.prompt,
    )

    campaign_state = state.register_campaign(config)

    result: dict[str, Any] = {
        "campaign_id": campaign_id,
        "objective": body.prompt,
        "phase": campaign_state.phase.value,
        "status": "created",
    }

    if body.auto_start:
        started = await state.start_campaign(campaign_id)
        if started:
            result["status"] = "started"

    return result


@router.get("/{campaign_id}", response_model=CampaignDetail)
async def get_campaign(campaign_id: str) -> CampaignDetail:
    """Get campaign status including current phase and progress."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)

    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    progress = state.get_progress(campaign_id)

    # Build a safe config summary (strip API keys)
    config_summary: dict[str, Any] = {
        "campaign_id": campaign.config.campaign_id,
        "objective": campaign.config.objective,
        "domain": campaign.config.domain,
        "max_followup_cycles": campaign.config.max_followup_cycles,
        "max_roundtable_rounds": campaign.config.max_roundtable_rounds,
        "num_roundtable_seats": campaign.config.num_roundtable_seats,
        "max_research_iterations": campaign.config.max_research_iterations,
        "use_real_tools": campaign.config.use_real_tools,
        "resource_limits": campaign.config.resource_limits.model_dump(),
    }

    return CampaignDetail(
        campaign_id=campaign.config.campaign_id,
        objective=campaign.config.objective,
        domain=campaign.config.domain,
        phase=campaign.phase.value,
        autonomy_mode=campaign.autonomy_mode.value,
        research_cycle=campaign.research_cycle,
        config=config_summary,
        progress=progress,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


@router.post("/{campaign_id}/start")
async def start_campaign(campaign_id: str) -> dict[str, Any]:
    """Start the research campaign as a background task."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)

    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    if campaign.running:
        raise HTTPException(
            status_code=409, detail="Campaign is already running."
        )

    started = await state.start_campaign(campaign_id)
    if not started:
        raise HTTPException(
            status_code=500, detail="Failed to start campaign."
        )

    return {
        "status": "started",
        "campaign_id": campaign_id,
        "phase": campaign.phase.value,
    }


@router.post("/{campaign_id}/stop")
async def stop_campaign(campaign_id: str) -> dict[str, Any]:
    """Stop a running campaign."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)

    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    if not campaign.running:
        raise HTTPException(
            status_code=409, detail="Campaign is not running."
        )

    stopped = await state.stop_campaign(campaign_id)
    if not stopped:
        raise HTTPException(
            status_code=500, detail="Failed to stop campaign."
        )

    return {
        "status": "stopped",
        "campaign_id": campaign_id,
        "phase": campaign.phase.value,
    }


@router.get("/{campaign_id}/config")
async def get_campaign_config(campaign_id: str) -> dict[str, Any]:
    """Get the campaign configuration (API keys redacted)."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)

    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    # Serialize config but strip sensitive fields
    config_dict = campaign.config.model_dump()
    # Redact API keys
    for provider in config_dict.get("llm_providers", []):
        if provider.get("api_key"):
            provider["api_key"] = "***"

    return config_dict


@router.get("/{campaign_id}/autonomy")
async def get_autonomy_mode(campaign_id: str) -> dict[str, Any]:
    """Get the current autonomy mode for a campaign."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return {
        "campaign_id": campaign_id,
        "mode": campaign.autonomy_mode.value,
    }


@router.put("/{campaign_id}/autonomy")
async def set_autonomy_mode(
    campaign_id: str, body: AutonomyModeRequest
) -> dict[str, Any]:
    """Change the autonomy mode for a running campaign."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    released = await state.set_autonomy_mode(campaign_id, body.mode)
    return {
        "campaign_id": campaign_id,
        "mode": body.mode.value,
        "checkpoints_released": released,
    }


@router.get("/{campaign_id}/progress", response_model=CampaignProgress)
async def get_campaign_progress(campaign_id: str) -> CampaignProgress:
    """Get pipeline phase progress for a campaign."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)

    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    progress = state.get_progress(campaign_id)
    if progress is None:
        return CampaignProgress(
            campaign_id=campaign_id,
            current_phase=campaign.phase.value,
        )

    return progress

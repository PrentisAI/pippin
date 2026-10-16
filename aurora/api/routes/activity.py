"""Activity feed, audit, hooks, agent and timeline endpoints.

Provides views into the campaign's event history, audit records,
hook firings, agent activity, and a timeline of phase transitions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from aurora.api.schemas import (
    ActivityEvent,
    AuditSummary,
    TimelineEntry,
)
from aurora.api.state import get_state_manager

router = APIRouter(
    prefix="/api/campaigns/{campaign_id}/activity",
    tags=["activity"],
)


@router.get("/events", response_model=list[ActivityEvent])
async def list_events(
    campaign_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[ActivityEvent]:
    """List recent events from the event bus log with pagination."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    return state.get_events(
        campaign_id=campaign_id, limit=limit, offset=offset
    )


@router.get("/audit", response_model=list[AuditSummary])
async def list_audit_records(
    campaign_id: str,
    outcome: str | None = Query(None, description="Filter by outcome"),
    trigger: str | None = Query(None, description="Filter by trigger"),
) -> list[AuditSummary]:
    """List audit records with optional filtering."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    artifacts = state.load_artifacts(campaign_id)
    if artifacts is None:
        return []

    results: list[AuditSummary] = []
    for record in artifacts.auditor_findings:
        if outcome and record.outcome.value != outcome:
            continue
        if trigger and record.trigger.value != trigger:
            continue
        results.append(AuditSummary(
            audit_id=record.audit_id,
            trigger=record.trigger.value,
            outcome=record.outcome.value,
            severity=record.severity,
            finding=record.finding,
            timestamp=record.timestamp,
        ))

    return results


@router.get("/hooks")
async def list_hook_results(campaign_id: str) -> list[dict[str, Any]]:
    """List hook firing results from the event log.

    Filters events whose name starts with ``hook_`` or contains
    audit-related keywords.
    """
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    # Extract hook-related events from the activity log
    all_events = state.get_events(campaign_id=campaign_id, limit=500)
    hook_events: list[dict[str, Any]] = []
    hook_keywords = {"hook_", "audit_", "anomaly_detected", "energy_computed"}

    for event in all_events:
        if any(event.name.startswith(kw) or event.name == kw for kw in hook_keywords):
            hook_events.append({
                "name": event.name,
                "data": event.data,
                "source": event.source,
                "timestamp": event.timestamp.isoformat(),
            })

    return hook_events


@router.get("/agents")
async def list_agent_activity(campaign_id: str) -> list[dict[str, Any]]:
    """List agent activity from the event log.

    Extracts phase transitions and agent-specific events to show
    which agents were active and what they did.
    """
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    all_events = state.get_events(campaign_id=campaign_id, limit=500)
    agent_events: list[dict[str, Any]] = []

    # Map event sources and names to agent identifiers
    agent_keywords = {
        "roundtable": "Roundtable",
        "screening": "Screener",
        "planning": "Planner",
        "researcher": "Researcher",
        "research_iteration": "Researcher",
        "analysis": "Analyzer",
        "writing": "Writer",
        "auditor": "Auditor",
        "engine": "Engine",
    }

    for event in all_events:
        agent_name = "Unknown"
        for keyword, name in agent_keywords.items():
            if keyword in event.source or keyword in event.name:
                agent_name = name
                break

        agent_events.append({
            "agent": agent_name,
            "event": event.name,
            "data": event.data,
            "timestamp": event.timestamp.isoformat(),
        })

    return agent_events


@router.get("/timeline", response_model=list[TimelineEntry])
async def get_timeline(campaign_id: str) -> list[TimelineEntry]:
    """Get a timeline view of the campaign.

    Returns phase transitions and key milestone events in
    chronological order.
    """
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    all_events = state.get_events(campaign_id=campaign_id, limit=1000)

    # Key event types that form the timeline
    timeline_events = {
        "campaign_started",
        "campaign_completed",
        "campaign_failed",
        "phase_started",
        "roundtable_complete",
        "screening_complete",
        "planning_complete",
        "research_loop_complete",
        "analysis_complete",
        "writing_complete",
        "experiment_started",
        "experiment_complete",
        "experiment_failed",
        "claim_added",
        "claim_promoted",
        "audit_recorded",
        "goal_updated",
        "human_intervention",
    }

    timeline: list[TimelineEntry] = []
    for event in all_events:
        if event.name not in timeline_events:
            continue

        # Build description from event data
        description = _build_timeline_description(event.name, event.data)
        phase = event.data.get("phase", "")

        timeline.append(TimelineEntry(
            timestamp=event.timestamp,
            event_type=event.name,
            phase=phase,
            description=description,
            metadata=event.data,
        ))

    # Sort chronologically (events come in reverse order from get_events)
    timeline.sort(key=lambda e: e.timestamp)
    return timeline


def _build_timeline_description(event_name: str, data: dict[str, Any]) -> str:
    """Build a human-readable description for a timeline event."""
    descriptions: dict[str, str] = {
        "campaign_started": "Research campaign started",
        "campaign_completed": "Research campaign completed successfully",
        "campaign_failed": f"Research campaign failed: {data.get('error', 'unknown')}",
        "phase_started": f"Phase '{data.get('phase', '?')}' started",
        "roundtable_complete": f"Roundtable produced proposal '{data.get('title', '?')}'",
        "screening_complete": (
            f"Idea screening {'passed' if data.get('selected') else 'rejected'} "
            f"(score: {data.get('score', '?')})"
        ),
        "planning_complete": f"Experiment plan created with {data.get('num_jobs', '?')} jobs",
        "research_loop_complete": (
            f"Research loop finished: {data.get('iterations', '?')} iterations"
        ),
        "analysis_complete": f"Analysis produced {data.get('num_actions', '?')} actions",
        "writing_complete": f"Manuscript draft v{data.get('version', '?')} written",
        "experiment_started": (
            f"Experiment '{data.get('job_id', '?')}' started "
            f"(skill: {data.get('skill', '?')})"
        ),
        "experiment_complete": (
            f"Experiment '{data.get('job_id', '?')}' completed "
            f"({data.get('observations', 0)} observations)"
        ),
        "experiment_failed": f"Experiment '{data.get('job_id', '?')}' failed",
        "claim_added": (
            f"New claim: {str(data.get('statement', ''))[:60]}"
        ),
        "claim_promoted": (
            f"Claim '{data.get('claim_id', '?')}' "
            f"{'accepted' if data.get('new_status') == 'accepted' else 'rejected'}"
        ),
        "audit_recorded": (
            f"Audit ({data.get('trigger', '?')}): "
            f"{data.get('outcome', '?')} [{data.get('severity', 'info')}]"
        ),
        "goal_updated": f"Research goal updated: {str(data.get('new_goal', ''))[:60]}",
        "human_intervention": (
            f"Human intervention ({data.get('category', '?')})"
        ),
    }
    return descriptions.get(event_name, event_name)

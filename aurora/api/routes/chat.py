"""Real-time chat endpoints for agentic researcher interaction.

Provides REST endpoints for:
* Sending a chat message to the running agentic researcher
* Retrieving chat history (messages + stream events)
* Checking whether the agent is actively processing
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aurora.api.state import get_state_manager

router = APIRouter(
    prefix="/api/campaigns/{campaign_id}/chat",
    tags=["chat"],
)


class ChatMessageBody(BaseModel):
    """Body for POST /chat/send."""
    text: str


class ApprovalBody(BaseModel):
    """Body for POST /chat/approve."""
    approval_id: str
    decision: str  # "approve" | "modify" | "reject"
    message: str = ""


@router.post("/send")
async def send_chat_message(
    campaign_id: str,
    body: ChatMessageBody,
) -> dict[str, Any]:
    """Send a message to the running researcher.

    If a Front Desk Agent is active, messages are routed through it.
    Otherwise, messages go directly to the agentic researcher via
    the chat handler (cooperative interrupt between tool calls).
    """
    state = get_state_manager()

    # Route through Front Desk Agent if available
    campaign = state.get_campaign_state(campaign_id)
    if campaign and campaign.engine and hasattr(campaign.engine, "_frontdesk") and campaign.engine._frontdesk:
        frontdesk = campaign.engine._frontdesk
        await frontdesk.handle_user_message(body.text)
        return {"status": "sent_to_frontdesk", "active": True}

    # Fall back to direct chat handler
    handler = state.get_chat_handler(campaign_id)
    if handler is None:
        raise HTTPException(
            status_code=404,
            detail=f"No chat handler for campaign '{campaign_id}'. Is it running in agentic mode?",
        )

    result = await handler.send_user_message(body.text)
    return result


@router.get("/history")
async def get_chat_history(
    campaign_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return recent chat history (user messages + agent stream events)."""
    state = get_state_manager()

    # Front Desk history takes priority
    campaign = state.get_campaign_state(campaign_id)
    if campaign and campaign.engine and hasattr(campaign.engine, "_frontdesk") and campaign.engine._frontdesk:
        return campaign.engine._frontdesk.get_history(limit=limit)

    handler = state.get_chat_handler(campaign_id)
    if handler is None:
        return []
    return handler.get_history(limit=limit)


@router.get("/status")
async def get_chat_status(
    campaign_id: str,
) -> dict[str, Any]:
    """Check whether the agentic researcher is actively processing."""
    state = get_state_manager()
    handler = state.get_chat_handler(campaign_id)
    campaign = state.get_campaign_state(campaign_id)

    has_frontdesk = bool(
        campaign and campaign.engine
        and hasattr(campaign.engine, "_frontdesk")
        and campaign.engine._frontdesk
    )

    result: dict[str, Any] = {
        "campaign_id": campaign_id,
        "has_chat_handler": handler is not None,
        "has_frontdesk": has_frontdesk,
        "agent_active": handler.is_active if handler else False,
        "campaign_running": campaign.running if campaign else False,
        "campaign_paused": campaign.paused if campaign else False,
        "pending_messages": handler.user_queue.qsize() if handler else 0,
    }

    if has_frontdesk:
        engine = campaign.engine
        frontdesk = engine._frontdesk
        result["frontdesk_status"] = {
            "phase": frontdesk.status.phase,
            "iteration": frontdesk.status.iteration,
            "max_iterations": frontdesk.status.max_iterations,
            "agent_status": frontdesk.status.agent_status,
            "supervisor_status": frontdesk.status.supervisor_status,
            "pending_escalations": frontdesk.status.pending_escalations,
            "verifier_score": frontdesk.status.verifier_score,
        }
        result["pending_approvals"] = list(engine._approval_queues.keys())
        result["pending_escalations"] = list(engine._escalation_queues.keys())

    return result


@router.post("/approve")
async def submit_approval(
    campaign_id: str,
    body: ApprovalBody,
) -> dict[str, Any]:
    """Submit human approval/modification/rejection for an agent's proposed plan.

    Used in the escalation negotiation loop: after the agent proposes
    a plan in response to an escalation or human instruction, the human
    reviews and submits their decision here.
    """
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if not campaign or not campaign.engine:
        raise HTTPException(status_code=404, detail="Campaign not found or not running")

    engine = campaign.engine
    queue = engine._approval_queues.get(body.approval_id)
    if queue is None:
        raise HTTPException(
            status_code=404,
            detail=f"No pending approval '{body.approval_id}'",
        )

    await queue.put({
        "decision": body.decision,
        "message": body.message,
    })
    return {
        "status": "delivered",
        "approval_id": body.approval_id,
        "decision": body.decision,
    }


@router.get("/pending-approvals")
async def get_pending_approvals(
    campaign_id: str,
) -> dict[str, Any]:
    """List pending approval requests waiting for human decision."""
    state = get_state_manager()
    campaign = state.get_campaign_state(campaign_id)
    if not campaign or not campaign.engine:
        return {"pending": []}

    return {
        "pending": list(campaign.engine._approval_queues.keys()),
        "escalations": list(campaign.engine._escalation_queues.keys()),
    }

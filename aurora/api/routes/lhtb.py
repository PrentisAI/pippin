"""LHTB benchmark routes — start runs, list runs, stream live events.

Supports two autonomy modes:
- **autonomous**: watchdog auto-injects corrective messages, no human involvement
- **supervised**: critical watchdog verdicts pause the agent and escalate to the
  human via WebSocket; the human responds through ``POST /runs/{run_id}/decide``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from aurora.lhtb.runner import LHTBRunner, StreamEvent, TrajectoryEntry, _safe_serialize
from aurora.lhtb.task_loader import LHTBTask, load_task
from aurora.lhtb.watchdog import WatchdogAgent
from aurora.lhtb.watchdog_cc import ClaudeCodeWatchdog
from aurora.api.persistence import Store

logger = logging.getLogger(__name__)

store = Store()

router = APIRouter(prefix="/api/lhtb", tags=["lhtb"])

# ---------------------------------------------------------------------------
# In-memory run registry
# ---------------------------------------------------------------------------

class PendingDecision:
    """A decision escalated to the human, blocking the agent until resolved."""

    def __init__(
        self,
        decision_id: str,
        source: str,
        context: str,
        detail: dict[str, Any],
        options: list[str],
    ) -> None:
        self.decision_id = decision_id
        self.source = source
        self.context = context
        self.detail = detail
        self.options = options
        self.created_at: float = time.time()
        self.event: asyncio.Event = asyncio.Event()
        self.response: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "source": self.source,
            "context": self.context,
            "detail": self.detail,
            "options": self.options,
            "created_at": self.created_at,
            "resolved": self.event.is_set(),
        }


class LHTBRunState:
    """Tracks a single LHTB run's live state."""

    def __init__(self, run_id: str, task_name: str, model: str, autonomy_mode: str = "autonomous", campaign_id: str = "") -> None:
        self.run_id = run_id
        self.campaign_id = campaign_id
        self.task_name = task_name
        self.model = model
        self.autonomy_mode = autonomy_mode
        self.status: str = "starting"  # starting | running | completed | error | awaiting_human
        self.phase: int = 0
        self.turn: int = 0
        self.reward: float = 0.0
        self.started_at: float = time.time()
        self.finished_at: float | None = None
        self.events: list[dict[str, Any]] = []
        self.subscribers: list[WebSocket] = []
        self.error: str | None = None
        self._task: asyncio.Task[Any] | None = None
        self.watchdog: WatchdogAgent | None = None
        self.watchdog_verdicts: list[dict[str, Any]] = []
        self.pending_decision: PendingDecision | None = None
        self.decision_history: list[dict[str, Any]] = []
        self.chat_history: list[dict[str, Any]] = []
        self.runner: Any = None

    async def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self.campaign_id:
            store.append_event(self.campaign_id, self.run_id, event)
        stale: list[WebSocket] = []
        payload = json.dumps(event, default=str)
        for ws in self.subscribers:
            try:
                await ws.send_text(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.subscribers.remove(ws)

    async def escalate_to_human(
        self,
        source: str,
        context: str,
        detail: dict[str, Any],
        options: list[str] | None = None,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Block until the human responds or timeout.

        Returns the human's decision dict, or a timeout-default.
        """
        if options is None:
            options = ["approve", "override", "abort"]

        decision_id = f"dec_{int(time.time())}_{len(self.decision_history)}"
        decision = PendingDecision(
            decision_id=decision_id,
            source=source,
            context=context,
            detail=detail,
            options=options,
        )
        self.pending_decision = decision
        prev_status = self.status
        self.status = "awaiting_human"

        await self.broadcast({
            "type": "lhtb_event",
            "run_id": self.run_id,
            "event_type": "escalation",
            "data": decision.to_dict(),
            "timestamp": time.time(),
        })

        logger.info("Run %s: escalated to human — %s: %s", self.run_id, source, context[:200])

        try:
            await asyncio.wait_for(decision.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Run %s: escalation %s timed out after %.0fs", self.run_id, decision_id, timeout)
            decision.response = {
                "action": "timeout",
                "message": f"No human response after {timeout:.0f}s — auto-continuing",
            }

        self.pending_decision = None
        self.status = prev_status if prev_status != "awaiting_human" else "running"

        response = decision.response or {"action": "timeout"}
        self.decision_history.append({
            "decision_id": decision_id,
            "source": source,
            "context": context,
            "response": response,
            "wait_time": time.time() - decision.created_at,
            "timestamp": time.time(),
        })

        await self.broadcast({
            "type": "lhtb_event",
            "run_id": self.run_id,
            "event_type": "escalation_resolved",
            "data": {
                "decision_id": decision_id,
                "response": response,
            },
            "timestamp": time.time(),
        })

        return response

    def to_summary(self) -> dict[str, Any]:
        summary = {
            "run_id": self.run_id,
            "task_name": self.task_name,
            "model": self.model,
            "autonomy_mode": self.autonomy_mode,
            "status": self.status,
            "phase": self.phase,
            "turn": self.turn,
            "reward": self.reward,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "event_count": len(self.events),
            "error": self.error,
            "watchdog_active": self.watchdog is not None,
            "watchdog_verdicts": len(self.watchdog_verdicts),
            "watchdog_cycles": self.watchdog._cycles if self.watchdog else 0,
            "l2_escalations": self.watchdog._l2_escalations if self.watchdog else 0,
            "pending_decision": self.pending_decision.to_dict() if self.pending_decision else None,
            "decisions_made": len(self.decision_history),
        }
        return summary


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class StartRunRequest(BaseModel):
    task_dir: str
    model: str = "deepseek/deepseek-v4-flash"
    driver: str = "claude-code"
    base_url: str = "https://openrouter.ai/api/v1"
    max_turns: int = 200
    timeout: float | None = None
    no_docker: bool = False
    autonomy_mode: str = "autonomous"  # "autonomous" | "supervised"
    watchdog_mode: str = "claude-code"  # "claude-code" | "api" | "disabled"
    watchdog_model: str = "deepseek/deepseek-v4-flash"
    watchdog_url: str = "https://openrouter.ai/api/v1"
    # Hook-based checkpoints (forced synchronous analysis at critical points)
    checkpoint_events: list[str] = ["phase_started", "verifier_result"]
    checkpoint_interval: int = 50  # also checkpoint every N tool calls
    # L2 escalation config (only used in "api" mode)
    l2_model: str = "deepseek/deepseek-v4-flash"
    l2_base_url: str = "https://openrouter.ai/api/v1"
    # Escalation timeout: how long to wait for human response (supervised mode)
    escalation_timeout: float = 600.0
    # User-configurable phase skipping
    skip_phases: list[str] | None = None  # None = use LHTB defaults
    # Custom agent prompts (empty = use defaults)
    roundtable_prompt: str = ""
    roundtable_seat_prompt: str = ""
    roundtable_seats: list[dict] | None = None  # [{name, model, prompt}]
    screening_prompt: str = ""
    planning_prompt: str = ""
    research_prompt: str = ""
    analysis_prompt: str = ""
    writing_prompt: str = ""


class StartCustomRunRequest(BaseModel):
    """Start a free-form experiment from user instruction (no predefined task)."""
    instruction: str
    name: str = ""
    model: str = "deepseek/deepseek-v4-flash"
    driver: str = "claude-code"
    base_url: str = "https://openrouter.ai/api/v1"
    max_turns: int = 200
    no_docker: bool = True
    autonomy_mode: str = "autonomous"
    watchdog_mode: str = "disabled"
    data_paths: list[str] = []
    docker_image: str = "python:3.11-slim"
    skip_phases: list[str] | None = None  # None = use complexity-based defaults


# ---------------------------------------------------------------------------
# Phase pipeline presets
# ---------------------------------------------------------------------------

PHASE_PRESET_QUICK = ["roundtable", "screening", "planning", "analysis", "writing"]
PHASE_PRESET_STANDARD = ["roundtable", "writing"]
PHASE_PRESET_FULL: list[str] = []

PHASE_PRESETS = {
    "quick": PHASE_PRESET_QUICK,
    "standard": PHASE_PRESET_STANDARD,
    "full": PHASE_PRESET_FULL,
}


def suggest_skip_phases(instruction: str, has_data: bool = False) -> list[str]:
    """Suggest skip_phases based on task complexity heuristics.

    Returns a default skip list. Users can override via explicit skip_phases.
    """
    text = instruction.lower()
    complexity_signals = [
        "research", "investigate", "compare", "benchmark", "systematic",
        "evaluate multiple", "literature", "survey", "hypothesis",
        "experiment design", "ablation", "comprehensive",
    ]
    complex_count = sum(1 for s in complexity_signals if s in text)

    if complex_count >= 2 or len(instruction) > 1000:
        return list(PHASE_PRESET_STANDARD)
    if has_data or complex_count == 1 or len(instruction) > 400:
        return list(PHASE_PRESET_STANDARD)
    return list(PHASE_PRESET_QUICK)


class DecisionRequest(BaseModel):
    """Human decision in response to an escalation."""
    action: str  # "approve" | "override" | "abort" | "instruct"
    message: str = ""


class RunSummary(BaseModel):
    run_id: str
    task_name: str
    model: str
    status: str
    phase: int
    turn: int
    reward: float
    started_at: float
    finished_at: float | None
    event_count: int
    error: str | None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/phase-presets")
async def get_phase_presets() -> dict[str, Any]:
    """Return available phase pipeline presets and all phase names."""
    all_phases = ["roundtable", "screening", "planning", "research_loop", "analysis", "writing"]
    return {
        "phases": all_phases,
        "presets": {
            name: {
                "skip": skip_list,
                "active": [p for p in all_phases if p not in skip_list],
            }
            for name, skip_list in PHASE_PRESETS.items()
        },
        "descriptions": {
            "roundtable": "Multi-agent brainstorming to form a research proposal",
            "screening": "Evaluate approach feasibility (novelty, feasibility, significance)",
            "planning": "Decompose task into a structured execution plan",
            "research_loop": "Agent executes the task (always included)",
            "analysis": "Analyze results, decide if follow-up is needed",
            "writing": "Generate a structured report",
        },
    }


@router.get("/tasks")
async def list_tasks(tasks_dir: str = "") -> list[dict[str, Any]]:
    """List available LHTB tasks from a directory."""
    if not tasks_dir:
        # Try common locations
        for candidate in [
            "/data/yanlin.fei/LHTB/tasks",
            "./tasks",
        ]:
            if Path(candidate).is_dir():
                tasks_dir = candidate
                break
    if not tasks_dir or not Path(tasks_dir).is_dir():
        return []

    results = []
    for entry in sorted(Path(tasks_dir).iterdir()):
        toml_path = entry / "task.toml"
        if entry.is_dir() and toml_path.exists():
            try:
                task = load_task(str(entry))
                results.append({
                    "name": task.name,
                    "slug": entry.name,
                    "path": str(entry),
                    "description": task.description,
                    "category": task.category,
                    "difficulty": task.difficulty,
                    "tags": task.tags,
                    "keywords": task.keywords,
                    "docker_image": task.docker_image or "",
                    "timeout_sec": task.agent_timeout_sec,
                    "artifacts": task.artifacts,
                    "expert_time_min": task.expert_time_estimate_min,
                })
            except Exception as exc:
                logger.warning("Failed to load task %s: %s", entry.name, exc)
    return results


# ---------------------------------------------------------------------------
# Campaign → Experiment unified hierarchy
# ---------------------------------------------------------------------------

class ExperimentProposal:
    """A pending experiment proposal awaiting user confirmation."""

    def __init__(
        self, proposal_id: str, kind: str, params: dict[str, Any],
        suggested_skip: list[str], summary: str,
    ) -> None:
        self.proposal_id = proposal_id
        self.kind = kind  # "predefined" | "custom"
        self.params = params
        self.suggested_skip = suggested_skip
        self.summary = summary
        self.created_at: float = time.time()
        self.confirmed: bool = False
        self.superseded: bool = False

    def to_dict(self) -> dict[str, Any]:
        all_phases = ["roundtable", "screening", "planning",
                      "research_loop", "analysis", "writing"]
        return {
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "params": self.params,
            "skip_phases": self.suggested_skip,
            "active_phases": [p for p in all_phases if p not in self.suggested_skip],
            "summary": self.summary,
            "created_at": self.created_at,
            "confirmed": self.confirmed,
            "superseded": self.superseded,
        }


class CampaignState:
    """A research campaign containing multiple experiments."""

    def __init__(self, campaign_id: str, name: str, objective: str = "") -> None:
        self.campaign_id = campaign_id
        self.name = name
        self.objective = objective
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
        self.experiments: dict[str, LHTBRunState] = {}
        self.chat_history: list[dict[str, Any]] = []
        self.frontdesk: Any = None
        self.proposals: dict[str, ExperimentProposal] = {}

    def to_summary(self) -> dict[str, Any]:
        exps = list(self.experiments.values())
        active = sum(1 for e in exps if e.status in ("running", "starting", "awaiting_human"))
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "objective": self.objective,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "experiment_count": len(exps),
            "active_experiments": active,
            "completed_experiments": len(exps) - active,
            "best_reward": max((e.reward for e in exps), default=0.0),
            "needs_attention": any(e.status == "awaiting_human" for e in exps),
        }


_campaigns: dict[str, CampaignState] = {}


def _ensure_campaign(campaign_id: str) -> CampaignState | None:
    """Get campaign from memory, restoring from DB if needed."""
    if campaign_id in _campaigns:
        return _campaigns[campaign_id]
    db_camp = store.get_campaign(campaign_id)
    if not db_camp:
        return None
    campaign = CampaignState(
        campaign_id=campaign_id,
        name=db_camp.get("name", ""),
        objective=db_camp.get("objective", ""),
    )
    campaign.created_at = db_camp.get("created_at", campaign.created_at)
    campaign.updated_at = db_camp.get("updated_at", campaign.updated_at)
    # Restore finished experiments as lightweight run states
    for db_exp in store.list_experiments(campaign_id):
        rs = LHTBRunState(
            run_id=db_exp["run_id"],
            task_name=db_exp.get("task_name", ""),
            model=db_exp.get("model", ""),
            autonomy_mode=db_exp.get("autonomy_mode", "autonomous"),
            campaign_id=campaign_id,
        )
        rs.status = db_exp.get("status", "completed")
        rs.reward = db_exp.get("reward", 0.0)
        rs.started_at = db_exp.get("started_at", 0.0)
        rs.finished_at = db_exp.get("finished_at")
        campaign.experiments[db_exp["run_id"]] = rs
    _campaigns[campaign_id] = campaign
    return campaign


def _campaign_sys_event(campaign: CampaignState, text: str) -> None:
    """Append a system event to campaign chat history."""
    campaign.chat_history.append({
        "role": "system",
        "text": text,
        "timestamp": time.time(),
    })


campaign_router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class CreateCampaignRequest(BaseModel):
    name: str
    objective: str = ""


class QuickCreateRequest(BaseModel):
    prompt: str
    auto_start: bool = True


class SetAutonomyRequest(BaseModel):
    autonomy_mode: str  # "autonomous" | "supervised"


# ---------------------------------------------------------------------------
# Campaign CRUD
# ---------------------------------------------------------------------------

@campaign_router.post("/quick")
async def quick_create_campaign(req: QuickCreateRequest) -> dict[str, Any]:
    """Create a campaign from a single prompt — like typing in Claude Code."""
    import hashlib
    slug = hashlib.sha256(req.prompt.encode()).hexdigest()[:8]
    campaign_id = f"c_{slug}_{int(time.time()) % 100000}"
    name = req.prompt[:60] + ("..." if len(req.prompt) > 60 else "")
    campaign = CampaignState(campaign_id=campaign_id, name=name, objective=req.prompt)
    _campaigns[campaign_id] = campaign
    _campaign_sys_event(campaign, "Project created")
    store.save_campaign(campaign_id, name, req.prompt, campaign.created_at, campaign.updated_at)
    return {
        "campaign_id": campaign_id,
        "name": name,
        "objective": req.prompt,
        "status": "created",
    }


@campaign_router.post("/")
async def create_campaign(req: CreateCampaignRequest) -> dict[str, Any]:
    campaign_id = f"c_{int(time.time())}"
    campaign = CampaignState(campaign_id=campaign_id, name=req.name, objective=req.objective)
    _campaigns[campaign_id] = campaign
    _campaign_sys_event(campaign, "Project created")
    store.save_campaign(campaign_id, req.name, req.objective, campaign.created_at, campaign.updated_at)
    return campaign.to_summary()


@campaign_router.get("/")
async def list_campaigns() -> list[dict[str, Any]]:
    live_ids = set(_campaigns.keys())
    results = [c.to_summary() for c in _campaigns.values()]
    for db_campaign in store.list_campaigns():
        cid = db_campaign["campaign_id"]
        if cid not in live_ids:
            db_exps = store.list_experiments(cid)
            active = sum(1 for e in db_exps if e["status"] in ("running", "starting", "awaiting_human"))
            results.append({
                "campaign_id": cid,
                "name": db_campaign["name"],
                "objective": db_campaign["objective"],
                "created_at": db_campaign["created_at"],
                "updated_at": db_campaign["updated_at"],
                "experiment_count": len(db_exps),
                "active_experiments": active,
                "completed_experiments": len(db_exps) - active,
                "best_reward": max((e["reward"] for e in db_exps), default=0.0),
                "needs_attention": False,
            })
    return sorted(results, key=lambda c: c["updated_at"], reverse=True)


@campaign_router.get("/{campaign_id}")
async def get_campaign(campaign_id: str) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    if campaign:
        summary = campaign.to_summary()
        summary["experiments"] = sorted(
            [e.to_summary() for e in campaign.experiments.values()],
            key=lambda e: e["started_at"],
            reverse=True,
        )
        return summary
    db_campaign = store.get_campaign(campaign_id)
    if not db_campaign:
        return {"error": "Campaign not found"}
    db_exps = store.list_experiments(campaign_id)
    exp_summaries = []
    for e in db_exps:
        exp_summaries.append({
            "run_id": e["run_id"],
            "task_name": e["task_name"],
            "model": e["model"],
            "autonomy_mode": e["autonomy_mode"],
            "status": e["status"],
            "phase": e["phase"],
            "turn": e["turn"],
            "reward": e["reward"],
            "started_at": e["started_at"],
            "finished_at": e["finished_at"],
            "event_count": len(store.load_events(campaign_id, e["run_id"])),
            "error": e["error"],
            "watchdog_active": False,
            "watchdog_verdicts": 0,
            "watchdog_cycles": 0,
            "l2_escalations": 0,
            "pending_decision": None,
            "decisions_made": 0,
        })
    active = sum(1 for e in db_exps if e["status"] in ("running", "starting", "awaiting_human"))
    return {
        "campaign_id": campaign_id,
        "name": db_campaign["name"],
        "objective": db_campaign["objective"],
        "created_at": db_campaign["created_at"],
        "updated_at": db_campaign["updated_at"],
        "experiment_count": len(db_exps),
        "active_experiments": active,
        "completed_experiments": len(db_exps) - active,
        "best_reward": max((e["reward"] for e in db_exps), default=0.0),
        "needs_attention": False,
        "experiments": sorted(exp_summaries, key=lambda e: e["started_at"], reverse=True),
    }


@campaign_router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: str) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    if campaign:
        for exp in campaign.experiments.values():
            if exp.pending_decision and not exp.pending_decision.event.is_set():
                exp.pending_decision.response = {"action": "abort", "message": "Campaign deleted"}
                exp.pending_decision.event.set()
            if exp._task and not exp._task.done():
                exp._task.cancel()
                exp.status = "stopped"
                exp.finished_at = time.time()
        del _campaigns[campaign_id]
    store.delete_campaign(campaign_id)
    return {"ok": True, "campaign_id": campaign_id}


# ---------------------------------------------------------------------------
# Experiment lifecycle (within campaigns)
# ---------------------------------------------------------------------------

@campaign_router.post("/{campaign_id}/experiments")
async def start_experiment(campaign_id: str, req: StartRunRequest) -> dict[str, Any]:
    """Start a new experiment within a campaign."""
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}

    task = load_task(req.task_dir)
    exp_id = f"{int(time.time())}_{task.name.split('/')[-1][:30]}"
    run_state = LHTBRunState(
        run_id=exp_id, task_name=task.name, model=req.model,
        autonomy_mode=req.autonomy_mode, campaign_id=campaign_id,
    )
    campaign.experiments[exp_id] = run_state
    campaign.updated_at = time.time()
    task_slug = task.name.split("/")[-1]
    _campaign_sys_event(campaign, f"{task_slug} started")
    store.save_experiment(
        run_id=exp_id, campaign_id=campaign_id, task_name=task.name,
        model=req.model, driver=req.driver, autonomy_mode=req.autonomy_mode,
        started_at=run_state.started_at,
        config={"task_dir": req.task_dir, "base_url": req.base_url, "max_turns": req.max_turns,
                "watchdog_mode": req.watchdog_mode, "no_docker": req.no_docker},
        watchdog_mode=req.watchdog_mode,
    )
    store.update_campaign_ts(campaign_id, campaign.updated_at)

    api_key = (
        os.environ.get("OPENROUTER_API_KEY", "")
        or os.environ.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not api_key:
        del campaign.experiments[exp_id]
        store.delete_experiment(exp_id)
        return {"error": "No API key found. Set OPENROUTER_API_KEY in .env"}

    run_dir = Path("runs") / exp_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if req.watchdog_mode != "disabled":
        task_prompt = getattr(task, "prompt", "") or getattr(task, "description", "") or task.name

        async def watchdog_interrupt(message: str) -> None:
            logger.warning("Watchdog interrupt for exp %s: %s", exp_id, message[:200])

            if run_state.autonomy_mode == "supervised":
                response = await run_state.escalate_to_human(
                    source="watchdog_interrupt",
                    context=message,
                    detail={
                        "watchdog_message": message,
                        "phase": run_state.phase,
                        "turn": run_state.turn,
                        "reward": run_state.reward,
                    },
                    options=["approve", "override", "abort"],
                    timeout=req.escalation_timeout,
                )
                action = response.get("action", "timeout")
                if action == "abort":
                    if run_state._task and not run_state._task.done():
                        run_state._task.cancel()
                    return
                if action == "override":
                    human_msg = response.get("message", "")
                    if human_msg:
                        await runner.inject_message(
                            f"[HUMAN OVERRIDE]\n{human_msg}\n"
                            f"Follow the human operator's instructions above."
                        )
                    return
                await runner.inject_message(
                    f"[WATCHDOG INTERRUPT — APPROVED BY HUMAN]\n{message}\n"
                    f"You MUST acknowledge this interrupt and adjust your approach."
                )
            else:
                await runner.inject_message(
                    f"[WATCHDOG INTERRUPT — STOP AND READ THIS]\n{message}\n"
                    f"You MUST acknowledge this interrupt and adjust your approach."
                )

            await run_state.broadcast({
                "type": "lhtb_event",
                "run_id": exp_id,
                "event_type": "watchdog_interrupt",
                "data": {"message": message},
                "timestamp": time.time(),
            })

        async def watchdog_broadcast(event: dict[str, Any]) -> None:
            await run_state.broadcast({
                "type": "lhtb_event",
                "run_id": exp_id,
                "event_type": event["event_type"],
                "data": event.get("data", {}),
                "timestamp": event.get("timestamp", time.time()),
            })

        if req.watchdog_mode == "claude-code":
            watchdog = ClaudeCodeWatchdog(
                run_dir=run_dir,
                task_description=task_prompt,
                model=req.watchdog_model,
                openrouter_api_key=api_key,
                interrupt_callback=watchdog_interrupt,
                broadcast_callback=watchdog_broadcast,
            )
        else:
            watchdog = WatchdogAgent(
                judge_base_url=req.watchdog_url,
                judge_model=req.watchdog_model,
                judge_api_key=api_key,
                task_description=task_prompt,
                interrupt_callback=watchdog_interrupt,
                broadcast_callback=watchdog_broadcast,
                l2_model=req.l2_model,
                l2_api_key=api_key,
                l2_base_url=req.l2_base_url,
            )
        run_state.watchdog = watchdog

    async def _run_experiment() -> None:
        """Run via the unified core engine with LHTB adapter."""
        from aurora.core.engine import ResearchEngine
        from aurora.core.lhtb_adapter import LHTBTaskAdapter
        from aurora.core.event_bridge import EventBridge
        from aurora.core.execution_backend import LocalBackend

        try:
            config = LHTBTaskAdapter.to_config(
                task,
                model=req.model,
                driver=req.driver,
                api_key=api_key,
                base_url=req.base_url,
                skip_phases=req.skip_phases,
                max_turns=req.max_turns,
            )
            prompt_overrides: dict[str, Any] = {}
            if req.roundtable_prompt:
                prompt_overrides["roundtable_system_prompt"] = req.roundtable_prompt
            if req.roundtable_seat_prompt:
                prompt_overrides["roundtable_seat_prompt"] = req.roundtable_seat_prompt
            if req.roundtable_seats:
                from aurora.config import RoundtableSeatConfig
                prompt_overrides["roundtable_seats"] = [
                    RoundtableSeatConfig(**s) for s in req.roundtable_seats
                ]
            if req.screening_prompt:
                prompt_overrides["screening_system_prompt"] = req.screening_prompt
            if req.planning_prompt:
                prompt_overrides["planning_system_prompt"] = req.planning_prompt
            if req.research_prompt:
                prompt_overrides["research_system_prompt"] = req.research_prompt
            if req.analysis_prompt:
                prompt_overrides["analysis_system_prompt"] = req.analysis_prompt
            if req.writing_prompt:
                prompt_overrides["writing_system_prompt"] = req.writing_prompt
            if req.timeout:
                prompt_overrides["terminal_timeout_sec"] = req.timeout
            if prompt_overrides:
                config = config.model_copy(update=prompt_overrides)

            proposal = LHTBTaskAdapter.to_proposal(task)
            # Engine creates DockerBackend from config automatically.
            # Only pass explicit LocalBackend when Docker is disabled.
            backend = LocalBackend(str(task.task_dir / "environment")) if req.no_docker else None

            engine = ResearchEngine(
                config,
                initial_proposal=proposal,
                execution_backend=backend,
                terminal_system_prompt=LHTBTaskAdapter.build_system_prompt(task),
                terminal_task_instruction=task.instruction,
            )
            run_state.runner = engine

            bridge = EventBridge(
                engine.event_bus,
                exp_id,
                broadcast=run_state.broadcast,
                on_event_hook=(
                    run_state.watchdog.on_event if run_state.watchdog else None
                ),
            )

            if run_state.watchdog:
                engine.set_watchdog(run_state.watchdog)

            if backend is not None:
                await backend.setup()

            # Materialize distilled skills into workspace
            try:
                from aurora.skills.store import SkillStore
                from aurora.skills.materializer import SkillMaterializer
                from aurora.api.routes.skills import get_skill_store
                workspace_root = Path(getattr(backend, 'workspace_dir', '.'))
                materializer = SkillMaterializer(get_skill_store(), workspace_root)
                materialized = materializer.materialize_for_experiment(exp_id, campaign_id=campaign_id)
                if materialized:
                    logger.info("Materialized %d skills for %s", len(materialized), exp_id)
                run_state.skill_materializer = materializer
            except Exception:
                logger.debug("Skill materialization skipped", exc_info=True)

            await run_state.broadcast({
                "type": "lhtb_event",
                "run_id": exp_id,
                "event_type": "docker_started",
                "data": {"container_id": getattr(backend, 'container_id', None)},
                "timestamp": time.time(),
            })

            await run_state.broadcast({
                "type": "lhtb_event",
                "run_id": exp_id,
                "event_type": "pipeline_config",
                "data": {
                    "skip_phases": config.skip_phases,
                    "active_phases": [
                        p for p in ["roundtable", "screening", "planning",
                                    "research_loop", "analysis", "writing"]
                        if p not in config.skip_phases
                    ],
                },
                "timestamp": time.time(),
            })

            run_state.status = "running"
            result = await engine.run_campaign()

            final_reward = 0.0
            total_turns = 0
            for it in result.iterations:
                if it.job_result:
                    r = it.job_result.metrics.get("reward", 0)
                    if r > final_reward:
                        final_reward = r
                    total_turns += int(it.job_result.metrics.get("turns", 0))

            run_state.status = result.status
            run_state.reward = final_reward
            run_state.finished_at = time.time()
            campaign.updated_at = time.time()
            store.update_experiment(
                exp_id, status=result.status, reward=final_reward,
                turn=total_turns, finished_at=run_state.finished_at,
            )
            store.update_campaign_ts(campaign_id, campaign.updated_at)
            await run_state.broadcast({
                "type": "lhtb_event",
                "run_id": exp_id,
                "event_type": "run_finished",
                "data": {
                    "status": result.status,
                    "final_reward": final_reward,
                    "total_turns": total_turns,
                    "wall_time": result.total_wall_time_seconds,
                },
                "timestamp": time.time(),
            })
            task_slug = run_state.task_name.split("/")[-1]
            reward_pct = f"{final_reward * 100:.0f}%"
            _campaign_sys_event(campaign, f"{task_slug} completed · reward: {reward_pct}")

        except Exception as exc:
            logger.exception("Experiment %s failed", exp_id)
            run_state.status = "error"
            run_state.error = str(exc)
            run_state.finished_at = time.time()
            campaign.updated_at = time.time()
            store.update_experiment(
                exp_id, status="error", error=str(exc),
                finished_at=run_state.finished_at,
            )
            store.update_campaign_ts(campaign_id, campaign.updated_at)
            await run_state.broadcast({
                "type": "lhtb_event",
                "run_id": exp_id,
                "event_type": "run_error",
                "data": {"error": str(exc)},
                "timestamp": time.time(),
            })
            task_slug = run_state.task_name.split("/")[-1]
            _campaign_sys_event(campaign, f"{task_slug} error · {str(exc)[:80]}")
        finally:
            # Extract workspace before teardown (Docker containers are destroyed)
            try:
                ws_dest = store._data_dir / "workspaces" / exp_id
                if hasattr(backend, 'extract_workspace'):
                    await backend.extract_workspace(ws_dest)
                    logger.info("Workspace extracted to %s", ws_dest)
            except Exception:
                logger.debug("Workspace extraction failed", exc_info=True)
            # Also link engine's extracted workspace if ours is empty
            if not ws_dest.is_dir() or not any(ws_dest.iterdir()):
                try:
                    engine_ws = getattr(engine, '_workspace', None)
                    if engine_ws:
                        engine_extracted = engine_ws.campaign_dir / "workspace"
                        if engine_extracted.is_dir() and any(engine_extracted.iterdir()):
                            import shutil
                            ws_dest.mkdir(parents=True, exist_ok=True)
                            shutil.copytree(engine_extracted, ws_dest, dirs_exist_ok=True)
                            logger.info("Copied engine workspace to %s", ws_dest)
                except Exception:
                    logger.debug("Engine workspace copy failed", exc_info=True)
            try:
                if backend is not None:
                    await backend.teardown()
            except Exception:
                pass
            if hasattr(run_state, 'skill_materializer') and run_state.skill_materializer:
                try:
                    run_state.skill_materializer.cleanup()
                except Exception:
                    pass
            if run_state.watchdog:
                try:
                    await run_state.watchdog.stop()
                except Exception:
                    pass
            if run_state.watchdog and hasattr(run_state.watchdog, "verdicts"):
                run_state.watchdog_verdicts = [
                    {
                        "level": v.level, "reason": v.reason,
                        "suggestion": v.suggestion, "confidence": v.confidence,
                        "source": v.source, "timestamp": v.timestamp,
                    }
                    for v in run_state.watchdog.verdicts
                ]
            # Save session_id for resume
            sid = getattr(engine, '_session_id', None)
            if sid:
                try:
                    db = store.get_experiment(exp_id)
                    if db:
                        cfg = json.loads(db.get("config_json", "{}"))
                        cfg["session_id"] = sid
                        store.update_experiment(exp_id, config_json=json.dumps(cfg))
                except Exception:
                    logger.debug("Failed to save session_id", exc_info=True)

    run_state._task = asyncio.create_task(_run_experiment())
    effective_skip = req.skip_phases if req.skip_phases is not None else ["roundtable", "writing"]
    return {
        **run_state.to_summary(),
        "experiment_id": exp_id,
        "skip_phases": effective_skip,
        "active_phases": [
            p for p in ["roundtable", "screening", "planning",
                        "research_loop", "analysis", "writing"]
            if p not in effective_skip
        ],
    }


@campaign_router.post("/{campaign_id}/experiments/custom")
async def start_custom_experiment(campaign_id: str, req: StartCustomRunRequest) -> dict[str, Any]:
    """Start a free-form experiment routed through the core Engine pipeline."""
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}

    exp_name = req.name or req.instruction[:30].strip().replace(" ", "-").lower()
    exp_name = "".join(c for c in exp_name if c.isalnum() or c in "-_")[:30] or "custom"
    exp_id = f"{int(time.time())}_{exp_name}"

    run_state = LHTBRunState(
        run_id=exp_id, task_name=exp_name, model=req.model,
        autonomy_mode=req.autonomy_mode, campaign_id=campaign_id,
    )
    campaign.experiments[exp_id] = run_state
    campaign.updated_at = time.time()
    _campaign_sys_event(campaign, f"{exp_name} started")

    effective_skip = (
        req.skip_phases if req.skip_phases is not None
        else suggest_skip_phases(req.instruction, has_data=bool(req.data_paths))
    )

    store.save_experiment(
        run_id=exp_id, campaign_id=campaign_id, task_name=exp_name,
        model=req.model, driver=req.driver, autonomy_mode=req.autonomy_mode,
        started_at=run_state.started_at,
        config={"instruction": req.instruction, "base_url": req.base_url,
                "max_turns": req.max_turns, "no_docker": req.no_docker,
                "data_paths": req.data_paths, "skip_phases": effective_skip},
        watchdog_mode=req.watchdog_mode,
    )
    store.update_campaign_ts(campaign_id, campaign.updated_at)

    api_key = (
        os.environ.get("OPENROUTER_API_KEY", "")
        or os.environ.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not api_key:
        del campaign.experiments[exp_id]
        store.delete_experiment(exp_id)
        return {"error": "No API key found. Set OPENROUTER_API_KEY in .env"}

    run_dir = Path("runs") / exp_id
    run_dir.mkdir(parents=True, exist_ok=True)

    async def _run_custom():
        from aurora.core.engine import ResearchEngine
        from aurora.core.event_bridge import EventBridge
        from aurora.core.execution_backend import LocalBackend
        from aurora.config import LLMModelAssignment, LLMProviderConfig, AuroraConfig
        from aurora.models.roundtable import RoundtableProposal
        from datetime import datetime, timezone
        import uuid

        try:
            effective_model = req.model or "deepseek/deepseek-v4-flash"
            effective_key = api_key

            data_context = ""
            if req.data_paths:
                data_context = "\n\n## Data/Code Locations\n" + "\n".join(
                    f"- `{p}`" for p in req.data_paths
                )

            system_prompt = f"""You are an expert researcher and engineer. \
The user has given you an instruction to carry out. Work autonomously to \
complete the task, exploring the environment and producing results.

## Available Tools
- **shell**: Run any bash command
- **read_file**: Read file contents
- **write_file**: Create or overwrite a file
- **edit_file**: Search-and-replace edit
- **list_files**: List directory contents
- **search_files**: Grep for patterns
- **task_complete**: Signal you're done

## Guidelines
- Read and explore before acting.
- Run scripts/commands to verify your work.
- When finished, call task_complete with a summary of what you did.
{data_context}"""

            config = AuroraConfig(
                campaign_id=f"custom_{exp_id}",
                objective=req.instruction[:2000],
                domain="custom",
                skip_phases=effective_skip,
                use_terminal_iteration=True,
                terminal_driver=req.driver,
                terminal_model=effective_model,
                terminal_api_key=effective_key,
                terminal_base_url=req.base_url,
                terminal_max_turns=req.max_turns,
                max_research_iterations=1,
                use_docker=not req.no_docker,
                docker_image=req.docker_image,
                initial_autonomy_mode="autonomous",
                llm_providers=[
                    LLMProviderConfig(
                        provider="openrouter",
                        api_key=effective_key,
                        base_url=req.base_url,
                    ),
                ],
                llm_assignment=LLMModelAssignment(
                    idea_screening=effective_model,
                    researcher=effective_model,
                    auditor_triage=effective_model,
                    auditor_deep=effective_model,
                    analyzer=effective_model,
                    roundtable_chair=effective_model,
                    roundtable_seat=[effective_model],
                    writer=effective_model,
                ),
            )

            proposal = RoundtableProposal(
                proposal_id=f"custom_{uuid.uuid4().hex[:6]}",
                title=exp_name,
                description=req.instruction[:500],
                research_questions=[req.instruction[:200]],
                proposed_methods=["Autonomous agent execution"],
                expected_outcomes=["Task completion"],
                selected=True,
                created_at=datetime.now(timezone.utc),
            )

            backend = LocalBackend(str(run_dir)) if req.no_docker else None

            engine = ResearchEngine(
                config,
                initial_proposal=proposal,
                execution_backend=backend,
                terminal_system_prompt=system_prompt,
                terminal_task_instruction=req.instruction,
            )
            run_state.runner = engine

            bridge = EventBridge(
                engine.event_bus,
                exp_id,
                broadcast=run_state.broadcast,
            )

            if backend is not None:
                await backend.setup()

            await run_state.broadcast({
                "type": "lhtb_event",
                "run_id": exp_id,
                "event_type": "pipeline_config",
                "data": {
                    "skip_phases": effective_skip,
                    "active_phases": [
                        p for p in ["roundtable", "screening", "planning",
                                    "research_loop", "analysis", "writing"]
                        if p not in effective_skip
                    ],
                },
                "timestamp": time.time(),
            })

            run_state.status = "running"
            result = await engine.run_campaign()

            run_state.status = result.status
            run_state.finished_at = time.time()
            campaign.updated_at = time.time()
            store.update_experiment(
                exp_id, status=result.status,
                finished_at=run_state.finished_at,
            )
            store.update_campaign_ts(campaign_id, campaign.updated_at)
            await run_state.broadcast({
                "type": "lhtb_event",
                "run_id": exp_id,
                "event_type": "run_finished",
                "data": {
                    "status": result.status,
                    "wall_time": result.total_wall_time_seconds,
                    "phases_run": [
                        p for p in ["roundtable", "screening", "planning",
                                    "research_loop", "analysis", "writing"]
                        if p not in effective_skip
                    ],
                },
                "timestamp": time.time(),
            })
            _campaign_sys_event(campaign, f"{exp_name} {result.status}")

        except Exception as exc:
            logger.exception("Custom experiment %s failed", exp_id)
            run_state.status = "error"
            run_state.error = str(exc)
            run_state.finished_at = time.time()
            campaign.updated_at = time.time()
            store.update_experiment(
                exp_id, status="error", error=str(exc),
                finished_at=run_state.finished_at,
            )
            store.update_campaign_ts(campaign_id, campaign.updated_at)
            await run_state.broadcast({
                "type": "lhtb_event",
                "run_id": exp_id,
                "event_type": "run_error",
                "data": {"error": str(exc)},
                "timestamp": time.time(),
            })
            _campaign_sys_event(campaign, f"{exp_name} error · {str(exc)[:80]}")
        finally:
            try:
                ws_dest = store._data_dir / "workspaces" / exp_id
                if backend is not None and hasattr(backend, 'extract_workspace'):
                    await backend.extract_workspace(ws_dest)
            except Exception:
                logger.debug("Workspace extraction failed", exc_info=True)
            try:
                if backend is not None:
                    await backend.teardown()
            except Exception:
                pass

    run_state._task = asyncio.create_task(_run_custom())
    return {
        **run_state.to_summary(),
        "experiment_id": exp_id,
        "skip_phases": effective_skip,
        "active_phases": [
            p for p in ["roundtable", "screening", "planning",
                        "research_loop", "analysis", "writing"]
            if p not in effective_skip
        ],
    }


@campaign_router.get("/{campaign_id}/experiments/{exp_id}")
async def get_experiment(campaign_id: str, exp_id: str) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    if campaign:
        run_state = campaign.experiments.get(exp_id)
        if run_state:
            summary = run_state.to_summary()
            summary["campaign_id"] = campaign_id
            events = run_state.events[-200:]
            if not events:
                events = store.load_events(campaign_id, exp_id)[-200:]
            summary["events"] = events
            summary["watchdog_verdict_details"] = run_state.watchdog_verdicts
            summary["decision_history"] = run_state.decision_history
            if run_state.watchdog is not None:
                summary["watchdog_summary"] = run_state.watchdog.running_summary
                summary["watchdog_trajectory"] = [
                    {"timestamp": t.timestamp, "event_type": t.event_type, "data": t.data}
                    for t in run_state.watchdog.trajectory[-50:]
                ]
            return summary
    db_exp = store.get_experiment(exp_id)
    if not db_exp:
        return {"error": "Experiment not found"}
    events = store.load_events(campaign_id, exp_id)
    return {
        "run_id": db_exp["run_id"],
        "campaign_id": campaign_id,
        "task_name": db_exp["task_name"],
        "model": db_exp["model"],
        "autonomy_mode": db_exp["autonomy_mode"],
        "status": db_exp["status"],
        "phase": db_exp["phase"],
        "turn": db_exp["turn"],
        "reward": db_exp["reward"],
        "started_at": db_exp["started_at"],
        "finished_at": db_exp["finished_at"],
        "event_count": len(events),
        "error": db_exp["error"],
        "watchdog_active": False,
        "watchdog_verdicts": 0,
        "watchdog_cycles": 0,
        "l2_escalations": 0,
        "pending_decision": None,
        "decisions_made": 0,
        "events": events[-200:],
        "watchdog_verdict_details": [],
        "decision_history": [],
    }


@campaign_router.delete("/{campaign_id}/experiments/{exp_id}")
async def stop_experiment(campaign_id: str, exp_id: str) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}
    run_state = campaign.experiments.get(exp_id)
    if not run_state:
        return {"error": "Experiment not found"}
    if run_state.pending_decision and not run_state.pending_decision.event.is_set():
        run_state.pending_decision.response = {"action": "abort", "message": "Stopped by user"}
        run_state.pending_decision.event.set()
    if run_state.watchdog:
        try:
            await run_state.watchdog.stop()
        except Exception:
            logger.exception("Failed to stop watchdog for %s", exp_id)
    if run_state._task and not run_state._task.done():
        run_state._task.cancel()
        run_state.status = "stopped"
        run_state.finished_at = time.time()
        store.update_experiment(exp_id, status="stopped", finished_at=run_state.finished_at)
        task_slug = run_state.task_name.split("/")[-1]
        _campaign_sys_event(campaign, f"{task_slug} stopped by user")
    campaign.updated_at = time.time()
    store.update_campaign_ts(campaign_id, campaign.updated_at)
    return run_state.to_summary()


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/decide")
async def submit_decision(campaign_id: str, exp_id: str, req: DecisionRequest) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}
    run_state = campaign.experiments.get(exp_id)
    if not run_state:
        return {"error": "Experiment not found"}
    if not run_state.pending_decision:
        return {"error": "No pending decision"}

    decision = run_state.pending_decision
    decision.response = {"action": req.action, "message": req.message}
    decision.event.set()
    logger.info(
        "Exp %s: human decided '%s' for %s (waited %.1fs)",
        exp_id, req.action, decision.decision_id,
        time.time() - decision.created_at,
    )
    return {"ok": True, "decision_id": decision.decision_id, "action": req.action}


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/test-escalate")
async def test_escalate(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """DEV ONLY: Trigger an escalation on a running supervised experiment."""
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}
    run_state = campaign.experiments.get(exp_id)
    if not run_state:
        return {"error": "Experiment not found"}
    if run_state.status != "running":
        return {"error": f"Experiment is {run_state.status}, not running"}

    async def _do_escalate() -> None:
        response = await run_state.escalate_to_human(
            source="test_escalation",
            context="Test escalation: Human review requested for agent behavior",
            detail={"test": True, "turn": run_state.turn},
            options=["approve", "override", "abort"],
            timeout=120,
        )
        logger.info("Test escalation resolved: %s", response)

    asyncio.create_task(_do_escalate())
    await asyncio.sleep(0.3)
    return {
        "ok": True,
        "pending_decision": run_state.pending_decision.to_dict() if run_state.pending_decision else None,
    }


@campaign_router.put("/{campaign_id}/experiments/{exp_id}/autonomy")
async def set_experiment_autonomy(campaign_id: str, exp_id: str, req: SetAutonomyRequest) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}
    run_state = campaign.experiments.get(exp_id)
    if not run_state:
        return {"error": "Experiment not found"}
    if req.autonomy_mode not in ("autonomous", "supervised"):
        return {"error": "Invalid mode"}

    old_mode = run_state.autonomy_mode
    run_state.autonomy_mode = req.autonomy_mode
    store.update_experiment(exp_id, autonomy_mode=req.autonomy_mode)

    if req.autonomy_mode == "autonomous" and run_state.pending_decision and not run_state.pending_decision.event.is_set():
        run_state.pending_decision.response = {
            "action": "approve",
            "message": "Auto-resolved: switched to autonomous mode",
        }
        run_state.pending_decision.event.set()

    await run_state.broadcast({
        "type": "lhtb_event",
        "run_id": exp_id,
        "event_type": "autonomy_changed",
        "data": {"old_mode": old_mode, "new_mode": req.autonomy_mode},
        "timestamp": time.time(),
    })
    return {"ok": True, "autonomy_mode": req.autonomy_mode, "previous_mode": old_mode}


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/rerun")
async def rerun_experiment(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """Re-run a finished experiment with the same config."""
    db_exp = store.get_experiment(exp_id)
    if not db_exp:
        return {"error": "Experiment not found"}
    config = json.loads(db_exp.get("config_json", "{}"))
    task_dir = config.get("task_dir", "")
    if not task_dir:
        return {"error": "Original experiment has no task_dir in config — cannot re-run"}
    req = StartRunRequest(
        task_dir=task_dir,
        model=db_exp["model"],
        driver=db_exp.get("driver", "claude-code"),
        base_url=config.get("base_url", "https://openrouter.ai/api/v1"),
        max_turns=config.get("max_turns", 200),
        no_docker=config.get("no_docker", False),
        autonomy_mode=db_exp.get("autonomy_mode", "autonomous"),
        watchdog_mode=db_exp.get("watchdog_mode", "claude-code"),
    )
    return await start_experiment(campaign_id, req)


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/resume")
async def resume_experiment(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """Resume a stopped/errored experiment using Claude Code --resume."""
    db_exp = store.get_experiment(exp_id)
    if not db_exp:
        return {"error": "Experiment not found"}
    if db_exp["status"] in ("running", "starting"):
        return {"error": "Experiment is still running"}
    config = json.loads(db_exp.get("config_json", "{}"))
    session_id = config.get("session_id", "")
    if not session_id:
        return {"error": "No session_id saved — cannot resume (use Re-run instead)"}
    task_dir = config.get("task_dir", "")
    if not task_dir:
        return {"error": "No task_dir in config"}

    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}

    task = load_task(task_dir)
    new_exp_id = f"{int(time.time())}_{task.name.split('/')[-1][:30]}_resume"
    run_state = LHTBRunState(
        run_id=new_exp_id, task_name=task.name, model=db_exp["model"],
        autonomy_mode=db_exp.get("autonomy_mode", "autonomous"),
        campaign_id=campaign_id,
    )
    campaign.experiments[new_exp_id] = run_state
    campaign.updated_at = time.time()

    store.save_experiment(
        run_id=new_exp_id, campaign_id=campaign_id, task_name=task.name,
        model=db_exp["model"], driver=db_exp.get("driver", "claude-code"),
        autonomy_mode=db_exp.get("autonomy_mode", "autonomous"),
        started_at=run_state.started_at,
        config={**config, "resumed_from": exp_id},
        watchdog_mode=db_exp.get("watchdog_mode", "claude-code"),
    )
    store.update_campaign_ts(campaign_id, campaign.updated_at)

    api_key = (
        os.environ.get("OPENROUTER_API_KEY", "")
        or os.environ.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not api_key:
        del campaign.experiments[new_exp_id]
        store.delete_experiment(new_exp_id)
        return {"error": "No API key found. Set OPENROUTER_API_KEY in .env"}

    workspace_dir = store._data_dir / "workspaces" / new_exp_id
    prev_workspace = store._data_dir / "workspaces" / exp_id

    # Copy previous workspace to new experiment
    if prev_workspace.exists():
        import shutil
        shutil.copytree(str(prev_workspace), str(workspace_dir), dirs_exist_ok=True)

    run_dir = Path("runs") / new_exp_id
    run_dir.mkdir(parents=True, exist_ok=True)

    async def stream_callback(event: StreamEvent) -> None:
        d = event.data
        t = event.event_type
        ws_event: dict[str, Any] = {
            "type": "lhtb_event", "run_id": new_exp_id,
            "event_type": t, "data": d, "timestamp": time.time(),
        }
        if t == "phase_started":
            run_state.phase = d.get("phase", 0)
            run_state.status = "running"
            store.update_experiment(new_exp_id, phase=run_state.phase, status="running")
        elif t == "tool_call_start":
            run_state.turn = d.get("turn", run_state.turn)
        elif t == "verifier_result":
            run_state.reward = max(run_state.reward, d.get("reward", 0))
            store.update_experiment(new_exp_id, reward=run_state.reward)
        await run_state.broadcast(ws_event)

    runner = LHTBRunner(
        task,
        model=db_exp["model"],
        api_key=api_key,
        base_url=config.get("base_url", "https://openrouter.ai/api/v1"),
        driver=db_exp.get("driver", "claude-code"),
        use_docker=not config.get("no_docker", False),
        max_turns=config.get("max_turns", 200),
        stream_callback=stream_callback,
        workspace_dir=workspace_dir,
    )
    runner._resume_session_id = session_id

    async def _run_task() -> None:
        try:
            run_state.status = "running"
            # If Docker is used, restore workspace into the container after it starts
            result = await runner.run()
            run_state.status = result.status
            run_state.reward = result.final_reward
            run_state.finished_at = time.time()
            store.update_experiment(
                new_exp_id, status=result.status, reward=result.final_reward,
                turn=result.total_turns, finished_at=run_state.finished_at,
            )
            await run_state.broadcast({
                "type": "lhtb_event", "run_id": new_exp_id,
                "event_type": "run_finished",
                "data": {"status": result.status, "final_reward": result.final_reward,
                         "total_turns": result.total_turns, "wall_time": result.wall_time_seconds},
                "timestamp": time.time(),
            })
        except Exception as exc:
            logger.exception("Resume experiment %s failed", new_exp_id)
            run_state.status = "error"
            run_state.error = str(exc)
            run_state.finished_at = time.time()
            store.update_experiment(new_exp_id, status="error", error=str(exc), finished_at=run_state.finished_at)
            await run_state.broadcast({
                "type": "lhtb_event", "run_id": new_exp_id,
                "event_type": "run_error", "data": {"error": str(exc)}, "timestamp": time.time(),
            })
        finally:
            if runner.session_id:
                db = store.get_experiment(new_exp_id)
                if db:
                    cfg = json.loads(db.get("config_json", "{}"))
                    cfg["session_id"] = runner.session_id
                    store.update_experiment(new_exp_id, config_json=json.dumps(cfg))

    run_state._task = asyncio.create_task(_run_task())
    return run_state.to_summary()


# ---------------------------------------------------------------------------
# WebSocket — live stream for an experiment
# ---------------------------------------------------------------------------

@campaign_router.websocket("/{campaign_id}/experiments/{exp_id}/ws")
async def experiment_websocket(websocket: WebSocket, campaign_id: str, exp_id: str) -> None:
    campaign = _ensure_campaign(campaign_id)
    run_state = campaign.experiments.get(exp_id) if campaign else None

    if not run_state and not store.get_experiment(exp_id):
        await websocket.close(code=4004, reason="Experiment not found")
        return

    await websocket.accept()

    if run_state:
        run_state.subscribers.append(websocket)

    history = (run_state.events if run_state and run_state.events
               else store.load_events(campaign_id, exp_id))
    for event in history:
        try:
            await websocket.send_text(json.dumps(event, default=str))
        except Exception:
            break

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if run_state and websocket in run_state.subscribers:
            run_state.subscribers.remove(websocket)


# ---------------------------------------------------------------------------
# Campaign-level chat (Project Chat)
# ---------------------------------------------------------------------------

class CampaignChatBody(BaseModel):
    text: str


class CampaignApprovalBody(BaseModel):
    approval_id: str
    decision: str  # "approve" | "modify" | "reject"
    message: str = ""



# _FRONTDESK_SYSTEM and _FRONTDESK_TOOLS removed — system prompt and MCP
# tools now live in aurora/human/frontdesk.py (FrontDeskAgent).


def _build_campaign_context(campaign: CampaignState) -> str:
    """Build campaign context string for FrontDesk system prompt."""
    exps = list(campaign.experiments.values())
    exp_lines = []
    for e in exps[-10:]:
        exp_lines.append(f"  - {e.run_id}: {e.task_name} [{e.status}] reward={e.reward}")

    pending = ""
    unconfirmed = [
        p for p in campaign.proposals.values()
        if not p.confirmed and not p.superseded
    ]
    if unconfirmed:
        latest = max(unconfirmed, key=lambda p: p.created_at)
        p = latest
        pending = (
            f"\nPending Proposal (waiting for researcher to confirm or give feedback):\n"
            f"ID: {p.proposal_id}\n"
            f"Kind: {p.kind}\n"
            f"Summary: {p.summary}\n"
        )
        if p.kind == "custom":
            pending += f"Instruction: {p.params.get('instruction', '')[:500]}\n"
        else:
            pending += f"Task: {p.params.get('task_dir', '')}\n"
        all_phases = ["roundtable", "screening", "planning",
                      "research_loop", "analysis", "writing"]
        active = [ph for ph in all_phases if ph not in p.suggested_skip]
        pending += f"Pipeline: {' → '.join(active)}\n"
        pending += f"Model: {p.params.get('model', 'default')}\n"

    lines = [
        f"Name: {campaign.name}",
        f"Objective: {campaign.objective}",
        f"Experiments: {chr(10).join(exp_lines) if exp_lines else '(none)'}",
    ]
    if pending:
        lines.append(pending)
    return "\n".join(lines)


def _supersede_old_proposals(campaign: CampaignState) -> None:
    """Mark all unconfirmed proposals as superseded."""
    for p in campaign.proposals.values():
        if not p.confirmed and not p.superseded:
            p.superseded = True


def _get_frontdesk(campaign: CampaignState) -> "FrontDeskAgent":
    """Get or create the FrontDesk agent for a campaign."""
    from aurora.human.frontdesk import FrontDeskAgent

    if campaign.frontdesk is not None:
        return campaign.frontdesk

    async def on_list_tasks_fn() -> list[dict[str, Any]]:
        return await list_tasks()

    async def on_propose_experiment(params: dict[str, Any]) -> dict[str, Any]:
        task_dir = params.get("task_dir", "")
        if not task_dir:
            return {"status": "error", "reason": "task_dir is required"}
        _supersede_old_proposals(campaign)
        summary = params.get("summary", "")
        skip = params.get("skip_phases", list(PHASE_PRESET_STANDARD))
        proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
        p_params = {
            "task_dir": task_dir,
            "model": params.get("model", "deepseek/deepseek-v4-flash"),
            "driver": params.get("driver", "claude-code"),
            "max_turns": params.get("max_turns", 200),
        }
        proposal = ExperimentProposal(
            proposal_id=proposal_id,
            kind="predefined",
            params=p_params,
            suggested_skip=skip,
            summary=summary,
        )
        campaign.proposals[proposal_id] = proposal
        agent.set_proposal(proposal.to_dict())
        return {
            "status": "proposed",
            "proposal_id": proposal_id,
            "message": "Proposal created. The researcher will see a confirmation card.",
        }

    async def on_propose_custom(params: dict[str, Any]) -> dict[str, Any]:
        instruction = params.get("instruction", "")
        if not instruction:
            return {"status": "error", "reason": "instruction is required"}
        _supersede_old_proposals(campaign)
        summary = params.get("summary", instruction[:100])
        skip = params.get("skip_phases")
        if skip is None:
            skip = suggest_skip_phases(instruction, has_data=bool(params.get("data_paths")))
        proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
        p_params = {
            "instruction": instruction,
            "name": params.get("name", ""),
            "model": params.get("model", "deepseek/deepseek-v4-flash"),
            "max_turns": params.get("max_turns", 200),
            "data_paths": params.get("data_paths", []),
        }
        proposal = ExperimentProposal(
            proposal_id=proposal_id,
            kind="custom",
            params=p_params,
            suggested_skip=skip,
            summary=summary,
        )
        campaign.proposals[proposal_id] = proposal
        agent.set_proposal(proposal.to_dict())
        return {
            "status": "proposed",
            "proposal_id": proposal_id,
            "message": "Proposal created. The researcher will see a confirmation card.",
        }

    agent = FrontDeskAgent(
        model=os.environ.get("FRONTDESK_MODEL", "deepseek/deepseek-chat"),
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        base_url=os.environ.get("FRONTDESK_BASE_URL", "https://openrouter.ai/api/v1"),
        workspace_root=".",
        campaign_context_fn=lambda: _build_campaign_context(campaign),
        on_list_tasks=on_list_tasks_fn,
        on_propose_experiment=on_propose_experiment,
        on_propose_custom_experiment=on_propose_custom,
    )
    campaign.frontdesk = agent
    return agent


@campaign_router.post("/{campaign_id}/chat/send")
async def campaign_chat_send(campaign_id: str, body: CampaignChatBody) -> dict[str, Any]:
    """Send a chat message at the campaign level.

    Routes to the FrontDesk agent, which can start experiments, check status,
    and have multi-turn conversations to understand user intent.
    """
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}

    msg_record: dict[str, Any] = {
        "role": "user",
        "text": body.text,
        "timestamp": time.time(),
    }
    campaign.chat_history.append(msg_record)

    from aurora.api.app import ws_manager
    await ws_manager.broadcast({
        "type": "campaign_chat",
        "campaign_id": campaign_id,
        "data": msg_record,
    })

    try:
        frontdesk = _get_frontdesk(campaign)
        fd_result = await frontdesk.handle_user_message(body.text)
        response_text = fd_result["text"]
        proposal_data = fd_result.get("proposal")

        response_record: dict[str, Any] = {
            "role": "assistant",
            "text": response_text,
            "timestamp": time.time(),
        }
        if proposal_data:
            response_record["proposal"] = proposal_data

        campaign.chat_history.append(response_record)

        await ws_manager.broadcast({
            "type": "campaign_chat",
            "campaign_id": campaign_id,
            "data": response_record,
        })

        result: dict[str, Any] = {
            "status": "sent",
            "campaign_id": campaign_id,
            "response": response_text,
        }
        if proposal_data:
            result["proposal"] = proposal_data
        return result
    except Exception as exc:
        logger.error("FrontDesk error for campaign %s: %s", campaign_id, exc, exc_info=True)
        err_record = {
            "role": "assistant",
            "text": f"Front Desk error: {exc}",
            "timestamp": time.time(),
        }
        campaign.chat_history.append(err_record)
        return {"status": "error", "error": str(exc)}


class ConfirmProposalBody(BaseModel):
    skip_phases: list[str] = []
    model: str = ""  # empty = use proposal default


@campaign_router.post("/{campaign_id}/proposals/{proposal_id}/confirm")
async def confirm_proposal(
    campaign_id: str, proposal_id: str, body: ConfirmProposalBody,
) -> dict[str, Any]:
    """Confirm a pending experiment proposal and start the experiment."""
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}

    proposal = campaign.proposals.get(proposal_id)
    if not proposal:
        return {"error": "Proposal not found"}
    if proposal.confirmed:
        return {"error": "Proposal already confirmed"}

    proposal.confirmed = True
    params = dict(proposal.params)
    final_skip = body.skip_phases
    if body.model:
        params["model"] = body.model

    from aurora.api.app import ws_manager

    if proposal.kind == "predefined":
        req = StartRunRequest(
            task_dir=params["task_dir"],
            model=params.get("model", "deepseek/deepseek-v4-flash"),
            driver=params.get("driver", "claude-code"),
            max_turns=params.get("max_turns", 200),
            skip_phases=final_skip,
        )
        result = await start_experiment(campaign_id, req)
    else:
        req = StartCustomRunRequest(
            instruction=params["instruction"],
            name=params.get("name", ""),
            model=params.get("model", "deepseek/deepseek-v4-flash"),
            max_turns=params.get("max_turns", 200),
            data_paths=params.get("data_paths", []),
            skip_phases=final_skip,
        )
        result = await start_custom_experiment(campaign_id, req)

    if result.get("error"):
        proposal.confirmed = False
        return result

    sys_msg: dict[str, Any] = {
        "role": "system",
        "text": f"Experiment started: {result.get('experiment_id', '')}",
        "timestamp": time.time(),
    }
    campaign.chat_history.append(sys_msg)
    await ws_manager.broadcast({
        "type": "campaign_chat",
        "campaign_id": campaign_id,
        "data": sys_msg,
    })

    return {
        "status": "started",
        "experiment_id": result.get("experiment_id", ""),
        "skip_phases": final_skip,
        "active_phases": [
            p for p in ["roundtable", "screening", "planning",
                        "research_loop", "analysis", "writing"]
            if p not in final_skip
        ],
    }


@campaign_router.get("/{campaign_id}/chat/history")
async def campaign_chat_history(campaign_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Get campaign-level chat history with up-to-date proposal states."""
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return []
    history = campaign.chat_history[-limit:]
    for msg in history:
        if "proposal" in msg:
            pid = msg["proposal"].get("proposal_id")
            if pid and pid in campaign.proposals:
                msg["proposal"] = campaign.proposals[pid].to_dict()
    return history


@campaign_router.get("/{campaign_id}/chat/status")
async def campaign_chat_status(campaign_id: str) -> dict[str, Any]:
    """Check campaign chat status — active experiments, pending decisions."""
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {
            "campaign_id": campaign_id,
            "has_chat_handler": False,
            "has_frontdesk": False,
            "agent_active": False,
            "campaign_running": False,
            "campaign_paused": False,
            "pending_messages": 0,
        }

    exps = list(campaign.experiments.values())
    active_exps = [e for e in exps if e.status in ("running", "starting")]
    awaiting = [e for e in exps if e.status == "awaiting_human"]
    pending_decisions = [
        {
            "experiment_id": e.run_id,
            "decision_id": e.pending_decision.decision_id,
            "source": e.pending_decision.source,
            "context": e.pending_decision.context,
        }
        for e in exps
        if e.pending_decision is not None
    ]

    return {
        "campaign_id": campaign_id,
        "has_chat_handler": True,
        "has_frontdesk": len(exps) > 0,
        "agent_active": len(active_exps) > 0,
        "campaign_running": len(active_exps) > 0,
        "campaign_paused": False,
        "pending_messages": 0,
        "active_experiments": len(active_exps),
        "awaiting_human": len(awaiting),
        "pending_decisions": pending_decisions,
    }


@campaign_router.post("/{campaign_id}/chat/approve")
async def campaign_chat_approve(campaign_id: str, body: CampaignApprovalBody) -> dict[str, Any]:
    """Submit approval decision for a pending experiment decision."""
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}

    for exp_id, run_state in campaign.experiments.items():
        if (
            run_state.pending_decision
            and run_state.pending_decision.decision_id == body.approval_id
        ):
            run_state.pending_decision.response = {
                "decision": body.decision,
                "message": body.message,
            }
            run_state.pending_decision.event.set()
            campaign.chat_history.append({
                "role": "user",
                "text": f"[Approval: {body.decision}] {body.message}" if body.message else f"[Approval: {body.decision}]",
                "timestamp": time.time(),
                "type": "approval",
                "approval_id": body.approval_id,
            })
            return {
                "status": "delivered",
                "approval_id": body.approval_id,
                "decision": body.decision,
                "experiment_id": exp_id,
            }

    return {"error": f"No pending approval '{body.approval_id}'"}


@campaign_router.get("/{campaign_id}/chat/pending-approvals")
async def campaign_chat_pending_approvals(campaign_id: str) -> dict[str, Any]:
    """List all pending decisions across experiments in this campaign."""
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return {"pending": [], "escalations": []}

    pending = []
    for exp_id, run_state in campaign.experiments.items():
        if run_state.pending_decision is not None:
            pending.append(run_state.pending_decision.decision_id)

    return {"pending": pending, "escalations": []}


# ---------------------------------------------------------------------------
# Per-experiment chat
# ---------------------------------------------------------------------------

def _get_run_state(campaign_id: str, exp_id: str) -> LHTBRunState | None:
    campaign = _ensure_campaign(campaign_id)
    if not campaign:
        return None
    return campaign.experiments.get(exp_id)


class ExpChatBody(BaseModel):
    text: str


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/chat/send")
async def exp_chat_send(campaign_id: str, exp_id: str, body: ExpChatBody) -> dict[str, Any]:
    """Send a chat message to a running experiment's agent."""
    run_state = _get_run_state(campaign_id, exp_id)
    if not run_state:
        return {"error": "Experiment not found or not running"}

    run_state.chat_history.append({
        "role": "user", "text": body.text, "timestamp": time.time(),
    })

    if run_state.runner and hasattr(run_state.runner, "inject_message"):
        await run_state.runner.inject_message(
            f"[HUMAN MESSAGE]\n{body.text}"
        )

    await run_state.broadcast({
        "type": "lhtb_event",
        "run_id": exp_id,
        "event_type": "chat_message",
        "data": {"role": "user", "text": body.text},
        "timestamp": time.time(),
    })

    return {"status": "sent", "run_id": exp_id}


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/chat/history")
async def exp_chat_history(campaign_id: str, exp_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Get chat history for an experiment."""
    run_state = _get_run_state(campaign_id, exp_id)
    if run_state:
        return run_state.chat_history[-limit:]

    events = store.load_events(campaign_id, exp_id)
    chat_events = [
        {"role": e["data"]["role"], "text": e["data"]["text"], "timestamp": e["timestamp"]}
        for e in events
        if e.get("event_type") == "chat_message" and "data" in e
    ]
    return chat_events[-limit:]


# ---------------------------------------------------------------------------
# Workspace file browsing
# ---------------------------------------------------------------------------

def _build_tree_node(base: Path, rel: Path) -> dict[str, Any]:
    """Recursively build a file tree dict from a local directory."""
    full = base / rel
    name = rel.name or str(rel)
    if full.is_dir():
        children = []
        try:
            for child in sorted(full.iterdir()):
                if child.name.startswith(".") and child.name not in (".claude",):
                    continue
                children.append(_build_tree_node(base, rel / child.name))
        except PermissionError:
            pass
        return {"name": name, "type": "dir", "children": children}
    ext = full.suffix.lstrip(".")
    return {"name": name, "path": str(rel), "type": ext or "file"}


def _parse_find_output(output: str, root: str = "/app") -> list[dict[str, Any]]:
    """Parse `find` output into a nested tree structure."""
    tree: dict[str, Any] = {"name": Path(root).name, "type": "dir", "children": []}
    dirs: dict[str, dict] = {"": tree}

    lines = sorted(output.strip().splitlines())
    for line in lines:
        line = line.strip()
        if not line or line == root:
            continue
        rel = line[len(root):].lstrip("/")
        if not rel or rel.startswith(".") and not rel.startswith(".claude"):
            continue
        parts = rel.split("/")
        parent_key = "/".join(parts[:-1])
        name = parts[-1]
        if name.startswith(".") and name != ".claude":
            continue

        parent = dirs.get(parent_key, tree)
        if "children" not in parent:
            parent["children"] = []

        if line.endswith("/"):
            node: dict[str, Any] = {"name": name, "type": "dir", "children": []}
            dirs[rel.rstrip("/")] = node
        else:
            ext = Path(name).suffix.lstrip(".")
            node = {"name": name, "path": rel, "type": ext or "file"}

        parent["children"].append(node)

    return tree.get("children", [])


async def _get_workspace_tree(
    run_state: LHTBRunState | None,
    exp_id: str,
) -> list[dict[str, Any]]:
    """Get file tree — from live Docker container or extracted workspace."""
    # Try live container first
    if run_state and run_state.runner:
        backend = getattr(run_state.runner, '_execution_backend', None)
        if backend and hasattr(backend, 'exec_command'):
            try:
                code, stdout, _ = await backend.exec_command(
                    "find /app -maxdepth 4 \\( -not -path '*/\\.*' -print \\) -o \\( -name '.claude' -print \\)",
                    timeout=10.0,
                )
                if code == 0 and stdout.strip():
                    return _parse_find_output(stdout)
            except Exception:
                pass

    # Fall back to extracted workspace on disk
    ws_dir = _find_workspace_dir(exp_id)
    if ws_dir and ws_dir.is_dir():
        node = _build_tree_node(ws_dir, Path(""))
        return node.get("children", [])

    return []


def _find_workspace_dir(exp_id: str) -> Path | None:
    """Find the workspace directory for an experiment, checking multiple locations."""
    # Primary: store's workspaces dir
    primary = store._data_dir / "workspaces" / exp_id
    # Also check engine workspace dirs (workspace/lhtb_<task>_<hash>/workspace/)
    engine_ws = None
    ws_root = Path("workspace")
    if ws_root.is_dir():
        task_name = exp_id.split("_", 1)[1] if "_" in exp_id else exp_id
        for d in sorted(ws_root.iterdir(), key=lambda p: p.stat().st_mtime if p.is_dir() else 0, reverse=True):
            if d.is_dir() and task_name in d.name:
                ws_sub = d / "workspace"
                if ws_sub.is_dir() and any(ws_sub.iterdir()):
                    engine_ws = ws_sub
                    break
    # Prefer engine workspace (has agent-created files) over store (may only have user writes)
    if engine_ws:
        return engine_ws
    if primary.is_dir() and any(primary.iterdir()):
        return primary
    return None


async def _read_workspace_file(
    run_state: LHTBRunState | None,
    exp_id: str,
    file_path: str,
) -> str | None:
    """Read a single file — from live Docker or extracted workspace."""
    # Validate path
    if ".." in file_path or file_path.startswith("/"):
        return None

    # Try live container
    if run_state and run_state.runner:
        backend = getattr(run_state.runner, '_execution_backend', None)
        if backend and hasattr(backend, 'exec_command'):
            try:
                code, stdout, _ = await backend.exec_command(
                    f"cat '/app/{file_path}'",
                    timeout=10.0,
                )
                if code == 0:
                    return stdout
            except Exception:
                pass

    # Fall back to disk
    ws_dir = _find_workspace_dir(exp_id)
    if ws_dir:
        local_path = ws_dir / file_path
        if local_path.is_file():
            try:
                return local_path.read_text(errors="replace")
            except Exception:
                return None

    return None


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/workspace/tree")
async def workspace_tree(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """Return the workspace file tree for an experiment."""
    run_state = _get_run_state(campaign_id, exp_id)
    files = await _get_workspace_tree(run_state, exp_id)
    return {"experiment_id": exp_id, "files": files}


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/workspace/file")
async def workspace_file(
    campaign_id: str,
    exp_id: str,
    path: str,
) -> dict[str, Any]:
    """Read a single file from the experiment workspace."""
    from fastapi import HTTPException as HE
    if not path:
        raise HE(400, "path query parameter required")

    run_state = _get_run_state(campaign_id, exp_id)
    content = await _read_workspace_file(run_state, exp_id, path)
    if content is None:
        raise HE(404, f"File not found: {path}")

    is_binary = "\x00" in content[:512] if content else False
    if is_binary:
        return {
            "path": path,
            "content": None,
            "binary": True,
            "size": len(content),
        }

    return {
        "path": path,
        "content": content[:500_000],
        "binary": False,
        "size": len(content),
        "truncated": len(content) > 500_000,
    }


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/workspace/raw")
async def workspace_file_raw(
    campaign_id: str,
    exp_id: str,
    path: str,
) -> Any:
    """Serve a workspace file as raw bytes (for images, etc.)."""
    from fastapi import HTTPException as HE
    from fastapi.responses import Response
    import mimetypes

    if not path:
        raise HE(400, "path query parameter required")
    if ".." in path or path.startswith("/"):
        raise HE(400, "Invalid path")

    run_state = _get_run_state(campaign_id, exp_id)

    raw_bytes: bytes | None = None

    # Try disk first for raw bytes
    ws_dir = _find_workspace_dir(exp_id)
    if ws_dir:
        local_path = ws_dir / path
        if local_path.is_file():
            try:
                raw_bytes = local_path.read_bytes()
            except Exception:
                pass

    # Try live container
    if raw_bytes is None and run_state and run_state.runner:
        backend = getattr(run_state.runner, '_execution_backend', None)
        if backend and hasattr(backend, 'exec_command'):
            try:
                code, stdout, _ = await backend.exec_command(
                    f"base64 '/app/{path}'",
                    timeout=10.0,
                )
                if code == 0:
                    import base64
                    raw_bytes = base64.b64decode(stdout.strip())
            except Exception:
                pass

    if raw_bytes is None:
        raise HE(404, f"File not found: {path}")

    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(content=raw_bytes, media_type=content_type)


class WorkspaceWriteBody(BaseModel):
    path: str
    content: str


async def _write_workspace_file(
    run_state: LHTBRunState | None,
    exp_id: str,
    file_path: str,
    content: str,
) -> bool:
    """Write a file — to live Docker container AND extracted workspace."""
    if ".." in file_path or file_path.startswith("/"):
        return False

    written = False

    # Write to live container if running
    if run_state and run_state.runner:
        backend = getattr(run_state.runner, '_execution_backend', None)
        if backend and hasattr(backend, 'exec_command'):
            try:
                import shlex
                escaped = content.replace("\\", "\\\\").replace("'", "'\\''")
                # Use printf to handle newlines correctly
                import base64
                b64 = base64.b64encode(content.encode()).decode()
                code, _, stderr = await backend.exec_command(
                    f"echo '{b64}' | base64 -d > '/app/{file_path}'",
                    timeout=10.0,
                )
                if code == 0:
                    written = True
                else:
                    logger.warning("Docker write failed for %s: %s", file_path, stderr)
            except Exception:
                logger.debug("Docker write failed", exc_info=True)

    # Also persist to extracted workspace on disk
    ws_dir = _find_workspace_dir(exp_id) or (store._data_dir / "workspaces" / exp_id)
    local_path = ws_dir / file_path
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(content)
        written = True
    except Exception:
        logger.debug("Local write failed for %s", file_path, exc_info=True)

    return written


@campaign_router.put("/{campaign_id}/experiments/{exp_id}/workspace/file")
async def workspace_write(
    campaign_id: str,
    exp_id: str,
    body: WorkspaceWriteBody,
) -> dict[str, Any]:
    """Write a file to the experiment workspace (syncs to Docker if running)."""
    from fastapi import HTTPException as HE
    if not body.path:
        raise HE(400, "path is required")
    if ".." in body.path or body.path.startswith("/"):
        raise HE(400, "Invalid path")

    run_state = _get_run_state(campaign_id, exp_id)
    ok = await _write_workspace_file(run_state, exp_id, body.path, body.content)
    if not ok:
        raise HE(500, "Failed to write file")

    return {"path": body.path, "size": len(body.content), "synced_to_docker": run_state is not None}

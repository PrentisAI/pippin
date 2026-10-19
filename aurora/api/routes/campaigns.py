"""Projects and experiments of the web console.

A *project* (``/api/campaigns``, for historical reasons) groups experiments
and has a conversation with the project front desk, which drafts experiment
proposals for the researcher to confirm.  An *experiment* is one research
campaign run by :class:`~aurora.core.engine.ResearchEngine`; its events stream
over a WebSocket, and while it runs the researcher can

* talk to it (the campaign's front desk answers and relays instructions),
* answer human gates (``/decide``), switch between supervised and
  autonomous mode, stop it, and browse or edit its files.

Experiments are configured by the server's base configuration
(``aurora serve --config``) plus the options given when they start.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel

from aurora.api.persistence import LazyStore
from aurora.config import PHASES

logger = logging.getLogger(__name__)

store = LazyStore()
campaign_router = APIRouter(prefix="/api/campaigns", tags=["projects"])

# Answers the approval bar sends, mapped to human-gate actions
_GATE_ACTIONS = {"continue": "continue", "approve": "continue", "instruct": "instruct",
                 "override": "instruct", "modify": "instruct", "stop": "stop", "abort": "stop",
                 "reject": "stop"}
_SAFE_PATH = re.compile(r"^[\w .@+=,%-]+(/[\w .@+=,%-]+)*$")


def base_config() -> dict[str, Any]:
    """The server's base campaign configuration (``aurora serve --config``), as a dict."""
    path = os.environ.get("AURORA_SERVER_CONFIG")
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.pop("objective", None)
    data.pop("campaign_id", None)
    return data


# Experiments started from the console get this cost limit unless the server's base
# config sets budget.max_cost_usd or the researcher changes it on the proposal card
WEB_DEFAULT_MAX_COST_USD = 20.0


def default_max_cost() -> float | None:
    budget = base_config().get("budget") or {}
    return budget["max_cost_usd"] if "max_cost_usd" in budget else WEB_DEFAULT_MAX_COST_USD


def default_ideation_mode() -> str:
    return (base_config().get("ideation") or {}).get("mode", "roundtable")


def _projects_root() -> Path:
    return store.data_dir / "projects"


# ---------------------------------------------------------------------------
# Live state
# ---------------------------------------------------------------------------

class ExperimentRunState:
    """Live state of one experiment: status, event log, subscribers and its engine."""

    def __init__(self, run_id: str, campaign_id: str, name: str, model: str,
                 autonomy_mode: str = "supervised") -> None:
        self.run_id = run_id
        self.campaign_id = campaign_id
        self.task_name = name
        self.model = model
        self.autonomy_mode = autonomy_mode
        self.status = "starting"   # starting | running | awaiting_human | completed | failed | stopped
        self.phase = ""
        self.turn = 0
        self.cost_usd = 0.0
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.error: str | None = None
        self.events: list[dict[str, Any]] = []
        self.subscribers: list[WebSocket] = []
        self.chat_history: list[dict[str, Any]] = []
        self.decision_history: list[dict[str, Any]] = []
        self.engine: Any = None
        self.task: asyncio.Task | None = None

    @property
    def campaign_dir(self) -> Path:
        return _projects_root() / self.campaign_id / self.run_id

    async def broadcast(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        store.append_event(self.campaign_id, self.run_id, event)
        payload = json.dumps(event, default=str)
        for ws in list(self.subscribers):
            try:
                await ws.send_text(payload)
            except Exception:
                self.subscribers.remove(ws)

    def pending_decision(self) -> dict[str, Any] | None:
        gates = self.engine.human_gate.pending if self.engine is not None else []
        if not gates:
            return None
        g = gates[0]
        return {"decision_id": g.gate_id, "source": g.source, "context": f"{g.title}\n\n{g.detail}",
                "detail": g.to_dict(), "options": list(g.options), "created_at": g.created_at,
                "resolved": False}

    def to_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "campaign_id": self.campaign_id,
            "task_name": self.task_name,
            "model": self.model,
            "autonomy_mode": self.autonomy_mode,
            "status": self.status,
            "phase": self.phase,
            "turn": self.turn,
            "cost_usd": round(self.cost_usd, 4),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "event_count": len(self.events),
            "error": self.error,
            "pending_decision": self.pending_decision(),
            "decisions_made": len(self.decision_history),
        }


class ExperimentProposal:
    """An experiment drafted by the project front desk, waiting for the researcher to confirm."""

    def __init__(self, params: dict[str, Any], skip_phases: list[str], summary: str) -> None:
        self.max_cost_usd = default_max_cost()
        self.proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
        self.kind = "custom"
        self.params = params
        self.suggested_skip = skip_phases
        self.summary = summary
        self.created_at = time.time()
        self.confirmed = False
        self.superseded = False
        self.confirmed_settings: dict[str, Any] | None = None   # what the researcher started

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id, "kind": self.kind, "params": self.params,
            "skip_phases": self.suggested_skip,
            "active_phases": [p for p in PHASES if p not in self.suggested_skip],
            "max_cost_usd": self.max_cost_usd,
            "summary": self.summary, "created_at": self.created_at,
            "confirmed": self.confirmed, "superseded": self.superseded,
            "confirmed_settings": self.confirmed_settings,
        }


class CampaignState:
    """A project: experiments, the project conversation and pending proposals."""

    def __init__(self, campaign_id: str, name: str, objective: str = "") -> None:
        self.campaign_id = campaign_id
        self.name = name
        self.objective = objective
        self.created_at = time.time()
        self.updated_at = time.time()
        self.experiments: dict[str, ExperimentRunState] = {}
        self.chat_history: list[dict[str, Any]] = []
        self.frontdesk: Any = None
        self.proposals: dict[str, ExperimentProposal] = {}

    def to_summary(self) -> dict[str, Any]:
        exps = list(self.experiments.values())
        active = sum(1 for e in exps if e.status in ("running", "starting", "awaiting_human"))
        return {
            "campaign_id": self.campaign_id, "name": self.name, "objective": self.objective,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "experiment_count": len(exps), "active_experiments": active,
            "completed_experiments": len(exps) - active,
            "needs_attention": any(e.status == "awaiting_human" for e in exps),
        }

    def note(self, text: str) -> None:
        self.chat_history.append({"role": "system", "text": text, "timestamp": time.time()})


_campaigns: dict[str, CampaignState] = {}


def _ensure_campaign(campaign_id: str) -> CampaignState:
    """The project, from memory or restored from the database; 404 if unknown."""
    if campaign_id in _campaigns:
        return _campaigns[campaign_id]
    row = store.get_campaign(campaign_id)
    if not row:
        raise HTTPException(404, f"Project {campaign_id} not found")
    campaign = CampaignState(campaign_id, row.get("name", ""), row.get("objective", ""))
    campaign.created_at = row.get("created_at", campaign.created_at)
    campaign.updated_at = row.get("updated_at", campaign.updated_at)
    for e in store.list_experiments(campaign_id):
        rs = ExperimentRunState(e["run_id"], campaign_id, e.get("task_name", ""), e.get("model", ""),
                                e.get("autonomy_mode", "supervised"))
        rs.status = e.get("status", "completed")
        rs.started_at = e.get("started_at", 0.0)
        rs.finished_at = e.get("finished_at")
        rs.error = e.get("error") or None
        rs.phase = e.get("phase") or ""
        rs.cost_usd = e.get("cost_usd") or 0.0
        rs.turn = e.get("turn") or 0
        campaign.experiments[e["run_id"]] = rs
    _campaigns[campaign_id] = campaign
    return campaign


def _get_run(campaign_id: str, exp_id: str) -> ExperimentRunState:
    run = _ensure_campaign(campaign_id).experiments.get(exp_id)
    if run is None:
        raise HTTPException(404, f"Experiment {exp_id} not found")
    return run


def _touch(campaign: CampaignState) -> None:
    campaign.updated_at = time.time()
    store.update_campaign_ts(campaign.campaign_id, campaign.updated_at)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class CreateCampaignRequest(BaseModel):
    name: str
    objective: str = ""


class QuickCreateRequest(BaseModel):
    prompt: str
    auto_start: bool = True


@campaign_router.post("/quick")
async def quick_create_campaign(req: QuickCreateRequest) -> dict[str, Any]:
    """Create a project from the first message the researcher types."""
    campaign_id = f"p_{time.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
    name = req.prompt[:60] + ("..." if len(req.prompt) > 60 else "")
    campaign = CampaignState(campaign_id, name, req.prompt)
    _campaigns[campaign_id] = campaign
    campaign.note("Project created")
    store.save_campaign(campaign_id, name, req.prompt, campaign.created_at, campaign.updated_at)
    return {"campaign_id": campaign_id, "name": name, "objective": req.prompt, "status": "created"}


@campaign_router.post("/")
async def create_campaign(req: CreateCampaignRequest) -> dict[str, Any]:
    campaign_id = f"p_{time.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
    campaign = CampaignState(campaign_id, req.name, req.objective)
    _campaigns[campaign_id] = campaign
    campaign.note("Project created")
    store.save_campaign(campaign_id, req.name, req.objective, campaign.created_at, campaign.updated_at)
    return campaign.to_summary()


@campaign_router.get("/")
async def list_campaigns() -> list[dict[str, Any]]:
    results = [_ensure_campaign(row["campaign_id"]).to_summary() for row in store.list_campaigns()]
    return sorted(results, key=lambda c: c["updated_at"], reverse=True)


@campaign_router.get("/{campaign_id}")
async def get_campaign(campaign_id: str) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    summary = campaign.to_summary()
    summary["experiments"] = sorted((e.to_summary() for e in campaign.experiments.values()),
                                    key=lambda e: e["started_at"], reverse=True)
    return summary


@campaign_router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: str) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    for run in campaign.experiments.values():
        if run.engine is not None:
            run.engine.request_stop("project deleted")
    _campaigns.pop(campaign_id, None)
    store.delete_campaign(campaign_id)
    return {"ok": True, "campaign_id": campaign_id}


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------

class StartExperimentRequest(BaseModel):
    """Start an experiment. Every field except the instruction falls back to the server's base config."""

    instruction: str
    name: str = ""
    skip_phases: list[str] | None = None
    model: str = ""                    # models.agent (shorthand)
    models: dict[str, Any] | None = None  # any keys of the models section, e.g. {"research": "..."}
    docker_image: str = ""             # sandbox.image
    inputs: list[str] = []             # paths on the server
    data_paths: list[str] = []         # older name of inputs
    autonomy_mode: str = ""            # supervised | autonomous
    ideation_mode: str = ""            # roundtable | independent
    max_cost_usd: float | None = None
    mock_mode: bool | None = None


def default_skip_phases(instruction: str) -> list[str]:
    """Skip ideation and screening for concrete tasks; open questions get the whole pipeline."""
    text = instruction.lower()
    open_ended = sum(w in text for w in ("research", "investigate", "explore", "hypothesis", "why",
                                         "whether", "find out", "understand", "novel", "idea"))
    return [] if open_ended >= 2 or len(instruction) > 800 else ["ideation", "screening"]


def _experiment_config(campaign: CampaignState, run_id: str, req: StartExperimentRequest) -> Any:
    from aurora.config import AuroraConfig

    raw = base_config()
    raw["objective"] = req.instruction
    raw["campaign_id"] = run_id
    raw["workspace"] = {**raw.get("workspace", {}), "root": str(_projects_root() / campaign.campaign_id)}
    raw["skip_phases"] = req.skip_phases if req.skip_phases is not None else default_skip_phases(req.instruction)
    if req.model:
        raw.setdefault("models", {})["agent"] = req.model
    if req.models:
        raw["models"] = {**raw.get("models", {}), **{k: v for k, v in req.models.items() if v not in ("", None)}}
    if req.docker_image:
        raw.setdefault("sandbox", {})["image"] = req.docker_image
    if req.inputs or req.data_paths:
        raw["inputs"] = list(raw.get("inputs", [])) + req.inputs + req.data_paths
    if req.autonomy_mode:
        raw.setdefault("human", {})["autonomy"] = req.autonomy_mode
    if req.ideation_mode:
        raw.setdefault("ideation", {})["mode"] = req.ideation_mode
    # an explicit null in the request means no cost limit
    max_cost = req.max_cost_usd if "max_cost_usd" in req.model_fields_set else default_max_cost()
    raw.setdefault("budget", {})["max_cost_usd"] = max_cost
    if req.mock_mode is not None:
        raw["mock_mode"] = req.mock_mode
    return AuroraConfig.model_validate(raw)


async def _launch(campaign: CampaignState, run: ExperimentRunState, config: Any, *, resume: bool = False) -> None:
    """Run the engine for *run* in the background and mirror its events into the run state."""
    from aurora.core.engine import ResearchEngine
    from aurora.core.event_bridge import EventBridge
    from aurora.core.requirements import check_runtime_requirements

    check_runtime_requirements(config)   # fail at request time, with the reason, not in the background
    engine = ResearchEngine(config)
    engine.project_id = campaign.campaign_id
    run.engine = engine

    async def mirror(event: Any) -> None:
        name, data = event.name, event.data
        if name == "phase_started":
            run.phase = data.get("phase", run.phase)
            store.update_experiment(run.run_id, status="running", phase=run.phase)
        elif name == "tool_call_start":
            run.turn += 1
        elif name == "human_gate_opened":
            run.status = "awaiting_human"
            campaign.note(f"{run.task_name}: needs your decision — {data.get('title', '')}")
        elif name == "human_gate_resolved":
            if run.status == "awaiting_human":
                run.status = "running"
            run.decision_history.append({**data, "timestamp": time.time()})
        elif name == "frontdesk_response":
            run.chat_history.append({"role": "assistant", "text": data.get("text", ""), "timestamp": time.time()})
        run.cost_usd = engine.cost_usd

    engine.event_bus.subscribe_all(mirror)
    EventBridge(engine.event_bus, run.run_id, broadcast=run.broadcast)

    async def runner() -> None:
        run.status = "running"
        try:
            result = await engine.run_campaign(resume=resume)
            run.status = result.status
            run.error = None if result.status == "completed" else result.summary
            run.cost_usd = result.cost_usd
            report = result.manuscript_path
            if report and Path(report).is_relative_to(run.campaign_dir):
                report = str(Path(report).relative_to(run.campaign_dir))
            await run.broadcast({"type": "experiment_event", "run_id": run.run_id, "event_type": "run_finished",
                                 "data": {"status": result.status, "summary": result.summary,
                                          "report": report, "cost_usd": result.cost_usd,
                                          "wall_time": result.total_wall_time_seconds},
                                 "timestamp": time.time()})
        except Exception as exc:
            logger.exception("Experiment %s crashed", run.run_id)
            run.status, run.error = "failed", str(exc)
            await run.broadcast({"type": "experiment_event", "run_id": run.run_id, "event_type": "run_error",
                                 "data": {"error": str(exc)}, "timestamp": time.time()})
        finally:
            run.finished_at = time.time()
            store.update_experiment(run.run_id, status=run.status, finished_at=run.finished_at,
                                    error=run.error or "", cost_usd=run.cost_usd, turn=run.turn)
            campaign.note(f"{run.task_name}: {run.status}")
            _touch(campaign)

    run.task = asyncio.create_task(runner())


async def _start(campaign: CampaignState, req: StartExperimentRequest, *, run_id: str | None = None,
                 resume: bool = False) -> dict[str, Any]:
    slug = re.sub(r"[^a-z0-9]+", "-", (req.name or req.instruction[:40]).lower()).strip("-")[:30] or "experiment"
    run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}_{slug}"
    try:
        config = _experiment_config(campaign, run_id, req)
    except Exception as exc:
        raise HTTPException(400, f"Invalid experiment settings: {exc}") from exc
    run = ExperimentRunState(run_id, campaign.campaign_id, req.name or slug, config.models.for_phase("research"),
                             config.human.autonomy.value)
    if resume:
        # keep the earlier stream and conversation visible
        run.events = store.load_events(campaign.campaign_id, run_id)
        for e in run.events:
            if e.get("event_type") == "chat_message":
                run.chat_history.append({"role": "user", "text": e["data"].get("text", ""),
                                         "timestamp": e.get("timestamp")})
            elif e.get("event_type") == "frontdesk_response":
                run.chat_history.append({"role": "assistant", "text": e["data"].get("text", ""),
                                         "timestamp": e.get("timestamp")})
    try:
        await _launch(campaign, run, config, resume=resume)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    campaign.experiments[run_id] = run
    store.save_experiment(run_id=run_id, campaign_id=campaign.campaign_id, task_name=run.task_name,
                          model=run.model, autonomy_mode=run.autonomy_mode, started_at=run.started_at,
                          config={"request": req.model_dump()})
    campaign.note(f"{run.task_name} started")
    _touch(campaign)
    return {**run.to_summary(), "experiment_id": run_id, "skip_phases": config.skip_phases,
            "active_phases": [p for p in PHASES if p not in config.skip_phases]}


@campaign_router.post("/{campaign_id}/experiments")
async def start_experiment(campaign_id: str, req: StartExperimentRequest) -> dict[str, Any]:
    """Start an experiment in the project."""
    return await _start(_ensure_campaign(campaign_id), req)


@campaign_router.post("/{campaign_id}/experiments/custom")
async def start_custom_experiment(campaign_id: str, req: StartExperimentRequest) -> dict[str, Any]:
    """Same as ``POST /experiments`` (kept for older clients)."""
    return await _start(_ensure_campaign(campaign_id), req)


@campaign_router.get("/{campaign_id}/experiments/{exp_id}")
async def get_experiment(campaign_id: str, exp_id: str) -> dict[str, Any]:
    run = _get_run(campaign_id, exp_id)
    summary = run.to_summary()
    summary["events"] = (run.events or store.load_events(campaign_id, exp_id))[-300:]
    summary["decision_history"] = run.decision_history or _gates_from_disk(run)
    report = sorted((run.campaign_dir / "phases" / "writing").glob("**/draft_*.md"))
    summary["report"] = str(report[-1].relative_to(run.campaign_dir)) if report else None
    return summary


def _gates_from_disk(run: ExperimentRunState) -> list[dict[str, Any]]:
    path = run.campaign_dir / "human_gates.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            entry = json.loads(line)
            req, dec = entry.get("request", {}), entry.get("decision", {})
            out.append({**dec, "source": req.get("source"), "title": req.get("title"),
                        "timestamp": entry.get("resolved_at")})
    return out


@campaign_router.delete("/{campaign_id}/experiments/{exp_id}")
async def stop_experiment(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """Stop a running experiment (it ends at the next safe point and keeps its files)."""
    run = _get_run(campaign_id, exp_id)
    if run.engine is not None and run.status in ("starting", "running", "awaiting_human"):
        run.engine.request_stop("stopped by the user")
    return run.to_summary()


class DecisionRequest(BaseModel):
    action: str            # continue | instruct | stop (approve / override / abort also accepted)
    message: str = ""
    gate_id: str = ""      # default: the oldest open gate


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/decide")
async def submit_decision(campaign_id: str, exp_id: str, req: DecisionRequest) -> dict[str, Any]:
    """Answer the experiment's open human gate."""
    run = _get_run(campaign_id, exp_id)
    pending = run.engine.human_gate.pending if run.engine is not None else []
    if not pending:
        raise HTTPException(409, "No decision is pending")
    gate_id = req.gate_id or pending[0].gate_id
    action = _GATE_ACTIONS.get(req.action.lower())
    if action is None:
        raise HTTPException(400, f"Unknown action {req.action!r}; use continue, instruct or stop")
    if action == "continue" and req.message.strip():
        action = "instruct"     # a continue with a message is an instruction
    if action == "instruct" and not req.message.strip():
        raise HTTPException(400, "An instruction needs a message")
    if not run.engine.human_gate.resolve(gate_id, action, req.message):
        raise HTTPException(409, f"Gate {gate_id} is not open")
    return {"ok": True, "decision_id": gate_id, "action": action}


class SetAutonomyRequest(BaseModel):
    autonomy_mode: str   # supervised | autonomous


@campaign_router.put("/{campaign_id}/experiments/{exp_id}/autonomy")
async def set_experiment_autonomy(campaign_id: str, exp_id: str, req: SetAutonomyRequest) -> dict[str, Any]:
    run = _get_run(campaign_id, exp_id)
    if req.autonomy_mode not in ("autonomous", "supervised"):
        raise HTTPException(400, "autonomy_mode must be 'autonomous' or 'supervised'")
    old, run.autonomy_mode = run.autonomy_mode, req.autonomy_mode
    if run.engine is not None:
        run.engine.human_gate.set_autonomy(req.autonomy_mode)
    store.update_experiment(exp_id, autonomy_mode=req.autonomy_mode)
    await run.broadcast({"type": "experiment_event", "run_id": exp_id, "event_type": "autonomy_changed",
                         "data": {"old_mode": old, "new_mode": req.autonomy_mode}, "timestamp": time.time()})
    return {"ok": True, "autonomy_mode": req.autonomy_mode, "previous_mode": old}


def _saved_request(exp_id: str) -> StartExperimentRequest:
    row = store.get_experiment(exp_id)
    if not row:
        raise HTTPException(404, f"Experiment {exp_id} not found")
    saved = json.loads(row.get("config_json") or "{}").get("request")
    if not saved:
        raise HTTPException(409, "This experiment has no saved settings to start from")
    return StartExperimentRequest.model_validate(saved)


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/rerun")
async def rerun_experiment(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """Start a new experiment with the same settings."""
    return await _start(_ensure_campaign(campaign_id), _saved_request(exp_id))


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/resume")
async def resume_experiment(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """Continue a stopped or failed experiment from its last checkpoint (same files, same ID)."""
    campaign = _ensure_campaign(campaign_id)
    run = _get_run(campaign_id, exp_id)
    if run.status in ("starting", "running", "awaiting_human"):
        raise HTTPException(409, "The experiment is still running")
    if not (run.campaign_dir / "checkpoint.json").exists():
        raise HTTPException(409, "No checkpoint to resume from; use Re-run instead")
    req = _saved_request(exp_id)
    del campaign.experiments[exp_id]
    return await _start(campaign, req, run_id=exp_id, resume=True)


@campaign_router.websocket("/{campaign_id}/experiments/{exp_id}/ws")
async def experiment_websocket(websocket: WebSocket, campaign_id: str, exp_id: str) -> None:
    try:
        run = _get_run(campaign_id, exp_id)
    except HTTPException:
        await websocket.close(code=4004, reason="Experiment not found")
        return
    await websocket.accept()
    run.subscribers.append(websocket)
    for event in run.events or store.load_events(campaign_id, exp_id):
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
        if websocket in run.subscribers:
            run.subscribers.remove(websocket)


# ---------------------------------------------------------------------------
# Conversation with a running experiment (duplex interaction)
# ---------------------------------------------------------------------------

class ChatBody(BaseModel):
    text: str


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/chat/send")
async def exp_chat_send(campaign_id: str, exp_id: str, body: ChatBody) -> dict[str, Any]:
    """A message to the running experiment; its front desk answers (and may relay it to the agent)."""
    run = _get_run(campaign_id, exp_id)
    run.chat_history.append({"role": "user", "text": body.text, "timestamp": time.time()})
    await run.broadcast({"type": "experiment_event", "run_id": exp_id, "event_type": "chat_message",
                         "data": {"role": "user", "text": body.text}, "timestamp": time.time()})
    if run.engine is None or run.status not in ("starting", "running", "awaiting_human"):
        return {"status": "not_running", "response": "This experiment is not running."}
    reply = await run.engine.handle_user_message(body.text)
    if run.engine._frontdesk is None and reply.get("text"):
        run.chat_history.append({"role": "assistant", "text": reply["text"], "timestamp": time.time()})
    return {"status": "sent", "run_id": exp_id, "response": reply.get("text", "")}


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/chat/history")
async def exp_chat_history(campaign_id: str, exp_id: str, limit: int = 100) -> list[dict[str, Any]]:
    run = _get_run(campaign_id, exp_id)
    if run.chat_history:
        return run.chat_history[-limit:]
    history = []
    for e in store.load_events(campaign_id, exp_id):
        if e.get("event_type") == "chat_message":
            history.append({"role": "user", "text": e["data"].get("text", ""), "timestamp": e.get("timestamp")})
        elif e.get("event_type") == "frontdesk_response":
            history.append({"role": "assistant", "text": e["data"].get("text", ""), "timestamp": e.get("timestamp")})
    return history[-limit:]


# ---------------------------------------------------------------------------
# Project conversation: the front desk drafts experiments for the researcher
# ---------------------------------------------------------------------------

def _project_context(campaign: CampaignState) -> str:
    lines = [f"Project: {campaign.name}", f"Objective: {campaign.objective}", "Experiments:"]
    for e in list(campaign.experiments.values())[-10:]:
        lines.append(f"  - {e.run_id}: {e.task_name} [{e.status}]")
    if len(lines) == 3:
        lines.append("  (none yet)")
    open_props = [p for p in campaign.proposals.values() if not p.confirmed and not p.superseded]
    if open_props:
        p = max(open_props, key=lambda p: p.created_at)
        lines.append(f"Pending proposal {p.proposal_id}: {p.summary}\n"
                     f"Instruction: {p.params.get('instruction', '')[:800]}\n"
                     f"Phases: {' → '.join(x for x in PHASES if x not in p.suggested_skip)}")
    return "\n".join(lines)


def _project_frontdesk(campaign: CampaignState) -> Any:
    from aurora.config import AuroraConfig
    from aurora.human.frontdesk import PROJECT_PROMPT, FrontDeskAgent, FrontDeskTool

    if campaign.frontdesk is not None:
        return campaign.frontdesk
    cfg = AuroraConfig.model_validate({**base_config(), "objective": campaign.objective or "project"})

    async def propose(params: dict[str, Any]) -> dict[str, Any]:
        instruction = str(params.get("instruction", "")).strip()
        if not instruction:
            return {"status": "error", "reason": "instruction is required"}
        skip = params.get("skip_phases")
        skip = [p for p in skip if p in PHASES] if isinstance(skip, list) else default_skip_phases(instruction)
        for p in campaign.proposals.values():
            if not p.confirmed:
                p.superseded = True
        proposal = ExperimentProposal(
            {"instruction": instruction, "name": params.get("name", ""),
             "inputs": params.get("inputs", []), "model": params.get("model", ""),
             "ideation_mode": params.get("ideation_mode") if params.get("ideation_mode") in
             ("roundtable", "independent") else default_ideation_mode()},
            skip, str(params.get("summary", instruction[:100])))
        campaign.proposals[proposal.proposal_id] = proposal
        agent.set_proposal(proposal.to_dict())
        return {"status": "proposed", "proposal_id": proposal.proposal_id,
                "message": "The researcher now sees a card to confirm or change."}

    tool = FrontDeskTool(
        "propose_experiment",
        "Show the researcher an experiment proposal to confirm. It replaces any earlier proposal.",
        {"type": "object", "properties": {
            "instruction": {"type": "string", "description": "self-contained brief for the agents"},
            "summary": {"type": "string", "description": "one line for the card"},
            "name": {"type": "string", "description": "short name"},
            "skip_phases": {"type": "array", "items": {"type": "string", "enum": list(PHASES)}},
            "ideation_mode": {"type": "string", "enum": ["roundtable", "independent"],
                              "description": "roundtable: the models discuss their ideas in a group meeting "
                                             "before the chair decides; independent: the chair picks one of "
                                             "the independent ideas (about four times cheaper)"},
            "inputs": {"type": "array", "items": {"type": "string"},
                       "description": "paths on the server the researcher mentioned"},
        }, "required": ["instruction", "summary"]},
        propose)
    agent = FrontDeskAgent(model=cfg.models.frontdesk, api_key=cfg.api_key_value, base_url=cfg.api_base_url,
                           system_prompt=PROJECT_PROMPT, tools=[tool],
                           status_fn=lambda: _project_context(campaign))
    campaign.frontdesk = agent
    return agent


@campaign_router.post("/{campaign_id}/chat/send")
async def campaign_chat_send(campaign_id: str, body: ChatBody) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    campaign.chat_history.append({"role": "user", "text": body.text, "timestamp": time.time()})
    try:
        result = await _project_frontdesk(campaign).handle_user_message(body.text)
    except Exception as exc:
        logger.exception("Project front desk failed")
        campaign.chat_history.append({"role": "assistant", "text": f"Front desk error: {exc}",
                                      "timestamp": time.time()})
        return {"status": "error", "error": str(exc)}
    record: dict[str, Any] = {"role": "assistant", "text": result.get("text", ""), "timestamp": time.time()}
    if result.get("proposal"):
        record["proposal"] = result["proposal"]
    campaign.chat_history.append(record)
    return {"status": "sent", "campaign_id": campaign_id, "response": record["text"],
            **({"proposal": record["proposal"]} if "proposal" in record else {})}


class ConfirmProposalBody(BaseModel):
    skip_phases: list[str] | None = None
    model: str = ""
    models: dict[str, Any] | None = None   # per-role model overrides from the card's advanced settings
    autonomy_mode: str = ""
    ideation_mode: str = ""             # roundtable | independent; empty: the proposal's
    max_cost_usd: float | None = None   # null: no limit; omitted: the proposal's default


@campaign_router.post("/{campaign_id}/proposals/{proposal_id}/confirm")
async def confirm_proposal(campaign_id: str, proposal_id: str, body: ConfirmProposalBody) -> dict[str, Any]:
    """The researcher confirms a proposal: start it as an experiment."""
    campaign = _ensure_campaign(campaign_id)
    proposal = campaign.proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(404, "Proposal not found")
    if proposal.confirmed:
        raise HTTPException(409, "Proposal already confirmed")
    p = proposal.params
    req = StartExperimentRequest(
        instruction=p["instruction"], name=p.get("name", ""), inputs=p.get("inputs", []),
        model=body.model or p.get("model", ""),
        skip_phases=body.skip_phases if body.skip_phases is not None else proposal.suggested_skip,
        ideation_mode=body.ideation_mode or p.get("ideation_mode", ""),
        models=body.models, autonomy_mode=body.autonomy_mode,
        max_cost_usd=body.max_cost_usd if "max_cost_usd" in body.model_fields_set else proposal.max_cost_usd)
    result = await _start(campaign, req)
    proposal.confirmed = True
    run = campaign.experiments.get(result["experiment_id"])
    cfg = getattr(getattr(run, "engine", None), "_config", None)
    proposal.confirmed_settings = {
        "experiment_id": result["experiment_id"],
        "skip_phases": result["skip_phases"],
        "ideation_mode": cfg.ideation.mode if cfg else req.ideation_mode,
        "max_cost_usd": cfg.budget.max_cost_usd if cfg else req.max_cost_usd,
        "autonomy_mode": cfg.human.autonomy.value if cfg else req.autonomy_mode,
        "models": cfg.models.model_dump() if cfg else (req.models or {}),
    }
    campaign.note(f"Experiment started: {result['experiment_id']}")
    return {"status": "started", "experiment_id": result["experiment_id"],
            "skip_phases": result["skip_phases"], "active_phases": result["active_phases"]}


@campaign_router.get("/{campaign_id}/chat/history")
async def campaign_chat_history(campaign_id: str, limit: int = 100) -> list[dict[str, Any]]:
    campaign = _ensure_campaign(campaign_id)
    history = campaign.chat_history[-limit:]
    for msg in history:
        pid = msg["proposal"].get("proposal_id") if "proposal" in msg else None
        if pid in campaign.proposals:
            msg["proposal"] = campaign.proposals[pid].to_dict()
    return history


@campaign_router.get("/{campaign_id}/chat/status")
async def campaign_chat_status(campaign_id: str) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    exps = list(campaign.experiments.values())
    pending = [{"experiment_id": e.run_id, **d} for e in exps if (d := e.pending_decision())]
    return {
        "campaign_id": campaign_id,
        "agent_active": any(e.status in ("running", "starting") for e in exps),
        "active_experiments": sum(e.status in ("running", "starting") for e in exps),
        "awaiting_human": sum(e.status == "awaiting_human" for e in exps),
        "pending_decisions": pending,
    }


# ---------------------------------------------------------------------------
# Experiment files
# ---------------------------------------------------------------------------

_CONTAINER_PREFIX = "@container/"


def _check_path(path: str) -> str:
    if not path or ".." in path.split("/") or path.startswith("/") or not _SAFE_PATH.match(path):
        raise HTTPException(400, f"Invalid path: {path!r}")
    return path


def _container(run: ExperimentRunState) -> tuple[Any, str] | None:
    backend = getattr(run.engine, "_execution_backend", None) if run.engine else None
    cid = getattr(backend, "container_id", None)
    if backend is None or not cid or run.status not in ("running", "awaiting_human", "starting"):
        return None
    return backend, cid


def _disk_tree(base: Path, rel: Path = Path("")) -> list[dict[str, Any]]:
    out = []
    try:
        entries = sorted((base / rel).iterdir(), key=lambda p: (p.is_file(), p.name))
    except OSError:
        return out
    for child in entries:
        if child.name.startswith(".") and child.name != ".claude":
            continue
        r = rel / child.name
        if child.is_dir():
            out.append({"name": child.name, "type": "dir", "children": _disk_tree(base, r)})
        else:
            out.append({"name": child.name, "path": str(r), "type": child.suffix.lstrip(".") or "file"})
    return out


def _find_tree(output: str) -> list[dict[str, Any]]:
    """Nested tree from ``find /app -printf '%y %P\\n'`` output (paths under @container/)."""
    root: dict[str, Any] = {"children": []}
    dirs = {"": root}
    for line in sorted(output.splitlines(), key=lambda line: line[2:]):
        kind, _, rel = line.partition(" ")
        if not rel:
            continue
        parent_key, _, name = rel.rpartition("/")
        parent = dirs.get(parent_key)
        if parent is None:
            continue
        if kind == "d":
            node = {"name": name, "type": "dir", "children": []}
            dirs[rel] = node
        else:
            node = {"name": name, "path": _CONTAINER_PREFIX + rel, "type": Path(name).suffix.lstrip(".") or "file"}
        parent["children"].append(node)
    return root["children"]


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/workspace/tree")
async def workspace_tree(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """The experiment's files: its campaign directory and, while it runs, the container's /app."""
    run = _get_run(campaign_id, exp_id)
    files = _disk_tree(run.campaign_dir) if run.campaign_dir.is_dir() else []
    live = _container(run)
    if live:
        backend, _ = live
        code, out, _ = await backend.exec_command(
            "find /app -mindepth 1 -maxdepth 5 -not -path '*/.*' -printf '%y %P\\n' | head -3000", timeout=15)
        if code == 0:
            files.insert(0, {"name": "app (live container)", "type": "dir", "children": _find_tree(out)})
    return {"experiment_id": exp_id, "files": files}


async def _read_bytes(run: ExperimentRunState, path: str) -> bytes:
    path = _check_path(path)
    if path.startswith(_CONTAINER_PREFIX):
        live = _container(run)
        if not live:
            raise HTTPException(404, "The container is not running")
        backend, _ = live
        code, out, err = await backend.exec_command(
            f"base64 -w0 /app/{_check_path(path[len(_CONTAINER_PREFIX):])}", timeout=30)
        if code != 0:
            raise HTTPException(404, err.strip() or "File not found")
        return base64.b64decode(out)
    local = (run.campaign_dir / path).resolve()
    if not local.is_relative_to(run.campaign_dir.resolve()) or not local.is_file():
        raise HTTPException(404, f"File not found: {path}")
    return local.read_bytes()


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/workspace/file")
async def workspace_file(campaign_id: str, exp_id: str, path: str) -> dict[str, Any]:
    data = await _read_bytes(_get_run(campaign_id, exp_id), path)
    if b"\x00" in data[:1024]:
        return {"path": path, "content": None, "binary": True, "size": len(data)}
    text = data.decode(errors="replace")
    return {"path": path, "content": text[:500_000], "binary": False, "size": len(data),
            "truncated": len(text) > 500_000}


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/workspace/raw")
async def workspace_file_raw(campaign_id: str, exp_id: str, path: str) -> Response:
    data = await _read_bytes(_get_run(campaign_id, exp_id), path)
    return Response(content=data, media_type=mimetypes.guess_type(path)[0] or "application/octet-stream")


class WorkspaceWriteBody(BaseModel):
    path: str
    content: str


@campaign_router.put("/{campaign_id}/experiments/{exp_id}/workspace/file")
async def workspace_write(campaign_id: str, exp_id: str, body: WorkspaceWriteBody) -> dict[str, Any]:
    """Edit a file of the experiment (the researcher's own edits; the agents see them)."""
    run = _get_run(campaign_id, exp_id)
    path = _check_path(body.path)
    if path.startswith(_CONTAINER_PREFIX):
        live = _container(run)
        if not live:
            raise HTTPException(409, "The container is not running")
        _, cid = live
        target = "/app/" + _check_path(path[len(_CONTAINER_PREFIX):])
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", cid, "sh", "-c", 'mkdir -p "$(dirname "$1")" && cat > "$1"', "sh", target,
            stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate(body.content.encode())
        if proc.returncode != 0:
            raise HTTPException(500, err.decode().strip() or "write failed")
        return {"path": path, "size": len(body.content), "synced_to_docker": True}
    local = (run.campaign_dir / path).resolve()
    if not local.is_relative_to(run.campaign_dir.resolve()):
        raise HTTPException(400, "Invalid path")
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(body.content)
    return {"path": path, "size": len(body.content), "synced_to_docker": False}


# ---------------------------------------------------------------------------
# Defaults for the console's forms
# ---------------------------------------------------------------------------

settings_router = APIRouter(prefix="/api/settings", tags=["settings"])

# Offered in the model fields of the proposal card (any OpenRouter ID can be typed)
MODEL_SUGGESTIONS = [
    "anthropic/claude-opus-5.5", "anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5",
    "anthropic/claude-fable-5.1", "openai/gpt-5.6-sol", "openai/gpt-5.6-luna", "openai/gpt-5.6-terra",
    "google/gemini-3.1-pro-preview", "google/gemini-3-flash-preview", "moonshotai/kimi-k3",
    "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4.1-flash", "x-ai/grok-4.7", "z-ai/glm-5.3",
    "qwen/qwen3.8-max-prime",
]


@settings_router.get("")
async def get_settings() -> dict[str, Any]:
    """Settings a new experiment gets unless the proposal card changes them."""
    from aurora.config import AuroraConfig
    from aurora.core.requirements import available_models

    cfg = AuroraConfig.model_validate({**base_config(), "objective": "defaults"})
    served = available_models(cfg.api_base_url) if "openrouter.ai" in cfg.api_base_url else None
    return {
        "models": cfg.models.model_dump(),
        "autonomy_mode": cfg.human.autonomy.value,
        "ideation_mode": cfg.ideation.mode,
        "max_cost_usd": default_max_cost(),
        "model_suggestions": [m for m in MODEL_SUGGESTIONS if served is None or m in served],
    }

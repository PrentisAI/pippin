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
import contextlib
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
from aurora.examples import is_example

logger = logging.getLogger(__name__)

# Events sent to open pages but not stored or replayed
TRANSIENT_EVENTS = {"ideation_stream"}

store = LazyStore()
campaign_router = APIRouter(prefix="/api/campaigns", tags=["projects"])

# Answers the approval bar sends, mapped to human-gate actions
_GATE_ACTIONS = {"continue": "continue", "approve": "continue", "instruct": "instruct",
                 "override": "instruct", "modify": "instruct", "stop": "stop", "abort": "stop",
                 "reject": "stop", "propose": "propose"}
_SAFE_PATH = re.compile(r"^[\w .@+=,%-]+(/[\w .@+=,%-]+)*$")


def server_config() -> dict[str, Any]:
    """The server's base campaign configuration (``aurora serve --config``), as a dict."""
    path = os.environ.get("AURORA_SERVER_CONFIG")
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.pop("objective", None)
    data.pop("campaign_id", None)
    return data


def base_config() -> dict[str, Any]:
    """What new experiments start from: the server's base config with the console's settings over it.

    With accounts, the API key is the asking person's (theirs, else the team's if the team allows):
    model calls a person causes are paid by them, never by the server's key by accident.  Their own
    defaults (chosen in the welcome wizard: how hands-on, the budget) come over the console's."""
    from aurora.api.console_settings import deep_merge, load_overlay
    data = deep_merge(server_config(), load_overlay())
    from aurora.auth.guard import current_user
    user = current_user()
    if not user.is_local:
        from aurora.auth.store import auth_store
        mine = auth_store.get_pref(user.user_id, "defaults")
        if isinstance(mine, dict):
            data = deep_merge(data, mine)
        from aurora.auth.secrets import key_for
        key, _ = key_for(user)
        data["api_key"] = key or NO_KEY
    return data


# stands in for a missing personal key, so nothing falls back to the server's own key
NO_KEY = "no-key-set"


def _require_key() -> None:
    from aurora.auth.guard import current_user
    user = current_user()
    if user.is_local:
        return
    from aurora.auth.secrets import key_for
    if not key_for(user)[0]:
        raise HTTPException(400, "You have no API key yet: add yours under your account (or ask an admin "
                                 "to let members use the team's key)")


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

def _person_name(user_id: str) -> str | None:
    if user_id in ("", "local"):
        return None
    from aurora.auth.store import auth_store
    u = auth_store.get_user(user_id)
    return u["name"] if u else None


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
        # the front desk: on while the run runs, and afterwards for questions about it; off: no model calls
        self.frontdesk_enabled = True
        self.finished_desks: dict[str, Any] = {}  # per person: the front desk once the run no longer runs
        self.started_by = "local"         # who started it: their key pays, their channels are notified

    @property
    def campaign_dir(self) -> Path:
        return _projects_root() / self.campaign_id / self.run_id

    async def broadcast(self, event: dict[str, Any]) -> None:
        # live previews (text still being written) go to the open pages only; the finished
        # text arrives as its own event, which is kept
        if event.get("event_type") not in TRANSIENT_EVENTS:
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
            "resources": self.engine.resources if self.engine is not None else self._saved_resources(),
            "frontdesk_enabled": self.frontdesk_enabled,
            "started_by": _person_name(self.started_by),
            "max_cost_usd": (self.engine._config.budget.max_cost_usd if self.engine is not None
                             else self._saved_budget()),
            "estimate": self._estimate(),
        }

    def _estimate(self) -> dict[str, Any] | None:
        """What the run was estimated to cost and take, when it was started from a card that showed it."""
        if self.engine is not None:
            b = self.engine._config.budget
            return {"cost": b.estimate_usd, "hours": b.estimate_hours} if b.estimate_usd else None
        try:
            row = store.get_experiment(self.run_id) or {}
            est = (json.loads(row.get("config_json") or "{}").get("request") or {}).get("estimate")
            return est if est and est.get("cost") else None
        except Exception:
            return None

    def _saved_budget(self) -> float | None:
        try:
            row = store.get_experiment(self.run_id) or {}
            req = json.loads(row.get("config_json") or "{}").get("request") or {}
            return req.get("max_cost_usd") if "max_cost_usd" in req else default_max_cost()
        except Exception:
            return None

    def _saved_resources(self) -> dict[str, Any] | None:
        """Limits a resume or re-run would use: the saved request's, else the base config's."""
        try:
            row = store.get_experiment(self.run_id) or {}
            req = json.loads(row.get("config_json") or "{}").get("request") or {}
            sb = base_config().get("sandbox", {})
            from aurora.config import SandboxConfig
            d = SandboxConfig.model_validate(sb)
            return {"docker": d.docker, "cpus": req.get("cpus") or d.cpus,
                    "memory_mb": req.get("memory_mb") or d.memory_mb, "container": None}
        except Exception:
            return None


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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExperimentProposal:
        p = cls(d.get("params", {}), d.get("skip_phases", []), d.get("summary", ""))
        p.proposal_id = d.get("proposal_id", p.proposal_id)
        p.kind = d.get("kind", p.kind)
        p.max_cost_usd = d.get("max_cost_usd", p.max_cost_usd)
        p.created_at = d.get("created_at", p.created_at)
        p.confirmed = bool(d.get("confirmed"))
        p.superseded = bool(d.get("superseded"))
        p.confirmed_settings = d.get("confirmed_settings")
        return p


class Conversation:
    """One person's conversation with a project's front desk, and the proposals drafted in it."""

    def __init__(self) -> None:
        self.chat_history: list[dict[str, Any]] = []
        self.frontdesk: Any = None
        self.proposals: dict[str, ExperimentProposal] = {}


def _me() -> str:
    from aurora.auth.guard import current_user
    return current_user().user_id


class CampaignState:
    """A project: experiments, and each person's conversation with its front desk.

    ``chat_history``, ``frontdesk`` and ``proposals`` are those of the person asking (the local
    owner without accounts): nobody sees another person's conversation or drafts.
    """

    def __init__(self, campaign_id: str, name: str, objective: str = "") -> None:
        self.campaign_id = campaign_id
        self.name = name
        self.objective = objective
        self.created_at = time.time()
        self.updated_at = time.time()
        self.experiments: dict[str, ExperimentRunState] = {}
        self._conversations: dict[str, Conversation] = {}

    def conversation(self, user_id: str | None = None) -> Conversation:
        uid = user_id or _me()
        conv = self._conversations.get(uid)
        if conv is None:
            conv = Conversation()
            from aurora.auth.store import auth_store
            access = auth_store.project_access(self.campaign_id) or {}
            # the single conversation from before accounts belongs to the project's owner
            legacy = uid == "local" or uid == access.get("owner_id")
            chat, proposals = store.load_project_chat(self.campaign_id, uid, legacy=legacy)
            conv.chat_history = chat
            for d in proposals:
                prop = ExperimentProposal.from_dict(d)
                conv.proposals[prop.proposal_id] = prop
            self._conversations[uid] = conv
        return conv

    @property
    def chat_history(self) -> list[dict[str, Any]]:
        return self.conversation().chat_history

    @chat_history.setter
    def chat_history(self, value: list[dict[str, Any]]) -> None:
        self.conversation().chat_history = value

    @property
    def frontdesk(self) -> Any:
        return self.conversation().frontdesk

    @frontdesk.setter
    def frontdesk(self, value: Any) -> None:
        self.conversation().frontdesk = value

    @property
    def proposals(self) -> dict[str, ExperimentProposal]:
        return self.conversation().proposals

    def to_summary(self) -> dict[str, Any]:
        exps = list(self.experiments.values())
        active = sum(1 for e in exps if e.status in ("running", "starting", "awaiting_human"))
        return {
            "campaign_id": self.campaign_id, "name": self.name, "objective": self.objective,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "experiment_count": len(exps), "active_experiments": active,
            "completed_experiments": len(exps) - active,
            "needs_attention": any(e.status == "awaiting_human" for e in exps),
            "example": is_example(self.campaign_id),
        }

    def note(self, text: str) -> None:
        self.chat_history.append({"role": "system", "text": text, "timestamp": time.time()})
        self.save()

    def save(self) -> None:
        """Keep the asking person's conversation and its proposals (they survive a server restart)."""
        try:
            store.save_project_chat(self.campaign_id, _me(), self.chat_history[-500:],
                                    [p.to_dict() for p in self.proposals.values()][-50:])
        except Exception:
            logger.warning("Could not save the conversation of project %s", self.campaign_id, exc_info=True)


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
        try:
            cfg_json = json.loads(e.get("config_json") or "{}")
            saved = cfg_json.get("request") or {}
            rs.frontdesk_enabled = saved.get("frontdesk") is not False
            rs.started_by = cfg_json.get("started_by") or "local"
        except ValueError:
            pass
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


def _own(campaign_id: str) -> None:
    """The person who creates a project owns it (new projects follow the team's confidentiality rule)."""
    from aurora.auth.guard import current_user
    from aurora.auth.store import auth_store
    user = current_user()
    auth_store.init_project(campaign_id, user.user_id,
                            confidential=bool(not user.is_local and auth_store.rules().get("new_projects_confidential")))
    if not user.is_local:
        auth_store.log(user.user_id, "created a project", campaign_id)


@campaign_router.post("/quick")
async def quick_create_campaign(req: QuickCreateRequest) -> dict[str, Any]:
    """Create a project from the first message the researcher types."""
    campaign_id = f"p_{time.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
    name = req.prompt[:60] + ("..." if len(req.prompt) > 60 else "")
    campaign = CampaignState(campaign_id, name, req.prompt)
    _campaigns[campaign_id] = campaign
    campaign.note("Project created")
    store.save_campaign(campaign_id, name, req.prompt, campaign.created_at, campaign.updated_at)
    _own(campaign_id)
    return {"campaign_id": campaign_id, "name": name, "objective": req.prompt, "status": "created"}


@campaign_router.post("/")
async def create_campaign(req: CreateCampaignRequest) -> dict[str, Any]:
    return await create_quick_project(req.name, req.objective, summary=True)


async def create_quick_project(name: str, objective: str, *, summary: bool = False) -> dict[str, Any]:
    """A new project owned by the asking person."""
    campaign_id = f"p_{time.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
    campaign = CampaignState(campaign_id, name, objective)
    _campaigns[campaign_id] = campaign
    campaign.note("Project created")
    store.save_campaign(campaign_id, name, objective, campaign.created_at, campaign.updated_at)
    _own(campaign_id)
    return campaign.to_summary() if summary else {"campaign_id": campaign_id, "name": name, "objective": objective}


@campaign_router.get("/")
async def list_campaigns() -> list[dict[str, Any]]:
    """The projects the asking user may see."""
    from aurora.auth.guard import current_user, visible
    from aurora.auth.store import auth_store
    me = current_user()
    ids = visible(me, [row["campaign_id"] for row in store.list_campaigns()])
    if auth_store.get_pref(me.user_id, "examples_hidden"):
        ids = [cid for cid in ids if not is_example(cid)]
    results = [{**_ensure_campaign(cid).to_summary(), "sharing": _sharing(cid, me)} for cid in ids]
    return sorted(results, key=lambda c: c["updated_at"], reverse=True)


@campaign_router.get("/others")
async def other_projects() -> list[dict[str, Any]]:
    """Projects of the team the asking member is not in, when the team's rules let members see that
    they exist: only the name and whom to ask.  Never confidential ones."""
    from aurora.auth.guard import current_user, visible
    from aurora.auth.store import auth_store
    me = current_user()
    if me.is_local or me.is_guest or not auth_store.rules().get("members_see_all_projects"):
        return []
    all_ids = [row["campaign_id"] for row in store.list_campaigns()]
    mine = set(visible(me, all_ids))
    out = []
    for cid in all_ids:
        access = auth_store.project_access(cid) or {}
        if cid in mine or access.get("confidential"):
            continue
        owner = auth_store.get_user(access.get("owner_id") or "") or {}
        row = store.get_campaign(cid) or {}
        out.append({"campaign_id": cid, "name": row.get("name", ""), "owner": owner.get("name", ""),
                    "owner_email": owner.get("email", "")})
    return sorted(out, key=lambda x: x["name"].lower())


def _sharing(cid: str, user: Any) -> dict[str, Any] | None:
    """How a project is shared, as its list entry shows it (None: the console has no accounts)."""
    from aurora.auth.guard import accounts_on, rights
    from aurora.auth.store import auth_store
    if not accounts_on():
        return None
    access = auth_store.project_access(cid) or {}
    return {"members": len(auth_store.members(cid)), "team_access": access.get("team_access") or "none",
            "confidential": bool(access.get("confidential")), "my_rights": sorted(rights(user, cid))}


@campaign_router.get("/{campaign_id}")
async def get_campaign(campaign_id: str) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    summary = campaign.to_summary()
    from aurora.auth.guard import current_user
    summary["sharing"] = _sharing(campaign_id, current_user())
    summary["experiments"] = sorted((e.to_summary() for e in campaign.experiments.values()),
                                    key=lambda e: e["started_at"], reverse=True)
    return summary


class RenameRequest(BaseModel):
    name: str


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise HTTPException(400, "The name is empty")
    return name[:200]


@campaign_router.patch("/{campaign_id}")
async def rename_campaign(campaign_id: str, req: RenameRequest) -> dict[str, Any]:
    """Rename a project (its ID and directory stay)."""
    campaign = _ensure_campaign(campaign_id)
    campaign.name = _clean_name(req.name)
    store.rename_campaign(campaign_id, campaign.name)
    return campaign.to_summary()


def _active(run: ExperimentRunState) -> bool:
    return run.status in ("starting", "running", "awaiting_human")


def _dir_size(path: Path) -> int:
    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


async def _remove_dir(path: Path) -> None:
    """Delete a project's or run's directory; files a container left to root are removed through Docker."""
    import shutil
    root = _projects_root().resolve()
    path = path.resolve()
    if not path.is_relative_to(root) or path == root or not path.exists():
        return
    try:
        await asyncio.to_thread(shutil.rmtree, path)
    except PermissionError:
        from aurora.core.docker_backend import PROXY_IMAGE, _run
        code, _, err = await _run("docker", "run", "--rm", "-v", f"{path.parent}:/parent", PROXY_IMAGE,
                                  "rm", "-rf", f"/parent/{path.name}", timeout=1800)
        if code != 0:
            raise HTTPException(500, f"Could not delete {path}: {err.strip()}") from None


@campaign_router.get("/{campaign_id}/disk")
async def campaign_disk_usage(campaign_id: str) -> dict[str, Any]:
    """Bytes the project's files take on disk."""
    _ensure_campaign(campaign_id)
    path = _projects_root() / campaign_id
    return {"bytes": await asyncio.to_thread(_dir_size, path), "path": str(path)}


@campaign_router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: str, delete_files: bool = False) -> dict[str, Any]:
    """Delete a project and its runs from AURORA; with *delete_files*, also their files on disk.
    Running experiments must be stopped first."""
    campaign = _ensure_campaign(campaign_id)
    running = [r.task_name or r.run_id for r in campaign.experiments.values() if _active(r)]
    if running:
        raise HTTPException(409, f"Stop the running experiments first: {', '.join(running)}")
    if delete_files:
        await _remove_dir(_projects_root() / campaign_id)
    _campaigns.pop(campaign_id, None)
    store.delete_campaign(campaign_id)
    store.delete_events(campaign_id)
    from aurora.auth.guard import current_user
    from aurora.auth.store import auth_store
    auth_store.drop_project(campaign_id)
    from aurora.literature.library import library
    library.drop_project(campaign_id)
    from aurora.brainstorm.store import bs_store
    bs_store.drop_space(campaign_id)
    from aurora.feed.store import feed_store
    feed_store.drop_project(campaign_id)
    auth_store.log(current_user().user_id, "deleted a project", campaign_id)
    return {"ok": True, "campaign_id": campaign_id, "files_deleted": delete_files}


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
    ideation_live: bool = False        # the round table runs live in the project's brainstorm space
    ideation_develop: bool = False     # the seats develop the instruction as the researcher's own idea
    source_card: str = ""              # the brainstorm idea it starts from
    add_found: str = ""                # literature.add_found: all | cited | none
    max_cost_usd: float | None = None
    mock_mode: bool | None = None
    cpus: float | None = None          # sandbox.cpus
    memory_mb: int | None = None       # sandbox.memory_mb
    max_parallel_jobs: int | None = None   # agent.max_parallel_jobs
    based_on: list[str] = []           # runs of the project this experiment builds on
    frontdesk: bool | None = None      # human.frontdesk
    estimate: dict[str, Any] | None = None  # what it was estimated to cost and take ({cost: [lo, hi], hours: [lo, hi]})
    sandbox_docker: bool | None = None  # sandbox.docker (the demo runs without a container)


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
    if req.cpus:
        raw.setdefault("sandbox", {})["cpus"] = req.cpus
    if req.memory_mb:
        raw.setdefault("sandbox", {})["memory_mb"] = req.memory_mb
    if req.max_parallel_jobs:
        raw.setdefault("agent", {})["max_parallel_jobs"] = req.max_parallel_jobs
    if req.frontdesk is not None:
        raw.setdefault("human", {})["frontdesk"] = req.frontdesk
    if req.based_on:
        raw["objective"] = req.instruction + _prior_runs_note(campaign, req.based_on)
    if req.inputs or req.data_paths:
        from aurora.api.routes.files import resolve_input  # project:<path> -> the project's files
        raw["inputs"] = list(raw.get("inputs", [])) + [resolve_input(campaign.campaign_id, x)
                                                       for x in req.inputs + req.data_paths]
    if req.autonomy_mode:
        raw.setdefault("human", {})["autonomy"] = req.autonomy_mode
    if req.ideation_mode:
        raw.setdefault("ideation", {})["mode"] = req.ideation_mode
    if req.ideation_live:
        raw.setdefault("ideation", {}).update({"live": True, "mode": "roundtable"})
    if req.ideation_develop:
        raw.setdefault("ideation", {})["develop_objective"] = True
    if req.source_card:
        raw.setdefault("ideation", {})["source_card"] = req.source_card
    from aurora.literature.library import library
    add_found = req.add_found or library.project_rule(campaign.campaign_id)
    if add_found:
        raw.setdefault("literature", {})["add_found"] = add_found
    # an explicit null in the request means no cost limit
    max_cost = req.max_cost_usd if "max_cost_usd" in req.model_fields_set else default_max_cost()
    raw.setdefault("budget", {})["max_cost_usd"] = max_cost
    if req.estimate and isinstance(req.estimate.get("cost"), list) and isinstance(req.estimate.get("hours"), list):
        raw["budget"]["estimate_usd"] = [float(x) for x in req.estimate["cost"][:2]]
        raw["budget"]["estimate_hours"] = [float(x) for x in req.estimate["hours"][:2]]
    if req.mock_mode is not None:
        raw["mock_mode"] = req.mock_mode
    if req.sandbox_docker is not None:
        raw["sandbox"] = {**raw.get("sandbox", {}), "docker": req.sandbox_docker}
    return AuroraConfig.model_validate(raw)


def live_notifications(user_id: str | None = None) -> Any:
    """Notification settings as they are now (channels added later reach running experiments):
    a person's own with accounts, else the console's."""
    from aurora.config import NotificationsConfig
    uid = user_id or _me()
    if uid != "local":
        from aurora.auth.store import auth_store
        return NotificationsConfig.model_validate(auth_store.get_pref(uid, "notifications") or {})
    from aurora.api.console_settings import deep_merge, load_overlay
    return NotificationsConfig.model_validate(deep_merge(server_config(), load_overlay()).get("notifications") or {})


def console_url() -> str:
    return (live_notifications().console_url or os.environ.get("AURORA_CONSOLE_URL", "")).rstrip("/")


def _notifier(campaign: CampaignState, run: ExperimentRunState) -> Any:
    from aurora.notify import Notifier
    base = console_url()

    def actions(event: Any) -> list[tuple[str, str]]:
        """Signed links that answer the decision from the message (only with the console's address)."""
        from aurora.human import actions as act
        gate = event.data.get("gate_id") or ""
        if not base or not gate:
            return []
        options = event.data.get("options") or ["continue", "stop"]
        return [(act.LABELS[a], f"{base}/a/{act.make(campaign.campaign_id, run.run_id, gate, a, run.started_by or 'local')}")
                for a in ("continue", "stop") if a in options]

    def hold(item: dict[str, Any]) -> None:
        """Quiet hours: keep it for the person; the console sends what was held when they end."""
        from aurora.auth.store import auth_store
        uid = run.started_by or "local"
        held = auth_store.get_pref(uid, "notify_held") or []
        held.append(item)
        auth_store.set_pref(uid, "notify_held", held[-200:])
    return Notifier(lambda: live_notifications(run.started_by), run_name=run.task_name,
                    link=f"{base}/campaigns/{campaign.campaign_id}/experiments/{run.run_id}" if base else "",
                    extra={"project_id": campaign.campaign_id, "project": campaign.name,
                           "experiment_id": run.run_id}, actions=actions, hold=hold)


async def send_held_notifications() -> int:
    """Send each person what was held during their quiet hours, once those have ended; how many people."""
    from aurora.auth.store import auth_store
    from aurora.notify import deliver, held_digest, in_quiet_hours
    users = [u["user_id"] for u in auth_store.list_users()] or ["local"]
    sent = 0
    for uid in users:
        held = auth_store.get_pref(uid, "notify_held") or []
        if not held:
            continue
        cfg = live_notifications(uid)
        if in_quiet_hours(cfg):
            continue
        auth_store.set_pref(uid, "notify_held", [])
        kinds = {h["kind"] for h in held}
        title, text = held_digest(held)
        for c in cfg.channels:
            if c.enabled and kinds & set(c.events):
                error = await deliver(c, "held", title, text, console_url())
                if error:
                    logger.warning("Sending held notifications to %s failed: %s", c.name or c.type, error)
        sent += 1
    return sent


async def _launch(campaign: CampaignState, run: ExperimentRunState, config: Any, *, resume: bool = False) -> None:
    """Run the engine for *run* in the background and mirror its events into the run state."""
    from aurora.core.engine import ResearchEngine
    from aurora.core.event_bridge import EventBridge
    from aurora.core.requirements import check_runtime_requirements

    check_runtime_requirements(config)   # fail at request time, with the reason, not in the background
    engine = ResearchEngine(config)
    engine.project_id = campaign.campaign_id
    engine.display_name = run.task_name
    engine.notifier = _notifier(campaign, run)
    engine.may_decide = lambda: may_decide(campaign.campaign_id, run)
    engine.prior_chat = list(run.chat_history)
    engine.extra_frontdesk_tools = _run_tools(campaign) + _run_control_tools(run)
    run.engine = engine
    run.finished_desks = {}
    # brainstorm with me: the meeting, live in the project's brainstorm space
    meeting = None
    if getattr(config.ideation, "live", False):
        from aurora.brainstorm.live import LiveMeeting
        meeting = LiveMeeting(campaign.campaign_id, run.run_id, run.task_name, run.started_by,
                              source_card=config.ideation.source_card)

    async def mirror(event: Any) -> None:
        name, data = event.name, event.data
        if meeting is not None:
            await meeting.on_event(name, data)
        if name == "phase_started":
            run.phase = data.get("phase", run.phase)
            store.update_experiment(run.run_id, status="running", phase=run.phase)
        elif name == "tool_call_start":
            run.turn += 1
        elif name == "human_gate_opened":
            run.status = "awaiting_human"
            campaign.note(f"{run.task_name}: needs your decision — {data.get('title', '')}")
            try:
                from aurora.memory import alerts
                base = console_url()
                alerts.decision_opened(run.started_by or "local", str(data.get("gate_id", "")), run.task_name,
                                       str(data.get("title", "")),
                                       f"{base}/campaigns/{campaign.campaign_id}/experiments/{run.run_id}" if base else "")
            except Exception:
                logger.debug("the decision alert could not be raised", exc_info=True)
        elif name == "human_gate_resolved":
            if run.status == "awaiting_human":
                run.status = "running"
            try:
                from aurora.memory import alerts
                alerts.decision_closed(run.started_by or "local", str(data.get("gate_id", "")))
            except Exception:
                logger.debug("the decision alert could not be closed", exc_info=True)
            run.decision_history.append({**data, "timestamp": time.time()})
        elif name == "resources_updated":     # changed live (console or front desk): a resume keeps it
            _update_saved_request(run.run_id, cpus=data.get("cpus"), memory_mb=data.get("memory_mb"))
        elif name == "frontdesk_response":
            run.chat_history.append({"role": "assistant", "text": data.get("text", ""), "timestamp": time.time()})
        run.cost_usd = engine.total_cost_usd

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
        except asyncio.CancelledError:
            # cancelled from outside (server shutting down): record it instead of staying "running"
            run.status, run.error = "stopped", "the run was cancelled"
            raise
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
    # the random part keeps two runs started in the same second apart
    run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}_{slug}-{uuid.uuid4().hex[:4]}"
    if not req.mock_mode:          # scripted models need no key
        _require_key()
    previous = campaign.experiments.get(run_id)
    try:
        config = _experiment_config(campaign, run_id, req)
    except Exception as exc:
        raise HTTPException(400, f"Invalid experiment settings: {exc}") from exc
    from aurora.auth.providers import config_models, models_problem
    problem = models_problem(config_models(config), campaign.campaign_id)
    if problem:
        raise HTTPException(400, problem + " Choose other models in the run's settings.")
    run = ExperimentRunState(run_id, campaign.campaign_id, req.name or slug, config.models.for_phase("research"),
                             config.human.autonomy.value)
    run.frontdesk_enabled = config.human.frontdesk
    run.started_by = (previous.started_by if previous is not None and resume else None) or _me()
    if resume:
        # keep the earlier stream and conversation visible (and in the front desk's memory)
        run.events = store.load_events(campaign.campaign_id, run_id)
        run.chat_history = _chat_from_events(run.events)
    if req.based_on:
        _write_prior_runs(campaign, run.campaign_dir, req.based_on)
    try:
        await _launch(campaign, run, config, resume=resume)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    campaign.experiments[run_id] = run
    store.save_experiment(run_id=run_id, campaign_id=campaign.campaign_id, task_name=run.task_name,
                          model=run.model, autonomy_mode=run.autonomy_mode, started_at=run.started_at,
                          config={"request": req.model_dump(), "started_by": run.started_by})
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


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/stop")
async def stop_experiment(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """Stop a running experiment (it ends at the next safe point and keeps its files)."""
    run = _get_run(campaign_id, exp_id)
    if run.engine is not None and _active(run):
        run.engine.request_stop("stopped by the user")
    return run.to_summary()


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/compute")
async def experiment_cluster_jobs(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """The experiment's cluster (SLURM) jobs, running or done."""
    from aurora.compute.slurm import saved_jobs
    run = _get_run(campaign_id, exp_id)
    compute = run.engine.compute if run.engine is not None else None
    if compute is not None:
        return {"enabled": True, "live": True, **await compute.status()}
    jobs = saved_jobs(run.campaign_dir)
    return {"enabled": bool(jobs), "live": False, "jobs": [j.view() for j in jobs],
            "gpu_hours": round(sum(j.gpu_hours for j in jobs), 3)}


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/compute/gpus")
async def experiment_gpu_usage(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """What the GPUs of the experiment's jobs are doing (nvidia-smi on the nodes or the machine)."""
    run = _get_run(campaign_id, exp_id)
    compute = run.engine.compute if run.engine is not None else None
    if compute is None:
        return {"backend": "", "gpus": [], "errors": [], "live": False}
    return {**await compute.gpu_usage(), "live": True}


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/compute/{job}/cancel")
async def cancel_cluster_job(campaign_id: str, exp_id: str, job: str) -> dict[str, Any]:
    """Cancel one of the experiment's cluster jobs; its agent is told."""
    run = _get_run(campaign_id, exp_id)
    compute = run.engine.compute if run.engine is not None else None
    if compute is None:
        raise HTTPException(409, "The experiment is not running")
    try:
        result = await compute.cancel(job, reason="cancelled by the researcher")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    j = result["job"]
    with contextlib.suppress(Exception):
        await run.engine.inject_message(f"[The researcher cancelled cluster job {j['id']} ({j['name']}).]")
    return result


@campaign_router.patch("/{campaign_id}/experiments/{exp_id}")
async def rename_experiment(campaign_id: str, exp_id: str, req: RenameRequest) -> dict[str, Any]:
    """Rename a run (its ID and directory stay; a resume or re-run keeps the name)."""
    run = _get_run(campaign_id, exp_id)
    run.task_name = _clean_name(req.name)
    if run.engine is not None and run.engine.notifier is not None:
        run.engine.notifier.run_name = run.task_name
    store.update_experiment(exp_id, task_name=run.task_name)
    _update_saved_request(exp_id, name=run.task_name)
    return run.to_summary()


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/disk")
async def experiment_disk_usage(campaign_id: str, exp_id: str) -> dict[str, Any]:
    """Bytes the run's files take on disk."""
    run = _get_run(campaign_id, exp_id)
    return {"bytes": await asyncio.to_thread(_dir_size, run.campaign_dir), "path": str(run.campaign_dir)}


@campaign_router.delete("/{campaign_id}/experiments/{exp_id}")
async def delete_experiment(campaign_id: str, exp_id: str, delete_files: bool = False) -> dict[str, Any]:
    """Delete a run from AURORA; with *delete_files*, also its files on disk. Stop it first."""
    campaign = _ensure_campaign(campaign_id)
    run = _get_run(campaign_id, exp_id)
    if _active(run):
        raise HTTPException(409, "Stop the experiment first")
    if delete_files:
        await _remove_dir(run.campaign_dir)
    campaign.experiments.pop(exp_id, None)
    store.delete_experiment(exp_id)
    store.delete_events(campaign_id, exp_id)
    _touch(campaign)
    return {"ok": True, "experiment_id": exp_id, "files_deleted": delete_files}


class DecisionRequest(BaseModel):
    action: str            # continue | instruct | propose | stop (approve / override / abort also accepted)
    message: str = ""
    gate_id: str = ""      # default: the oldest open gate
    rerun: list[str] = []  # for propose: run the direction through "ideation" and/or "screening" first


def may_decide(campaign_id: str, run: ExperimentRunState) -> tuple[bool, str]:
    """Whether the asking person may answer the run's decisions (the project's "who decides"
    setting), and their name for the record."""
    from aurora.auth.guard import current_user, rights
    from aurora.auth.store import auth_store
    user = current_user()
    if user.is_local:
        return True, ""
    have = rights(user, campaign_id)
    if "decide" not in have:
        return False, user.name
    access = auth_store.project_access(campaign_id) or {}
    rule = access.get("deciders") or "starter"
    if rule == "user":
        ok = user.user_id == access.get("decider_user")
    elif rule == "owners":
        ok = "manage" in have
    else:                                    # the starter, and the project's owners can step in
        ok = user.user_id == run.started_by or "manage" in have
    return ok, user.name


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/decide")
async def submit_decision(campaign_id: str, exp_id: str, req: DecisionRequest) -> dict[str, Any]:
    """Answer the experiment's open human gate."""
    run = _get_run(campaign_id, exp_id)
    allowed, who = may_decide(campaign_id, run)
    if not allowed:
        raise HTTPException(403, "In this project another person answers this run's decisions")
    pending = run.engine.human_gate.pending if run.engine is not None else []
    if not pending:
        raise HTTPException(409, "No decision is pending")
    gate_id = req.gate_id or pending[0].gate_id
    action = _GATE_ACTIONS.get(req.action.lower())
    if action is None:
        raise HTTPException(400, f"Unknown action {req.action!r}; use continue, instruct, propose or stop")
    if action == "instruct" and not req.message.strip():
        raise HTTPException(400, "An instruction needs a message")
    try:
        resolved = run.engine.human_gate.resolve(gate_id, action, req.message, rerun=req.rerun, by=who)
    except ValueError as exc:     # an action this gate does not offer, or a proposal without text
        raise HTTPException(400, str(exc)) from exc
    if not resolved:
        raise HTTPException(409, f"Gate {gate_id} is not open")
    from aurora.auth.guard import current_user
    from aurora.auth.store import auth_store
    auth_store.set_pref(current_user().user_id, "onboarding_decided", True)   # the getting-started checklist
    return {"ok": True, "decision_id": gate_id, "action": action}


class SetAutonomyRequest(BaseModel):
    autonomy_mode: str   # supervised | autonomous


@campaign_router.put("/{campaign_id}/experiments/{exp_id}/autonomy")
async def set_experiment_autonomy(campaign_id: str, exp_id: str, req: SetAutonomyRequest) -> dict[str, Any]:
    run = _get_run(campaign_id, exp_id)
    if req.autonomy_mode not in ("autonomous", "supervised"):
        raise HTTPException(400, "autonomy_mode must be 'autonomous' or 'supervised'")
    old = await _set_autonomy(run, req.autonomy_mode)
    return {"ok": True, "autonomy_mode": req.autonomy_mode, "previous_mode": old}


async def _set_autonomy(run: ExperimentRunState, mode: str) -> str:
    old, run.autonomy_mode = run.autonomy_mode, mode
    if run.engine is not None:
        run.engine.human_gate.set_autonomy(mode)
    store.update_experiment(run.run_id, autonomy_mode=mode)
    _update_saved_request(run.run_id, autonomy_mode=mode)   # a resume keeps the mode
    await run.broadcast({"type": "experiment_event", "run_id": run.run_id, "event_type": "autonomy_changed",
                         "data": {"old_mode": old, "new_mode": mode}, "timestamp": time.time()})
    return old


class SetBudgetRequest(BaseModel):
    max_cost_usd: float | None = None     # null: no limit


@campaign_router.put("/{campaign_id}/experiments/{exp_id}/budget")
async def set_experiment_budget(campaign_id: str, exp_id: str, req: SetBudgetRequest) -> dict[str, Any]:
    """Change an experiment's cost limit, at once if it runs; a resume keeps it."""
    run = _get_run(campaign_id, exp_id)
    if req.max_cost_usd is not None and req.max_cost_usd <= 0:
        raise HTTPException(400, "The budget must be positive, or null for no limit")
    return await _set_budget(run, req.max_cost_usd)


async def _set_budget(run: ExperimentRunState, value: float | None) -> dict[str, Any]:
    old = None
    if run.engine is not None:
        old = run.engine._config.budget.max_cost_usd
        run.engine._config.budget.max_cost_usd = value      # read at every budget check
        if run.engine._frontdesk_status is not None:
            run.engine._frontdesk_status.budget_usd = value
    _update_saved_request(run.run_id, max_cost_usd=value)
    spent = run.engine.total_cost_usd if run.engine is not None else run.cost_usd
    await run.broadcast({"type": "experiment_event", "run_id": run.run_id, "event_type": "budget_changed",
                         "data": {"old": old, "new": value, "spent": round(spent, 2)}, "timestamp": time.time()})
    note = ("already spent more than that: the experiment stops at its next budget check"
            if value is not None and spent >= value and _active(run) else "")
    return {"ok": True, "max_cost_usd": value, "spent_usd": round(spent, 2), **({"note": note} if note else {})}


def _run_control_tools(run: ExperimentRunState) -> list[Any]:
    """Tools of a running experiment's front desk that change its settings."""
    from aurora.human.frontdesk import FrontDeskTool

    async def mode(p: dict[str, Any]) -> Any:
        new = str(p.get("mode", ""))
        if new not in ("supervised", "autonomous"):
            return {"error": "mode must be supervised or autonomous"}
        return {"status": "switched", "from": await _set_autonomy(run, new), "to": new}

    async def budget(p: dict[str, Any]) -> Any:
        value = p.get("max_cost_usd")
        if value is not None and float(value) <= 0:
            return {"error": "the budget must be positive, or null for no limit"}
        return await _set_budget(run, None if value is None else float(value))

    return [
        FrontDeskTool("set_decision_mode",
                      "Switch how decisions are handled: supervised (gates wait for the researcher) or "
                      "autonomous (they continue at once). Only when the researcher asks.",
                      {"type": "object", "properties": {"mode": {"type": "string",
                                                                 "enum": ["supervised", "autonomous"]}},
                       "required": ["mode"]}, mode),
        FrontDeskTool("set_budget",
                      "Change the experiment's cost limit in US$ (null: no limit), effective at once. Only "
                      "when the researcher asks.",
                      {"type": "object", "properties": {"max_cost_usd": {"type": ["number", "null"]}},
                       "required": ["max_cost_usd"]}, budget),
    ]


class SetResourcesRequest(BaseModel):
    cpus: float | None = None
    memory_mb: int | None = None


async def _check_resources(cpus: float | None, memory_mb: int | None) -> None:
    from aurora.core.docker_backend import host_limits
    max_cpus, max_mem = await host_limits()
    if cpus is not None and not 0 < cpus <= max_cpus:
        raise HTTPException(400, f"cpus must be between 0 and {max_cpus} (the CPUs of the Docker host)")
    if memory_mb is not None and not 256 <= memory_mb <= (max_mem or memory_mb):
        raise HTTPException(400, f"memory_mb must be between 256 and {max_mem} (the memory of the Docker host)")


@campaign_router.put("/{campaign_id}/experiments/{exp_id}/resources")
async def set_experiment_resources(campaign_id: str, exp_id: str, req: SetResourcesRequest) -> dict[str, Any]:
    """Change the container's CPU and memory limits, live if the experiment runs; a resume or
    re-run keeps them."""
    run = _get_run(campaign_id, exp_id)
    if req.cpus is None and req.memory_mb is None:
        raise HTTPException(400, "Give cpus and/or memory_mb")
    await _check_resources(req.cpus, req.memory_mb)
    live = run.engine is not None and run.status in ("starting", "running", "awaiting_human")
    if live:
        try:
            resources = await run.engine.update_resources(req.cpus, req.memory_mb)
        except RuntimeError as exc:      # docker refused, e.g. less memory than the container uses
            raise HTTPException(409, str(exc)) from exc
    else:
        _update_saved_request(exp_id, **{k: v for k, v in (("cpus", req.cpus), ("memory_mb", req.memory_mb))
                                         if v is not None})
        saved = _saved_request(exp_id)
        resources = {"cpus": saved.cpus, "memory_mb": saved.memory_mb}
    return {"ok": True, "live": live, **resources}


def _update_saved_request(exp_id: str, **fields: Any) -> None:
    """Change settings of the saved request, which a resume or re-run starts from."""
    row = store.get_experiment(exp_id)
    if not row:
        return
    config = json.loads(row.get("config_json") or "{}")
    if "request" in config:
        config["request"].update(fields)
        store.update_experiment(exp_id, config_json=json.dumps(config))


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
    result = await _start(campaign, req, run_id=exp_id, resume=True)
    return result


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
    text: str = ""
    # files sent with the message (already uploaded): paths in the project's files (project chat)
    # or in the experiment's inputs/ (experiment chat)
    attachments: list[str] = []
    # items from the Library sent with a project message: [{kind: feed, item_id} | {kind: paper, paper_id}]
    library: list[dict[str, str]] = []


def _seed_keys(campaign_id: str, keys: list[Any]) -> list[str]:
    """The keys of real papers (found in the indexes, so stored in the library); a made-up key is dropped."""
    from aurora.literature.library import library
    out = []
    for k in keys:
        entry = library.paper(str(k).strip().strip("[]"))
        if entry and entry["key"] not in out:
            out.append(entry["key"])
    return out[:10]


def _seed_block(campaign_id: str, keys: list[str]) -> str:
    """The seed papers, as the run's agents read them in its brief (and recorded as its seeds)."""
    if not keys:
        return ""
    from aurora.api.routes.literature import _who
    from aurora.literature.library import library
    from aurora.literature.service import paper_card
    entries = [e for e in (library.paper(k) for k in keys) if e]
    for e in entries:          # the seeds join the project's literature
        library.add_item(campaign_id, e["paper_id"], source="person", added_by=_who())
        library.record_use(campaign_id, "seed", paper_id=e["paper_id"], by=_who())
    notes = library.notes(campaign_id, [e["paper_id"] for e in entries])
    cards = "\n".join(paper_card({**e, "notes": notes.get(e["paper_id"], [])}, abstract_words=150) for e in entries)
    return ("\n\n## Seed papers\nThe researcher brought these papers from their Library to start from. Develop the "
            "ideas from them (what they leave open, what to test on this project's data), cite them by key, and read "
            "them before planning (`aurora papers show <Key>`, `aurora papers pdf <Key>`).\n\n" + cards)


def _with_library(text: str, refs: list[dict[str, str]]) -> tuple[str, list[dict[str, Any]]]:
    """The message with the Library items it carries, and how they show in the conversation."""
    from aurora.api.routes.feed import library_ref, ref_text
    from aurora.feed.store import feed_store
    shown, read = [], []
    for ref in refs[:10]:
        body = ref_text(ref, _me())
        if body is None:
            continue
        read.append(body)
        if ref.get("kind") == "feed":
            shown.append(library_ref(feed_store.item(ref["item_id"])))
        else:
            from aurora.literature.library import library
            entry = library.paper(ref.get("paper_id", ""))
            shown.append({"kind": "paper", "paper_id": ref.get("paper_id"), "title": entry["paper"].title if entry else "",
                          "label": "Paper", "ok": True, "by": entry["paper"].byline() if entry else ""})
    if not read:
        return text, []
    return (f"{text}\n\n[From the researcher's Library, read it before answering:\n\n" + "\n\n".join(read)
            + "\n\nIf it calls for an experiment, offer concrete options (testing it on this project's data, "
            "explaining a disagreement with an earlier run, ...), and propose the one the researcher picks with "
            "propose_experiment. A paper with a key can also seed a run: propose_experiment with seed_papers=[its key] "
            "and ideation develops ideas from it.]"), shown


def _with_attachments(text: str, attachments: list[str], where: str) -> str:
    """The message as the front desk reads it: the text and what came with it."""
    if not attachments:
        return text
    listed = "\n".join(f"- {a}" for a in attachments[:50])
    return f"{text}\n\n[Attached by the researcher, {where}:\n{listed}]".strip()


@campaign_router.post("/{campaign_id}/experiments/{exp_id}/chat/send")
async def exp_chat_send(campaign_id: str, exp_id: str, body: ChatBody) -> dict[str, Any]:
    """A message about an experiment. While it runs, its front desk answers (and may relay it to the
    agent); afterwards a front desk answers questions about it from its files. With the front desk
    off, a running experiment's agent gets the message directly and a finished one gets no answer."""
    campaign = _ensure_campaign(campaign_id)
    run = _get_run(campaign_id, exp_id)
    if not run.chat_history:
        run.chat_history = _chat_from_events(run.events or store.load_events(campaign_id, exp_id))
    if not body.text.strip() and not body.attachments:
        raise HTTPException(400, "An empty message")
    by = _person_name(_me())
    run.chat_history.append({"role": "user", "text": body.text, "timestamp": time.time(),
                             **({"by": by} if by else {}),
                             **({"attachments": body.attachments} if body.attachments else {})})
    await run.broadcast({"type": "experiment_event", "run_id": exp_id, "event_type": "chat_message",
                         "data": {"role": "user", "text": body.text, **({"by": by} if by else {}),
                                  **({"attachments": body.attachments} if body.attachments else {})},
                         "timestamp": time.time()})
    message = _with_attachments(body.text, [f"inputs/{a}" for a in body.attachments],
                                "uploaded to the experiment's inputs/ (the agents see /app/inputs)")
    if by:
        message = f"[{by} writes] {message}"
    if run.engine is not None and _active(run):
        reply = await run.engine.handle_user_message(message)
        if run.engine._frontdesk is None and reply.get("text"):
            run.chat_history.append({"role": "assistant", "text": reply["text"], "timestamp": time.time()})
        return {"status": "sent", "run_id": exp_id, "response": reply.get("text", "")}
    if not run.frontdesk_enabled:
        return {"status": "frontdesk_off", "response": "The front desk of this experiment is off."}
    desk = _finished_desk(campaign, run)
    reply = await desk.handle_user_message(message)
    return {"status": "sent", "run_id": exp_id, "response": reply.get("text", "")}


def _chat_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation of a run, from its event log."""
    history = []
    for e in events:
        if e.get("event_type") == "chat_message" and (e.get("data") or {}).get("role", "user") == "user":
            history.append({"role": "user", "text": e["data"].get("text", ""), "timestamp": e.get("timestamp"),
                            **({"by": e["data"]["by"]} if e["data"].get("by") else {}),
                            **({"attachments": e["data"]["attachments"]} if e["data"].get("attachments") else {})})
        elif e.get("event_type") == "frontdesk_response":
            history.append({"role": "assistant", "text": e["data"].get("text", ""), "timestamp": e.get("timestamp")})
    return history


def _finished_desk(campaign: CampaignState, run: ExperimentRunState) -> Any:
    """The front desk of a run that no longer runs: questions about it, answered from its files."""
    from aurora.human.frontdesk import FINISHED_PROMPT, FrontDeskAgent
    if run.finished_desks.get(_me()) is not None:
        return run.finished_desks[_me()]
    try:
        cfg = _experiment_config(campaign, run.run_id, _saved_request(run.run_id))
    except HTTPException:
        from aurora.config import AuroraConfig
        cfg = AuroraConfig.model_validate({**base_config(), "objective": run.task_name or "experiment"})

    async def broadcast(data: dict[str, Any]) -> None:
        name = data.get("type", "frontdesk_event")
        if name == "frontdesk_response":
            run.chat_history.append({"role": "assistant", "text": data["data"].get("text", ""),
                                     "timestamp": time.time()})
        await run.broadcast({"type": "experiment_event", "run_id": run.run_id, "event_type": name,
                             "data": data.get("data", {}), "timestamp": time.time()})

    def status() -> str:
        return "\n".join([f"Experiment {run.run_id} ({run.task_name}), status: {run.status}, "
                          f"last phase: {run.phase or '-'}, cost: ${run.cost_usd:.2f}"]
                         + ([f"Error: {run.error}"] if run.error else []))

    desk = FrontDeskAgent(model=cfg.models.frontdesk, api_key=cfg.api_key_value, base_url=cfg.api_base_url,
                          system_prompt=FINISHED_PROMPT + f"\nExperiment objective: {cfg.objective[:3000]}\n",
                          workspace_root=run.campaign_dir, tools=_run_tools(campaign),
                          status_fn=status, broadcast_fn=broadcast)
    desk.restore(run.chat_history[:-1])          # all but the message being answered now
    run.finished_desks[_me()] = desk           # each person's questions use their own key
    return desk


class FrontdeskRequest(BaseModel):
    enabled: bool


@campaign_router.put("/{campaign_id}/experiments/{exp_id}/frontdesk")
async def set_experiment_frontdesk(campaign_id: str, exp_id: str, req: FrontdeskRequest) -> dict[str, Any]:
    """Switch an experiment's front desk on or off (off: no model calls for it). A resume keeps it."""
    run = _get_run(campaign_id, exp_id)
    run.frontdesk_enabled = req.enabled
    if run.engine is not None and _active(run):
        await run.engine.set_frontdesk(req.enabled, history=list(run.chat_history))
    if not req.enabled:
        run.finished_desks = {}
    _update_saved_request(exp_id, frontdesk=req.enabled)
    await run.broadcast({"type": "experiment_event", "run_id": exp_id, "event_type": "frontdesk_switched",
                         "data": {"enabled": req.enabled}, "timestamp": time.time()})
    return {"ok": True, "frontdesk_enabled": req.enabled}


@campaign_router.get("/{campaign_id}/experiments/{exp_id}/chat/history")
async def exp_chat_history(campaign_id: str, exp_id: str, limit: int = 100) -> list[dict[str, Any]]:
    run = _get_run(campaign_id, exp_id)
    if run.chat_history:
        return run.chat_history[-limit:]
    return _chat_from_events(run.events or store.load_events(campaign_id, exp_id))[-limit:]


# ---------------------------------------------------------------------------
# The runs of a project, as the front desks see them
# ---------------------------------------------------------------------------

def _run_instruction(run_id: str) -> str:
    try:
        return _saved_request(run_id).instruction
    except HTTPException:
        return ""


def _report_path(run: ExperimentRunState) -> Path | None:
    drafts = sorted((run.campaign_dir / "phases" / "writing").glob("draft_*.md"))
    return drafts[-1] if drafts else None


def _run_findings(run_dir: Path) -> list[str]:
    """Findings, open questions and the mental model of a run, as lines."""
    lines: list[str] = []
    try:
        art = json.loads((run_dir / "artifacts.json").read_text())
        for c in (art.get("claims") or [])[:15]:
            lines.append(f"- [{c.get('status', '')}] {c.get('statement', '')}")
        for q in (art.get("open_questions") or [])[:8]:
            lines.append(f"- open question: {q if isinstance(q, str) else q.get('question', q)}")
    except (OSError, ValueError):
        pass
    try:
        mm = json.loads((run_dir / "mental_model.json").read_text())
        if mm.get("current_rationale"):
            lines.append(f"Mental model: {mm['current_rationale']}")
        for h in (mm.get("active_hypotheses") or [])[:8]:
            lines.append(f"- hypothesis [{h.get('status', '')}]: {h.get('statement', '')}")
    except (OSError, ValueError):
        pass
    return lines


def _run_tools(campaign: CampaignState) -> list[Any]:
    from aurora.human.frontdesk import project_run_tools

    async def list_runs() -> Any:
        out = []
        for r in campaign.experiments.values():
            report = _report_path(r)
            out.append({"run_id": r.run_id, "name": r.task_name, "status": r.status, "cost_usd": round(r.cost_usd, 2),
                        "started_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(r.started_at)),
                        "instruction": _run_instruction(r.run_id)[:400],
                        "report": str(report.relative_to(r.campaign_dir)) if report else None})
        return out or "This project has no runs yet."

    async def read_run(run_id: str, part: str, path: str) -> Any:
        run = campaign.experiments.get(run_id)
        if run is None:
            return {"error": f"no run {run_id!r} in this project; see list_runs"}
        root = run.campaign_dir.resolve()
        if part == "summary":
            report = _report_path(run)
            return {"name": run.task_name, "status": run.status, "phase": run.phase, "cost_usd": round(run.cost_usd, 2),
                    "error": run.error, "instruction": _run_instruction(run_id)[:2000],
                    "findings": _run_findings(root),
                    "report_path": str(report.relative_to(root)) if report else None,
                    "report_start": report.read_text(errors="replace")[:4000] if report else None}
        if part == "report":
            report = _report_path(run)
            return {"content": report.read_text(errors="replace")[:60000]} if report else {"error": "no report yet"}
        target = (root / (path or ".")).resolve()
        if not target.is_relative_to(root):
            return {"error": "paths outside the run are not accessible"}
        if part == "files":
            if not target.is_dir():
                return {"error": f"not a directory: {path}"}
            return [f"{q.name}/" if q.is_dir() else f"{q.name} ({q.stat().st_size} bytes)"
                    for q in sorted(target.iterdir(), key=lambda q: (q.is_file(), q.name))[:200]]
        if not target.is_file():
            return {"error": f"not a file: {path}"}
        text = target.read_text(errors="replace")
        return {"content": text[-30000:], **({"note": "the last 30000 characters"} if len(text) > 30000 else {})}

    return project_run_tools(list_runs=list_runs, read_run=read_run)


def _prior_runs_note(campaign: CampaignState, run_ids: list[str]) -> str:
    """What the objective of a new run says about the runs it builds on."""
    parts = []
    for rid in run_ids:
        run = _ensure_campaign(campaign.campaign_id).experiments.get(rid)
        if run is None:
            continue
        findings = "\n".join(_run_findings(run.campaign_dir)[:10])
        parts.append(f"### {run.task_name or rid} ({rid}, {run.status})\n{findings or '(no recorded findings)'}")
    if not parts:
        return ""
    return ("\n\n## Earlier runs this experiment builds on\nTheir reports, findings and mental models are in "
            "prior_runs/ in the campaign directory: read them first, build on their results and do not repeat "
            "what they established.\n\n" + "\n\n".join(parts))


def _write_prior_runs(campaign: CampaignState, run_dir: Path, run_ids: list[str]) -> None:
    """Give a new run the reports and findings of the runs it builds on (prior_runs/<id>.md)."""
    dest = run_dir / "prior_runs"
    for rid in run_ids:
        run = campaign.experiments.get(rid)
        if run is None:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        report = _report_path(run)
        text = [f"# {run.task_name or rid}", f"Run {rid}, status {run.status}, cost ${run.cost_usd:.2f}",
                f"Its files: {run.campaign_dir}", "", "## What it set out to do", _run_instruction(rid), "",
                "## Findings and mental model", *(_run_findings(run.campaign_dir) or ["(none recorded)"]), "",
                "## Report", report.read_text(errors="replace")[:60000] if report else "(no report)"]
        (dest / f"{rid}.md").write_text("\n".join(text))


# ---------------------------------------------------------------------------
# Project conversation: the front desk drafts experiments for the researcher
# ---------------------------------------------------------------------------

def _project_context(campaign: CampaignState) -> str:
    lines = [f"Project: {campaign.name}", f"Objective: {campaign.objective}", "Experiments:"]
    for e in list(campaign.experiments.values())[-10:]:
        lines.append(f"  - {e.run_id}: {e.task_name} [{e.status}]")
    if len(lines) == 3:
        lines.append("  (none yet)")
    from aurora.api.routes.files import project_files_summary
    files = project_files_summary(campaign.campaign_id)
    lines.append("Project files (uploaded; give them to an experiment as inputs: project:<path>):")
    lines.extend(f"  - {f}" for f in files) if files else lines.append("  (none)")
    open_props = [p for p in campaign.proposals.values() if not p.confirmed and not p.superseded]
    if open_props:
        p = max(open_props, key=lambda p: p.created_at)
        lines.append(f"Pending proposal {p.proposal_id}: {p.summary}\n"
                     f"Instruction: {p.params.get('instruction', '')[:800]}\n"
                     f"Phases: {' → '.join(x for x in PHASES if x not in p.suggested_skip)}")
    return "\n".join(lines)


def _project_file_tools(campaign: CampaignState) -> list[Any]:
    """The project front desk looks at the files the researcher uploaded."""
    from aurora.api.routes.files import _inside, clean_rel, project_files_dir, read_preview
    from aurora.human.frontdesk import FrontDeskTool
    base = project_files_dir(campaign.campaign_id)

    async def list_files(p: dict[str, Any]) -> Any:
        rel = str(p.get("path") or "").strip("/")
        folder = _inside(base, clean_rel(rel)) if rel else base
        if not folder.is_dir():
            return {"error": f"not a folder: {rel or '/'}"}
        return [f"{q.name}/" if q.is_dir() else f"{q.name} ({q.stat().st_size} bytes)"
                for q in sorted(folder.iterdir(), key=lambda q: (q.is_file(), q.name)) if not q.name.startswith(".")][:300]

    async def read_file(p: dict[str, Any]) -> Any:
        target = _inside(base, clean_rel(str(p.get("path") or "")))
        if not target.is_file():
            return {"error": f"not a file: {p.get('path')}"}
        return read_preview(target)

    path = {"type": "object", "properties": {"path": {"type": "string",
                                                      "description": "relative to the project's files"}}}
    return [
        FrontDeskTool("list_project_files", "List a folder of the files the researcher uploaded to this project.",
                      path, list_files),
        FrontDeskTool("read_project_file", "Read the start of an uploaded file (e.g. a CSV's header and first "
                      "rows) to understand the data; binary files report their size only.",
                      {**path, "required": ["path"]}, read_file),
    ]


def _editable_keys() -> list[str]:
    from aurora.api.console_settings import KEYS
    return sorted(KEYS)


async def _default_settings_tool(params: dict[str, Any]) -> dict[str, Any]:
    """The project front desk changes the console's defaults: preview first, save once confirmed."""
    from aurora.api.console_settings import deep_merge, load_overlay, with_changes
    from aurora.auth.guard import current_user
    if not current_user().is_admin:
        return {"status": "error", "reason": "Only team admins can change the defaults for everyone; "
                "the researcher can still choose settings for one experiment on its card."}
    changes = params.get("changes") or {}
    reset = params.get("reset") or []
    if not isinstance(changes, dict) or not isinstance(reset, list) or not (changes or reset):
        return {"status": "error", "reason": "give changes (dotted setting -> value) and/or reset"}
    try:
        overlay = with_changes(load_overlay(), changes, reset)
        before = {k: _effective(base_config(), k) for k in [*changes, *reset]}
        after = {k: _effective(deep_merge(server_config(), overlay), k) for k in [*changes, *reset]}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    if not params.get("confirmed"):
        return {"status": "preview", "would_change": {k: {"from": before[k], "to": after[k]} for k in after},
                "next": "Tell the researcher exactly this and ask; call again with confirmed=true only if they agree."}
    try:
        apply_settings_change(changes, reset)
    except ValueError as exc:
        return {"status": "error", "reason": str(exc)}
    return {"status": "saved", "changed": {k: {"from": before[k], "to": after[k]} for k in after},
            "note": "New experiments start from these defaults; running ones are not changed."}


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
        based_on = [r for r in (params.get("based_on") or []) if r in campaign.experiments]
        extra = {k: params[k] for k in ("autonomy_mode", "models", "cpus", "memory_gb", "max_parallel_jobs")
                 if params.get(k) not in (None, "", {}, [])}
        seeds = _seed_keys(campaign.campaign_id, params.get("seed_papers") or [])
        if seeds:
            extra["seed_papers"] = seeds
        proposal = ExperimentProposal(
            {"instruction": instruction, "name": params.get("name", ""), "based_on": based_on, **extra,
             "inputs": params.get("inputs", []), "model": params.get("model", ""),
             "ideation_mode": params.get("ideation_mode") if params.get("ideation_mode") in
             ("roundtable", "independent") else default_ideation_mode()},
            skip, str(params.get("summary", instruction[:100])))
        if "max_cost_usd" in params:      # null: no limit
            proposal.max_cost_usd = params["max_cost_usd"]
        campaign.proposals[proposal.proposal_id] = proposal
        campaign.save()
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
                       "description": "files and folders the agents get (in inputs/): project:<path> for the "
                                      "project's uploaded files (see list_project_files), or paths on the "
                                      "server the researcher mentioned"},
            "based_on": {"type": "array", "items": {"type": "string"},
                         "description": "IDs of earlier runs of this project the experiment builds on; their "
                                        "reports and findings are given to its agents"},
            "seed_papers": {"type": "array", "items": {"type": "string"},
                            "description": "keys of papers in the project's literature the run starts from (a "
                                           "paper the researcher brought from the Library): ideation develops "
                                           "ideas from them and every phase reads them"},
            "max_cost_usd": {"type": ["number", "null"], "description": "cost limit in US$; null for none. "
                             "Only when the researcher asked for a budget; otherwise leave it out"},
            "autonomy_mode": {"type": "string", "enum": ["supervised", "autonomous"]},
            "models": {"type": "object", "additionalProperties": {"type": "string"},
                       "description": "model per role, only those the researcher asked for: agent, screening, "
                                      "planning, preflight, research, analysis, writing, ideation_chair, auditor, "
                                      "supervisor, frontdesk, skills (OpenRouter IDs, vendor/model)"},
            "cpus": {"type": "number", "description": "CPUs of the container"},
            "memory_gb": {"type": "number", "description": "memory of the container in GB"},
            "max_parallel_jobs": {"type": "integer", "description": "research jobs at the same time"},
        }, "required": ["instruction", "summary"]},
        propose)
    defaults_tool = FrontDeskTool(
        "update_default_settings",
        "Change the defaults for all new experiments (the console's Settings), e.g. 'from now on use "
        "Opus for research'. First call it with confirmed=false: it checks the change and returns what "
        "would change; tell the researcher and ask. Only after they explicitly agree, call it again with "
        "confirmed=true. Not for one experiment: put that in propose_experiment.",
        {"type": "object", "properties": {
            "changes": {"type": "object", "description": "dotted setting -> value, e.g. "
                        "{\"models.research\": \"anthropic/claude-opus-5.5\", \"budget.max_cost_usd\": 50}. "
                        "Settings: " + ", ".join(sorted(_editable_keys()))},
            "reset": {"type": "array", "items": {"type": "string"},
                      "description": "settings to put back to the server's value"},
            "confirmed": {"type": "boolean"},
        }, "required": ["confirmed"]},
        lambda p: _default_settings_tool(p))
    agent = FrontDeskAgent(model=cfg.models.frontdesk, api_key=cfg.api_key_value, base_url=cfg.api_base_url,
                           system_prompt=PROJECT_PROMPT,
                           tools=[tool, defaults_tool, *_project_file_tools(campaign), *_run_tools(campaign)],
                           status_fn=lambda: _project_context(campaign))
    agent.restore([m for m in campaign.chat_history if m.get("role") in ("user", "assistant")])
    campaign.frontdesk = agent
    return agent


@campaign_router.post("/{campaign_id}/chat/send")
async def campaign_chat_send(campaign_id: str, body: ChatBody) -> dict[str, Any]:
    campaign = _ensure_campaign(campaign_id)
    _require_key()
    if not body.text.strip() and not body.attachments and not body.library:
        raise HTTPException(400, "An empty message")
    from aurora.auth.providers import model_problem
    from aurora.config import AuroraConfig
    problem = model_problem(AuroraConfig.model_validate({**base_config(), "objective": "project"}).models.frontdesk,
                            campaign_id)
    if problem:
        raise HTTPException(400, problem + " An admin can change the front desk's model under Settings.")
    message, shown = _with_library(body.text, body.library)
    campaign.chat_history.append({"role": "user", "text": body.text, "timestamp": time.time(),
                                  **({"attachments": body.attachments} if body.attachments else {}),
                                  **({"library": shown} if shown else {})})
    message = _with_attachments(message, body.attachments,
                                "uploaded to the project's files (give them to an experiment as project:<path>)")
    try:
        result = await _project_frontdesk(campaign).handle_user_message(message)
    except Exception as exc:
        logger.exception("Project front desk failed")
        campaign.chat_history.append({"role": "assistant", "text": f"Front desk error: {exc}",
                                      "timestamp": time.time()})
        return {"status": "error", "error": str(exc)}
    record: dict[str, Any] = {"role": "assistant", "text": result.get("text", ""), "timestamp": time.time()}
    if result.get("proposal"):
        record["proposal"] = result["proposal"]
    campaign.chat_history.append(record)
    campaign.save()
    return {"status": "sent", "campaign_id": campaign_id, "response": record["text"],
            **({"proposal": record["proposal"]} if "proposal" in record else {})}


class ConfirmProposalBody(BaseModel):
    skip_phases: list[str] | None = None
    model: str = ""
    models: dict[str, Any] | None = None   # per-role model overrides from the card's advanced settings
    autonomy_mode: str = ""
    ideation_mode: str = ""             # roundtable | independent; empty: the proposal's
    ideation_live: bool | None = None   # brainstorm with me; None: the proposal's
    add_found: str = ""                 # papers the agents find: all | cited | none; empty: the default
    max_cost_usd: float | None = None   # null: no limit; omitted: the proposal's default
    cpus: float | None = None           # container limits; empty: the base config's
    memory_mb: int | None = None
    max_parallel_jobs: int | None = None
    inputs: list[str] | None = None     # files for the agents (project:<path> for the project's files)
    estimate: dict[str, Any] | None = None   # the estimate the card showed ({cost: [lo, hi], hours: [lo, hi]})


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
    memory_mb = body.memory_mb or (int(float(p["memory_gb"]) * 1024) if p.get("memory_gb") else None)
    seeds = _seed_keys(campaign_id, p.get("seed_papers") or [])
    req = StartExperimentRequest(
        instruction=p["instruction"] + _seed_block(campaign_id, seeds), name=p.get("name", ""),
        inputs=body.inputs if body.inputs is not None else p.get("inputs", []),
        model=body.model or p.get("model", ""),
        skip_phases=body.skip_phases if body.skip_phases is not None else proposal.suggested_skip,
        ideation_mode=body.ideation_mode or p.get("ideation_mode", ""),
        ideation_live=body.ideation_live if body.ideation_live is not None else bool(p.get("ideation_live")),
        ideation_develop=bool(p.get("ideation_develop")), source_card=str(p.get("source_card") or ""),
        add_found=body.add_found or p.get("add_found", ""),
        # what the card says, over what the front desk proposed
        models={**(p.get("models") or {}), **(body.models or {})} or None,
        autonomy_mode=body.autonomy_mode or p.get("autonomy_mode", ""),
        cpus=body.cpus or p.get("cpus"), memory_mb=memory_mb,
        max_parallel_jobs=body.max_parallel_jobs or p.get("max_parallel_jobs"), based_on=p.get("based_on") or [],
        max_cost_usd=body.max_cost_usd if "max_cost_usd" in body.model_fields_set else proposal.max_cost_usd,
        estimate=body.estimate)
    result = await _start(campaign, req)
    proposal.confirmed = True
    run = campaign.experiments.get(result["experiment_id"])
    cfg = getattr(getattr(run, "engine", None), "_config", None)
    proposal.confirmed_settings = {
        "experiment_id": result["experiment_id"],
        "skip_phases": result["skip_phases"],
        "ideation_mode": cfg.ideation.mode if cfg else req.ideation_mode,
        "ideation_live": cfg.ideation.live if cfg else req.ideation_live,
        "add_found": cfg.literature.add_found if cfg else req.add_found,
        "max_cost_usd": cfg.budget.max_cost_usd if cfg else req.max_cost_usd,
        "autonomy_mode": cfg.human.autonomy.value if cfg else req.autonomy_mode,
        "models": cfg.models.model_dump() if cfg else (req.models or {}),
        "cpus": cfg.sandbox.cpus if cfg else req.cpus,
        "memory_mb": cfg.sandbox.memory_mb if cfg else req.memory_mb,
        "max_parallel_jobs": cfg.agent.max_parallel_jobs if cfg else req.max_parallel_jobs,
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
    """Settings a new experiment gets unless the proposal card changes them; the console's own
    settings (``editable``) and the state of the environment."""
    from aurora.config import AuroraConfig
    from aurora.core.docker_backend import host_limits
    from aurora.core.requirements import available_models

    cfg = AuroraConfig.model_validate({**base_config(), "objective": "defaults"})
    host = await host_limits()
    served = available_models(cfg.api_base_url) if "openrouter.ai" in cfg.api_base_url else None
    return {
        "models": cfg.models.model_dump(),
        "autonomy_mode": cfg.human.autonomy.value,
        "ideation_mode": cfg.ideation.mode,
        "literature": {"enabled": cfg.literature.enabled, "add_found": cfg.literature.add_found},
        "max_cost_usd": default_max_cost(),
        "max_parallel_jobs": cfg.agent.max_parallel_jobs,
        "sandbox": {"docker": cfg.sandbox.docker, "cpus": cfg.sandbox.cpus, "memory_mb": cfg.sandbox.memory_mb,
                    "host_cpus": host[0], "host_memory_mb": host[1]},
        "model_suggestions": [m for m in MODEL_SUGGESTIONS if served is None or m in served],
        "editable": _editable_settings(),
        "environment": await asyncio.to_thread(_environment, cfg, host),
    }


def _effective(data: dict[str, Any], key: str) -> Any:
    """The value *key* takes for a new experiment started from *data* (defaults filled in)."""
    from aurora.api.console_settings import get_dotted
    from aurora.config import AuroraConfig
    if key == "budget.max_cost_usd":
        found, value = get_dotted(data, key)
        return value if found else WEB_DEFAULT_MAX_COST_USD
    node: Any = AuroraConfig.model_validate({**data, "objective": "defaults"})
    for part in key.split("."):
        node = getattr(node, part)
    return getattr(node, "value", node)


def _editable_settings() -> list[dict[str, Any]]:
    from aurora.api.console_settings import EDITABLE, get_dotted, load_overlay
    from aurora.config import AuroraConfig
    server, overlay, merged = server_config(), load_overlay(), base_config()
    out = []
    for entry in EDITABLE:
        key, group, label, kind = entry[:4]
        model, info = AuroraConfig, None
        for part in key.split("."):
            info = getattr(model, "model_fields", {}).get(part)
            model = info.annotation if info is not None else None
        out.append({"key": key, "group": group, "label": label, "kind": kind,
                    "choices": entry[4] if len(entry) > 4 else None,
                    "help": info.description if info is not None else "",
                    "value": _effective(merged, key), "server_value": _effective(server, key),
                    "overridden": get_dotted(overlay, key)[0]})
    return out


def _environment(cfg: Any, host: tuple[int, int]) -> dict[str, Any]:
    """What the server has to run experiments with (no secrets: the key's presence only)."""
    import shutil
    import subprocess

    from aurora.api.console_settings import overlay_path
    from aurora.core.requirements import _docker_ok
    from aurora.paths import aurora_home
    docker_ok, docker_msg = _docker_ok()
    claude = shutil.which("claude")
    version = ""
    if claude:
        try:
            version = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            version = "installed (version unknown)"
    home = aurora_home()
    free = shutil.disk_usage(home if home.exists() else Path.home()).free
    return {"api_key": bool(cfg.api_key_value), "api_base_url": cfg.api_base_url,
            "docker": {"ok": docker_ok, "detail": docker_msg},
            "claude": {"ok": bool(claude), "version": version},
            "host_cpus": host[0], "host_memory_mb": host[1],
            "aurora_home": str(home), "disk_free_gb": round(free / 1e9, 1),
            "server_config": os.environ.get("AURORA_SERVER_CONFIG") or None,
            "console_settings": str(overlay_path())}


class SettingsChange(BaseModel):
    changes: dict[str, Any] = {}     # dotted key -> value
    reset: list[str] = []            # dotted keys back to the server's value


def apply_settings_change(changes: dict[str, Any], reset: list[str] | None = None) -> dict[str, Any]:
    """Check and save a change of the console's settings; raises ValueError with the reason."""
    from aurora.api.console_settings import deep_merge, load_overlay, save_overlay, with_changes
    from aurora.config import AuroraConfig
    from aurora.core.requirements import unknown_models
    overlay = with_changes(load_overlay(), changes, reset)
    try:
        cfg = AuroraConfig.model_validate({**deep_merge(server_config(), overlay), "objective": "defaults"})
    except Exception as exc:
        raise ValueError(f"invalid setting: {exc}") from exc
    changed_models = {f"models.{k.split('.', 1)[1]}" for k in changes if k.startswith("models.")}
    problems = [p for p in unknown_models(cfg) if p.split(":")[0].split("[")[0] in changed_models]
    if problems:
        raise ValueError("; ".join(problems))
    save_overlay(overlay)
    return {"saved": True, "changes": changes, "reset": reset or []}


@settings_router.put("")
async def update_settings(req: SettingsChange) -> dict[str, Any]:
    """Change the defaults for new experiments (kept over the server's base config)."""
    try:
        result = apply_settings_change(req.changes, req.reset)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**result, "editable": _editable_settings()}


# ---------------------------------------------------------------------------
# Notifications (Settings → Notifications)
# ---------------------------------------------------------------------------

def _hint(target: str) -> str:
    """Enough of a webhook URL or key to recognise it, not to use it."""
    from urllib.parse import urlparse
    if "://" in target:
        u = urlparse(target)
        return f"{u.scheme}://{u.netloc}/…{target[-4:]}"
    return f"{target[:4]}…{target[-4:]}" if len(target) > 10 else "…"


def _notification_settings() -> dict[str, Any]:
    from aurora.config import DEFAULT_NOTIFY_EVENTS, NOTIFY_EVENTS
    cfg = live_notifications()
    return {
        "console_url": cfg.console_url, "console_url_default": os.environ.get("AURORA_CONSOLE_URL", ""),
        "budget_warning_at": cfg.budget_warning_at,
        "quiet_start": cfg.quiet_start, "quiet_end": cfg.quiet_end, "quiet_except": cfg.quiet_except,
        "action_links": cfg.action_links,
        "channels": [{"id": c.id, "type": c.type, "name": c.name, "target_hint": _hint(c.target),
                      "events": c.events, "enabled": c.enabled} for c in cfg.channels],
        "events": [{"key": k, "label": v, "default": k in DEFAULT_NOTIFY_EVENTS} for k, v in NOTIFY_EVENTS.items()],
    }


class ChannelBody(BaseModel):
    id: str = ""
    type: str
    name: str = ""
    target: str = ""            # empty: keep the channel's current target (by id)
    events: list[str] | None = None
    enabled: bool = True


class NotificationsBody(BaseModel):
    console_url: str = ""
    budget_warning_at: float = 0.8
    channels: list[ChannelBody] = []
    quiet_start: str = ""
    quiet_end: str = ""
    quiet_except: list[str] = []
    action_links: bool = True


def _channels(bodies: list[ChannelBody]) -> list[Any]:
    from aurora.config import DEFAULT_NOTIFY_EVENTS, NotifyChannel
    current = {c.id: c for c in live_notifications().channels if c.id}
    out = []
    for b in bodies:
        target = b.target.strip() or (current[b.id].target if b.id in current else "")
        if not target:
            raise ValueError(f"channel {b.name or b.type}: the webhook URL or key is missing")
        out.append(NotifyChannel(id=b.id or uuid.uuid4().hex[:8], type=b.type, name=b.name.strip(), target=target,
                                 events=b.events if b.events is not None else list(DEFAULT_NOTIFY_EVENTS),
                                 enabled=b.enabled))
    return out


@settings_router.get("/notifications")
async def get_notification_settings() -> dict[str, Any]:
    """Where notifications go (webhook URLs and keys are shown shortened)."""
    return _notification_settings()


@settings_router.put("/notifications")
async def update_notification_settings(req: NotificationsBody) -> dict[str, Any]:
    """Replace the notification settings (a channel sent without its target keeps the one it has)."""
    from aurora.api.console_settings import load_overlay, save_overlay
    from aurora.config import NotificationsConfig
    try:
        from aurora.notify import _minutes
        for t in (req.quiet_start, req.quiet_end):
            if t.strip() and _minutes(t) is None:
                raise ValueError(f"quiet hours look like 22:00, not {t!r}")
        cfg = NotificationsConfig(console_url=req.console_url.strip().rstrip("/"),
                                  budget_warning_at=req.budget_warning_at, channels=_channels(req.channels),
                                  quiet_start=req.quiet_start.strip(), quiet_end=req.quiet_end.strip(),
                                  quiet_except=req.quiet_except, action_links=req.action_links)
    except Exception as exc:
        raise HTTPException(400, f"invalid notification settings: {exc}") from exc
    if _me() != "local":                      # each person's own channels
        from aurora.auth.store import auth_store
        auth_store.set_pref(_me(), "notifications", cfg.model_dump(mode="json"))
        return _notification_settings()
    overlay = load_overlay()
    overlay["notifications"] = cfg.model_dump(mode="json")
    save_overlay(overlay)
    return _notification_settings()


@settings_router.post("/notifications/test")
async def test_notification(req: ChannelBody) -> dict[str, Any]:
    """Send a test message to one channel (saved or not); says why it failed if it did."""
    from aurora.notify import deliver
    try:
        channel = _channels([req])[0]
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    base = console_url()
    error = await deliver(channel, "test", "AURORA test notification",
                          "Notifications reach you here: decisions waiting for you, finished and failed "
                          "experiments, budgets running out.", base)
    return {"ok": not error, "error": error}

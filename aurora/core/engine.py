"""Research engine — runs a campaign from ideation to the final report.

    ideation → screening → planning → research → analysis → writing
                                         ↑___________|  (analysis may ask for more research)

Every hand-off between phases passes the milestone auditor; during research
the live auditor (supervisor) watches the research agent.  Both can open a
human gate (:mod:`aurora.core.human_gate`).  In real runs each phase is a
Claude Code session in the campaign's execution backend; in mock mode the
scripted mock models and tools stand in.

The engine is the only component that knows the pipeline's shape; the
phase agents never call it back.

Checkpoint-based resumption
---------------------------
After each major phase the engine writes a ``checkpoint.json`` to the
workspace.  On restart with ``resume=True`` the engine reads this file
and skips completed phases.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from aurora.agents.analyzer import AnalyzerAgent
from aurora.agents.base import AgentActionType, AgentContext
from aurora.agents.roundtable.roundtable import Roundtable
from aurora.agents.writer import WriterAgent
from aurora.config import AuroraConfig, PipelineStage
from aurora.core.agent_session import AgentSession
from aurora.core.events import Event, EventBus
from aurora.core.human_gate import HumanGate
from aurora.core.lifecycle import (
    IterationResult,
    ResearchIteration,
)
from aurora.core.registry import ComponentRegistry
from aurora.hooks.base import HookContext, HookPoint, HookRegistry
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter, create_router
from aurora.models.artifacts import ResearchArtifacts
from aurora.models.experiment import ExperimentJob, ExperimentPlan
from aurora.models.roundtable import IdeaScore, RoundtableProposal
from aurora.planning.dependency_graph import DependencyGraph
from aurora.planning.planner import ExperimentPlanner
from aurora.screening.screener import IdeaScreener
from aurora.skills.registry import SkillRegistry
from aurora.state.artifacts import ArtifactStore
from aurora.state.context import ContextReconstructor
from aurora.state.mental_model import MentalModelManager
from aurora.state.sessions import CONTINUE_NOTE, RESTART_NOTE, SessionRegistry
from aurora.state.workspace import WorkspaceManager
from aurora.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase enum & checkpoint
# ---------------------------------------------------------------------------


class CampaignPhase(str, Enum):
    """Phases of a research campaign (values match ``skip_phases``)."""

    INITIALIZING = "initializing"
    IDEATION = "ideation"
    SCREENING = "screening"
    PLANNING = "planning"
    PREFLIGHT = "preflight"
    RESEARCH = "research"
    ANALYSIS = "analysis"
    WRITING = "writing"
    COMPLETED = "completed"
    FAILED = "failed"


class ResearcherDirection(BaseModel):
    """How the researcher's message at the screening gate is read."""

    basis: str = Field(description="Exactly one ID from the lists (a proposal like prop_1a2b3c4d, or a meeting "
                                   "idea like idea_p03), or exactly 'new' for a direction of their own; "
                                   "nothing else")
    interpretation: str = Field(description="One or two sentences: how you understood the message")
    changes: str = Field(description="Only the changes the researcher asks for to that proposal or idea; "
                                     "an empty string if they ask for none or if basis is 'new'")
    title: str = Field(description="For 'new': a short title of their direction; empty otherwise")
    description: str = Field(description="For 'new': their direction written out as a research proposal "
                                         "(question, approach), keeping everything they said; empty otherwise")


_DEVELOP_DIRECTION = """\
The researcher has chosen the direction of this campaign (below). Work within it: develop it into \
the strongest version you can (a sharper question, a testable hypothesis, a concrete method) and \
raise what is wrong with it, but do not propose a different direction.

{direction}"""


_DIRECTION_PROMPT = """\
A research campaign's screening rejected the proposals from ideation, and the researcher has now \
said which direction to pursue. Read their message the way a colleague would and work out what \
they mean: one of the proposals as it is ("the latest one is fine"), one of the ideas presented at \
the meeting ("go with the e-bike idea"), one of them with changes, or a direction of their own. \
Refer to it by its ID. Do not add ideas the researcher did not ask for. If the message only \
approves without naming one, they mean the latest proposal."""


async def _clone(src: Path, dest: Path) -> None:
    """Copy a file or folder for the agents: a copy-on-write clone where the file system allows it
    (instant, no extra space, and the agents' changes do not reach the original), else a copy."""
    import shutil
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)       # cp copies the folder's contents into it
    if shutil.which("cp"):
        proc = await asyncio.create_subprocess_exec(
            "cp", "-R", "--reflink=auto", "--preserve=timestamps", f"{src}/." if src.is_dir() else str(src), str(dest),
            stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode == 0:
            return
        logger.debug("cp --reflink failed (%s); copying", err.decode().strip())
    if src.is_dir():
        await asyncio.to_thread(shutil.copytree, src, dest, dirs_exist_ok=True)
    else:
        await asyncio.to_thread(shutil.copy2, src, dest)


class CampaignStopped(Exception):
    """Raised to end a campaign early: a human chose stop, or a budget ran out."""


# How often screening may reject a proposal before a human gate decides
_MAX_SCREENING_REJECTIONS = 2


class Checkpoint(BaseModel):
    """Persisted campaign checkpoint for resumption."""

    campaign_id: str
    phase: str  # CampaignPhase value
    iteration: int = 0
    research_cycle: int = 0
    proposal_id: str | None = None
    plan_id: str | None = None
    completed_jobs: list[str] = Field(default_factory=list)
    current_iteration_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


# ---------------------------------------------------------------------------
# Campaign result
# ---------------------------------------------------------------------------


class CampaignResult(BaseModel):
    """Final result of a complete research campaign."""

    campaign_id: str
    status: str  # "completed" | "failed" | "stopped"
    proposal: RoundtableProposal | None = None
    plan: ExperimentPlan | None = None
    iterations: list[IterationResult] = Field(default_factory=list)
    manuscript_path: str | None = None
    research_cycles: int = 0
    total_wall_time_seconds: float = 0.0
    total_llm_tokens: int = 0
    cost_usd: float = 0.0
    workspace: str = ""
    summary: str = ""


# ---------------------------------------------------------------------------
# Research Engine
# ---------------------------------------------------------------------------


class ResearchEngine:
    """Orchestrates a complete AURORA research campaign.

    Parameters
    ----------
    config : AuroraConfig
        Full campaign configuration.
    """

    def __init__(
        self,
        config: AuroraConfig,
        *,
        initial_proposal: RoundtableProposal | None = None,
        execution_backend: Any = None,
        terminal_system_prompt: str = "",
        terminal_task_instruction: str = "",
    ) -> None:
        self._config = config
        self._phase = CampaignPhase.INITIALIZING
        self._bus = EventBus()
        self._registry = ComponentRegistry()

        # Pre-set proposal (skips Round Table when provided)
        self._initial_proposal = initial_proposal

        # Pluggable execution backend (e.g. DockerBackend for LHTB)
        self._execution_backend = execution_backend

        # Terminal iteration prompts (fall back to config default)
        self._terminal_system_prompt = terminal_system_prompt or config.prompts.research
        self._terminal_task_instruction = terminal_task_instruction

        # Subsystems (initialised in ``_setup``)
        self._workspace: WorkspaceManager | None = None
        self._router: LLMRouter | None = None
        self._store: ArtifactStore | None = None
        self._ctx: ContextReconstructor | None = None
        self._mm: MentalModelManager | None = None

        # Session ID of the last completed phase (for audit feedback resume)
        self._last_phase_session_id: str | None = None

        # Human gates (escalations waiting for a decision); answered by the web
        # console, the front desk agent or the terminal
        self.human_gate = HumanGate(config.human, self._publish)

        # Research runner of the current research phase (for inject_message)
        self.current_iteration_runner: Any = None
        self._session_id: str | None = None

        # Live auditor that watches the research agent
        self._supervisor: Any = None

        # Front desk agent: the human's conversation partner while the campaign runs
        self._frontdesk: Any = None
        self._frontdesk_listener: Any = None
        # set by the web console before the run: the conversation so far (restored into the front
        # desk's memory) and tools that look into the project's other runs
        self.prior_chat: list[dict[str, Any]] = []
        self.extra_frontdesk_tools: list[Any] = []
        # Next research-agent text goes to the front desk (after it relayed an instruction)
        self._awaiting_agent_response: bool = False

        self._frontdesk_status: Any = None
        # instructions relayed before the research agent existed
        self._queued_instructions: list[str] = []
        # Web console project this campaign belongs to (scopes project skills)
        self.project_id: str = ""
        # name used in notifications; set by the web console (empty: the objective)
        self.display_name: str = ""
        # whether the person writing to the front desk may answer decisions, and their name
        # (the web console sets it with accounts; None: anyone may)
        self.may_decide: Any = None
        # sends notifications (aurora.notify.Notifier); the web console sets its own before running
        self.notifier: Any = None
        # cluster jobs of the research agents (aurora.compute.slurm.SlurmCompute), when enabled
        self.compute: Any = None
        # the project's literature (aurora.literature.service.ProjectLiterature); made on first use
        self._literature: Any = None
        # the round table while it meets (a live meeting takes the researcher's remarks)
        self._roundtable: Any = None
        self._stopped_by_user = False
        self._budget_warned = False

        # Budget accounting
        self._started_at: float | None = None
        self._main_task: asyncio.Task | None = None
        self._resuming = False
        self._sessions: SessionRegistry | None = None
        self.cost_usd: float = 0.0
        self._prior_cost = 0.0      # spent before this run, when resuming (from cost.json)
        self._finishing = False     # run_campaign is cleaning up (container, front desk)
        self._stop_requested: str | None = None

        # Cross-phase context: each phase writes its transcript to disk,
        # subsequent phases can read prior transcripts for context.
        self._phase_transcripts: list[dict[str, str]] = []

        # Current round number — incremented on analysis loop-back.
        self._current_round: int = 0

        # Skill distillation store (initialized in _setup)
        self._skill_store: Any = None
        self._skill_tasks: set[asyncio.Task] = set()     # skill learning running in the background

        # Pause mechanism — starts *set* (not paused).
        # ``clear()`` pauses; ``set()`` resumes.
        self._paused = asyncio.Event()
        self._paused.set()

    # ==================================================================
    # Public API
    # ==================================================================

    async def inject_message(self, message: str) -> None:
        """Inject a message into the currently running agent loop."""
        if self.current_iteration_runner and hasattr(
            self.current_iteration_runner, "inject_message"
        ):
            await self.current_iteration_runner.inject_message(message)

    async def run_campaign(self, *, resume: bool = False) -> CampaignResult:
        """Run (or resume from its checkpoint) the whole campaign."""
        campaign_start = datetime.now(UTC)
        import time as _time
        self._started_at = _time.time()
        self._main_task = asyncio.current_task()
        self._resuming = resume
        from aurora.core.requirements import check_runtime_requirements
        check_runtime_requirements(self._config)
        self._setup()
        assert self._router is not None
        assert self._workspace is not None
        self._setup_notifier()
        if resume:
            self._prior_cost = self._load_spent()

        all_iterations: list[IterationResult] = []
        proposal: RoundtableProposal | None = self._initial_proposal
        plan: ExperimentPlan | None = None
        manuscript_path: str | None = None
        research_cycle = 0
        status, summary = "completed", ""

        try:
            checkpoint = self._load_checkpoint() if resume else None
            finished = checkpoint is not None and checkpoint.phase == CampaignPhase.COMPLETED.value
            if self._execution_backend is not None and not finished:   # nothing left to run: no container
                await self._execution_backend.setup()
                logger.info("Execution backend started (container=%s)",
                            getattr(self._execution_backend, "container_id", None) or "none, local")
                if resume:
                    await self._restore_container_files()
                await self._start_compute()
            if not finished:            # with or without a container
                await self._copy_inputs()

            start_phase = CampaignPhase(checkpoint.phase) if checkpoint else CampaignPhase.IDEATION
            research_cycle = checkpoint.research_cycle if checkpoint else 0
            self._current_round = research_cycle
            # a resumed campaign keeps what it has learnt (claims, mental model)
            if self._store and (not resume or not self._workspace.artifacts_path.exists()):
                self._store.initialize(objective=self._config.objective)
            await self._publish("campaign_started", campaign_id=self._config.campaign_id,
                                workspace=str(self._workspace.campaign_dir), resume=resume)
            if self._config.human.frontdesk and not self._config.mock_mode:
                await self._start_frontdesk()

            skip = set(self._config.skip_phases)
            if proposal is not None:
                skip.add(CampaignPhase.IDEATION.value)
            elif resume:
                proposal = self._load_latest_proposal()
            if proposal is None and CampaignPhase.IDEATION.value in skip:
                # no ideation: the objective itself is the direction to pursue
                proposal = self._objective_proposal()
                self._workspace.atomic_write_json(
                    self._workspace.proposal_path(proposal.proposal_id, self._current_round), proposal)
            order = [CampaignPhase.IDEATION, CampaignPhase.SCREENING, CampaignPhase.PLANNING,
                     CampaignPhase.PREFLIGHT, CampaignPhase.RESEARCH, CampaignPhase.ANALYSIS,
                     CampaignPhase.WRITING]

            def next_phase(phase: CampaignPhase) -> CampaignPhase:
                """*phase* itself, or the first later phase that is not skipped."""
                for candidate in order[order.index(phase):]:
                    if candidate.value not in skip:
                        return candidate
                    logger.info("Skipping phase: %s", candidate.value)
                return CampaignPhase.COMPLETED

            def after(phase: CampaignPhase) -> CampaignPhase:
                i = order.index(phase)
                return next_phase(order[i + 1]) if i + 1 < len(order) else CampaignPhase.COMPLETED

            current = next_phase(start_phase) if start_phase in order else start_phase
            # proposals screening has rejected so far (kept across a resume)
            rejections = int(checkpoint.metadata.get("screening_rejections", 0)) if checkpoint else 0
            # instruction from a human gate (or a screening rejection), passed to the next phase
            guidance = (checkpoint.metadata.get("guidance", "") if checkpoint
                        and start_phase == CampaignPhase.IDEATION else "")
            # the researcher chose a direction and asked for ideation but not screening on it
            skip_screening = bool(checkpoint.metadata.get("skip_screening")) if checkpoint else False

            def meta(**extra: Any) -> dict[str, Any]:
                return {"screening_rejections": rejections, "skip_screening": skip_screening, **extra}

            while current != CampaignPhase.COMPLETED:
                self._check_budget()
                self._phase = current
                self._update_frontdesk_status(phase=current.value)
                logger.info("=== Phase: %s (round %d) ===", current.value, research_cycle)

                if current == CampaignPhase.IDEATION:
                    proposal = await self._run_roundtable(guidance)
                    guidance = ""
                    following = after(current)

                elif current == CampaignPhase.SCREENING:
                    proposal = proposal or self._load_latest_proposal()
                    if proposal is None:
                        raise RuntimeError("Screening needs a proposal, but ideation was skipped and none exists.")
                    if skip_screening and proposal.score is None:
                        # the researcher's direction, developed by ideation; they chose not to screen it
                        skip_screening = False
                        proposal = proposal.model_copy(update={"selected": True})
                        self._workspace.atomic_write_json(
                            self._workspace.proposal_path(proposal.proposal_id, self._current_round), proposal)
                        await self._publish("screening_skipped", proposal_id=proposal.proposal_id,
                                            reason="the researcher chose not to screen their direction")
                        current = after(CampaignPhase.SCREENING)
                        self._save_checkpoint(current, research_cycle, proposal_id=proposal.proposal_id,
                                              metadata=meta())
                        continue
                    if proposal.score is not None:
                        # resumed after this proposal was screened: keep its verdict
                        await self._publish("phase_started", phase="screening")
                        logger.info("Proposal %s was already screened (%.1f/10)", proposal.proposal_id,
                                    proposal.score.composite)
                    else:
                        proposal = await self._run_screening(proposal)
                        rejections += 0 if proposal.selected else 1
                        self._workspace.atomic_write_json(
                            self._workspace.proposal_path(proposal.proposal_id, self._current_round), proposal)
                    following = after(current)
                    if not proposal.selected:
                        if rejections <= _MAX_SCREENING_REJECTIONS and CampaignPhase.IDEATION.value not in skip:
                            logger.warning("Screening rejected the proposal (%d/%d); new ideation round",
                                           rejections, _MAX_SCREENING_REJECTIONS)
                            guidance = (f"The previous proposal '{proposal.title}' was rejected at screening: "
                                        f"{proposal.score.explanation if proposal.score else ''}")
                            current = CampaignPhase.IDEATION
                            self._save_checkpoint(current, research_cycle, metadata=meta(guidance=guidance))
                            continue
                        self._save_checkpoint(current, research_cycle, proposal_id=proposal.proposal_id,
                                              metadata=meta())
                        decision = await self.human_gate.request(
                            "screening", "Screening keeps rejecting the proposals",
                            f"Screening rejected {rejections} proposals. Latest: '{proposal.title}' "
                            f"(score {proposal.score.composite if proposal.score else '?'}/10).\n"
                            "continue: pursue the latest proposal anyway (a message goes to planning); "
                            "instruct: run ideation again with your guidance; propose: say which direction "
                            "to pursue, in your words (one of the proposals, with changes, or your own idea), "
                            "and whether it goes through ideation and/or screening again (by default "
                            "neither); stop: end the campaign.",
                            options=["continue", "instruct", "propose", "stop"])
                        if decision.action == "stop":
                            self._stopped_by_user = decision.decided_by in ("human", "frontdesk")
                            raise CampaignStopped(f"stopped at screening: {decision.message or 'human decision'}")
                        if decision.action == "instruct":
                            guidance = decision.message
                            current = CampaignPhase.IDEATION
                            self._save_checkpoint(current, research_cycle, metadata=meta(guidance=guidance))
                            continue
                        if decision.action == "propose":
                            # the researcher chose the direction; it goes through ideation and/or
                            # screening only if they asked for it, otherwise planning starts from it
                            # (no audit of the rejected screening)
                            proposal = await self._adopt_researcher_proposal(decision.message, decision.rerun)
                            if "ideation" in decision.rerun:
                                guidance = _DEVELOP_DIRECTION.format(direction=self._direction_text(proposal))
                                skip_screening = "screening" not in decision.rerun
                                current = CampaignPhase.IDEATION
                                self._save_checkpoint(current, research_cycle, metadata=meta(guidance=guidance))
                            elif "screening" in decision.rerun:
                                proposal = proposal.model_copy(update={"selected": False})
                                current = CampaignPhase.SCREENING
                                self._save_checkpoint(current, research_cycle, proposal_id=proposal.proposal_id,
                                                      metadata=meta())
                            else:
                                current = after(CampaignPhase.SCREENING)
                                self._save_checkpoint(current, research_cycle, proposal_id=proposal.proposal_id,
                                                      metadata=meta())
                            continue
                        proposal = proposal.model_copy(update={"selected": True})
                        guidance = decision.message      # a note for planning, if any

                elif current == CampaignPhase.PLANNING:
                    proposal = proposal or self._load_latest_proposal()
                    plan = await self._run_planning(proposal, self._with_queued(guidance))
                    guidance = ""
                    following = after(current)

                elif current == CampaignPhase.PREFLIGHT:
                    proposal = proposal or self._load_latest_proposal()
                    plan = plan or self._load_plan() or self._single_job_plan(proposal)
                    plan, problems = await self._run_preflight(plan, proposal, self._with_queued(guidance))
                    guidance = ""
                    following = after(current)
                    if problems:
                        decision = await self.human_gate.request(
                            "preflight", "Pre-flight checks found problems in the plan",
                            "\n".join(f"- {p}" for p in problems)
                            + "\ncontinue: run the plan anyway (the research agents are told about the "
                            "problems); instruct: plan again following your message; stop: end the campaign.")
                        if decision.action == "stop":
                            self._stopped_by_user = decision.decided_by in ("human", "frontdesk")
                            raise CampaignStopped(f"stopped at pre-flight: {decision.message or 'human decision'}")
                        if decision.action == "instruct":
                            guidance = decision.message
                            current = CampaignPhase.PLANNING
                            self._save_checkpoint(current, research_cycle, metadata=meta())
                            continue
                        guidance = decision.message

                elif current == CampaignPhase.RESEARCH:
                    plan = plan or self._load_plan() or self._single_job_plan(proposal)
                    completed_jobs = (checkpoint.completed_jobs if checkpoint
                                      and checkpoint.phase == CampaignPhase.RESEARCH.value else None)
                    loop_results = await self._run_research_loop(plan, completed_jobs=completed_jobs,
                                                                 guidance=self._with_queued(guidance),
                                                                 proposal=proposal)
                    guidance = ""
                    all_iterations.extend(loop_results)
                    # analysis and writing on top of zero successful jobs would only restate
                    # canned or empty results as findings, so stop here instead
                    if loop_results and not any(r.job_result is not None and r.job_result.success
                                                for r in loop_results):
                        first_err = next((r.error for r in loop_results if r.error), None) or "unknown error"
                        raise RuntimeError(f"All {len(loop_results)} research jobs failed; stopping before "
                                           f"analysis. First error: {first_err}")
                    following = after(current)

                elif current == CampaignPhase.ANALYSIS:
                    loop_back_to = await self._run_analysis()
                    if loop_back_to is not None and research_cycle < self._config.agent.max_followup_cycles:
                        research_cycle += 1
                        self._current_round = research_cycle
                        logger.info("Analysis asks for another round from %s (round %d)",
                                    loop_back_to.value, research_cycle)
                        following = next_phase(loop_back_to)
                    else:
                        if loop_back_to is not None:
                            logger.warning("Analysis asked for more work, but max_followup_cycles (%d) is "
                                           "reached; writing up what there is", self._config.agent.max_followup_cycles)
                        following = after(current)

                elif current == CampaignPhase.WRITING:
                    manuscript_path = await self._run_writing()
                    following = CampaignPhase.COMPLETED
                    # the report is audited first, then the human decides whether to finish
                    guidance = await self._fire_milestone(current, following, len(all_iterations)) or guidance
                    if self._config.human.approve_termination:
                        decision = await self.human_gate.request(
                            "termination", "The campaign is ready to finish",
                            f"The report is at {manuscript_path}.\ncontinue: finish; instruct: run another "
                            "research round with your guidance; stop: finish now.")
                        if decision.action == "instruct":
                            research_cycle += 1
                            self._current_round = research_cycle
                            guidance = decision.message
                            following = CampaignPhase.RESEARCH
                else:
                    raise RuntimeError(f"Unknown phase {current}")

                if current != CampaignPhase.WRITING and following != CampaignPhase.COMPLETED:
                    guidance = await self._fire_milestone(current, following, len(all_iterations)) or guidance
                self._save_checkpoint(following, research_cycle,
                                      proposal_id=proposal.proposal_id if proposal else None,
                                      plan_id=plan.plan_id if plan else None, metadata=meta())
                current = following

            summary = (f"Campaign completed with {len(all_iterations)} research jobs and "
                       f"{research_cycle} follow-up rounds.")

        except CampaignStopped as exc:
            status, summary = "stopped", f"Campaign stopped: {exc}"
            logger.warning(summary)
        except asyncio.CancelledError:
            if not self._stop_requested:
                raise
            # request_stop() cancelled the running phase; finish cleanly as stopped
            asyncio.current_task().uncancel()
            status, summary = "stopped", f"Campaign stopped: {self._stop_requested}"
            logger.warning(summary)
        except Exception as exc:
            status, summary = "failed", f"Campaign failed: {exc}"
            logger.exception("Campaign failed")
        finally:
            self._finishing = True
            try:
                await self._finish_skill_learning()
            except Exception:
                logger.debug("waiting for skill learning failed", exc_info=True)
            self._phase = {"completed": CampaignPhase.COMPLETED}.get(status, CampaignPhase.FAILED)
            self.human_gate.cancel_all("campaign ended")
            await self._stop_supervisor()
            await self._stop_frontdesk()
            if self.compute is not None:
                try:
                    await self.compute.stop()
                except Exception:
                    logger.warning("Stopping the cluster jobs failed", exc_info=True)
            if self._execution_backend is not None:
                if self._workspace:
                    dest = self._workspace.campaign_dir / "workspace"
                    try:
                        await self._execution_backend.extract_workspace(dest)
                        logger.info("Container files copied to %s", dest)
                    except Exception:
                        logger.warning("Copying the container files failed", exc_info=True)
                try:
                    await self._execution_backend.teardown()
                except Exception:
                    logger.warning("Backend teardown failed", exc_info=True)
            if self._router:
                await self._router.close_all()

        total = (datetime.now(UTC) - campaign_start).total_seconds()
        spent = self._spent()
        self._record_spent()
        await self._publish(f"campaign_{status}", status=status, summary=summary,
                            cost_usd=round(spent, 4), wall_time_seconds=round(total),
                            by_user=self._stopped_by_user)
        if self.notifier is not None:
            await self.notifier.drain()
        return CampaignResult(
            campaign_id=self._config.campaign_id,
            status=status,
            proposal=proposal,
            plan=plan,
            iterations=all_iterations,
            manuscript_path=manuscript_path,
            research_cycles=research_cycle,
            total_wall_time_seconds=total,
            total_llm_tokens=self._router.usage_tracker.total_tokens if self._router else 0,
            cost_usd=round(spent, 4),
            workspace=str(self._workspace.campaign_dir) if self._workspace else "",
            summary=summary,
        )

    def _spent(self) -> float:
        """API cost so far: earlier runs of a resumed campaign, finished sessions, the running
        live auditor and direct calls."""
        live = self._supervisor.cost_usd if self._supervisor is not None else 0.0
        return (self._prior_cost + self.cost_usd + live
                + (self._router.usage_tracker.total_cost if self._router else 0.0))

    @property
    def total_cost_usd(self) -> float:
        """What the campaign has cost so far, across resumes."""
        return self._spent()

    def _record_spent(self) -> None:
        """Keep the cost on disk (``cost.json``), so a resumed campaign counts it against its budget."""
        if self._workspace is None:
            return
        try:
            self._workspace.campaign_dir.mkdir(parents=True, exist_ok=True)
            (self._workspace.campaign_dir / "cost.json").write_text(
                json.dumps({"cost_usd": round(self._spent(), 6), "updated_at": datetime.now(UTC).isoformat()}))
        except OSError:
            logger.debug("Could not record the cost", exc_info=True)

    def _load_spent(self) -> float:
        path = self._workspace.campaign_dir / "cost.json" if self._workspace else None
        try:
            return float(json.loads(path.read_text())["cost_usd"]) if path and path.exists() else 0.0
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0

    def _remaining_budget(self) -> float | None:
        cap = self._config.budget.max_cost_usd
        return None if cap is None else max(cap - self._spent(), 0.0)

    def _check_budget(self) -> None:
        """Raise :class:`CampaignStopped` when the user stopped the campaign or a budget is used up."""
        import time as _time
        if self._stop_requested:
            raise CampaignStopped(self._stop_requested)
        budget = self._config.budget
        if self._started_at and _time.time() - self._started_at > budget.max_hours * 3600:
            raise CampaignStopped(f"time budget of {budget.max_hours:g} h used up")
        spent = self._spent()
        if budget.max_cost_usd is not None and spent >= budget.max_cost_usd:
            raise CampaignStopped(f"cost budget of ${budget.max_cost_usd:g} used up (${spent:.2f} spent)")

    def _with_queued(self, guidance: str) -> str:
        """Guidance for the next phase plus instructions the researcher gave earlier."""
        queued = "\n".join(f"- {m}" for m in self._queued_instructions)
        parts = [guidance, f"Instructions from the researcher:\n{queued}" if queued else ""]
        return "\n\n".join(p for p in parts if p)

    def _objective_proposal(self) -> RoundtableProposal:
        objective = self._config.objective.strip()
        return RoundtableProposal(
            proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
            title=objective.splitlines()[0][:100],
            description=objective,
            research_questions=[objective[:500]],
            selected=True,
            created_at=datetime.now(UTC),
        )

    async def _adopt_researcher_proposal(self, text: str, rerun: list[str] | None = None) -> RoundtableProposal:
        """Make the direction the researcher chose at the screening gate the campaign's proposal.

        The message is read, not copied: it may pick one of this round's proposals ("the latest
        one is fine"), change one, or describe an idea of the researcher's own.  The model's
        reading is shown to the researcher; if it fails, the message itself becomes the direction.
        """
        text = text.strip()
        candidates = self._round_proposals()
        ideas = self._meeting_ideas()
        reading: ResearcherDirection | None = None
        try:
            answer = await self._router.generate_structured(
                PipelineStage.IDEA_SCREENING,
                [Message.system(_DIRECTION_PROMPT),
                 Message.user(self._direction_request(text, candidates, ideas))],
                ResearcherDirection, temperature=0.0, max_tokens=4000)
            reading = ResearcherDirection.model_validate(dict(answer))      # all fields present
        except Exception as exc:
            logger.warning("Could not interpret the researcher's direction (%s); using the message as it is", exc)

        basis = reading.basis.strip() if reading else ""
        base = next((c for c in candidates if c.proposal_id == basis), None)
        idea = ideas.get(basis)
        changes = reading.changes.strip() if reading else ""
        if re.match(r"^(none|no changes|n/?a)\b", changes, re.I):
            changes = ""
        note = {"mode": "researcher", "message": text}
        if base is None and idea is not None:
            # an idea from the meeting that did not become the proposal
            base = RoundtableProposal(
                proposal_id=basis, title=idea["headline"] or f"{idea['seat']}'s idea",
                description=idea["content"], research_questions=[idea["headline"]] if idea["headline"] else [],
                created_at=datetime.now(UTC))
        if base is not None:
            proposal = base.model_copy(update={
                "proposal_id": f"prop_{uuid.uuid4().hex[:8]}",
                "description": base.description + (f"\n\nChanges asked for by the researcher: {changes}"
                                                    if changes else ""),
                "selected": True,
                "decision": {**note, "basis": base.proposal_id, "interpretation": reading.interpretation,
                             "changes": changes},
                "created_at": datetime.now(UTC),
            })
        else:
            changes = ""
            title = (reading.title if reading and reading.title else "") or next(
                (ln.strip().lstrip("#*- ").strip() for ln in text.splitlines() if ln.strip()), "")
            proposal = RoundtableProposal(
                proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                title=title[:100] or "Researcher's proposal",
                description=(reading.description if reading and reading.description else text),
                research_questions=[text[:500]],
                selected=True,
                decision={**note, "basis": "new",
                          "interpretation": reading.interpretation if reading else "the message as written"},
                created_at=datetime.now(UTC),
            )
        # the newest proposal of the round, so later phases and a resume use it
        self._workspace.atomic_write_json(
            self._workspace.proposal_path(proposal.proposal_id, self._current_round), proposal)
        await self._publish("proposal_from_researcher", proposal_id=proposal.proposal_id, title=proposal.title,
                            text=text, basis=proposal.decision.get("basis"),
                            basis_title=base.title if base is not None else "",
                            interpretation=proposal.decision.get("interpretation", ""),
                            changes=proposal.decision.get("changes", ""), rerun=list(rerun or []))
        return proposal

    @staticmethod
    def _direction_text(proposal: RoundtableProposal) -> str:
        parts = [f"Title: {proposal.title}", proposal.hypothesis or "", proposal.description,
                 f"The researcher's words: \"{proposal.decision.get('message', '')}\""]
        return "\n\n".join(p for p in parts if p)

    def _round_proposals(self) -> list[RoundtableProposal]:
        """This round's proposals, oldest first."""
        rd = self._workspace.phases_dir / "ideation" / f"round_{self._current_round}"
        out = []
        for path in sorted((p for p in rd.glob("*.json") if not p.name.startswith("meeting_state")),
                           key=lambda p: p.stat().st_mtime):
            try:
                out.append(RoundtableProposal.model_validate_json(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return out

    def _meeting_ideas(self) -> dict[str, dict[str, str]]:
        """The ideas presented at this round's last ideation meeting, by ID (``idea_p01``, ...)."""
        from aurora.agents.roundtable.seat import headline
        rd = self._workspace.phases_dir / "ideation" / f"round_{self._current_round}"
        ideas: dict[str, dict[str, str]] = {}
        for name in ("meeting_state.finished.json", "meeting_state.json"):
            try:
                state = json.loads((rd / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for c in state.get("contributions", []):
                if c.get("kind") == "presentation" and c.get("content"):
                    ideas[f"idea_{c['seat_id']}"] = {"seat": c["seat_id"], "model": c.get("model_id", ""),
                                                     "content": c["content"], "headline": headline(c["content"])}
        return ideas

    def _direction_request(self, text: str, candidates: list[RoundtableProposal],
                           ideas: dict[str, dict[str, str]] | None = None) -> str:
        blocks = []
        for i, c in enumerate(candidates, 1):
            score = f"{c.score.composite:g}/10 — {c.score.explanation[:400]}" if c.score else "not screened"
            latest = " (the latest)" if i == len(candidates) else ""
            blocks.append(f"### {c.proposal_id}{latest}: {c.title}\nScreening: {score}\n"
                          f"{(c.hypothesis or c.description)[:1500]}")
        idea_blocks = [f"### {key}: {i['headline']} (presented by {i['seat']}, {i['model']})\n{i['content'][:1500]}"
                       for key, i in (ideas or {}).items()]
        return (f"## Campaign objective\n{self._config.objective[:2000]}\n\n"
                f"## Proposals from ideation, oldest first\n\n" + "\n\n".join(blocks or ["(none)"])
                + ("\n\n## Ideas presented at the last ideation meeting\n\n" + "\n\n".join(idea_blocks)
                   if idea_blocks else "")
                + f"\n\n## The researcher's message\n{text}")

    def _single_job_plan(self, proposal: RoundtableProposal | None) -> ExperimentPlan:
        """Plan used when planning is skipped: one job that carries out the proposal or objective."""
        what = (proposal.description or proposal.title) if proposal else self._config.objective
        return ExperimentPlan(
            plan_id=f"plan_{uuid.uuid4().hex[:8]}",
            proposal_id=proposal.proposal_id if proposal else "none",
            jobs=[ExperimentJob(job_id="main", skill_name="Carry out the research",
                                parameters={"description": what[:2000]})],
        )

    @property
    def resources(self) -> dict[str, Any]:
        """The container's resource limits (as configured; live if the container runs)."""
        sb = self._config.sandbox
        return {"docker": sb.docker, "cpus": sb.cpus, "memory_mb": sb.memory_mb,
                "container": getattr(self._execution_backend, "container_id", None)}

    async def update_resources(self, cpus: float | None = None, memory_mb: int | None = None) -> dict[str, Any]:
        """Change the container's CPU and memory limits while the campaign runs.

        The running container is updated in place (nothing restarts), the new limits are kept for
        the rest of the campaign, and a research agent at work is told.
        """
        from aurora.core.docker_backend import DockerBackend
        backend = self._execution_backend
        if isinstance(backend, DockerBackend):
            await backend.update_resources(cpus, memory_mb)
        sb = self._config.sandbox
        old = (sb.cpus, sb.memory_mb)
        sb.cpus = sb.cpus if cpus is None else cpus
        sb.memory_mb = sb.memory_mb if memory_mb is None else memory_mb
        await self._publish("resources_updated", cpus=sb.cpus, memory_mb=sb.memory_mb,
                            old_cpus=old[0], old_memory_mb=old[1], live=bool(getattr(backend, "container_id", None)))
        runner = self.current_iteration_runner
        if runner is not None and (sb.cpus, sb.memory_mb) != old:
            await runner.inject_message(
                f"[Note] The container's limits were changed by the researcher: now {sb.cpus:g} CPUs and "
                f"{sb.memory_mb / 1024:g} GB of memory (before: {old[0]:g} CPUs, {old[1] / 1024:g} GB). "
                "Adjust parallelism and memory use accordingly.")
        return self.resources

    @property
    def phase(self) -> CampaignPhase:
        return self._phase

    @property
    def event_bus(self) -> EventBus:
        return self._bus

    async def handle_user_message(self, text: str) -> dict[str, Any]:
        """A message from the human while the campaign runs (duplex interaction).

        The front desk agent answers it, relaying instructions to the research
        agent when needed; without a front desk the message goes straight to
        the research agent.
        """
        if self._frontdesk is not None:
            return await self._frontdesk.handle_user_message(text)
        if self.current_iteration_runner is not None:
            await self.current_iteration_runner.inject_message(f"[Message from the researcher]\n{text}")
            return {"text": "Forwarded to the research agent.", "relayed": True}
        return {"text": "No agent is running right now; the message was not delivered.", "relayed": False}

    def request_stop(self, reason: str = "stopped by the user", *, by_user: bool = True) -> None:
        """Stop the campaign now: the running phase is cancelled, then everything is cleaned up."""
        if self._stop_requested:
            return
        self._stop_requested = reason
        self._stopped_by_user = by_user
        self.human_gate.cancel_all(reason)
        if self.current_iteration_runner is not None and hasattr(self.current_iteration_runner, "request_stop"):
            self.current_iteration_runner.request_stop()
        task = self._main_task
        # once the campaign is cleaning up, cancelling it would leave the container running and
        # the result unreported; the clean-up finishes by itself
        if task is not None and not task.done() and task is not asyncio.current_task() and not self._finishing:
            task.cancel()

    def pause(self) -> None:
        """Pause the engine after the current iteration completes."""
        self._paused.clear()
        logger.info("Engine pause requested")

    def resume(self) -> None:
        """Resume a paused engine."""
        self._paused.set()
        logger.info("Engine resumed")

    async def _wait_if_paused(self) -> None:
        """Wait until the engine is unpaused.

        This is an ``asyncio.Event.wait()`` call so it does **not** block
        the event loop.
        """
        if not self._paused.is_set():
            logger.info("Engine paused — waiting for resume")
            await self._publish("engine_paused")
            await self._paused.wait()
            await self._publish("engine_resumed")
            logger.info("Engine resumed — continuing")

    # ==================================================================
    # Setup
    # ==================================================================

    def _setup(self) -> None:
        """Create the workspace, execution backend, router, stores and hooks.

        A pre-assigned ``_router`` or execution backend is kept.  The
        container itself is started in :meth:`run_campaign`.
        """
        self._workspace = WorkspaceManager(
            config=self._config.workspace,
            campaign_id=self._config.campaign_id,
        )
        self._workspace.initialize()
        # the effective configuration, for `aurora resume` (without secrets)
        import yaml
        saved = self._config.model_dump(mode="json", exclude={"api_key"})
        for provider in saved.get("providers", []):
            provider.pop("api_key", None)
        # webhook URLs and keys are secrets too (the agents can read this directory)
        saved.get("notifications", {})["channels"] = []
        (self._workspace.campaign_dir / "config.yaml").write_text(
            yaml.safe_dump(saved, sort_keys=False, allow_unicode=True))

        # Create Docker backend from config if needed
        sandbox = self._config.sandbox
        if self._execution_backend is None and sandbox.docker:
            from aurora.core.docker_backend import DockerBackend
            self._execution_backend = DockerBackend(
                docker_image=sandbox.image,
                cpus=sandbox.cpus,
                memory_mb=sandbox.memory_mb,
                allow_network=sandbox.network,
                tests_dir=sandbox.tests_dir,
                campaign_dir=str(self._workspace.campaign_dir),
                workspace_dir=str(self._workspace.campaign_dir / "workspace") if sandbox.mount_workspace else "",
                agent=self._container_agent_settings(),
                extra_mounts=self._compute_mounts(),
            )
        # Without a container the Claude Code sessions still run, confined to the workspace
        elif self._execution_backend is None and not self._config.mock_mode:
            from aurora.core.execution_backend import LocalBackend
            self._execution_backend = LocalBackend(str(self._workspace.root))
        # Named seats may set their own model; the router reads seat models from models.ideation_seats
        seats = self._config.ideation.seats
        if seats:
            pool = self._config.models.ideation_seats
            seat_models = [sc.model or pool[i % len(pool)] for i, sc in enumerate(seats)]
            self._config.models.ideation_seats = seat_models
        if self._router is None:
            self._router = create_router(self._config)
        self._store = ArtifactStore(self._workspace)
        self._ctx = ContextReconstructor(self._workspace)
        self._mm = MentalModelManager(self._workspace)

        # Register subsystems
        self._registry.register(WorkspaceManager, self._workspace)
        self._registry.register(LLMRouter, self._router)
        if self._store:
            self._registry.register(ArtifactStore, self._store)
        self._registry.register(ContextReconstructor, self._ctx)
        self._registry.register(MentalModelManager, self._mm)
        self._registry.register(EventBus, self._bus)

        # Mock mode drives a scripted materials study with simulated tools and
        # skills; real runs use Claude Code's own tools and the skill library
        skill_reg = SkillRegistry()
        if self._config.mock_mode:
            self._register_mock_skills(skill_reg)
            from aurora.tools import create_tool_registry
            self._registry.register(ToolRegistry, create_tool_registry(self._config))
        self._registry.register(SkillRegistry, skill_reg)

        # Lightweight audit hooks (the hook-based audit of mock mode, periodic checks)
        from aurora.hooks.audit_hooks import (
            ClaimPromotionHook,
            ConsistencyHook,
            ConvergenceHook,
            EnergyAnomalyHook,
            GoalDriftHook,
            MilestoneAuditHook,
            NovelResultHook,
            PeriodicAuditHook,
            RepeatedFailureHook,
            TerminationGuardHook,
        )
        hook_reg = HookRegistry()
        for hook_cls in (EnergyAnomalyHook, ConvergenceHook, ConsistencyHook, PeriodicAuditHook,
                         GoalDriftHook, RepeatedFailureHook, NovelResultHook, ClaimPromotionHook,
                         TerminationGuardHook, MilestoneAuditHook):
            hook_reg.register(hook_cls(self._router))
        self._hooks = hook_reg
        self._registry.register(HookRegistry, hook_reg)

        self.human_gate._log_path = self._workspace.campaign_dir / "human_gates.jsonl"
        self._sessions = SessionRegistry(self._workspace.campaign_dir / "sessions.json")
        self._setup_skills()

    def _setup_skills(self) -> None:
        """Put the skill library and the user's skill directories where Claude Code finds them."""
        import shutil

        from aurora.paths import aurora_home
        from aurora.skills.materializer import SkillMaterializer
        from aurora.skills.store import SkillStore

        try:
            self._skill_store = SkillStore(aurora_home() / "skills.db")
            n = len(SkillMaterializer(self._skill_store, self._workspace.root)
                    .materialize_for_experiment(self._config.campaign_id, campaign_id=self.project_id))
        except Exception:
            logger.warning("Skill library unavailable", exc_info=True)
            n = 0
        self._bus.subscribe_all(self._on_feedback_event)
        dest = self._workspace.root / ".claude" / "skills"
        for d in self._config.skills_dirs:
            src = Path(d).expanduser()
            if not src.is_dir():
                raise FileNotFoundError(f"skills_dirs entry not found: {src}")
            for skill in src.iterdir():
                if (skill / "SKILL.md").is_file():
                    shutil.copytree(skill, dest / skill.name, dirs_exist_ok=True)
                    n += 1
        if n:
            logger.info("%d skills available to the agents in %s", n, dest)

    def _container_agent_settings(self) -> dict[str, str] | None:
        """What the container needs to run research agents itself (sandbox.agent_in_container)."""
        import shutil
        sb = self._config.sandbox
        if not (sb.agent_in_container and sb.docker and sb.mount_workspace):
            return None
        claude = shutil.which("claude")
        if not claude:
            logger.warning("agent_in_container: the claude executable was not found; agents work from the host")
            return None
        return {"claude": os.path.realpath(claude), "upstream": self._config.claude_code_base_url,
                "key": self._config.api_key_value}

    async def _sync_container_files(self) -> None:
        """Copy the container's /app to <campaign>/workspace (after every research job).

        If the process dies, the files of finished jobs are already on the host.
        """
        backend = self._execution_backend
        if backend is None or not getattr(backend, "container_id", None) or not self._workspace:
            return
        if getattr(backend, "mounted", False):
            return          # /app is the workspace directory: nothing to copy
        try:
            await backend.extract_workspace(self._workspace.campaign_dir / "workspace")
        except Exception:
            logger.warning("Copying /app to the campaign directory failed", exc_info=True)

    async def _restore_container_files(self) -> None:
        """On resume, put the files of the previous run (saved to <campaign>/workspace) back into /app."""
        container = getattr(self._execution_backend, "container_id", None)
        saved = self._workspace.campaign_dir / "workspace"
        if getattr(self._execution_backend, "mounted", False):
            return          # mounted as /app: the files are there already
        if not container or not saved.is_dir() or not any(saved.iterdir()):
            return
        proc = await asyncio.create_subprocess_exec(
            "docker", "cp", f"{saved}/.", f"{container}:/app",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Could not restore the previous files into the container: {err.decode().strip()}")
        logger.info("Restored the previous run's files into /app")

    async def _copy_inputs(self) -> None:
        """Copy ``config.inputs`` to inputs/ in the agents' working directory."""
        if not self._config.inputs:
            return
        container = getattr(self._execution_backend, "container_id", None)
        mounted = getattr(self._execution_backend, "mounted", False)
        for item in self._config.inputs:
            src = Path(item).expanduser().resolve()
            if not src.exists():
                raise FileNotFoundError(f"inputs entry not found: {src}")
            if mounted:     # /app is <campaign>/workspace: copy there on the host
                await _clone(src, self._workspace.campaign_dir / "workspace" / "inputs" / src.name)
            elif container:
                await self._execution_backend.exec_command("mkdir -p /app/inputs")
                proc = await asyncio.create_subprocess_exec(
                    "docker", "cp", str(src), f"{container}:/app/inputs/{src.name}",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                _, err = await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(f"Could not copy {src} into the container: {err.decode().strip()}")
            else:
                await _clone(src, self._workspace.root / "inputs" / src.name)
        logger.info("Copied %d inputs for the agents", len(self._config.inputs))

    def _register_mock_skills(self, skill_reg: SkillRegistry) -> None:
        """Skills the scripted mock study uses (simulated materials workflows)."""
        from aurora.skills.materials.compute_orchestration import ComputeOrchestrationSkill
        from aurora.skills.materials.convergence_analysis import ConvergenceAnalysisSkill
        from aurora.skills.materials.scientific_visualization import ScientificVisualizationSkill
        from aurora.skills.materials.segregation_analysis import SegregationAnalysisSkill
        from aurora.skills.materials.simulation_diagnosis import SimulationDiagnosisSkill
        from aurora.skills.materials.statistical_validation import StatisticalValidationSkill
        from aurora.skills.materials.structure_construction import StructureConstructionSkill
        for skill_cls in (StructureConstructionSkill, ConvergenceAnalysisSkill, SegregationAnalysisSkill,
                          SimulationDiagnosisSkill, StatisticalValidationSkill,
                          ScientificVisualizationSkill, ComputeOrchestrationSkill):
            skill_reg.register(skill_cls())

    # ==================================================================
    # Event helper
    # ==================================================================

    async def _publish(self, topic: str, **data: Any) -> None:
        await self._bus.publish(Event(name=topic, data=data, source="engine"))

    # ==================================================================
    # Hook helpers
    # ==================================================================

    _AUDITOR_SYSTEM_PROMPT = """\
You are the milestone auditor of a research pipeline. A phase has just finished;
review its output before the pipeline hands it to the next phase.

## What to check
1. **Goal alignment** — does the work still serve the campaign objective?
2. **Completeness** — did the phase produce what it should? Missing files or steps?
3. **Correctness** — are the results plausible and internally consistent? Look for
   errors, silent failures, fabricated or unsupported numbers, overclaiming.
4. **Traceability** — can every claim be traced to a file, a command or a source?

Read the actual files; do not guess.  With a container, run read-only commands
in it (`docker exec`) to check outputs.

## Verdict
- **pass** — sound enough to proceed; list minor issues in recommendations.
- **reject** — the phase must revise its output; the recommendations are sent
  back to it as required fixes.
- **escalate** — a decision only a human should make (the objective itself looks
  wrong or infeasible, a risky or costly step, a conflict you cannot resolve).

Write the report as JSON to {audit_path}:
```json
{{
  "completed_phase": "<phase>",
  "next_phase": "<phase>",
  "verdict": "pass" | "reject" | "escalate",
  "dimensions": {{
    "goal_alignment": {{"ok": true, "note": "..."}},
    "completeness": {{"ok": true, "note": "..."}},
    "correctness": {{"ok": true, "note": "..."}},
    "traceability": {{"ok": true, "note": "..."}}
  }},
  "summary": "one paragraph",
  "recommendations": ["specific, actionable items"],
  "question_for_human": "only for escalate: the decision the human must make"
}}
```
Be specific: cite file names and concrete observations.
"""

    async def _fire_milestone(
        self,
        completed_phase: CampaignPhase,
        next_phase: CampaignPhase,
        iteration_count: int,
    ) -> str:
        """Audit the hand-off from *completed_phase* to *next_phase*.

        ``reject`` sends the auditor's fixes back into the phase's session (up to
        ``audit.milestone_max_retries`` times); ``escalate``, or a phase that is
        still rejected after its retries, opens a human gate.  Returns guidance
        from the human for the next phase ('' if none); raises
        :class:`CampaignStopped` when the human stops the campaign.
        """
        if not self._workspace or not self._config.audit.milestone:
            return ""
        audit_dir = self._workspace.root / "audit_records"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / f"round{self._current_round}_{completed_phase.value}_to_{next_phase.value}.json"

        if not self._should_use_agent_session():
            await self._fire_milestone_hooks(completed_phase, next_phase, iteration_count, audit_path)
            return ""

        phase_session_id = self._last_phase_session_id
        retries = self._config.audit.milestone_max_retries
        report: dict[str, Any] = {}
        for attempt in range(retries + 1):
            self._check_budget()
            audit_path.unlink(missing_ok=True)
            await self._publish("milestone_audit_started", completed_phase=completed_phase.value,
                                next_phase=next_phase.value, attempt=attempt + 1)
            await self._run_agent_session(
                phase_name="audit",
                session_key=f"audit:{completed_phase.value}->{next_phase.value}:round{self._current_round}",
                system_prompt=self._AUDITOR_SYSTEM_PROMPT.format(audit_path=str(audit_path)),
                user_message=(f"The '{completed_phase.value}' phase has finished; next is "
                              f"'{next_phase.value}'.\n\nCampaign objective: {self._config.objective[:1500]}\n\n"
                              "Review the phase's output and write your audit report."),
                max_turns=self._config.agent.phase_max_turns,
                model_override=self._config.models.auditor,
            )
            report = self._read_audit_report(audit_path)
            verdict = report.get("verdict", "missing")
            await self._publish("milestone_audit", completed_phase=completed_phase.value,
                                next_phase=next_phase.value, verdict=verdict, attempt=attempt + 1,
                                summary=report.get("summary", ""),
                                recommendations=report.get("recommendations", []),
                                report_path=str(audit_path))
            logger.info("Milestone audit %s -> %s: %s", completed_phase.value, next_phase.value, verdict)

            if verdict == "missing":
                logger.warning("The auditor wrote no valid report (%s); proceeding without it", audit_path)
            if verdict in ("pass", "missing"):
                await self._distill_phase_skills(completed_phase)
                return ""
            if verdict == "escalate":
                break
            # reject: the phase revises its output with the auditor's fixes
            if attempt >= retries or not phase_session_id:
                break
            result = await self._run_agent_session(
                phase_name=completed_phase.value, system_prompt="",
                user_message=self._audit_feedback(report, attempt),
                max_turns=self._config.agent.phase_max_turns, resume_session_id=phase_session_id)
            phase_session_id = result.get("session_id") or phase_session_id

        escalated = report.get("verdict") == "escalate"
        decision = await self.human_gate.request(
            "milestone_audit",
            (f"Auditor escalated the {completed_phase.value} → {next_phase.value} hand-off" if escalated
             else f"{completed_phase.value} still fails its audit after {retries} revisions"),
            "\n".join(x for x in [
                report.get("question_for_human", ""), report.get("summary", ""),
                "Recommendations:\n" + "\n".join(f"- {r}" for r in report.get("recommendations", []))
                if report.get("recommendations") else "",
                f"Full report: {audit_path}",
                f"continue: proceed to {next_phase.value}; instruct: proceed, following your message; "
                "stop: end the campaign.",
            ] if x))
        if decision.action == "stop":
            self._stopped_by_user = decision.decided_by in ("human", "frontdesk")
            raise CampaignStopped(f"stopped at the {completed_phase.value} audit: {decision.message or 'human decision'}")
        await self._distill_phase_skills(completed_phase)
        return decision.message if decision.action in ("instruct", "continue") else ""

    @staticmethod
    def _read_audit_report(path: Path) -> dict[str, Any]:
        try:
            report = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        # older prompt wording: overall_verdict pass/warning/fail
        verdict = str(report.get("verdict") or report.get("overall_verdict") or "").lower()
        report["verdict"] = {"warning": "pass", "fail": "reject"}.get(verdict, verdict) or "missing"
        if report["verdict"] not in ("pass", "reject", "escalate"):
            report["verdict"] = "missing"
        return report

    @staticmethod
    def _audit_feedback(report: dict[str, Any], attempt: int) -> str:
        lines = [f"## Milestone audit: your output was rejected (revision {attempt + 1})", "",
                 report.get("summary", "")]
        for name, dim in (report.get("dimensions") or {}).items():
            if isinstance(dim, dict) and dim.get("ok") is False:
                lines.append(f"- **{name}**: {dim.get('note', '')}")
        if report.get("recommendations"):
            lines += ["", "Required fixes:"] + [f"- {r}" for r in report["recommendations"]]
        lines += ["", "Fix these issues and update your output files."]
        return "\n".join(lines)

    async def _fire_milestone_hooks(self, completed_phase: CampaignPhase, next_phase: CampaignPhase,
                                    iteration_count: int, audit_path: Path) -> None:
        """Mock mode: the lightweight hook-based audit (no agent session)."""
        artifacts = self._store.load() if self._store else None
        state_summary = (f"Objective: {artifacts.initial_objective}; Goal: {artifacts.current_goal}; "
                         f"Experiments: {len(artifacts.experiments)}; Claims: {len(artifacts.claims)}"
                         ) if artifacts else ""
        ctx = HookContext(
            hook_point=HookPoint.MILESTONE_COMPLETE,
            iteration=iteration_count,
            data={"completed_phase": completed_phase.value, "next_phase": next_phase.value,
                  "state_summary": state_summary, "iteration_count": iteration_count},
            metadata={"campaign_id": self._config.campaign_id},
        )
        hook_results = await self._hooks.fire(HookPoint.MILESTONE_COMPLETE, ctx)
        record = {
            "completed_phase": completed_phase.value,
            "next_phase": next_phase.value,
            "verdict": "pass" if all(r.passed for r in hook_results) else "reject",
            "hooks": [{"name": r.hook_name, "passed": r.passed, "message": r.message,
                       "severity": r.severity} for r in hook_results],
        }
        audit_path.write_text(json.dumps(record, indent=2, default=str))
        await self._publish("milestone_audit", completed_phase=completed_phase.value,
                            next_phase=next_phase.value, verdict=record["verdict"], attempt=1,
                            summary=f"{sum(r.passed for r in hook_results)}/{len(hook_results)} checks passed",
                            recommendations=[], report_path=str(audit_path))

    # ------------------------------------------------------------------
    # Skills: learning (self-distillation, the researcher's corrections) and using them
    # ------------------------------------------------------------------

    async def _distill_phase_skills(self, completed_phase: CampaignPhase) -> None:
        """Self-distillation after a phase hand-off (research jobs are distilled one by one)."""
        if completed_phase in (CampaignPhase.IDEATION, CampaignPhase.RESEARCH):
            return
        entry = next((t for t in reversed(self._phase_transcripts) if t.get("phase") == completed_phase.value), None)
        if entry and entry.get("path"):
            self._learn(self._learn_from_work(f"the {completed_phase.value} phase", entry["path"],
                                              source_phase=completed_phase.value))

    def _learn(self, coro: Any) -> None:
        """Run skill learning in the background; the campaign does not wait for it."""
        if self._skill_store is None or self._router is None:
            coro.close()
            return
        task = asyncio.create_task(coro)
        self._skill_tasks.add(task)
        task.add_done_callback(self._skill_tasks.discard)

    async def _learn_from_work(self, what: str, transcript: str, *, job: str | None = None,
                               source_phase: str = "") -> None:
        """Self-distillation: skills from what an agent did in a phase or a research job."""
        from aurora.skills import learning
        if not self._config.skills.distill:
            return
        try:
            digest = await asyncio.to_thread(learning.transcript_digest, transcript, job=job)
            proposals = await learning.distill(
                self._router, PipelineStage.SKILLS, digest=digest, library=self._library_text(),
                what=f"{what}, in a campaign on: {self._config.objective[:600]}")
        except Exception:
            logger.warning("Self-distillation of %s failed", what, exc_info=True)
            return
        await self._offer_skills(proposals, origin="self", source_phase=source_phase)

    async def _on_feedback_event(self, event: Event) -> None:
        """The researcher's words to the agents: candidates for skills (first occurrence)."""
        if not self._config.skills.from_feedback:
            return
        d = event.data
        if event.name == "instruction_relayed" and d.get("message"):
            message, where = str(d["message"]), "an instruction relayed to the research agent while it worked"
        elif event.name == "instruction_queued" and d.get("message"):
            message, where = str(d["message"]), (f"an instruction given in the {d.get('phase', '')} phase, "
                                                 "kept for the planning and research agents")
        elif (event.name == "human_gate_resolved" and str(d.get("message", "")).strip()
              and d.get("decided_by") in ("human", "frontdesk") and d.get("action") in ("instruct", "continue")):
            message, where = str(d["message"]), f"the researcher's answer at a decision point: {d.get('title', '')}"
        else:
            return
        self._learn(self._learn_from_feedback(message, where))

    async def _learn_from_feedback(self, message: str, where: str) -> None:
        from aurora.skills import learning
        context = f"Phase: {self._phase.value}. This was {where}. Campaign objective: {self._config.objective[:600]}"
        st = getattr(self, "_frontdesk_status", None)
        if st is not None and getattr(st, "recent_actions", None):
            context += "\nWhat the research agent was doing:\n" + "\n".join(st.recent_actions[-8:])
        try:
            proposals = await learning.from_feedback(self._router, PipelineStage.SKILLS, message=message,
                                                     context=context, library=self._library_text())
        except Exception:
            logger.warning("Turning the researcher's message into a skill failed", exc_info=True)
            return
        await self._offer_skills(proposals, origin="human", source_phase=self._phase.value, feedback=message)

    def _library_text(self) -> str:
        from aurora.skills.learning import library_summary
        try:
            return library_summary(self._skill_store.list_for_experiment(self._config.campaign_id,
                                                                         campaign_id=self.project_id))
        except Exception:
            return "(the library is unavailable)"

    async def _offer_skills(self, proposals: list, *, origin: str, source_phase: str, feedback: str = "") -> None:
        """Offer proposed skills to the researcher, or add them at once (skills.auto_accept)."""
        from aurora.skills import learning
        from aurora.skills.store import DistilledSkill
        existing = {s.name: s for s in self._skill_store.list_all()}
        for p in learning.clean(proposals, existing):
            old = existing.get(p.name)
            data = {"suggestion_id": uuid.uuid4().hex[:12],
                    "action": p.action, "name": p.name, "description": p.description, "kind": p.kind,
                    "origin": origin, "text": p.text, "rule": p.text, "reason": p.reason,
                    "source_phase": source_phase, "source_experiment": self._config.campaign_id,
                    "feedback": feedback}
            if old is not None:
                data.update(skill_id=old.skill_id, previous_text=old.rule, version=old.version)
            if not self._config.skills.auto_accept:
                await self._publish("skill_suggestion", **data)
                continue
            if old is not None:
                self._skill_store.revise(old.skill_id, p.text, reason=p.reason, description=p.description,
                                         source_experiment=self._config.campaign_id)
            else:
                self._skill_store.create(DistilledSkill(
                    name=p.name, rule=p.text, description=p.description, kind=p.kind, origin=origin,
                    source_phase=source_phase, source_experiment=self._config.campaign_id,
                    source_campaign=self.project_id, scope="global"))
            await self._publish("skill_learned", **data)
        self._refresh_skills()

    def _refresh_skills(self) -> None:
        """Write the current library where the agents' Claude Code finds it (also in the container)."""
        if self._skill_store is None or self._workspace is None:
            return
        from aurora.skills.materializer import SkillMaterializer
        roots = [self._workspace.root]
        if getattr(self._execution_backend, "agent_env", None):
            roots.append(self._workspace.root / "agent_home")     # ~ of agents inside the container
        try:
            for root in roots:
                SkillMaterializer(self._skill_store, root).materialize_for_experiment(
                    self._config.campaign_id, campaign_id=self.project_id)
        except Exception:
            logger.debug("Refreshing the skills failed", exc_info=True)

    def _skills_note(self) -> str:
        """The skills an agent can use, for its system prompt."""
        skills_dir = self._workspace.root / ".claude" / "skills" if self._workspace else None
        if skills_dir is None or not skills_dir.is_dir():
            return ""
        lines = []
        for d in sorted(skills_dir.iterdir()):
            md = d / "SKILL.md"
            if md.is_file():
                m = re.search(r"^description:\s*(.+)$", md.read_text(errors="replace"), re.M)
                lines.append(f"- {d.name}: {m.group(1).strip() if m else ''}")
        if not lines:
            return ""
        return ("\n\n## Skills\nThe team's skill library (learned from earlier work and the researcher's "
                "corrections). When your task matches one, load it with the Skill tool and follow its "
                "procedure, validation checks and failure modes:\n" + "\n".join(lines[:60]))

    async def _finish_skill_learning(self, timeout: float = 180.0) -> None:
        if self._skill_tasks:
            await asyncio.wait(set(self._skill_tasks), timeout=timeout)

    # ==================================================================
    # Phase implementations
    # ==================================================================

    def _should_use_agent_session(self) -> bool:
        """Whether to use Claude Code agent sessions for pipeline phases."""
        return (
            not self._config.mock_mode
            and self._execution_backend is not None
        )

    async def _run_agent_session(
        self,
        phase_name: str,
        system_prompt: str,
        user_message: str,
        max_turns: int = 30,
        model_override: str | None = None,
        resume_session_id: str | None = None,
        session_key: str | None = None,
    ) -> dict[str, Any]:
        """Run a Claude Code agent session and return the full result.

        Each phase starts a fresh session; prior phases' outputs and transcripts
        are on disk and the system prompt says where.  The session is recorded
        under *session_key* (default: phase and round): if the campaign is
        stopped and resumed before it finishes, it is continued instead of
        started over.

        Returns dict with keys: final_text, session_id, turns, cost_usd, error, ...
        """
        container_id = getattr(self._execution_backend, "container_id", None)
        key = session_key or f"{phase_name}:round{self._current_round}"
        resumed = False
        if resume_session_id is None and self._resuming and self._sessions is not None:
            resume_session_id = self._sessions.open_session(key)
            resumed = resume_session_id is not None
        if resume_session_id and not system_prompt and self._sessions is not None:
            system_prompt = self._sessions.system_prompt(key)

        prior_context = self._build_prior_context_prompt(phase_name)
        self._refresh_skills()
        full_system = system_prompt + prior_context + (self._skills_note() if phase_name != "audit" else "")
        if resumed:
            logger.info("Continuing the interrupted %s session %s", key, resume_session_id)
            user_message = f"{CONTINUE_NOTE}\n\n{user_message}"

        live_path = None
        if self._workspace:
            live_path = str(self._workspace.phase_transcript_path(phase_name, self._current_round))

        session = AgentSession(
            self._config,
            self._bus,
            phase_name=phase_name,
            docker_container=container_id,
            cwd=str(self._workspace.root) if self._workspace else None,
            max_turns=max_turns,
            live_transcript_path=live_path,
            model_override=model_override or self._config.models.for_phase(phase_name),
            resume_session_id=resume_session_id,
            max_budget_usd=self._remaining_budget(),
            session_callback=(lambda sid: self._sessions.record(key, sid, full_system)) if self._sessions else None,
        )
        result = await session.run(full_system, user_message)
        self.cost_usd += float(result.get("cost_usd") or 0.0)
        self._record_spent()
        if resumed and result.get("error") and not result.get("turns"):
            # the old session could not be continued (e.g. another machine): start over, but
            # point the agent at what the interrupted attempt left behind
            logger.warning("Could not continue session %s (%s); starting a new one", resume_session_id,
                           result["error"])
            self._sessions.done(key)
            return await self._run_agent_session(
                phase_name, system_prompt, user_message.replace(CONTINUE_NOTE, RESTART_NOTE),
                max_turns=max_turns, model_override=model_override, session_key=key)
        if result.get("error"):
            logger.warning("The %s session ended with an error: %s", phase_name, result["error"])
        elif self._sessions is not None:
            self._sessions.done(key)
        self._save_phase_transcript(phase_name, result)
        return result

    _PHASE_ROLES: dict[str, str] = {
        "planning": (
            "You are the Planning agent. Your job is to decompose the research "
            "proposal into concrete, executable experiment jobs with clear "
            "dependencies. Produce a structured experiment plan."
        ),
        "research": (
            "You are the Research agent. Your job is to execute the assigned "
            "experiment job in its working environment: write code, run "
            "experiments, check and save the outputs, and summarise what was done."
        ),
        "analysis": (
            "You are the Analysis agent. Your job is to evaluate the research "
            "results: what succeeded, what failed and why, whether the findings "
            "are sufficient. Decide whether to proceed to writing or loop back "
            "to an earlier phase for another round."
        ),
        "writing": (
            "You are the Writing agent. Your job is to synthesize all prior "
            "phases' work into a coherent research report — covering motivation, "
            "methods, results, and conclusions."
        ),
    }

    def _build_prior_context_prompt(self, current_phase: str) -> str:
        """Build a system prompt explaining the pipeline, the agent's position, and where to find prior work."""
        if self._workspace is None:
            return ""

        ws = self._workspace
        lines: list[str] = []

        # --- 1. Pipeline overview ---
        lines.append("\n\n## Pipeline Overview")
        lines.append(
            "You are part of a multi-phase research pipeline:\n\n"
            "  IDEATION → SCREENING → PLANNING → RESEARCH → ANALYSIS → WRITING\n\n"
            "Each phase produces artifacts (plans, code, results, reports) stored on disk. "
            "Analysis can loop back to any earlier phase, creating a new round. "
            "Each round's artifacts are stored separately under `round_N/`."
        )

        # --- 2. Current position ---
        lines.append("\n## Your Position")
        lines.append(f"- **Current phase**: {current_phase}")
        lines.append(f"- **Current round**: {self._current_round}")

        completed = [e["phase"] for e in self._phase_transcripts]
        if completed:
            lines.append(f"- **Completed phases this campaign**: {', '.join(completed)}")

        # --- 3. Role ---
        role = self._PHASE_ROLES.get(current_phase, "")
        if role:
            lines.append("\n## Your Responsibility")
            lines.append(role)

        # --- 4. Workspace layout ---
        lines.append("\n## Workspace")
        lines.append(f"All artifacts are under: `{ws.campaign_dir}`\n")

        # Campaign-level files
        campaign_files = []
        if ws.artifacts_path.exists():
            campaign_files.append("- `artifacts.json` — shared experiment records and observations")
        if ws.mental_model_path.exists():
            campaign_files.append("- `mental_model.json` — evolving research mental model")
        if campaign_files:
            lines.extend(campaign_files)

        # Phase directories with actual content
        lines.append("\n### Phase Artifacts (`phases/`)\n")

        phase_order = ["ideation", "screening", "planning", "preflight", "research", "analysis", "writing"]
        for phase in phase_order:
            if phase == current_phase:
                lines.append(f"**{phase}/** ← you are here")
                continue

            if phase == "writing":
                wd = ws.writing_dir
                if wd.exists() and any(wd.iterdir()):
                    lines.append(f"**{phase}/**")
                    self._append_dir_listing(lines, wd, indent=1)
                continue

            rounds = ws.list_rounds(phase)
            if not rounds:
                continue

            lines.append(f"**{phase}/**")
            for rn in rounds:
                rd = ws.phase_round_dir(phase, rn)
                if phase == "research":
                    jobs = ws.list_research_jobs(rn)
                    lines.append(f"  round_{rn}/ — {len(jobs)} job(s)")
                    for job_id in jobs:
                        iters = ws.list_research_iterations(rn, job_id)
                        lines.append(f"    {job_id}/ — {len(iters)} iteration(s)")
                        for it in iters:
                            iter_dir = ws.research_iter_dir(rn, job_id, it)
                            files = sorted(f.name for f in iter_dir.iterdir() if f.is_file())
                            dirs = sorted(d.name + "/" for d in iter_dir.iterdir() if d.is_dir())
                            contents = ", ".join(files + dirs) if (files or dirs) else "(empty)"
                            lines.append(f"      iter_{it}/ — {contents}")
                else:
                    lines.append(f"  round_{rn}/")
                    self._append_dir_listing(lines, rd, indent=2)

        # --- 5. Guidance ---
        lines.append("\n## How to Use Prior Context")
        lines.append(
            "- Read files directly with your tools to understand prior phases' work.\n"
            "- `transcript.jsonl` files contain the full agent trajectory "
            "(thinking, tool calls, results) — useful for understanding reasoning and decisions.\n"
            "- `workspace/` directories contain files the research agent produced (code, data, outputs).\n"
            "- `plan.json`, `verdict.json`, `proposal.json` etc. are structured outputs from each phase.\n"
            "- Focus on what's relevant to your task — you don't need to read everything."
        )

        return "\n".join(lines)

    @staticmethod
    def _append_dir_listing(
        lines: list[str], directory: Path, indent: int = 1
    ) -> None:
        """Append a shallow listing of files/dirs under *directory*."""
        prefix = "  " * indent
        for entry in sorted(directory.iterdir()):
            if entry.is_file():
                lines.append(f"{prefix}{entry.name}")
            elif entry.is_dir():
                children = list(entry.iterdir())
                lines.append(f"{prefix}{entry.name}/ ({len(children)} items)")

    def _save_phase_transcript(
        self, phase_name: str, result: dict[str, Any]
    ) -> None:
        """Record transcript path and backfill if live writing was not used."""
        if self._workspace is None:
            return
        path = self._workspace.phase_transcript_path(phase_name, self._current_round)

        # If live transcript already wrote the file, skip re-writing.
        # Only backfill when the file is missing or empty (non-Claude-Code drivers).
        if not path.exists() or path.stat().st_size == 0:
            trajectory = result.get("trajectory", [])
            import dataclasses as _dc
            with open(path, "w") as f:
                for entry in trajectory:
                    if _dc.is_dataclass(entry):
                        row = _dc.asdict(entry)
                    elif hasattr(entry, "__dict__"):
                        row = entry.__dict__
                    else:
                        row = entry
                    f.write(json.dumps(row, default=str) + "\n")

                if not trajectory:
                    f.write(json.dumps({
                        "phase": phase_name,
                        "text": result.get("text", ""),
                        "turns": result.get("turns", 0),
                    }) + "\n")

        self._phase_transcripts.append({
            "phase": phase_name,
            "path": str(path),
        })

    def meeting_say(self, name: str, text: str) -> bool:
        """The researcher speaks in a live round table: heard before the next turn. False: no meeting now."""
        if self._roundtable is None or not self._config.ideation.live:
            return False
        self._roundtable.researcher_says.append((name, text))
        return True

    @property
    def literature(self) -> Any:
        """The project's literature, or None when searching and checking papers is off."""
        lit = self._config.literature
        if not lit.enabled or self._config.mock_mode:
            return None
        if self._literature is None:
            from aurora.literature.service import ProjectLiterature
            # without the web console a run is its own project
            self._literature = ProjectLiterature(
                self.project_id or f"run:{self._config.campaign_id}", run_id=self._config.campaign_id,
                rule=lit.add_found, publish=self._publish)
        return self._literature

    async def _cite(self, text: str, by: str, place: str) -> Any:
        """Check the references in a text an agent wrote (None when literature is off)."""
        if self.literature is None or not text.strip():
            return None
        try:
            return await self.literature.check_text(text, by=by, place=place)
        except Exception:
            logger.warning("Checking the references of %s failed", place, exc_info=True)
            return None

    async def _prior_work(self, proposal: RoundtableProposal) -> str:
        """The closest prior work in the indexes, for judging novelty (empty when off or nothing found)."""
        if self.literature is None or not self._config.literature.screening_search:
            return ""
        from aurora.literature.service import paper_card
        queries = [proposal.title, " ".join((proposal.hypothesis or proposal.description).split()[:25])]
        seen: dict[str, dict] = {}
        for q in [q for q in queries if q.strip()]:
            try:
                for e in await self.literature.search(q, by="Screening", context="Screening · closest prior work",
                                                      limit=self._config.literature.results_per_query):
                    seen.setdefault(e["paper_id"], e)
            except Exception:
                logger.warning("Searching prior work failed", exc_info=True)
        cited = [k for k in proposal.references]
        if not seen and not cited:
            return ""
        cards = "\n".join(paper_card(e, abstract_words=80) for e in list(seen.values())[:10])
        return ("## Closest prior work (from OpenAlex, arXiv, Crossref and Semantic Scholar)\n\n"
                "Judge novelty against these real papers: say which one comes closest and what the proposal "
                "adds beyond it. Cite them by key, e.g. [Key21]." + (f" The proposal itself cites: {', '.join(cited)}."
                                                                      if cited else "") + f"\n\n{cards}")

    async def _run_roundtable(self, guidance: str = "") -> RoundtableProposal:
        """Multi-agent ideation (roundtable or independent selection, see ``ideation.mode``)."""
        assert self._router is not None
        assert self._workspace is not None

        artifacts = self._store.load() if self._store else None
        await self._publish("phase_started", phase="ideation", mode=self._config.ideation.mode,
                            seats=self._config.models.ideation_seats)
        ctx = self._build_agent_context(artifacts)
        topic = (artifacts.current_goal if artifacts else "") or self._config.objective
        if guidance:
            topic = f"{topic}\n\nGuidance for this round: {guidance}"
        rt = Roundtable(config=self._config, router=self._router, progress=self._publish,
                        state_path=self._workspace.phase_round_dir("ideation", self._current_round)
                        / "meeting_state.json", literature=self.literature)
        self._roundtable = rt
        try:
            proposal = await rt.run(ctx, topic=topic)
        finally:
            self._roundtable = None
        cites = await self._cite("\n".join([proposal.description, proposal.hypothesis, *proposal.proposed_methods,
                                            *proposal.alternatives_considered, proposal.falsification_check or ""]),
                                 by="Ideation", place="Proposal")
        if cites is not None:
            proposal = proposal.model_copy(update={"references": cites.verified})

        self._workspace.atomic_write_json(
            self._workspace.proposal_path(proposal.proposal_id, self._current_round),
            proposal,
        )

        await self._publish(
            "ideation_complete",
            proposal_id=proposal.proposal_id,
            title=proposal.title,
            hypothesis=proposal.hypothesis,
        )
        return proposal

    async def _run_screening(
        self, proposal: RoundtableProposal
    ) -> RoundtableProposal:
        """Screen the proposal for novelty, feasibility, and significance."""
        await self._publish("phase_started", phase="screening")

        prior = await self._prior_work(proposal)
        if self._should_use_agent_session():
            score = await self._run_screening_agent(proposal, prior)
        else:
            assert self._router is not None
            screener = IdeaScreener(
                self._router,
                system_prompt=self._config.prompts.screening,
            )
            score = await screener.screen(proposal, prior_work=prior)
        await self._cite(score.explanation, by="Screening", place="Screening")

        proposal = proposal.model_copy(
            update={
                "score": score,
                "selected": score.composite >= 6.0,
            }
        )

        await self._publish(
            "screening_complete",
            proposal_id=proposal.proposal_id,
            selected=proposal.selected,
            score=score.composite,
        )
        return proposal

    async def _run_screening_agent(
        self, proposal: RoundtableProposal, prior_work: str = ""
    ) -> IdeaScore:
        """Run screening via Claude Code agent session."""
        assert self._workspace is not None

        score_path = self._workspace.phase_round_dir("screening", self._current_round) / "score.json"
        score_path.parent.mkdir(parents=True, exist_ok=True)

        user_msg = (
            f"## Proposal to Screen\n\n"
            f"**Title**: {proposal.title}\n"
            f"**Description**: {proposal.description}\n"
            f"**Research Questions**: {proposal.research_questions}\n"
            f"**Proposed Methods**: {proposal.proposed_methods}\n"
            f"**Expected Outcomes**: {proposal.expected_outcomes}\n\n"
            + (f"{prior_work}\n\n" if prior_work else "")
            + f"Evaluate this proposal and write your screening verdict as JSON to:\n"
            f"  {score_path}\n\n"
            f"The JSON must have this structure:\n"
            f'{{"novelty": 0-10, "feasibility": 0-10, "significance": 0-10, '
            f'"composite": 0-10, "explanation": "..."}}\n\n'
            f"You may explore the working environment to assess feasibility."
        )

        result = await self._run_agent_session(
            "screening",
            self._config.prompts.screening or (
                "You are a research proposal screener. Evaluate the proposal's "
                "novelty, feasibility, and significance. Be rigorous but fair."
            ),
            user_msg,
            max_turns=self._config.agent.phase_max_turns,
        )
        self._last_phase_session_id = result.get("session_id")

        for attempt in range(2):
            try:
                return IdeaScore(**json.loads(score_path.read_text()))
            except Exception as exc:
                if attempt == 1 or not self._last_phase_session_id:
                    raise RuntimeError(f"Screening produced no valid score at {score_path}: {exc}") from exc
                result = await self._run_agent_session(
                    "screening", "", f"{score_path} is missing or not valid JSON ({exc}). Write it now "
                    "with the required fields.", max_turns=5, resume_session_id=self._last_phase_session_id)
        raise AssertionError("unreachable")

    async def _run_planning(
        self, proposal: RoundtableProposal | None, guidance: str = ""
    ) -> ExperimentPlan:
        """Convert a screened proposal into an experiment plan.

        When Claude Code mode is active, the planner runs as a full
        agent session that can explore the environment before planning.
        Otherwise falls back to the single-shot LLM planner.
        """
        assert self._workspace is not None
        await self._publish("phase_started", phase="planning")

        if self._should_use_agent_session():
            plan = await self._run_planning_agent(proposal, guidance)
        else:
            assert self._router is not None
            skill_reg = self._registry.get_optional(SkillRegistry)
            planner = ExperimentPlanner(
                router=self._router,
                skill_registry=skill_reg,
                workspace=self._workspace,
                system_prompt=self._config.prompts.planning,
            )
            plan = await planner.plan(proposal)
            plan = await self._verify_plan(plan, planner, proposal)

        plan_path = self._workspace.experiment_plan_path_for_round(self._current_round)
        self._workspace.atomic_write_json(plan_path, plan)

        await self._publish(
            "planning_complete",
            plan_id=plan.plan_id,
            num_jobs=len(plan.jobs),
        )
        return plan

    async def _run_planning_agent(
        self, proposal: RoundtableProposal | None, guidance: str = ""
    ) -> ExperimentPlan:
        """Planning as a Claude Code session that writes the plan as JSON."""
        plan_path = self._workspace.experiment_plan_path_for_round(self._current_round)
        proposal_id = proposal.proposal_id if proposal else "none"
        user_msg = (
            f"{self._campaign_brief(proposal)}\n\n"
            + (f"## Guidance from the researcher\n\n{guidance}\n\n" if guidance else "")
            + "## Instructions\n\n"
            "1. Look at the working directory and the environment to see what is available.\n"
            "2. Break the work into a small number of concrete jobs (typically 1-6). Each job is done "
            "by one research agent session of up to "
            f"{self._config.agent.research_timeout_minutes:g} minutes, in dependency order.\n"
            f"3. Write the plan as JSON to {plan_path}:\n\n"
            "```json\n"
            f'{{"plan_id": "plan-1", "proposal_id": "{proposal_id}", "jobs": [\n'
            '  {"job_id": "short-id", "skill_name": "one-line title of the job",\n'
            '   "parameters": {"description": "what to do, how, and how to check it"},\n'
            '   "expected_outputs": ["files or results the job must produce"],\n'
            '   "dependencies": ["job_ids that must finish first"]}\n'
            "]}\n```"
        )
        result = await self._run_agent_session(
            "planning", self._config.prompts.planning, user_msg,
            max_turns=self._config.agent.phase_max_turns,
        )
        self._last_phase_session_id = result.get("session_id")
        for attempt in range(2):
            try:
                plan = ExperimentPlan.model_validate_json(plan_path.read_text())
                if plan.jobs:
                    return plan
                raise ValueError("the plan has no jobs")
            except Exception as exc:
                if attempt == 1 or not self._last_phase_session_id:
                    logger.warning("Planning produced no valid plan (%s); using one job for the whole proposal", exc)
                    await self._publish("planning_fallback", reason=str(exc))
                    return self._single_job_plan(proposal)
                await self._run_agent_session(
                    "planning", "", f"{plan_path} is missing or invalid ({exc}). Write it now in the "
                    "required format.", max_turns=5, resume_session_id=self._last_phase_session_id)
        raise AssertionError("unreachable")

    async def _verify_plan(
        self,
        plan: ExperimentPlan,
        planner: ExperimentPlanner,
        proposal: RoundtableProposal,
        max_retries: int = 2,
    ) -> ExperimentPlan:
        """Run preflight checks on a plan; re-plan on failure."""
        try:
            from aurora.planning.preflight import PreflightVerifier
        except ImportError:
            return plan

        skill_reg = self._registry.get_optional(SkillRegistry)
        verifier = PreflightVerifier(skill_registry=skill_reg or SkillRegistry())

        for attempt in range(max_retries + 1):
            results = await verifier.verify(plan)
            failed = [r for r in results if not r.passed]

            if not failed:
                if attempt > 0:
                    logger.info("Plan passed preflight after %d revision(s)", attempt)
                return plan

            diagnostics = []
            for r in failed:
                diagnostics.append(f"Job {r.job_id}: {'; '.join(r.diagnostics)}")
            diag_str = "\n".join(diagnostics)

            if attempt < max_retries:
                logger.warning(
                    "Preflight failed (%d/%d issues) — asking planner to revise:\n%s",
                    len(failed), len(results), diag_str,
                )
                await self._publish(
                    "preflight_failed",
                    attempt=attempt + 1,
                    failures=len(failed),
                )
                plan = await planner.plan(proposal)
            else:
                logger.warning(
                    "Preflight still failing after %d retries — proceeding anyway:\n%s",
                    max_retries, diag_str,
                )

        return plan

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    async def _run_preflight(self, plan: ExperimentPlan, proposal: RoundtableProposal | None,
                             guidance: str = "") -> tuple[ExperimentPlan, list[str]]:
        """Check every job before research: prerequisites and a smoke run (Section 3.1).

        When jobs have problems, planning revises the plan with the report and the checks run
        again (``agent.preflight_max_revisions`` times).  The findings are attached to the jobs
        (``parameters.preflight``) so the research agents know them.  Returns the plan and the
        problems still open.
        """
        await self._publish("phase_started", phase="preflight")
        if not self._should_use_agent_session():
            # mock mode: the plan's static checks already ran at planning
            await self._publish("preflight_complete", ok=len(plan.jobs), problems=0, untestable=0, revisions=0)
            return plan, []
        revisions = self._config.agent.preflight_max_revisions
        for attempt in range(revisions + 1):
            self._check_budget()
            report = await self._preflight_check(plan, proposal, attempt, guidance)
            jobs = report.get("jobs", [])
            problems = [f"{j.get('job_id')}: {'; '.join(map(str, j.get('problems') or [])) or 'problem'}"
                        for j in jobs if j.get("status") == "problem"]
            await self._publish("preflight_complete", attempt=attempt + 1, revisions=attempt,
                                ok=sum(j.get("status") == "ok" for j in jobs), problems=len(problems),
                                untestable=sum(j.get("status") == "untestable" for j in jobs),
                                summary=str(report.get("summary", ""))[:1000],
                                jobs=[{k: j.get(k) for k in ("job_id", "status", "smoke_run", "estimate", "problems")}
                                      for j in jobs])
            if not problems or attempt == revisions:
                break
            await self._publish("preflight_revision", attempt=attempt + 1, problems=problems)
            plan = await self._run_planning(proposal, (
                "Pre-flight checks of your plan found problems. Revise the plan to fix them; keep what "
                f"passed. The full report is at {self._preflight_report_path(attempt)}.\n\n"
                + "\n".join(f"- {p}" for p in problems)))
            await self._publish("phase_started", phase="preflight")
        plan = self._attach_preflight(plan, report)
        self._workspace.atomic_write_json(self._workspace.experiment_plan_path_for_round(self._current_round), plan)
        return plan, problems

    def _preflight_report_path(self, attempt: int) -> Path:
        return self._workspace.phase_round_dir("preflight", self._current_round) / f"report_{attempt}.json"

    async def _preflight_check(self, plan: ExperimentPlan, proposal: RoundtableProposal | None,
                               attempt: int, guidance: str = "") -> dict[str, Any]:
        """One pre-flight session over the whole plan; its report, with every job accounted for."""
        report_path = self._preflight_report_path(attempt)
        plan_path = self._workspace.experiment_plan_path_for_round(self._current_round)
        sb = self._config.sandbox
        limit = self._config.agent.preflight_timeout_minutes
        user_msg = (
            f"{self._campaign_brief(proposal)}\n\n"
            + (f"## Guidance from the researcher\n\n{guidance}\n\n" if guidance else "")
            + f"## The plan\n\nThe plan to check is {plan_path} ({len(plan.jobs)} jobs). The container has "
            f"{sb.cpus:g} CPUs and {sb.memory_mb / 1024:g} GB of memory; each research job may run for "
            f"{self._config.agent.research_timeout_minutes:g} minutes. You have about {limit:g} minutes for "
            "all checks, so keep each smoke run short.\n\n"
            f"## Report\n\nWrite the report as JSON to {report_path}, and update it after each job, so the "
            "results so far are kept if time runs out:\n\n```json\n"
            '{"jobs": [{"job_id": "...", "status": "ok | problem | untestable",\n'
            '  "checks": ["what you checked, and the result"],\n'
            '  "smoke_run": "what ran, on what input, how long, peak memory, and the outcome",\n'
            '  "estimate": "expected time and memory of the full job",\n'
            '  "problems": ["what would make the full job fail or go wrong"],\n'
            '  "suggested_changes": ["changes to the plan that would fix them"],\n'
            '  "notes_for_research": "what the research agent should know before it starts"}],\n'
            ' "summary": "..."}\n```'
        )
        try:
            await asyncio.wait_for(self._run_agent_session(
                "preflight", self._config.prompts.preflight, user_msg,
                max_turns=max(self._config.agent.phase_max_turns, 15 * len(plan.jobs)),
                session_key=f"preflight:round{self._current_round}:attempt{attempt}"), timeout=limit * 60)
        except TimeoutError:
            logger.warning("Pre-flight checks ran out of time (%g min); using the report so far", limit)
        try:
            report = json.loads(report_path.read_text())
            if not isinstance(report, dict) or not isinstance(report.get("jobs"), list):
                raise ValueError("no jobs list")
        except (OSError, ValueError) as exc:
            logger.warning("No usable pre-flight report (%s)", exc)
            report = {"jobs": [], "summary": f"no usable report: {exc}"}
        seen = {str(j.get("job_id")) for j in report["jobs"] if isinstance(j, dict)}
        report["jobs"] = [j for j in report["jobs"] if isinstance(j, dict)] + [
            {"job_id": job.job_id, "status": "untestable", "problems": [],
             "notes_for_research": "Not checked before the run (pre-flight ended first)."}
            for job in plan.jobs if job.job_id not in seen]
        return report

    @staticmethod
    def _attach_preflight(plan: ExperimentPlan, report: dict[str, Any]) -> ExperimentPlan:
        """Put each job's pre-flight findings into its parameters, for its research agent."""
        by_id = {str(j.get("job_id")): j for j in report.get("jobs", [])}
        jobs = []
        for job in plan.jobs:
            found = by_id.get(job.job_id)
            if found:
                note = {k: found[k] for k in ("status", "smoke_run", "estimate", "problems", "notes_for_research")
                        if found.get(k)}
                job = job.model_copy(update={"parameters": {**job.parameters, "preflight": note}})
            jobs.append(job)
        return plan.model_copy(update={"jobs": jobs})

    async def _run_research_loop(
        self,
        plan: ExperimentPlan,
        completed_jobs: list[str] | None = None,
        *,
        guidance: str = "",
        proposal: RoundtableProposal | None = None,
    ) -> list[IterationResult]:
        """Run the plan's jobs in dependency order: up to ``agent.max_parallel_jobs`` at once, each
        with its own research agent, a new one starting as soon as its dependencies are done."""
        from aurora.core.terminal_iteration import RunningJobs, TerminalIteration
        assert self._router is not None
        await self._publish("phase_started", phase="research")
        dag = DependencyGraph(plan)
        if completed_jobs:
            dag.restore_completed(completed_jobs)
            logger.info("Restored %d completed jobs from the checkpoint", len(completed_jobs))
        max_iters = self._config.agent.max_research_iterations
        max_parallel = max(1, self._config.agent.max_parallel_jobs)
        agents = not self._config.mock_mode and self._execution_backend is not None
        transcript_dir = self._workspace.phase_dir("research") if (agents and self._workspace) else None

        async def _agent_text(text: str) -> None:
            # after a relayed instruction, the agent's next words go back to the human
            if self._frontdesk and self._awaiting_agent_response and text.strip():
                self._awaiting_agent_response = False
                await self._publish("agent_reply_to_human", text=text[:2000])
                await self._frontdesk.notify(f"The research agent replied: {text[:2000]}")

        if agents and (completed_jobs is not None or self._resuming):
            guidance = "\n\n".join(x for x in [guidance, (
                "This campaign was interrupted and resumed. Files from the earlier attempt are already "
                "in the working directory: look at them first and continue from where the work stopped "
                "instead of starting over.")] if x)
        running: dict[asyncio.Task, ExperimentJob] = {}

        def share_of_budget() -> float | None:
            # each running agent may spend its share of what is left, so together they cannot overrun
            left = self._remaining_budget()
            return None if left is None else left / max(1, len(running))

        def new_runner() -> Any:
            if agents:
                self._refresh_skills()
                return TerminalIteration(
                    config=self._config,
                    backend=self._execution_backend,
                    event_bus=self._bus,
                    system_prompt=self._terminal_system_prompt + self._skills_note(),
                    task_instruction="\n\n".join(x for x in [self._terminal_task_instruction, guidance] if x),
                    campaign_brief=self._campaign_brief(proposal),
                    artifact_store=self._store,
                    prior_phase_context=self._build_prior_context_prompt("research"),
                    transcript_dir=transcript_dir,
                    text_callback=_agent_text,
                    cwd=str(self._workspace.root) if self._workspace else None,
                    budget_fn=share_of_budget,
                    sessions=self._sessions,
                    round_n=self._current_round,
                    resuming=self._resuming,
                    agent_extras=self._agent_extras(),
                    compute=self.compute,
                )
            return ResearchIteration(
                config=self._config,
                router=self._router,
                artifact_store=self._store,
                context_builder=self._ctx,
                mental_model_mgr=self._mm,
                event_bus=self._bus,
                skill_registry=self._registry.get_optional(SkillRegistry),
                tool_registry=self._registry.get_optional(ToolRegistry),
                hook_registry=self._hooks,
            )

        if agents and self._config.audit.live and transcript_dir:
            await self._start_supervisor(str(transcript_dir / "research_loop.jsonl"), proposal)
        if not agents:
            logger.info("Mock mode: using the scripted research iteration")
        group = RunningJobs()
        self.current_iteration_runner = group
        self._update_frontdesk_status(phase="research", max_iterations=max_iters, agent_status="running")

        results: list[IterationResult] = []
        started = 0
        last_runner: Any = None
        watchdog = asyncio.create_task(self._watch_budget())

        async def run_job(job: ExperimentJob, runner: Any, n: int, others: list[str]) -> IterationResult:
            kwargs = {"concurrent": others} if others and isinstance(runner, TerminalIteration) else {}
            try:
                return await runner.run(job, plan, n, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Research job %s raised", job.job_id)
                return IterationResult(iteration_id=f"err_{job.job_id}", job_id=job.job_id,
                                       decision="error", error=str(exc))

        try:
            while True:
                self._check_budget()
                # start what is ready, up to the limits
                while len(running) < max_parallel and started < max_iters:
                    ready = dag.ready_jobs()
                    if not ready:
                        break
                    job = ready[0]
                    dag.mark_running(job.job_id)
                    others = [j.job_id for j in running.values()]
                    started += 1
                    runner = last_runner = new_runner()
                    group.add(job.job_id, runner)
                    await self._publish("job_started", job_id=job.job_id, title=job.skill_name,
                                        index=started, total=len(plan.jobs), parallel_with=others)
                    running[asyncio.create_task(run_job(job, runner, started - 1, others))] = job
                if not running:
                    break
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    job = running.pop(task)
                    group.remove(job.job_id)
                    result = task.result()
                    results.append(result)
                    self.cost_usd += (result.job_result.metrics.get("cost_usd", 0.0) if result.job_result else 0.0)
                    self._record_iteration_result(job, result)
                    if agents and transcript_dir:
                        outcome = "succeeded" if result.job_result and result.job_result.success else \
                            f"failed ({result.error or 'see the transcript'})"
                        self._learn(self._learn_from_work(
                            f"research job {job.job_id} ({job.skill_name}), which {outcome}",
                            str(transcript_dir / "research_loop.jsonl"), job=job.job_id, source_phase="research"))
                    if result.job_result and result.job_result.success:
                        dag.mark_complete(job.job_id, result.job_result)
                    else:
                        dag.mark_failed(job.job_id, [result.error or "failed"])
                    self._update_frontdesk_status(iteration=len(results), cost_usd=self.cost_usd)
                    await self._sync_container_files()
                    self._save_checkpoint(CampaignPhase.RESEARCH, self._current_round, plan_id=plan.plan_id,
                                          completed_jobs=dag.completed_job_ids(), iteration=len(results),
                                          current_iteration_id=result.iteration_id)
                if self._stop_requested:
                    raise CampaignStopped(self._stop_requested)
            await self._hooks.fire(HookPoint.PERIODIC, HookContext(
                hook_point=HookPoint.PERIODIC, iteration=len(results),
                data={"state_summary": f"{len(dag.completed_job_ids())} of {len(plan.jobs)} jobs completed",
                      "recent_events": [{"job_id": r.job_id, "decision": r.decision} for r in results[-5:]]},
                metadata={"campaign_id": self._config.campaign_id}))
        finally:
            watchdog.cancel()
            for task in running:            # stopped or failed: end the jobs still running
                task.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)
            self._update_frontdesk_status(agent_status="idle")
            await self._stop_supervisor()
            # a live-auditor question is about research work that is over now
            self.human_gate.close_source("supervisor", "the research phase ended")
            self.current_iteration_runner = None
            if getattr(last_runner, "transcript_path", None):
                self._phase_transcripts.append({"phase": "research", "path": last_runner.transcript_path})
            if getattr(last_runner, "session_id", None):
                self._session_id = last_runner.session_id

        await self._publish("research_loop_complete", iterations=len(results), completed=dag.is_complete())
        return results

    async def _watch_budget(self) -> None:
        """During research, stop the campaign as soon as the time or cost budget is used up."""
        while True:
            await asyncio.sleep(20)
            try:
                self._check_budget()
            except CampaignStopped as exc:
                if not self._stop_requested:
                    logger.warning("Budget reached during research: %s", exc)
                    self.request_stop(str(exc), by_user=False)
            await self._warn_budget()
            self._update_frontdesk_status(cost_usd=self._spent())
            self._record_spent()

    async def _warn_budget(self) -> None:
        """Once, when most of the cost or time budget is used (notifications.budget_warning_at)."""
        if self._budget_warned or not self._started_at:
            return
        import time as _time
        at = self._config.notifications.budget_warning_at
        budget = self._config.budget
        hours = (_time.time() - self._started_at) / 3600
        spent = self._spent()
        shares = [(hours / budget.max_hours, f"{hours:.1f} of {budget.max_hours:g} hours")]
        if budget.max_cost_usd:
            shares.append((spent / budget.max_cost_usd, f"${spent:.2f} of ${budget.max_cost_usd:g}"))
        share, detail = max(shares)
        if share >= at:
            self._budget_warned = True
            await self._publish("budget_warning", share=round(share, 3),
                                detail=f"{detail} used. The experiment stops when the budget runs out; "
                                       f"you can raise it in the console.")

    def _setup_notifier(self) -> None:
        """Notifications from this campaign's events (the web console may have set its own notifier)."""
        if self.notifier is None:
            from aurora.notify import Notifier
            self.notifier = Notifier(lambda: self._config.notifications)
        if not self.notifier.run_name:
            self.notifier.run_name = self.display_name or self._config.objective[:80]
        self._bus.subscribe_all(self.notifier.on_event)

    async def _start_compute(self) -> None:
        """Cluster jobs for the research agents (compute.slurm), if enabled."""
        slurm = self._config.compute.slurm
        if not slurm.enabled or self.compute is not None:
            return
        backend = self._execution_backend
        sb = self._config.sandbox
        if not (sb.docker and sb.mount_workspace and getattr(backend, "container_id", None)):
            logger.warning("compute.slurm needs the Docker container with mount_workspace; cluster jobs are off")
            return
        from aurora.compute.slurm import SlurmCompute
        self.compute = SlurmCompute(slurm, campaign_dir=self._workspace.campaign_dir,
                                    workspace_dir=self._workspace.campaign_dir / "workspace",
                                    image=sb.image, backend=backend, publish=self._publish)
        await self.compute.start()
        logger.info("Cluster jobs on (socket %s)", self.compute.socket_path)

    def _compute_mounts(self) -> list[str]:
        """The job socket's directory and the job client, for agents inside the container."""
        if not (self._config.compute.slurm.enabled and self._config.sandbox.mount_workspace):
            return []
        from aurora.compute.slurm import CLIENT_PATH, CONTAINER_CLIENT, CONTAINER_SOCKET_DIR, socket_dir_for
        sockets = socket_dir_for(self._workspace.campaign_dir)
        sockets.mkdir(parents=True, exist_ok=True)
        return [f"{sockets}:{CONTAINER_SOCKET_DIR}", f"{CLIENT_PATH}:{CONTAINER_CLIENT}:ro"]

    def _agent_extras(self) -> dict[str, Any]:
        if self.compute is None:
            return {}
        inside = bool(getattr(self._execution_backend, "agent_env", None))   # agents in the container
        return {"extra_tools": [] if inside else self.compute.allowed_tools,
                "extra_env": self.compute.agent_env(in_container=inside),
                "extra_prompt": self.compute.agent_prompt(in_container=inside)}

    def _campaign_brief(self, proposal: RoundtableProposal | None) -> str:
        lines = [f"## Research objective\n\n{self._config.objective}"]
        if proposal is not None:
            lines.append(f"## Chosen direction: {proposal.title}")
            if proposal.decision.get("mode") == "researcher":
                lines.append(f"The researcher chose this direction: \"{proposal.decision.get('message', '')}\"")
            for label, value in (("Hypothesis", proposal.hypothesis or proposal.description),
                                 ("Questions", "; ".join(proposal.research_questions)),
                                 ("Method", "; ".join(proposal.proposed_methods)),
                                 ("Expected outcomes", "; ".join(proposal.expected_outcomes)),
                                 ("How it could be falsified", getattr(proposal, "falsification_check", ""))):
                if value:
                    lines.append(f"**{label}:** {value}")
        if self._config.inputs:
            lines.append("Input files are in `inputs/` (in the container: `/app/inputs`).")
        understanding = self._understanding_text()
        if understanding:
            lines.append(understanding)
        return "\n\n".join(lines)

    # ------------------------------------------------------------------
    # Live auditor
    # ------------------------------------------------------------------

    async def _start_supervisor(self, transcript_path: str, proposal: RoundtableProposal | None) -> None:
        from aurora.core.supervisor import Supervisor, SupervisorVerdict

        async def advise(msg: str, job: str = "") -> None:
            if self.current_iteration_runner is not None:
                await self.current_iteration_runner.inject_message(msg, job or None)

        async def interrupt(msg: str, job: str = "") -> None:
            if self.current_iteration_runner is not None:
                self.current_iteration_runner.interrupt(job or None)
                await self.current_iteration_runner.inject_message(msg, job or None)

        async def escalate(verdict: SupervisorVerdict) -> None:
            asyncio.create_task(self._supervisor_gate(verdict))

        async def withdraw(verdict: SupervisorVerdict) -> None:
            self.human_gate.close_source("supervisor", verdict.reason or "no decision needed",
                                         decided_by="auditor")

        async def broadcast(data: dict) -> None:
            name = data.get("event_type", "supervisor_event")
            await self._bus.publish(Event(name=name, data=data.get("data", {}), source="supervisor"))
            self._update_frontdesk_status(supervisor_status=name.replace("supervisor_", ""))

        assert self._workspace is not None
        self._supervisor = Supervisor(
            run_dir=self._workspace.phase_dir("supervisor") / f"round_{self._current_round}",
            task_description=self._campaign_brief(proposal),
            model=self._config.models.supervisor,
            api_key=self._config.api_key_value,
            base_url=self._config.claude_code_base_url,
            cwd=self._workspace.root,
            docker_container=getattr(self._execution_backend, "container_id", None),
            advise_callback=advise,
            interrupt_callback=interrupt,
            escalate_callback=escalate,
            withdraw_callback=withdraw,
            broadcast_callback=broadcast,
        )
        await self._supervisor.start(transcript_path)

    async def _supervisor_gate(self, verdict: Any) -> None:
        """The live auditor escalated: pause the agent(s) concerned where they are and ask the human.

        A paused agent's session waits (its job clock stands still, other messages wait too) until
        the decision, which it then receives and goes on from where it stopped.
        """
        group = self.current_iteration_runner
        job = getattr(verdict, "job", "") or None
        paused = group.pause_for_decision(job) if hasattr(group, "pause_for_decision") else []
        who = f"the research agent of job {job}" if job else "the research agent"
        title = f"The live auditor escalated: {verdict.reason[:120]}"
        decision = await self.human_gate.request(
            "supervisor", title,
            f"{verdict.reason}\n\nSuggestion: {verdict.suggestion}\n\n{who[0].upper() + who[1:]} is paused "
            "until you decide. continue: let it go on; instruct: send it your instruction; stop: end the campaign.")
        if decision.action == "stop" and decision.decided_by != "auditor":
            if decision.decided_by != "system":     # system: the campaign is ending anyway
                self.request_stop(f"stopped after a live-auditor escalation: {decision.message or 'human decision'}",
                                  by_user=decision.decided_by in ("human", "frontdesk"))
            return
        if not paused or group is not self.current_iteration_runner:
            return
        why = f"You were paused because the live auditor asked the researcher: {verdict.reason}"
        if decision.decided_by == "auditor":
            text = f"[Resumed] {why}\nThe auditor withdrew the question: {decision.message}\nContinue where you stopped."
        elif decision.message.strip() and decision.decided_by != "system":
            text = f"[Decision of the researcher] {why}\n\n{decision.message}\n\nContinue where you stopped."
        else:
            text = f"[Decision of the researcher] {why}\nContinue where you stopped."
        await group.resume_after_decision(text, paused)

    async def _stop_supervisor(self) -> None:
        if self._supervisor is not None:
            await self._supervisor.stop()
            self.cost_usd += self._supervisor.cost_usd
            self._supervisor = None

    # ------------------------------------------------------------------
    # Front desk
    # ------------------------------------------------------------------

    def _update_frontdesk_status(self, **kwargs: Any) -> None:
        if self._frontdesk is not None:
            for key, value in kwargs.items():
                if hasattr(self._frontdesk_status, key):
                    setattr(self._frontdesk_status, key, value)

    async def _start_frontdesk(self) -> None:
        from aurora.human.frontdesk import CAMPAIGN_PROMPT, FrontDeskAgent, SystemStatus, campaign_tools

        async def relay(message: str, job: str = "") -> dict[str, Any]:
            if not message:
                return {"status": "error", "reason": "empty message"}
            runner = self.current_iteration_runner
            if runner is None:
                # no research agent yet: the instruction goes to planning and research
                self._queued_instructions.append(message)
                await self._publish("instruction_queued", message=message, phase=self._phase.value)
                return {"status": "queued",
                        "reason": f"no research agent runs in the {self._phase.value} phase; the instruction "
                                  "will be given to the planning and research agents"}
            self._awaiting_agent_response = True
            await runner.inject_message(
                f"[Instruction from the researcher]\n{message}\n\nAcknowledge it in one or two "
                "sentences, then adjust your work accordingly.", job or None)
            await self._publish("instruction_relayed", message=message, job_id=job)
            return {"status": "delivered", "to": job or "all running jobs"}

        async def interrupt(reason: str, job: str = "") -> dict[str, Any]:
            if self.current_iteration_runner is None:
                return {"status": "not_running"}
            self.current_iteration_runner.interrupt(job or None)
            await self._publish("agent_interrupted", reason=reason, by="human", job_id=job)
            return {"status": "interrupted", "job": job or "all running jobs"}

        async def answer_gate(gate_id: str, action: str, message: str,
                              rerun: list[str] | None = None) -> dict[str, Any]:
            try:
                who = self.may_decide() if self.may_decide is not None else (True, "")
                if not who[0]:
                    return {"ok": False, "reason": "The person writing cannot answer decisions in this project; "
                                                   "tell them who can (the run's starter or the project's owners)."}
                ok = self.human_gate.resolve(gate_id, action, message, decided_by="frontdesk", rerun=rerun, by=who[1])
            except ValueError as exc:
                return {"status": "error", "reason": str(exc)}
            return {"status": "recorded" if ok else "no such open gate"}

        async def set_resources(cpus: float | None, memory_mb: int | None) -> dict[str, Any]:
            from aurora.core.docker_backend import host_limits
            max_cpus, max_mem = await host_limits()
            if (cpus is not None and not 0 < cpus <= max_cpus) or (
                    memory_mb is not None and not 256 <= memory_mb <= (max_mem or memory_mb)):
                return {"status": "error", "reason": f"the Docker host has {max_cpus} CPUs and "
                                                     f"{max_mem / 1024:.0f} GB of memory"}
            try:
                return {"status": "updated", **await self.update_resources(cpus, memory_mb)}
            except RuntimeError as exc:
                return {"status": "error", "reason": str(exc)}

        def status() -> str:
            st = self._frontdesk_status
            st.open_gates = [g.to_dict() for g in self.human_gate.pending]
            sb = self._config.sandbox
            st.container = f"{sb.cpus:g} CPUs, {sb.memory_mb / 1024:g} GB memory" if sb.docker else ""
            st.cost_usd = self._spent()
            return st.format()

        async def broadcast(data: dict[str, Any]) -> None:
            await self._bus.publish(Event(name=data.get("type", "frontdesk_event"),
                                          data=data.get("data", {}), source="frontdesk"))

        async def on_event(event: Event) -> None:
            st = self._frontdesk_status
            d = event.data
            if event.name == "phase_started":
                st.phase = d.get("phase", st.phase)
                st.activity = {
                    "ideation": f"ideation ({d.get('mode', '')}): the seat models prepare research directions; "
                                "no research agent runs yet",
                    "screening": "the screening agent scores the proposal",
                    "planning": "the planning agent writes the plan of jobs",
                    "preflight": "the pre-flight agent checks each job's prerequisites and smoke-runs it",
                    "research": "the research agent works on the jobs in the container",
                    "analysis": "the analysis agent checks the results",
                    "writing": "the writing agent writes the report",
                }.get(st.phase, st.phase)
            elif event.name == "ideation_progress":
                step = d.get("step")
                text = {
                    "preparing": f"{len(d.get('seats', []))} seats prepare their ideas",
                    "prepared": "ideas ready: " + "; ".join(
                        f"{i['seat']} ({i['model']}): {i['headline'][:90]}" for i in d.get("ideas", [])),
                    "agenda_item": f"meeting item {d.get('item')}/{d.get('of')}: {d.get('presenter')} presents, "
                                   "the others discuss",
                    "item_notes": f"chair's notes on {d.get('presenter')}: {str(d.get('summary', ''))[:200]}",
                    "open_discussion": "open discussion",
                    "chair_selecting": "the chair compares the ideas",
                    "chair_deciding": "the chair decides the direction",
                    "decided": f"chosen: {d.get('chosen')} — {str(d.get('headline', ''))[:200]}",
                }.get(step, str(step))
                st.ideation_log = (st.ideation_log + [text])[-20:]
                st.activity = f"ideation: {text[:200]}"
            elif event.name == "ideation_turn" and d.get("content"):
                what = {"presentation": "presented", "remarks": f"remarked on {d.get('item')}'s idea",
                        "response": "answered the remarks", "open": "spoke in the open discussion"}
                gist = d.get("headline") or " ".join(str(d.get("content", "")).split())[:200]
                st.ideation_log = (st.ideation_log + [
                    f"{d.get('seat')} ({d.get('model')}) {what.get(d.get('kind'), d.get('kind'))}: {gist}"])[-20:]
            elif event.name == "milestone_audit_started":
                st.activity = (f"the milestone auditor reviews the {d.get('completed_phase')} output "
                               f"before {d.get('next_phase')}")
            elif event.name == "milestone_audit":
                st.activity = f"milestone audit {d.get('completed_phase')} → {d.get('next_phase')}: {d.get('verdict')}"
            elif event.name == "human_gate_opened":
                st.activity = f"waiting for the researcher's decision: {d.get('title')}"
            elif event.name == "waiting_for_cluster_jobs":
                st.activity = (f"research job {d.get('job_id')} waits for its cluster jobs "
                               + ", ".join(f"{j['id']} ({j['name']})" for j in d.get("cluster_jobs", [])))
            elif event.name in ("compute_job_submitted", "compute_job_ended"):
                st.activity = f"cluster job {d.get('id')} ({d.get('name')}): {str(d.get('state', '')).lower()}"
            elif event.name == "job_started":
                st.running_jobs = {**st.running_jobs, str(event.data.get("job_id")):
                                   f"{event.data.get('index')}/{event.data.get('total')} {event.data.get('title', '')}"}
                st.current_job = "; ".join(f"{k}: {v}" for k, v in st.running_jobs.items())
            elif event.name == "iteration_complete":
                st.running_jobs = {k: v for k, v in st.running_jobs.items() if k != str(event.data.get("job_id"))}
                st.current_job = "; ".join(f"{k}: {v}" for k, v in st.running_jobs.items())
            elif event.name == "tool_call_start" and event.source == "terminal_iteration":
                import time as _time
                st.recent_actions = (st.recent_actions + [
                    f"{_time.strftime('%H:%M:%S')} {event.data.get('tool_name')}: "
                    f"{str(event.data.get('input_preview', ''))[:160]}"])[-20:]
            elif event.name == "reasoning" and event.source == "terminal_iteration":
                st.recent_actions = (st.recent_actions + [f"(thinking) {event.data.get('text', '')[:160]}"])[-20:]
            if event.name == "human_gate_opened" and self._frontdesk is not None:
                asyncio.create_task(self._frontdesk.notify(
                    f"Human gate {event.data.get('gate_id')} opened ({event.data.get('source')}): "
                    f"{event.data.get('title')}\n{event.data.get('detail', '')}", explain=True))

        self._frontdesk_status = SystemStatus(budget_usd=self._config.budget.max_cost_usd)
        self._frontdesk = FrontDeskAgent(
            model=self._config.models.frontdesk,
            api_key=self._config.api_key_value,
            base_url=self._config.api_base_url,
            system_prompt=CAMPAIGN_PROMPT + f"\nCampaign objective: {self._config.objective}\n",
            workspace_root=self._workspace.root if self._workspace else None,
            tools=campaign_tools(relay=relay, interrupt=interrupt, answer_gate=answer_gate,
                                 set_resources=set_resources if self._config.sandbox.docker else None)
            + list(self.extra_frontdesk_tools),
            status_fn=status,
            broadcast_fn=broadcast,
        )
        if self.prior_chat:
            self._frontdesk.restore(self.prior_chat)
        self._frontdesk_listener = on_event
        self._bus.subscribe_all(on_event)
        logger.info("Front desk ready (model=%s)", self._config.models.frontdesk)

    async def set_frontdesk(self, enabled: bool, history: list[dict[str, Any]] | None = None) -> None:
        """Switch the front desk of the running campaign on or off (off: no model calls; messages go
        straight to the research agent)."""
        self._config.human.frontdesk = enabled
        if not enabled:
            await self._stop_frontdesk()
        elif self._frontdesk is None and not self._config.mock_mode and not self._finishing:
            if history is not None:
                self.prior_chat = history
            await self._start_frontdesk()

    async def _stop_frontdesk(self) -> None:
        if self._frontdesk_listener is not None:
            self._bus.unsubscribe("*", self._frontdesk_listener)
            self._frontdesk_listener = None
        if self._frontdesk is not None:
            await self._frontdesk.close()
            self._frontdesk = None

    async def _run_analysis(self) -> CampaignPhase | None:
        """Run the analyzer agent on accumulated results.

        Returns the phase to loop back to if more work is needed,
        or None if results are sufficient (proceed to writing).
        """
        await self._publish("phase_started", phase="analysis")

        if self._should_use_agent_session():
            loop_back_to = await self._run_analysis_agent()
        else:
            loop_back_to = await self._run_analysis_api()

        await self._publish(
            "analysis_complete",
            loop_back_to=loop_back_to.value if loop_back_to else None,
        )
        return loop_back_to

    async def _run_analysis_api(self) -> CampaignPhase | None:
        """Run analysis via single-shot API call (fallback)."""
        assert self._router is not None

        artifacts = self._store.load() if self._store else None
        ctx = self._build_agent_context(artifacts)

        analyzer = AnalyzerAgent(
            self._router,
            custom_system_prompt=self._config.prompts.analysis,
        )
        actions = await analyzer.run(ctx)

        loop_back_to: CampaignPhase | None = None

        for action in actions:
            if action.action_type == AgentActionType.RECORD_OBSERVATION:
                logger.info(
                    "Analysis observation: %s",
                    action.payload.get("content", "")[:200]
                    if isinstance(action.payload.get("content"), str)
                    else str(action.payload)[:200],
                )
            elif action.action_type == AgentActionType.TRIGGER_FOLLOWUP:
                target = action.payload.get("target_phase", "research")
                target = {"research_loop": "research", "roundtable": "ideation"}.get(target, target)
                try:
                    loop_back_to = CampaignPhase(target)
                except ValueError:
                    loop_back_to = CampaignPhase.RESEARCH
                logger.info(
                    "Analysis requests loop-back to %s: %s",
                    loop_back_to.value,
                    action.payload.get("reason", action.payload.get("experiments", [])),
                )
            elif action.action_type == AgentActionType.LOG_MESSAGE:
                level = action.payload.get("level", "info")
                msg = action.payload.get("message", "")
                getattr(logger, level, logger.info)(
                    "Analyzer: %s", msg[:300]
                )

        return loop_back_to

    async def _run_analysis_agent(self) -> CampaignPhase | None:
        """Analysis as a Claude Code session: verdict, findings and the updated mental model."""
        assert self._workspace is not None
        verdict_path = self._workspace.analysis_verdict_path(self._current_round)
        mm_path = self._workspace.mental_model_path
        understanding = self._understanding_text() or "## Current understanding\n(none recorded yet)"
        user_msg = (
            f"## Research objective\n{self._config.objective}\n\n"
            f"{self._results_summary()}\n\n"
            f"{understanding}\n\n"
            "## Instructions\n"
            "Analyse the results: read the job outputs (in the container under /app, and the research "
            "transcript), check them, and run your own analyses where needed. Then decide whether they "
            "answer the objective well enough to be written up, and update the campaign's scientific "
            f"mental model (the previous one, if any, is {mm_path}).\n\n"
            f"Write your verdict as JSON to {verdict_path}:\n"
            "```json\n"
            '{"sufficient": true, "target_phase": "research|planning|ideation", "reason": "...",\n'
            ' "findings": [{"statement": "...", "status": "supported|tentative|refuted",\n'
            '               "evidence": ["file paths, job ids or numbers that back it"], "confidence": 0.0}],\n'
            ' "open_questions": ["..."],\n'
            ' "mental_model": {\n'
            '   "active_hypotheses": [{"hypothesis_id": "h1", "statement": "...",\n'
            '      "status": "active|competing|confirmed|refuted", "confidence": 0.0,\n'
            '      "supporting_evidence": ["..."], "contradicting_evidence": ["..."]}],\n'
            '   "inferred_mechanisms": ["..."], "unresolved_ambiguity": ["..."],\n'
            '   "current_rationale": "what we now believe and why, in a few sentences"}}\n'
            "```\n"
            "List ALL current findings and hypotheses (this replaces the previous lists), keeping "
            "refuted ones with status refuted so the record shows what was overturned. "
            'Set "sufficient": false and "target_phase" to the phase that should run again when more '
            "work is needed: research (more or corrected experiments), planning (a different plan) or "
            "ideation (a different direction)."
        )
        result = await self._run_agent_session(
            "analysis", self._config.prompts.analysis, user_msg,
            max_turns=self._config.agent.phase_max_turns,
        )
        self._last_phase_session_id = result.get("session_id")
        try:
            verdict = json.loads(verdict_path.read_text())
        except Exception as exc:
            logger.warning("Analysis wrote no valid verdict (%s); proceeding to writing", exc)
            await self._publish("analysis_verdict_missing", reason=str(exc))
            return None
        await self._apply_analysis(verdict)
        if verdict.get("sufficient", True):
            return None
        target = {"research_loop": "research", "roundtable": "ideation"}.get(
            verdict.get("target_phase", "research"), verdict.get("target_phase", "research"))
        try:
            return CampaignPhase(target)
        except ValueError:
            return CampaignPhase.RESEARCH

    async def _apply_analysis(self, verdict: dict[str, Any]) -> None:
        """Record the analysis: findings as claims, open questions, and the new mental model."""
        from aurora.models.claims import ScientificClaim
        from aurora.models.mental_model import Hypothesis, ScientificMentalModel

        status_map = {"supported": "accepted", "tentative": "tentative", "refuted": "rejected"}
        if self._store is not None and isinstance(verdict.get("findings"), list):
            artifacts = self._store.load().model_copy(deep=True)
            artifacts.claims = []
            for i, f in enumerate(verdict["findings"]):
                if isinstance(f, str):
                    f = {"statement": f}
                if not isinstance(f, dict) or not f.get("statement"):
                    continue
                try:
                    claim = ScientificClaim(
                        claim_id=f"claim_r{self._current_round}_{i + 1}", statement=str(f["statement"]),
                        status=status_map.get(str(f.get("status", "tentative")), "tentative"),
                        supporting_evidence=[str(e) for e in f.get("evidence", [])],
                        confidence=min(max(float(f.get("confidence", 0.5) or 0.5), 0.0), 1.0))
                except Exception as exc:
                    logger.warning("Skipping malformed finding %s: %s", f, exc)
                    continue
                artifacts = self._store.add_claim(artifacts, claim)
            if isinstance(verdict.get("open_questions"), list):
                artifacts = artifacts.model_copy(update={
                    "open_questions": [str(q) for q in verdict["open_questions"]]})
                self._store.save(artifacts)
        mm = verdict.get("mental_model")
        if isinstance(mm, dict) and self._mm is not None:
            try:
                hyps = [Hypothesis(**h) for h in mm.get("active_hypotheses", []) if isinstance(h, dict)]
                model = ScientificMentalModel(
                    active_hypotheses=[h for h in hyps if h.status != "competing"],
                    competing_hypotheses=[h for h in hyps if h.status == "competing"],
                    inferred_mechanisms=[str(x) for x in mm.get("inferred_mechanisms", [])],
                    unresolved_ambiguity=[str(x) for x in mm.get("unresolved_ambiguity", [])],
                    current_rationale=str(mm.get("current_rationale", "")),
                    reconstructed_from_evidence_at=datetime.now(UTC),
                )
                self._mm.save(model)
                await self._publish("mental_model_updated", round=self._current_round,
                                    hypotheses=[{"statement": h.statement, "status": h.status,
                                                 "confidence": h.confidence} for h in hyps],
                                    rationale=model.current_rationale)
            except Exception as exc:
                logger.warning("The analysis returned an invalid mental model (%s); keeping the previous one", exc)
                await self._publish("mental_model_invalid", reason=str(exc))
        else:
            logger.warning("The analysis did not update the mental model")

    def _results_summary(self) -> str:
        """What the research jobs reported, for analysis and writing."""
        artifacts = self._store.load() if self._store else None
        if artifacts is None or not artifacts.observations:
            return "## Research jobs\n(no results recorded)"
        lines = ["## Research jobs and what they reported"]
        for o in artifacts.observations[-20:]:
            lines.append(f"### {o.source_job_id} ({o.observation_id})\n{o.description[:3000]}")
        return "\n\n".join(lines)

    def _understanding_text(self) -> str:
        """The campaign's current understanding: mental model, findings and open questions."""
        parts: list[str] = []
        model = self._mm.load() if self._mm else None
        if model is not None:
            if model.current_rationale:
                parts.append(f"**Current rationale:** {model.current_rationale}")
            hyps = model.active_hypotheses + model.competing_hypotheses
            if hyps:
                parts.append("**Hypotheses:**\n" + "\n".join(
                    f"- [{h.status}, confidence {h.confidence:.2f}] {h.statement}"
                    + (f" (evidence: {', '.join(h.supporting_evidence[:4])})" if h.supporting_evidence else "")
                    for h in hyps))
            if model.inferred_mechanisms:
                parts.append("**Mechanisms:**\n" + "\n".join(f"- {m}" for m in model.inferred_mechanisms))
            if model.unresolved_ambiguity:
                parts.append("**Unresolved:**\n" + "\n".join(f"- {u}" for u in model.unresolved_ambiguity))
        artifacts = self._store.load() if self._store else None
        if artifacts is not None:
            if artifacts.claims:
                parts.append("**Findings so far:**\n" + "\n".join(
                    f"- [{c.status}] {c.statement}" for c in artifacts.claims[:30]))
            if artifacts.open_questions:
                parts.append("**Open questions:**\n" + "\n".join(f"- {q}" for q in artifacts.open_questions[:20]))
        return ("## Current understanding (from earlier rounds)\n" + "\n".join(parts)) if parts else ""

    async def _run_writing(self) -> str | None:
        """Run the writer agent to produce the final manuscript."""
        assert self._workspace is not None
        await self._publish("phase_started", phase="writing")

        if self._should_use_agent_session():
            ms_path = await self._run_writing_agent()
        else:
            ms_path = await self._run_writing_api()

        versions = self._list_manuscript_versions()
        version = max(versions, default=0) + 1
        if ms_path is None:
            ms_path = str(self._workspace.manuscript_path(version))
        elif Path(ms_path).exists():
            await self._reference_list(Path(ms_path))

        await self._publish(
            "writing_complete",
            version=version,
            path=ms_path,
        )
        return ms_path

    def _papers_brief(self) -> str:
        """The real papers this run cited so far, for the writer (empty when literature is off)."""
        if self.literature is None:
            return ""
        entries = self.literature.bibliography(self._config.campaign_id)
        if not entries:
            return ""
        from aurora.literature.service import paper_card
        return ("## Literature\n\nThese papers were cited in this run and are verified (found in the scholarly "
                "indexes). Cite them as [Key]. Do not cite other papers unless you are sure they exist: every "
                "reference in the report is checked, and the reference list is added for you from the keys you "
                "cite (do not write your own).\n\n" + "\n".join(paper_card(e, abstract_words=40) for e in entries))

    async def _reference_list(self, ms_path: Path) -> None:
        """Check the report's references and append its reference list (and references.bib)."""
        text = ms_path.read_text(errors="replace")
        cites = await self._cite(text, by="Writing", place="Report")
        if cites is None or not cites.verified:
            return
        from aurora.literature.bibtex import to_bibtex
        from aurora.literature.library import library
        entries = [e for e in (library.paper(k) for k in cites.verified) if e]
        lines = [f"- **[{e['key']}]** {e['paper'].byline()}. {e['paper'].title.rstrip('.')}."
                 + (f" https://doi.org/{e['paper'].doi}" if e["paper"].doi
                    else f" arXiv:{e['paper'].arxiv_id}" if e["paper"].arxiv_id else "") for e in entries]
        if cites.flagged or cites.unknown_keys:
            lines.append("\n*References that could not be found in any index are not listed; see the project's "
                         "Literature page.*")
        self._workspace.atomic_write_text(ms_path, text.rstrip() + "\n\n## References\n\n" + "\n".join(lines) + "\n")
        self._workspace.atomic_write_text(ms_path.parent / "references.bib", to_bibtex(entries))

    async def _run_writing_api(self) -> str | None:
        """Run writing via single-shot API call (fallback)."""
        assert self._router is not None
        assert self._workspace is not None

        artifacts = self._store.load() if self._store else None
        ctx = self._build_agent_context(artifacts)

        writer = WriterAgent(
            self._router,
            custom_system_prompt=self._config.prompts.writing,
        )
        actions = await writer.run(ctx)

        manuscript_content = ""
        for action in actions:
            if action.action_type == AgentActionType.WRITE_MANUSCRIPT:
                section = action.payload.get("section_name", "")
                content = action.payload.get("content", "")
                manuscript_content += f"\n\n## {section}\n\n{content}"
            elif action.action_type == AgentActionType.WRITE_ARTIFACT:
                title = action.payload.get("title", "")
                logger.info("Manuscript plan: %s", title)

        versions = self._list_manuscript_versions()
        version = max(versions, default=0) + 1
        ms_path = self._workspace.manuscript_path(version)
        self._workspace.atomic_write_text(ms_path, manuscript_content)
        return str(ms_path)

    async def _run_writing_agent(self) -> str | None:
        """Run writing via Claude Code agent session."""
        assert self._workspace is not None

        versions = self._list_manuscript_versions()
        version = max(versions, default=0) + 1
        ms_path = self._workspace.manuscript_path(version)
        user_msg = (
            f"## Research objective\n{self._config.objective}\n\n"
            f"{self._results_summary()}\n\n"
            f"{self._understanding_text()}\n\n"
            + (f"{self._papers_brief()}\n\n" if self._papers_brief() else "")
            + "## Instructions\n"
            "Write the report of this campaign. Read the results, figures and data (in the container "
            "under /app, and in the campaign directory) and the analysis verdicts. Report what was found "
            "with its evidence and uncertainty; include refuted hypotheses and open questions; do not "
            "claim more than the evidence supports.\n\n"
            f"Save the report as Markdown to: {ms_path}\n"
            "Copy the figures you use next to it and link them with relative paths."
        )

        result = await self._run_agent_session(
            "writing",
            self._config.prompts.writing,
            user_msg,
            max_turns=self._config.agent.phase_max_turns,
        )
        self._last_phase_session_id = result.get("session_id")

        if Path(ms_path).exists():
            return str(ms_path)
        logger.warning("The writing agent did not save a report at %s", ms_path)
        await self._publish("report_missing", path=str(ms_path))
        return None

    # ==================================================================
    # Artifact recording
    # ==================================================================

    def _record_iteration_result(
        self, job: ExperimentJob, result: IterationResult
    ) -> None:
        """Write an iteration result into the ArtifactStore.

        This bridges the gap between iteration runners (including
        TerminalIteration) and downstream phases like Analysis that
        read from ArtifactStore.
        """
        if self._store is None:
            return

        from aurora.models.artifacts import ExperimentRecord

        status = "completed" if (result.job_result and result.job_result.success) else "failed"
        record = ExperimentRecord(
            job_id=job.job_id,
            skill_used=job.skill_name,
            parameters=job.parameters,
            status=status,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            failure_diagnosis=(
                result.error if status == "failed" else None
            ),
        )

        try:
            artifacts = self._store.load() if self._store else None
        except Exception:
            logger.warning(
                "artifacts.json is corrupted (agent may have modified it); "
                "reinitialising from scratch"
            )
            from aurora.models.artifacts import ResearchArtifacts
            artifacts = ResearchArtifacts(
                initial_objective=self._config.objective,
            )
            self._store.save(artifacts)

        if status == "failed" and result.error:
            self._store.record_failure(artifacts, record, result.error)
        else:
            self._store.record_experiment(artifacts, record)

        if result.job_result:
            from aurora.models.artifacts import Observation
            obs = Observation(
                observation_id=f"obs_{result.iteration_id}",
                source_job_id=job.job_id,
                description=(
                    result.job_result.summary
                    or f"Job {job.job_id} ({job.skill_name}): "
                       f"{'done' if result.job_result.success else 'failed'} in "
                       f"{result.wall_time_seconds / 60:.1f} min"
                       + (f"; errors: {'; '.join(result.job_result.errors)}" if result.job_result.errors else "")
                ),
                numerical_values={
                    k: float(v) for k, v in result.job_result.metrics.items()
                    if isinstance(v, (int, float))
                },
                timestamp=datetime.now(UTC),
            )
            try:
                artifacts = self._store.load() if self._store else None
            except Exception:
                pass
            self._store.record_observation(artifacts, obs)

    # ==================================================================
    # Context building
    # ==================================================================

    def _build_agent_context(self, artifacts: ResearchArtifacts | None) -> AgentContext:
        """Build an AgentContext from the current artifacts."""
        if artifacts is not None:
            experiments = [
                {
                    "experiment_id": e.job_id,
                    "description": e.skill_used,
                    "result_summary": e.result_path or "",
                }
                for e in artifacts.experiments[-10:]
            ]
            observations = [
                {"content": o.description}
                for o in artifacts.observations[-10:]
            ]
            claims = [
                {"status": c.status, "statement": c.statement}
                for c in artifacts.claims
            ]
            goal = artifacts.current_goal
            summary = f"Objective: {artifacts.initial_objective}"
        else:
            experiments = []
            observations = []
            claims = []
            goal = self._config.objective
            summary = f"Objective: {self._config.objective}"

        skill_reg = self._registry.get_optional(SkillRegistry)
        available_skills = skill_reg.list_skills() if skill_reg else []

        return AgentContext(
            campaign_id=self._config.campaign_id,
            current_goal=goal,
            research_summary=summary,
            recent_experiments=experiments,
            recent_observations=observations,
            active_claims=claims,
            available_skills=available_skills,
            mental_model_summary=self._understanding_text(),
            # real runs: Claude Code brings its own tools, so none are listed here
            available_tools=(
                tool_reg.list_tools()
                if (tool_reg := self._registry.get_optional(ToolRegistry))
                else []
            ),
        )

    # ==================================================================
    # Checkpoint I/O
    # ==================================================================

    def _save_checkpoint(
        self,
        phase: CampaignPhase,
        research_cycle: int = 0,
        proposal_id: str | None = None,
        plan_id: str | None = None,
        completed_jobs: list[str] | None = None,
        iteration: int = 0,
        current_iteration_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._workspace is None:
            return
        self._record_spent()
        cp = Checkpoint(
            metadata=metadata or {},
            campaign_id=self._config.campaign_id,
            phase=phase.value,
            iteration=iteration,
            research_cycle=research_cycle,
            proposal_id=proposal_id,
            plan_id=plan_id,
            completed_jobs=completed_jobs or [],
            current_iteration_id=current_iteration_id,
            timestamp=datetime.now(UTC),
        )
        self._workspace.atomic_write_json(
            self._workspace.campaign_dir / "checkpoint.json", cp
        )

    def _load_checkpoint(self) -> Checkpoint | None:
        if self._workspace is None:
            return None
        path = self._workspace.campaign_dir / "checkpoint.json"
        if not path.exists():
            return None
        return Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_latest_proposal(self) -> RoundtableProposal | None:
        if self._workspace is None:
            return None
        # Search from the current round backward for the latest proposal
        for rn in range(self._current_round, -1, -1):
            rd = self._workspace.phases_dir / "ideation" / f"round_{rn}"
            if not rd.exists():
                continue
            # several ideation runs in one round (screening rejected the first) leave several
            # proposals; the newest one counts
            jsons = sorted((p for p in rd.glob("*.json") if not p.name.startswith("meeting_state")),
                           key=lambda p: p.stat().st_mtime)
            if jsons:
                return RoundtableProposal.model_validate_json(
                    jsons[-1].read_text(encoding="utf-8")
                )
        return None

    def _load_plan(self) -> ExperimentPlan | None:
        if self._workspace is None:
            return None
        path = self._workspace.experiment_plan_path
        if not path.exists():
            return None
        return ExperimentPlan.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def _list_manuscript_versions(self) -> list[int]:
        """Return sorted list of existing manuscript version numbers."""
        if self._workspace is None:
            return []
        ms_dir = self._workspace.writing_dir
        if not ms_dir.exists():
            return []
        versions = []
        for p in ms_dir.glob("draft_*.md"):
            try:
                v = int(p.stem.split("_")[1])
                versions.append(v)
            except (IndexError, ValueError):
                pass
        return sorted(versions)

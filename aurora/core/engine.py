"""Research engine — orchestrates the full ideation-to-paper pipeline.

The :class:`ResearchEngine` implements the top-level flow:

    roundtable  →  screening  →  planning  →  research loop  →  analysis
        ↺ (analysis may loop back to research if more data is needed)
        →  writing  →  completed

The engine is the **only** component that knows the full pipeline shape.
Each sub-component (roundtable, screener, planner, researcher, auditor,
analyzer, writer) operates through well-defined interfaces and never
calls the engine back.

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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from aurora.agents.analyzer import AnalyzerAgent
from aurora.agents.auditor import AuditorAgent
from aurora.agents.base import AgentActionType, AgentContext
from aurora.agents.researcher import ResearcherAgent
from aurora.agents.roundtable.roundtable import Roundtable
from aurora.agents.writer import WriterAgent
from aurora.config import AuroraConfig, PipelineStage
from aurora.core.events import Event, EventBus
from aurora.core.lifecycle import (
    AgenticResearchIteration,
    DuplexResearchIteration,
    IterationDecision,
    IterationResult,
    ResearchIteration,
)
from aurora.core.registry import ComponentRegistry
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter, create_router
from aurora.models.artifacts import ResearchArtifacts
from aurora.models.experiment import ExperimentJob, ExperimentPlan, JobResult
from aurora.models.roundtable import IdeaScore, RoundtableProposal
from aurora.planning.dependency_graph import DependencyGraph
from aurora.planning.planner import ExperimentPlanner
from aurora.screening.screener import IdeaScreener
from aurora.skills.registry import SkillRegistry
from aurora.state.artifacts import ArtifactStore
from aurora.state.context import ContextReconstructor
from aurora.state.mental_model import MentalModelManager
from aurora.state.workspace import WorkspaceManager
from aurora.hooks.base import HookContext, HookPoint, HookRegistry, HookResult
from aurora.human.checkpoint import CheckpointDecision, CheckpointRequest
from aurora.human.web_checkpoint import WebCheckpointHandler
from aurora.core.agent_session import AgentSession
from aurora.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase enum & checkpoint
# ---------------------------------------------------------------------------


class CampaignPhase(str, Enum):
    """High-level phases of a research campaign.

    Pipeline: ROUNDTABLE → SCREENING → PLANNING → RESEARCH_LOOP → ANALYSIS → WRITING
    Analysis decides whether to loop back to RESEARCH_LOOP or proceed to WRITING.
    """

    INITIALIZING = "initializing"
    ROUNDTABLE = "roundtable"
    SCREENING = "screening"
    PLANNING = "planning"
    RESEARCH_LOOP = "research_loop"
    ANALYSIS = "analysis"
    WRITING = "writing"
    COMPLETED = "completed"
    FAILED = "failed"


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
        self._terminal_system_prompt = terminal_system_prompt or config.research_system_prompt
        self._terminal_task_instruction = terminal_task_instruction

        # Subsystems (initialised in ``_setup``)
        self._workspace: WorkspaceManager | None = None
        self._router: LLMRouter | None = None
        self._store: ArtifactStore | None = None
        self._ctx: ContextReconstructor | None = None
        self._mm: MentalModelManager | None = None

        # Session ID of the last completed phase (for audit feedback resume)
        self._last_phase_session_id: str | None = None

        # Human-in-the-loop checkpoint handler (set via ``set_checkpoint_handler``)
        self._checkpoint_handler: WebCheckpointHandler | None = None

        # Chat handler for real-time interaction (set via ``set_chat_handler``)
        self._chat_handler: Any = None

        # Current iteration runner (for inject_message)
        self.current_iteration_runner: Any = None
        self._session_id: str | None = None

        # Supervisor — monitors the main agent during research
        self._supervisor: Any = None

        # Watchdog — event-driven agent that monitors execution quality
        self._watchdog: Any = None

        # Front Desk Agent — human's single interaction entry point
        self._frontdesk: Any = None
        # Pending escalation queues: escalation_id → asyncio.Queue
        self._escalation_queues: dict[str, asyncio.Queue] = {}
        # Pending approval queues: for relay instruction approval loop
        self._approval_queues: dict[str, asyncio.Queue] = {}
        # Flag: next agent text output should be forwarded to Front Desk
        self._awaiting_agent_response: bool = False

        # Cross-phase context: each phase writes its transcript to disk,
        # subsequent phases can read prior transcripts for context.
        self._phase_transcripts: list[dict[str, str]] = []

        # Current round number — incremented on analysis loop-back.
        self._current_round: int = 0

        # Skill distillation store (initialized in _setup)
        self._skill_store: Any = None

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
        """Run (or resume) the full research campaign."""
        campaign_start = datetime.now(timezone.utc)
        from aurora.core.requirements import check_runtime_requirements
        check_runtime_requirements(self._config)
        self._setup()
        assert self._router is not None
        assert self._workspace is not None

        # Start the execution backend (Docker container) if configured
        if self._execution_backend is not None:
            await self._execution_backend.setup()
            logger.info(
                "Execution backend started (container=%s)",
                getattr(self._execution_backend, "container_id", "local"),
            )

        checkpoint = self._load_checkpoint() if resume else None
        start_phase = (
            CampaignPhase(checkpoint.phase) if checkpoint else CampaignPhase.ROUNDTABLE
        )
        research_cycle = checkpoint.research_cycle if checkpoint else 0
        self._current_round = research_cycle

        all_iterations: list[IterationResult] = []
        proposal: RoundtableProposal | None = None
        plan: ExperimentPlan | None = None
        manuscript_path: str | None = None

        skip_phases = set(self._config.skip_phases)

        def _should_skip(phase: CampaignPhase) -> bool:
            return phase.value in skip_phases

        def _next_unskipped(phase: CampaignPhase) -> CampaignPhase:
            """Advance past skipped phases to find the next one to run."""
            phase_order = [
                CampaignPhase.ROUNDTABLE,
                CampaignPhase.SCREENING,
                CampaignPhase.PLANNING,
                CampaignPhase.RESEARCH_LOOP,
                CampaignPhase.ANALYSIS,
                CampaignPhase.WRITING,
                CampaignPhase.COMPLETED,
            ]
            idx = phase_order.index(phase) if phase in phase_order else 0
            while idx < len(phase_order) - 1:
                candidate = phase_order[idx]
                if not _should_skip(candidate):
                    return candidate
                logger.info("Skipping phase: %s", candidate.value)
                idx += 1
            return CampaignPhase.COMPLETED

        # Use initial_proposal if provided (replaces Round Table)
        if self._initial_proposal is not None:
            proposal = self._initial_proposal

        try:
            # Initialise artifacts if fresh start
            if self._store and (not resume or start_phase == CampaignPhase.ROUNDTABLE):
                self._store.initialize(objective=self._config.objective)
                await self._publish("campaign_started", campaign_id=self._config.campaign_id)

            current_phase = _next_unskipped(start_phase) if _should_skip(start_phase) else start_phase

            # Request approval before starting the research loop
            if self._needs_approval("research_start"):
                decision = await self._request_checkpoint(
                    event_type="research_start",
                    summary=(
                        f"Campaign '{self._config.campaign_id}' is ready to begin. "
                        f"Objective: {self._config.objective}"
                    ),
                    proposed_action="Start the research pipeline.",
                    risk_assessment="low",
                    recommendation="approve",
                )
                if decision == CheckpointDecision.TERMINATE:
                    self._phase = CampaignPhase.COMPLETED
                    return CampaignResult(
                        campaign_id=self._config.campaign_id,
                        status="stopped",
                        summary="Campaign terminated by human at research_start checkpoint.",
                    )
                elif decision == CheckpointDecision.REJECT:
                    self._phase = CampaignPhase.FAILED
                    return CampaignResult(
                        campaign_id=self._config.campaign_id,
                        status="failed",
                        summary="Campaign rejected by human at research_start checkpoint.",
                    )

            while current_phase != CampaignPhase.COMPLETED:
                # Check pause state before each phase
                await self._wait_if_paused()

                self._phase = current_phase
                self._update_frontdesk_status(phase=current_phase.value)
                logger.info(
                    "=== Phase: %s (cycle %d) ===",
                    current_phase.value,
                    research_cycle,
                )

                if current_phase == CampaignPhase.ROUNDTABLE:
                    proposal = await self._run_roundtable()
                    next_phase = _next_unskipped(CampaignPhase.SCREENING)
                    await self._fire_milestone(
                        CampaignPhase.ROUNDTABLE, next_phase,
                        len(all_iterations),
                    )
                    self._save_checkpoint(next_phase, research_cycle)
                    current_phase = next_phase

                elif current_phase == CampaignPhase.SCREENING:
                    if proposal is None:
                        proposal = self._load_latest_proposal()
                    proposal = await self._run_screening(proposal)
                    if not proposal.selected:
                        logger.warning(
                            "Proposal not selected — returning to roundtable"
                        )
                        current_phase = CampaignPhase.ROUNDTABLE
                        continue
                    next_phase = _next_unskipped(CampaignPhase.PLANNING)
                    await self._fire_milestone(
                        CampaignPhase.SCREENING, next_phase,
                        len(all_iterations),
                    )
                    self._save_checkpoint(
                        next_phase,
                        research_cycle,
                        proposal_id=proposal.proposal_id,
                    )
                    current_phase = next_phase

                elif current_phase == CampaignPhase.PLANNING:
                    if proposal is None:
                        proposal = self._load_latest_proposal()
                    plan = await self._run_planning(proposal)
                    next_phase = _next_unskipped(CampaignPhase.RESEARCH_LOOP)
                    await self._fire_milestone(
                        CampaignPhase.PLANNING, next_phase,
                        len(all_iterations),
                    )
                    self._save_checkpoint(
                        next_phase,
                        research_cycle,
                        plan_id=plan.plan_id,
                    )
                    current_phase = next_phase

                elif current_phase == CampaignPhase.RESEARCH_LOOP:
                    if plan is None:
                        plan = self._load_plan()
                    if plan is None:
                        plan = ExperimentPlan(
                            plan_id=f"synthetic_{self._config.campaign_id}",
                            proposal_id=self._initial_proposal.proposal_id if self._initial_proposal else "none",
                            jobs=[ExperimentJob(
                                job_id="main",
                                skill_name="terminal_execution",
                                parameters={"objective": self._config.objective[:500]},
                                expected_outputs=[],
                            )],
                            created_at=datetime.now(timezone.utc),
                        )
                    completed_jobs = (
                        checkpoint.completed_jobs
                        if checkpoint and checkpoint.phase == CampaignPhase.RESEARCH_LOOP.value
                        else None
                    )
                    loop_results = await self._run_research_loop(
                        plan, completed_jobs=completed_jobs
                    )
                    all_iterations.extend(loop_results)
                    # analysis and writing on top of zero successful jobs would only restate
                    # canned or empty results as findings, so stop here instead
                    if loop_results and not any(
                        r.job_result is not None and r.job_result.success for r in loop_results
                    ):
                        first_err = next((r.error for r in loop_results if r.error), None) or next(
                            (e for r in loop_results if r.job_result for e in r.job_result.errors), "unknown error")
                        raise RuntimeError(
                            f"All {len(loop_results)} research iterations failed; "
                            f"stopping before analysis. First error: {first_err}")
                    next_phase = _next_unskipped(CampaignPhase.ANALYSIS)
                    await self._fire_milestone(
                        CampaignPhase.RESEARCH_LOOP, next_phase,
                        len(all_iterations),
                    )
                    self._save_checkpoint(next_phase, research_cycle)
                    current_phase = next_phase

                elif current_phase == CampaignPhase.ANALYSIS:
                    loop_back_to = await self._run_analysis()
                    if (
                        loop_back_to is not None
                        and research_cycle < self._config.max_followup_cycles
                    ):
                        research_cycle += 1
                        self._current_round = research_cycle
                        logger.info(
                            "Analysis requests loop-back to %s — cycle %d",
                            loop_back_to.value,
                            research_cycle,
                        )
                        next_phase = _next_unskipped(loop_back_to)
                        await self._fire_milestone(
                            CampaignPhase.ANALYSIS, next_phase,
                            len(all_iterations),
                        )
                        self._save_checkpoint(next_phase, research_cycle)
                        current_phase = next_phase
                    else:
                        next_phase = _next_unskipped(CampaignPhase.WRITING)
                        await self._fire_milestone(
                            CampaignPhase.ANALYSIS, next_phase,
                            len(all_iterations),
                        )
                        self._save_checkpoint(next_phase, research_cycle)
                        current_phase = next_phase

                elif current_phase == CampaignPhase.WRITING:
                    manuscript_path = await self._run_writing()
                    # Fire termination guard before completing
                    term_decision = await self._fire_termination_guard(
                        all_iterations, plan
                    )
                    if term_decision == CheckpointDecision.REJECT:
                        logger.info(
                            "Termination rejected — returning to research"
                        )
                        research_cycle += 1
                        self._current_round = research_cycle
                        next_phase = _next_unskipped(CampaignPhase.RESEARCH_LOOP)
                        self._save_checkpoint(next_phase, research_cycle)
                        current_phase = next_phase
                        continue
                    self._save_checkpoint(
                        CampaignPhase.COMPLETED, research_cycle
                    )
                    current_phase = CampaignPhase.COMPLETED

            self._phase = CampaignPhase.COMPLETED
            total_time = (
                datetime.now(timezone.utc) - campaign_start
            ).total_seconds()

            result = CampaignResult(
                campaign_id=self._config.campaign_id,
                status="completed",
                proposal=proposal,
                plan=plan,
                iterations=all_iterations,
                manuscript_path=manuscript_path,
                research_cycles=research_cycle,
                total_wall_time_seconds=total_time,
                total_llm_tokens=(
                    self._router.usage_tracker.total_tokens if self._router else 0
                ),
                summary=(
                    f"Campaign completed in {total_time:.1f}s with "
                    f"{len(all_iterations)} iterations and "
                    f"{research_cycle} research cycles."
                ),
            )

            await self._publish("campaign_completed", status="completed")
            return result

        except Exception as exc:
            self._phase = CampaignPhase.FAILED
            logger.exception("Campaign failed: %s", exc)
            await self._publish("campaign_failed", error=str(exc))
            return CampaignResult(
                campaign_id=self._config.campaign_id,
                status="failed",
                iterations=all_iterations,
                total_wall_time_seconds=(
                    datetime.now(timezone.utc) - campaign_start
                ).total_seconds(),
                summary=f"Campaign failed: {exc}",
            )

        finally:
            # Extract workspace and tear down execution backend
            if self._execution_backend is not None:
                if self._workspace:
                    extract_dest = self._workspace.campaign_dir / "workspace"
                    try:
                        await self._execution_backend.extract_workspace(extract_dest)
                        logger.info("Workspace extracted to %s", extract_dest)
                    except Exception:
                        logger.warning("Workspace extraction failed", exc_info=True)
                try:
                    await self._execution_backend.teardown()
                    logger.info("Execution backend torn down")
                except Exception:
                    logger.warning("Backend teardown failed", exc_info=True)

            if self._router:
                await self._router.close_all()

    @property
    def phase(self) -> CampaignPhase:
        return self._phase

    @property
    def event_bus(self) -> EventBus:
        return self._bus

    def set_checkpoint_handler(self, handler: WebCheckpointHandler) -> None:
        """Assign a web checkpoint handler for human-in-the-loop approval."""
        self._checkpoint_handler = handler

    def set_chat_handler(self, handler: Any) -> None:
        """Assign a chat handler for real-time interaction with agentic loops."""
        self._chat_handler = handler

    def set_watchdog(self, watchdog: Any) -> None:
        """Assign a watchdog agent for execution monitoring.

        The watchdog receives all engine events via ``on_engine_event``
        and can interrupt the agent via inject_message.
        """
        self._watchdog = watchdog

    def pause(self) -> None:
        """Pause the engine after the current iteration completes."""
        self._paused.clear()
        logger.info("Engine pause requested")

    def resume(self) -> None:
        """Resume a paused engine."""
        self._paused.set()
        logger.info("Engine resumed")

    # ------------------------------------------------------------------
    # Checkpoint helpers
    # ------------------------------------------------------------------

    async def _request_checkpoint(
        self,
        event_type: str,
        summary: str,
        *,
        proposed_action: str = "",
        risk_assessment: str = "low",
        recommendation: str = "approve",
    ) -> CheckpointDecision:
        """Request human approval via the checkpoint handler.

        If no handler is configured the checkpoint is silently approved.

        Returns
        -------
        CheckpointDecision
            The human's decision.
        """
        if self._checkpoint_handler is None:
            return CheckpointDecision.APPROVE

        artifacts = self._store.load() if self._store else None
        request = WebCheckpointHandler.build_request(
            event_type=event_type,
            summary=summary,
            proposed_action=proposed_action,
            risk_assessment=risk_assessment,
            recommendation=recommendation,
            artifacts=artifacts,
        )

        await self._publish(
            "checkpoint_requested",
            checkpoint_id=request.checkpoint_id,
            event_type=event_type,
        )

        response = await self._checkpoint_handler.request_approval(request)
        decision = CheckpointDecision(response.decision)

        await self._publish(
            "checkpoint_resolved",
            checkpoint_id=request.checkpoint_id,
            decision=decision.value,
        )

        return decision

    def _needs_approval(self, event_type: str) -> bool:
        """Check whether *event_type* requires human approval."""
        return event_type in self._config.human_checkpoints.require_approval_for

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

    async def _drain_chat_messages(self) -> None:
        """Drain queued chat messages into the current iteration runner.

        Lightweight fallback for when the Front Desk is not active but
        a chat handler exists — injects user messages between iterations.
        """
        if self._frontdesk or not self._chat_handler or not self.current_iteration_runner:
            return
        queue = self._chat_handler.user_queue
        while not queue.empty():
            try:
                text = queue.get_nowait()
            except Exception:
                break
            if hasattr(self.current_iteration_runner, "inject_message"):
                await self.current_iteration_runner.inject_message(
                    f"[User Chat Message]\n{text}"
                )
                logger.info("Chat message injected into agent: %s", text[:80])

    # ==================================================================
    # Setup
    # ==================================================================

    def _setup(self) -> None:
        """Wire up all subsystems from the campaign configuration.

        If ``_router`` was pre-assigned (e.g. for mock/demo use), it is
        kept instead of creating a new one from configuration.

        When ``use_docker`` is True and no execution backend was
        pre-assigned, a ``DockerBackend`` is created from the config.
        The container is started in ``_setup_backend()`` (async, called
        from ``run_campaign``).
        """
        self._workspace = WorkspaceManager(
            config=self._config.workspace,
            campaign_id=self._config.campaign_id,
        )
        self._workspace.initialize()

        # Create Docker backend from config if needed
        if self._execution_backend is None and self._config.use_docker:
            from aurora.core.docker_backend import DockerBackend
            self._execution_backend = DockerBackend(
                docker_image=self._config.docker_image,
                cpus=self._config.docker_cpus,
                memory_mb=self._config.docker_memory_mb,
                allow_network=self._config.docker_network,
                tests_dir=self._config.docker_tests_dir,
            )
        # Without a container the Claude Code sessions still run, confined to the workspace
        elif self._execution_backend is None and self._config.use_terminal_iteration:
            from aurora.core.execution_backend import LocalBackend
            self._execution_backend = LocalBackend(str(self._workspace.root))
        # Sync per-seat model assignments into llm_assignment
        if self._config.roundtable_seats:
            seat_models = [
                sc.model or self._config.llm_assignment.roundtable_seat[
                    i % len(self._config.llm_assignment.roundtable_seat)
                ]
                for i, sc in enumerate(self._config.roundtable_seats)
            ]
            self._config = self._config.model_copy(
                update={"llm_assignment": self._config.llm_assignment.model_copy(
                    update={"roundtable_seat": seat_models}
                )}
            )
        if self._router is None:
            self._router = create_router(self._config)
        if self._config.domain == "lhtb_benchmark":
            self._store = None
        else:
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

        # Register skills — load domain-specific skills when available
        skill_reg = SkillRegistry()
        self._register_domain_skills(skill_reg)
        self._registry.register(SkillRegistry, skill_reg)

        # Register tools (real or mock depending on config)
        from aurora.tools import create_tool_registry
        tool_reg = create_tool_registry(self._config)
        self._registry.register(ToolRegistry, tool_reg)

        # Register audit hooks
        from aurora.hooks.base import HookRegistry
        from aurora.hooks.audit_hooks import (
            EnergyAnomalyHook, ConvergenceHook, ConsistencyHook,
            PeriodicAuditHook, GoalDriftHook, ResourceExhaustionHook,
            RepeatedFailureHook, NovelResultHook,
            ClaimPromotionHook, TerminationGuardHook, MilestoneAuditHook,
        )
        hook_reg = HookRegistry()
        for hook_cls in (
            EnergyAnomalyHook, ConvergenceHook, ConsistencyHook,
            PeriodicAuditHook, GoalDriftHook,
            RepeatedFailureHook, NovelResultHook,
            ClaimPromotionHook, TerminationGuardHook, MilestoneAuditHook,
        ):
            hook_reg.register(hook_cls(self._router))
        # ResourceExhaustionHook needs extra arg
        hook_reg.register(ResourceExhaustionHook(self._router, self._config.resource_limits))
        self._hooks = hook_reg
        self._registry.register(HookRegistry, hook_reg)

        # Initialize distilled skill store
        try:
            from aurora.skills.store import SkillStore
            db_dir = self._workspace.campaign_dir if self._workspace else Path("data/aurora")
            db_path = db_dir.parent / "aurora.db"
            self._skill_store = SkillStore(db_path)
        except Exception:
            logger.debug("Skill store not available", exc_info=True)

    def _register_domain_skills(self, skill_reg: SkillRegistry) -> None:
        """Load domain-specific skills based on config.domain."""
        domain = self._config.domain

        if domain == "computational_materials_science":
            try:
                from aurora.skills.materials.structure_construction import StructureConstructionSkill
                from aurora.skills.materials.convergence_analysis import ConvergenceAnalysisSkill
                from aurora.skills.materials.segregation_analysis import SegregationAnalysisSkill
                from aurora.skills.materials.simulation_diagnosis import SimulationDiagnosisSkill
                from aurora.skills.materials.statistical_validation import StatisticalValidationSkill
                from aurora.skills.materials.scientific_visualization import ScientificVisualizationSkill
                from aurora.skills.materials.compute_orchestration import ComputeOrchestrationSkill
                for skill_cls in (
                    StructureConstructionSkill,
                    ConvergenceAnalysisSkill,
                    SegregationAnalysisSkill,
                    SimulationDiagnosisSkill,
                    StatisticalValidationSkill,
                    ScientificVisualizationSkill,
                    ComputeOrchestrationSkill,
                ):
                    skill_reg.register(skill_cls())
            except Exception as exc:
                logger.debug("Could not register material skills: %s", exc)
        else:
            logger.info(
                "Domain '%s' has no built-in skills — pipeline agents "
                "will operate with generic prompts",
                domain,
            )

    # ==================================================================
    # Event helper
    # ==================================================================

    async def _publish(self, topic: str, **data: Any) -> None:
        await self._bus.publish(Event(name=topic, data=data, source="engine"))

    # ==================================================================
    # Hook helpers
    # ==================================================================

    _AUDITOR_SYSTEM_PROMPT = """\
You are the Auditor — an independent reviewer embedded in a research pipeline.
Your job is to audit the work completed in the previous phase before the pipeline
advances to the next phase.

## Audit Dimensions
Evaluate ALL of the following. For each, give a verdict (pass / warning / fail)
and a brief explanation:

1. **Goal Alignment** — Is the work still aligned with the original objective?
   Has the agent drifted from what was asked?
2. **Completeness** — Did the phase produce the expected outputs? Are there
   missing artifacts or incomplete steps?
3. **Correctness** — Are the results reasonable? Any obvious errors, anomalies,
   or inconsistencies in the outputs?
4. **Resource Usage** — Is resource consumption (tokens, time, iterations)
   proportionate to the work done? Any signs of looping or waste?
5. **Risk Assessment** — Are there risks in proceeding to the next phase?
   Anything that should be flagged for human review?

## Output Format
After your review, write a JSON audit report to:
  {audit_path}

The JSON must have this structure:
```json
{{
  "completed_phase": "<phase name>",
  "next_phase": "<phase name>",
  "overall_verdict": "pass" | "warning" | "fail",
  "human_intervention_required": false,
  "dimensions": {{
    "goal_alignment": {{"verdict": "pass|warning|fail", "explanation": "..."}},
    "completeness": {{"verdict": "pass|warning|fail", "explanation": "..."}},
    "correctness": {{"verdict": "pass|warning|fail", "explanation": "..."}},
    "resource_usage": {{"verdict": "pass|warning|fail", "explanation": "..."}},
    "risk_assessment": {{"verdict": "pass|warning|fail", "explanation": "..."}}
  }},
  "summary": "One-paragraph overall assessment",
  "recommendations": ["actionable items if any"]
}}
```

Set "human_intervention_required" to true ONLY if you find a critical issue that
must be reviewed by a human before proceeding.

## Rules
- Read the actual files and outputs, do not guess.
- Be concise but specific — cite file names and concrete observations.
- A "pass" means the work is solid and ready to proceed.
- A "warning" means minor issues that don't block progress.
- A "fail" means critical issues that should halt the pipeline.
"""

    async def _fire_milestone(
        self,
        completed_phase: CampaignPhase,
        next_phase: CampaignPhase,
        iteration_count: int,
    ) -> None:
        """Run an Auditor agent session at a phase boundary.

        The auditor is a Claude Code subprocess that inspects the workspace,
        reads phase outputs, and produces a structured audit report.
        """
        if not self._workspace:
            return

        audit_dir = self._workspace.root / "audit_records"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_filename = f"milestone_{completed_phase.value}_to_{next_phase.value}.json"
        audit_path = audit_dir / audit_filename

        if self._should_use_agent_session():
            max_audit_retries = 2
            phase_session_id = self._last_phase_session_id

            for attempt in range(max_audit_retries + 1):
                logger.info(
                    "Auditor: reviewing milestone %s → %s (attempt %d/%d)",
                    completed_phase.value, next_phase.value,
                    attempt + 1, max_audit_retries + 1,
                )

                # Run auditor
                audit_system = self._AUDITOR_SYSTEM_PROMPT.format(
                    audit_path=str(audit_path),
                )
                audit_message = (
                    f"The '{completed_phase.value}' phase has just completed. "
                    f"The pipeline is about to transition to '{next_phase.value}'.\n\n"
                    f"Campaign objective: {self._config.objective[:500]}\n\n"
                    f"Review the phase outputs and write your audit report."
                )
                await self._run_agent_session(
                    phase_name="audit",
                    system_prompt=audit_system,
                    user_message=audit_message,
                    max_turns=15,
                    model_override=self._config.llm_assignment.auditor_triage,
                )

                # Read the audit report
                if not audit_path.exists():
                    logger.warning("Auditor did not write report to %s", audit_path)
                    continue

                try:
                    report = json.loads(audit_path.read_text())
                except (json.JSONDecodeError, OSError):
                    logger.warning("Auditor did not produce valid JSON report at %s", audit_path)
                    audit_path.unlink(missing_ok=True)
                    continue

                overall = report.get("overall_verdict", "pass")
                human_intervention = report.get("human_intervention_required", False)
                logger.info(
                    "Audit: %s → %s — verdict=%s, intervention=%s",
                    completed_phase.value, next_phase.value,
                    overall, human_intervention,
                )

                if overall == "pass" or overall == "warning":
                    break

                # verdict == "fail": inject feedback into the phase session and retry
                if not phase_session_id:
                    logger.warning("Cannot resume phase session — no session_id available")
                    break

                if attempt >= max_audit_retries:
                    logger.warning("Audit still failing after %d retries, escalating", max_audit_retries)
                    await self._request_checkpoint(
                        event_type="audit_intervention",
                        summary=(
                            f"Auditor flagged critical issue at "
                            f"{completed_phase.value} → {next_phase.value} "
                            f"after {max_audit_retries + 1} attempts. "
                            f"See {audit_path} for details."
                        ),
                        proposed_action=f"Proceed to {next_phase.value} phase.",
                        risk_assessment="high",
                        recommendation="review",
                    )
                    break

                # Build feedback message from audit findings
                feedback_parts = [f"## Auditor Feedback (attempt {attempt + 1})\n"]
                feedback_parts.append(f"Overall verdict: **{overall}**\n")
                feedback_parts.append(report.get("summary", ""))
                dims = report.get("dimensions", {})
                for dim_name, dim_data in dims.items():
                    if isinstance(dim_data, dict) and dim_data.get("verdict") == "fail":
                        feedback_parts.append(
                            f"\n**{dim_name}** (FAIL): {dim_data.get('explanation', '')}"
                        )
                recs = report.get("recommendations", [])
                if recs:
                    feedback_parts.append("\n**Required fixes:**")
                    for rec in recs:
                        feedback_parts.append(f"- {rec}")
                feedback_parts.append(
                    "\nPlease address these issues and update your outputs."
                )
                feedback = "\n".join(feedback_parts)

                logger.info("Injecting auditor feedback into phase session %s", phase_session_id)
                result = await self._run_agent_session(
                    phase_name=completed_phase.value,
                    system_prompt="",
                    user_message=feedback,
                    max_turns=15,
                    resume_session_id=phase_session_id,
                )
                phase_session_id = result.get("session_id") or phase_session_id
        else:
            # Fallback: use lightweight hook-based auditing
            artifacts = self._store.load() if self._store else None
            state_summary = (
                f"Objective: {artifacts.initial_objective}; "
                f"Goal: {artifacts.current_goal}; "
                f"Experiments: {len(artifacts.experiments)}; "
                f"Claims: {len(artifacts.claims)}"
            ) if artifacts else ""

            ctx = HookContext(
                hook_point=HookPoint.MILESTONE_COMPLETE,
                iteration=iteration_count,
                data={
                    "completed_phase": completed_phase.value,
                    "next_phase": next_phase.value,
                    "state_summary": state_summary,
                    "iteration_count": iteration_count,
                },
                metadata={"campaign_id": self._config.campaign_id},
            )
            hook_results = await self._hooks.fire(HookPoint.MILESTONE_COMPLETE, ctx)
            self._save_audit_records(completed_phase, next_phase, hook_results)

            for r in hook_results:
                if r.human_intervention_required:
                    await self._request_checkpoint(
                        event_type="hook_intervention",
                        summary=(
                            f"Hook '{r.hook_name}' requires human intervention "
                            f"at phase boundary {completed_phase.value} -> "
                            f"{next_phase.value}: {r.message}"
                        ),
                        proposed_action=f"Proceed to {next_phase.value} phase.",
                        risk_assessment=r.severity,
                        recommendation="review",
                    )

        # Request phase_transition approval if configured
        if self._needs_approval("phase_transition"):
            artifacts = self._store.load() if self._store else None
            state_summary = (
                f"Objective: {artifacts.initial_objective}; "
                f"Goal: {artifacts.current_goal}; "
                f"Experiments: {len(artifacts.experiments)}; "
                f"Claims: {len(artifacts.claims)}"
            ) if artifacts else ""
            await self._request_checkpoint(
                event_type="phase_transition",
                summary=(
                    f"Phase '{completed_phase.value}' completed. "
                    f"Ready to transition to '{next_phase.value}'. "
                    f"{state_summary}"
                ),
                proposed_action=f"Begin the '{next_phase.value}' phase.",
                risk_assessment="low",
                recommendation="approve",
            )

        # Auto-distillation: suggest skills from completed phase
        artifacts = self._store.load() if self._store else None
        state_summary = (
            f"Objective: {artifacts.initial_objective}; "
            f"Goal: {artifacts.current_goal}"
        ) if artifacts else ""
        await self._distill_phase_skills(completed_phase, state_summary)

    def _save_audit_records(
        self,
        completed_phase: CampaignPhase,
        next_phase: CampaignPhase,
        hook_results: list,
    ) -> None:
        """Persist hook results to audit_records/ after each phase boundary."""
        if not self._workspace:
            return
        audit_dir = self._workspace.root / "audit_records"
        audit_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "completed_phase": completed_phase.value,
            "next_phase": next_phase.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "campaign_id": self._config.campaign_id,
            "hooks": [
                {
                    "name": r.hook_name,
                    "passed": r.passed,
                    "message": r.message,
                    "severity": r.severity,
                    "human_intervention_required": r.human_intervention_required,
                    "data": r.data,
                }
                for r in hook_results
            ],
        }
        filename = f"milestone_{completed_phase.value}_to_{next_phase.value}.json"
        (audit_dir / filename).write_text(json.dumps(record, indent=2, default=str))
        logger.info(
            "Audit: %s → %s — %d hooks fired, %d passed",
            completed_phase.value, next_phase.value,
            len(hook_results), sum(1 for r in hook_results if r.passed),
        )

    async def _distill_phase_skills(
        self,
        completed_phase: CampaignPhase,
        state_summary: str,
    ) -> None:
        """Extract reusable skill candidates from a completed phase.

        Emits ``skill_suggestion`` events via EventBus so the Front Desk
        can present them to the user.  Does not auto-save — the user
        must accept each suggestion.
        """
        if not self._router or not self._skill_store:
            return

        transcripts = self._phase_transcripts
        if not transcripts:
            return

        latest_transcript = transcripts[-1] if transcripts else {}
        phase_text = latest_transcript.get("content", "")
        if len(phase_text) < 100:
            return

        try:
            prompt = (
                "You are a skill distillation agent. Analyze the following "
                "phase transcript and extract 0-3 reusable rules or patterns "
                "that would help future experiments. Each rule should be a "
                "concise, actionable instruction.\n\n"
                f"Phase: {completed_phase.value}\n"
                f"Context: {state_summary}\n\n"
                f"Transcript (last 2000 chars):\n{phase_text[-2000:]}\n\n"
                "Respond in JSON: [{\"name\": \"kebab-case-name\", "
                "\"rule\": \"the actionable rule\", "
                "\"description\": \"one-line summary\"}]\n"
                "If no meaningful rules can be extracted, respond with []."
            )
            response = await self._router.generate(
                messages=[Message(role="user", content=prompt)],
                purpose="skill_distillation",
            )
            import re
            match = re.search(r"\[.*\]", response.content, re.DOTALL)
            if not match:
                return
            import json
            candidates = json.loads(match.group())
            for c in candidates:
                if not isinstance(c, dict) or "name" not in c or "rule" not in c:
                    continue
                await self._publish(
                    "skill_suggestion",
                    name=c["name"],
                    rule=c["rule"],
                    description=c.get("description", ""),
                    source_phase=completed_phase.value,
                    source_experiment=self._config.campaign_id,
                )
        except Exception:
            logger.debug("Skill distillation failed", exc_info=True)

    async def _fire_termination_guard(
        self,
        all_iterations: list[IterationResult],
        plan: ExperimentPlan | None,
    ) -> CheckpointDecision:
        """Fire :data:`HookPoint.TERMINATION_ATTEMPT` before campaign completion.

        If ``termination`` is in the ``require_approval_for`` list, a
        human checkpoint is raised after the hooks run.

        Returns
        -------
        CheckpointDecision
            The human's decision (APPROVE if no handler is configured
            or ``termination`` is not in ``require_approval_for``).
        """
        artifacts = self._store.load() if self._store else None

        open_questions: list[str] = []
        unvalidated_claims: list[str] = []
        if artifacts:
            for claim in artifacts.claims:
                if claim.status in ("hypothesized", "unvalidated"):
                    unvalidated_claims.append(claim.statement)

        total_experiments = len(plan.jobs) if plan else 0
        completed_experiments = sum(
            1 for r in all_iterations
            if r.job_result and r.job_result.success
        )

        ctx = HookContext(
            hook_point=HookPoint.TERMINATION_ATTEMPT,
            iteration=len(all_iterations),
            data={
                "open_questions": open_questions,
                "unvalidated_claims": unvalidated_claims,
                "completed_experiments": completed_experiments,
                "total_experiments": total_experiments,
            },
            metadata={"campaign_id": self._config.campaign_id},
        )
        hook_results = await self._hooks.fire(HookPoint.TERMINATION_ATTEMPT, ctx)

        # Check hooks for human intervention request
        for r in hook_results:
            if r.human_intervention_required:
                decision = await self._request_checkpoint(
                    event_type="hook_intervention",
                    summary=(
                        f"Hook '{r.hook_name}' requires human review before "
                        f"campaign termination: {r.message}"
                    ),
                    proposed_action="Complete the campaign.",
                    risk_assessment=r.severity,
                    recommendation="review",
                )
                if decision in (CheckpointDecision.REJECT, CheckpointDecision.TERMINATE):
                    return decision

        # Request termination approval if configured
        if self._needs_approval("termination"):
            summary_parts = [
                f"Campaign ready to terminate after {len(all_iterations)} iterations.",
                f"Completed: {completed_experiments}/{total_experiments} experiments.",
            ]
            if unvalidated_claims:
                summary_parts.append(
                    f"Unvalidated claims: {len(unvalidated_claims)}."
                )
            if open_questions:
                summary_parts.append(
                    f"Open questions: {len(open_questions)}."
                )

            return await self._request_checkpoint(
                event_type="termination",
                summary=" ".join(summary_parts),
                proposed_action="Complete the campaign and produce final outputs.",
                risk_assessment="medium" if unvalidated_claims else "low",
                recommendation="approve",
            )

        return CheckpointDecision.APPROVE

    # ==================================================================
    # Phase implementations
    # ==================================================================

    def _should_use_agent_session(self) -> bool:
        """Whether to use Claude Code agent sessions for pipeline phases."""
        return (
            self._config.use_terminal_iteration
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
    ) -> dict[str, Any]:
        """Run a Claude Code agent session and return the full result.

        Each phase starts a fresh session. Prior phases' transcripts are
        saved to disk — the system prompt tells the agent where to find
        them so it can read relevant context on demand.

        Returns dict with keys: text, session_id, turns, tokens, etc.
        """
        container_id = getattr(self._execution_backend, "container_id", None)

        prior_context = self._build_prior_context_prompt(phase_name)
        full_system = system_prompt + prior_context

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
            model_override=model_override,
            resume_session_id=resume_session_id,
        )
        result = await session.run(full_system, user_message)

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
            "experiment task in the terminal environment — write code, run "
            "experiments, produce outputs, and call task_complete when done."
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
            "  ROUNDTABLE → SCREENING → PLANNING → RESEARCH_LOOP → ANALYSIS → WRITING\n\n"
            "Each phase produces artifacts (plans, code, results, reports) stored on disk. "
            "Analysis can loop back to any earlier phase, creating a new round. "
            "Each round's artifacts are stored separately under `round_N/`."
        )

        # --- 2. Current position ---
        lines.append(f"\n## Your Position")
        lines.append(f"- **Current phase**: {current_phase}")
        lines.append(f"- **Current round**: {self._current_round}")

        completed = [e["phase"] for e in self._phase_transcripts]
        if completed:
            lines.append(f"- **Completed phases this campaign**: {', '.join(completed)}")

        # --- 3. Role ---
        role = self._PHASE_ROLES.get(current_phase, "")
        if role:
            lines.append(f"\n## Your Responsibility")
            lines.append(role)

        # --- 4. Workspace layout ---
        lines.append(f"\n## Workspace")
        lines.append(f"All artifacts are under: `{ws.campaign_dir}`\n")

        # Campaign-level files
        campaign_files = []
        if ws.artifacts_path.exists():
            campaign_files.append(f"- `artifacts.json` — shared experiment records and observations")
        if ws.mental_model_path.exists():
            campaign_files.append(f"- `mental_model.json` — evolving research mental model")
        if campaign_files:
            lines.extend(campaign_files)

        # Phase directories with actual content
        lines.append(f"\n### Phase Artifacts (`phases/`)\n")

        phase_order = ["roundtable", "screening", "planning", "research", "analysis", "writing"]
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
        lines.append(f"\n## How to Use Prior Context")
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

    async def _run_roundtable(self) -> RoundtableProposal:
        """Run the multi-model roundtable deliberation."""
        assert self._router is not None
        assert self._workspace is not None

        artifacts = self._store.load() if self._store else None
        await self._publish("phase_started", phase="roundtable")

        # Build an AgentContext for the roundtable
        ctx = self._build_agent_context(artifacts)

        rt = Roundtable(config=self._config, router=self._router)
        proposal = await rt.deliberate(ctx, topic=artifacts.current_goal)

        self._workspace.atomic_write_json(
            self._workspace.proposal_path(proposal.proposal_id, self._current_round),
            proposal,
        )

        await self._publish(
            "roundtable_complete",
            proposal_id=proposal.proposal_id,
            title=proposal.title,
        )
        return proposal

    async def _run_screening(
        self, proposal: RoundtableProposal
    ) -> RoundtableProposal:
        """Screen the proposal for novelty, feasibility, and significance."""
        await self._publish("phase_started", phase="screening")

        if self._should_use_agent_session():
            score = await self._run_screening_agent(proposal)
        else:
            assert self._router is not None
            screener = IdeaScreener(
                self._router,
                system_prompt=self._config.screening_system_prompt,
            )
            score = await screener.screen(proposal)

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
        self, proposal: RoundtableProposal
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
            f"Evaluate this proposal and write your screening verdict as JSON to:\n"
            f"  {score_path}\n\n"
            f"The JSON must have this structure:\n"
            f'{{"novelty": 0-10, "feasibility": 0-10, "significance": 0-10, '
            f'"composite": 0-10, "explanation": "..."}}\n\n'
            f"You may explore the Docker container environment to assess feasibility. "
            f"When done, call task_complete."
        )

        result = await self._run_agent_session(
            "screening",
            self._config.screening_system_prompt or (
                "You are a research proposal screener. Evaluate the proposal's "
                "novelty, feasibility, and significance. Be rigorous but fair."
            ),
            user_msg,
            max_turns=self._config.max_agentic_turns,
        )
        self._last_phase_session_id = result.get("session_id")

        try:
            data = json.loads(score_path.read_text())
            return IdeaScore(**data)
        except Exception:
            logger.warning("Screening agent did not produce valid score, using defaults")
            return IdeaScore()

    async def _run_planning(
        self, proposal: RoundtableProposal
    ) -> ExperimentPlan:
        """Convert a screened proposal into an experiment plan.

        When Claude Code mode is active, the planner runs as a full
        agent session that can explore the environment before planning.
        Otherwise falls back to the single-shot LLM planner.
        """
        assert self._workspace is not None
        await self._publish("phase_started", phase="planning")

        if self._should_use_agent_session():
            plan = await self._run_planning_agent(proposal)
        else:
            assert self._router is not None
            skill_reg = self._registry.get_optional(SkillRegistry)
            planner = ExperimentPlanner(
                router=self._router,
                skill_registry=skill_reg,
                workspace=self._workspace,
                system_prompt=self._config.planning_system_prompt,
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
        self, proposal: RoundtableProposal
    ) -> ExperimentPlan:
        """Run planning via Claude Code agent session."""
        from aurora.models.experiment import ExperimentJob, DependencyEdge

        plan_path = self._workspace.experiment_plan_path_for_round(self._current_round)
        user_msg = (
            f"## Research Proposal\n\n"
            f"**Title:** {proposal.title}\n\n"
            f"**Description:** {proposal.description}\n\n"
            f"**Research Questions:**\n"
            + "\n".join(f"  - {q}" for q in proposal.research_questions)
            + "\n\n"
            f"**Proposed Methods:**\n"
            + "\n".join(f"  - {m}" for m in proposal.proposed_methods)
            + "\n\n"
            f"## Instructions\n\n"
            f"1. Explore the workspace and environment to understand what's available.\n"
            f"2. Decompose this proposal into concrete executable jobs.\n"
            f"3. Write the plan as a JSON file to: {plan_path}\n\n"
            f"The JSON should have this structure:\n"
            f'{{"plan_id": "<unique-id>", "proposal_id": "{proposal.proposal_id}", '
            f'"jobs": [{{"job_id": "...", "name": "...", "skill_name": "...", '
            f'"parameters": {{}}, "depends_on": []}}], '
            f'"dependency_edges": [{{"from_job": "...", "to_job": "..."}}]}}\n\n'
            f"When done, call task_complete."
        )

        result = await self._run_agent_session(
            "planning",
            self._config.planning_system_prompt,
            user_msg,
            max_turns=self._config.max_agentic_turns,
        )
        self._last_phase_session_id = result.get("session_id")

        try:
            plan = self._workspace.read_json(
                plan_path, ExperimentPlan
            )
            return plan
        except Exception:
            pass

        # Fallback: parse from agent output
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        return ExperimentPlan(
            plan_id=plan_id,
            proposal_id=proposal.proposal_id,
            jobs=[ExperimentJob(
                job_id=f"job_{uuid.uuid4().hex[:8]}",
                name="main_task",
                skill_name="terminal_execution",
                parameters={"instruction": proposal.description},
            )],
            dependency_edges=[],
        )

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

    async def _run_research_loop(
        self,
        plan: ExperimentPlan,
        completed_jobs: list[str] | None = None,
    ) -> list[IterationResult]:
        """Execute the experiment DAG using the researcher's inner loop."""
        assert self._router is not None
        assert self._ctx is not None
        assert self._mm is not None
        await self._publish("phase_started", phase="research_loop")

        max_iters = self._config.max_research_iterations
        dag = DependencyGraph(plan)

        # Restore previously completed jobs when resuming from checkpoint
        if completed_jobs:
            dag.restore_completed(completed_jobs)
            logger.info(
                "Restored %d completed jobs from checkpoint", len(completed_jobs)
            )

        # Choose execution mode: terminal > duplex > agentic > legacy
        # the agentic loop talks to the Anthropic API directly; a mock researcher
        # (offline demos and tests) goes through the router-based legacy iteration
        mock_researcher = self._config.llm_assignment.researcher.startswith("mock")
        if mock_researcher and self._config.use_agentic_researcher:
            logger.info("Researcher model %s is a mock; using the legacy research iteration",
                        self._config.llm_assignment.researcher)
        if self._config.use_terminal_iteration and self._execution_backend is not None:
            from aurora.core.terminal_iteration import TerminalIteration
            logger.info("Using terminal iteration runner")
            transcript_dir = (
                self._workspace.phase_dir("research")
                if self._workspace else None
            )
            async def _agent_text_callback(text: str) -> None:
                """Forward agent text to Front Desk only when awaiting human review."""
                if self._frontdesk and self._awaiting_agent_response and text.strip():
                    self._awaiting_agent_response = False
                    await self._frontdesk.notify_agent_response(text)

            iteration_runner = TerminalIteration(
                config=self._config,
                backend=self._execution_backend,
                event_bus=self._bus,
                system_prompt=self._terminal_system_prompt,
                task_instruction=self._terminal_task_instruction,
                artifact_store=self._store,
                prior_phase_context=self._build_prior_context_prompt("research"),
                transcript_dir=transcript_dir,
                text_callback=_agent_text_callback if self._config.use_frontdesk else None,
                cwd=str(self._workspace.root) if self._workspace else None,
            )
            self.current_iteration_runner = iteration_runner

            # Start Supervisor if enabled
            if self._config.use_supervisor and transcript_dir:
                await self._start_supervisor(str(transcript_dir / "research_loop.jsonl"))

            # Start Watchdog if set
            if self._watchdog is not None:
                await self._start_watchdog()

            # Start Front Desk Agent if enabled
            if self._config.use_frontdesk:
                await self._start_frontdesk()
                self._update_frontdesk_status(
                    phase="research_loop",
                    max_iterations=max_iters,
                    agent_status="running",
                )

            # Subscribe to EventBus for live status updates to Front Desk
            async def _frontdesk_event_listener(event: Event) -> None:
                if not self._frontdesk:
                    return
                if event.name == "phase_started":
                    self._update_frontdesk_status(
                        agent_status="running",
                    )
                elif event.name == "verifier_result":
                    self._update_frontdesk_status(
                        verifier_score=event.data.get("reward"),
                    )
                elif event.name in ("supervisor_advise", "supervisor_continue"):
                    self._update_frontdesk_status(
                        supervisor_status=event.name.replace("supervisor_", ""),
                    )
                elif event.name == "supervisor_escalate":
                    self._update_frontdesk_status(
                        supervisor_status="escalated",
                        pending_escalations=len(self._escalation_queues),
                    )

            self._bus.subscribe_all(_frontdesk_event_listener)
        elif self._config.use_duplex_mode and self._config.use_agentic_researcher and not mock_researcher:
            logger.info("Using duplex researcher (Cerebellum-Brain architecture)")
            iteration_runner = DuplexResearchIteration(
                config=self._config,
                router=self._router,
                artifact_store=self._store,
                context_builder=self._ctx,
                mental_model_mgr=self._mm,
                event_bus=self._bus,
                tool_registry=self._registry.get_optional(ToolRegistry),
                hook_registry=self._hooks,
                checkpoint_handler=self._checkpoint_handler,
                chat_handler=self._chat_handler,
            )
        elif self._config.use_agentic_researcher and not mock_researcher:
            logger.info("Using agentic researcher (Claude tool_use loop)")
            iteration_runner = AgenticResearchIteration(
                config=self._config,
                router=self._router,
                artifact_store=self._store,
                context_builder=self._ctx,
                mental_model_mgr=self._mm,
                event_bus=self._bus,
                tool_registry=self._registry.get_optional(ToolRegistry),
                hook_registry=self._hooks,
                checkpoint_handler=self._checkpoint_handler,
                chat_handler=self._chat_handler,
            )
        else:
            logger.info("Using legacy researcher (prompt-and-parse)")
            iteration_runner = ResearchIteration(
                config=self._config,
                router=self._router,
                artifact_store=self._store,
                context_builder=self._ctx,
                mental_model_mgr=self._mm,
                event_bus=self._bus,
                skill_registry=self._registry.get_optional(SkillRegistry),
                tool_registry=self._registry.get_optional(ToolRegistry),
                hook_registry=self._hooks,
                checkpoint_handler=self._checkpoint_handler,
            )

        results: list[IterationResult] = []
        iteration_count = 0
        max_iters = self._config.max_research_iterations
        max_concurrent = self._config.resource_limits.max_concurrent_simulations

        while (
            not dag.is_complete()
            and not dag.is_blocked()
            and iteration_count < max_iters
        ):
            # Check pause state before each batch
            await self._wait_if_paused()
            await self._drain_chat_messages()

            ready = dag.ready_jobs()
            if not ready:
                logger.info("No ready jobs — waiting or blocked")
                break

            batch = ready[:max_concurrent]
            tasks = [
                iteration_runner.run(job, plan, iteration_count + i)
                for i, job in enumerate(batch)
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for job, result in zip(batch, batch_results):
                iteration_count += 1
                self._update_frontdesk_status(
                    iteration=iteration_count,
                    agent_status="running" if not isinstance(result, Exception) else "error",
                )
                if isinstance(result, Exception):
                    logger.error("Job %s raised: %s", job.job_id, result)
                    dag.mark_failed(job.job_id, [str(result)])
                    results.append(
                        IterationResult(
                            iteration_id=f"iter_err_{iteration_count}",
                            job_id=job.job_id,
                            decision="error",
                            error=str(result),
                        )
                    )
                else:
                    results.append(result)

                    # Record result in ArtifactStore so downstream phases see data
                    self._record_iteration_result(job, result)

                    # Handle BLOCKED decisions via checkpoint
                    if (
                        result.decision == IterationDecision.BLOCKED.value
                        and self._checkpoint_handler is not None
                    ):
                        block_decision = await self._request_checkpoint(
                            event_type="iteration_blocked",
                            summary=(
                                f"Iteration for job '{job.job_id}' is BLOCKED. "
                                f"Error: {result.error or 'Hook required human intervention.'}"
                            ),
                            proposed_action="Review and decide whether to continue, replan, or terminate.",
                            risk_assessment="high",
                            recommendation="review",
                        )
                        if block_decision == CheckpointDecision.TERMINATE:
                            logger.info("Human terminated campaign at blocked checkpoint")
                            dag.mark_failed(job.job_id, ["Terminated by human"])
                            # Break out of batch processing
                            break
                        elif block_decision == CheckpointDecision.APPROVE:
                            # Human approved continuing despite the block
                            if result.job_result and result.job_result.success:
                                dag.mark_complete(job.job_id, result.job_result)
                            else:
                                dag.mark_failed(
                                    job.job_id,
                                    result.job_result.errors
                                    if result.job_result
                                    else ["Blocked but approved"],
                                )
                            continue
                        # REJECT / MODIFY — mark as failed and continue
                        dag.mark_failed(job.job_id, ["Rejected by human at checkpoint"])
                        continue

                    if result.job_result and result.job_result.success:
                        dag.mark_complete(job.job_id, result.job_result)
                    else:
                        dag.mark_failed(
                            job.job_id,
                            (
                                result.job_result.errors
                                if result.job_result
                                else ["Unknown error"]
                            ),
                        )

            # Fire PERIODIC hooks after each batch
            artifacts = self._store.load() if self._store else None
            periodic_ctx = HookContext(
                hook_point=HookPoint.PERIODIC,
                iteration=iteration_count,
                data={
                    "state_summary": (
                        f"Iteration {iteration_count}: "
                        f"{len(dag.completed_job_ids())} jobs completed, "
                        f"DAG complete={dag.is_complete()}"
                    ),
                    "recent_events": [
                        {"job_id": r.job_id, "decision": r.decision}
                        for r in results[-len(batch):]
                    ],
                },
                metadata={"campaign_id": self._config.campaign_id},
            )
            await self._hooks.fire(HookPoint.PERIODIC, periodic_ctx)

            # Save a lightweight checkpoint after each batch so that
            # a crash mid-loop does not lose progress.
            last_iter_id = (
                results[-1].iteration_id if results else None
            )
            self._save_checkpoint(
                CampaignPhase.RESEARCH_LOOP,
                plan_id=plan.plan_id,
                completed_jobs=dag.completed_job_ids(),
                iteration=iteration_count,
                current_iteration_id=last_iter_id,
            )

        # Update status before stopping
        self._update_frontdesk_status(agent_status="completed")

        # Stop Supervisor, Watchdog, and Front Desk after research loop completes
        await self._stop_supervisor()
        await self._stop_watchdog()
        await self._stop_frontdesk()

        if hasattr(iteration_runner, 'transcript_path') and iteration_runner.transcript_path:
            self._phase_transcripts.append({
                "phase": "research_loop",
                "path": iteration_runner.transcript_path,
            })

        if hasattr(iteration_runner, 'session_id') and iteration_runner.session_id:
            self._session_id = iteration_runner.session_id

        await self._publish(
            "research_loop_complete",
            iterations=len(results),
            completed=dag.is_complete(),
        )
        return results

    # ------------------------------------------------------------------
    # Supervisor lifecycle
    # ------------------------------------------------------------------

    async def _start_supervisor(self, transcript_path: str) -> None:
        """Start the Supervisor to monitor the main agent."""
        import os
        from aurora.core.supervisor import Supervisor

        async def advise_callback(msg: str) -> None:
            """Inject supervisor's advice into the main agent's conversation."""
            if self.current_iteration_runner and hasattr(
                self.current_iteration_runner, "inject_message"
            ):
                await self.current_iteration_runner.inject_message(msg)

        async def broadcast_callback(data: dict) -> None:
            event_type = data.get("event_type", "supervisor_event")
            await self._bus.publish(Event(
                name=event_type,
                data=data.get("data", {}),
                source="supervisor",
            ))
            # Route escalations through the Front Desk blocking flow
            if event_type == "supervisor_escalate" and self._frontdesk:
                event_data = data.get("data", {})
                asyncio.create_task(self.handle_escalation(
                    reason=event_data.get("reason", event_data.get("message", "")),
                    suggestion=event_data.get("suggestion", ""),
                ))

        run_dir = (
            self._workspace.phase_dir("supervisor") / f"round_{self._current_round}"
            if self._workspace
            else Path(f"/tmp/supervisor_{self._config.campaign_id}")
        )

        self._supervisor = Supervisor(
            run_dir=run_dir,
            task_description=self._config.objective,
            model=self._config.supervisor_model,
            api_key=self._config.terminal_api_key or os.environ.get("OPENROUTER_API_KEY", ""),
            interrupt_callback=advise_callback,
            broadcast_callback=broadcast_callback,
        )
        await self._supervisor.start(transcript_path)
        logger.info("Supervisor started for research loop")

    async def _stop_supervisor(self) -> None:
        """Stop the Supervisor if running."""
        if self._supervisor is not None:
            await self._supervisor.stop()
            self._supervisor = None
            logger.info("Supervisor stopped")

    # ------------------------------------------------------------------
    # Watchdog lifecycle
    # ------------------------------------------------------------------

    async def _start_watchdog(self) -> None:
        """Start the watchdog and subscribe it to engine events."""
        if self._watchdog is None:
            return

        async def _watchdog_interrupt(message: str) -> None:
            """Inject watchdog interrupt into the main agent."""
            if self.current_iteration_runner and hasattr(
                self.current_iteration_runner, "inject_message"
            ):
                await self.current_iteration_runner.inject_message(
                    f"[WATCHDOG INTERRUPT — STOP AND READ THIS]\n{message}\n"
                    f"You MUST acknowledge this interrupt and adjust your approach."
                )
            await self._publish("watchdog_interrupt", message=message)

        if hasattr(self._watchdog, "_interrupt_callback"):
            self._watchdog._interrupt_callback = _watchdog_interrupt

        if hasattr(self._watchdog, "on_engine_event"):
            self._bus.subscribe_all(self._watchdog.on_engine_event)

        await self._watchdog.start()
        logger.info("Watchdog started and subscribed to EventBus")

    async def _stop_watchdog(self) -> None:
        """Stop the watchdog if running."""
        if self._watchdog is not None:
            try:
                await self._watchdog.stop()
            except Exception:
                logger.debug("Watchdog stop failed", exc_info=True)
            logger.info("Watchdog stopped")

    # ------------------------------------------------------------------
    # Front Desk Agent lifecycle
    # ------------------------------------------------------------------

    def _update_frontdesk_status(self, **kwargs: Any) -> None:
        """Push status updates to the Front Desk Agent (if active)."""
        if self._frontdesk is not None:
            self._frontdesk.update_status(**kwargs)

    async def _start_frontdesk(self) -> None:
        """Start the Front Desk Agent for human interaction."""
        import os
        from aurora.human.frontdesk import FrontDeskAgent, Escalation

        async def on_relay(message: str) -> dict[str, Any]:
            """Handle relay_to_agent: interrupt agent, inject instruction, wait for approval."""
            if not self.current_iteration_runner:
                return {"status": "error", "reason": "no agent running"}

            # Interrupt the agent
            if hasattr(self.current_iteration_runner, "interrupt"):
                self.current_iteration_runner.interrupt()

            # Create approval queue for the negotiation loop
            approval_id = f"approval_{uuid.uuid4().hex[:8]}"
            self._approval_queues[approval_id] = asyncio.Queue(maxsize=1)

            # Set flag so next agent text output is forwarded to Front Desk
            self._awaiting_agent_response = True

            # Inject human instruction with mandatory acknowledgement requirement
            instruction_prompt = (
                f"[Human Instruction — MANDATORY]\n\n"
                f"{message}\n\n"
                f"You MUST:\n"
                f"1. Acknowledge receipt of this instruction\n"
                f"2. Explain how you will adjust your approach\n"
                f"3. Wait — your proposed plan needs human approval before you continue"
            )
            if hasattr(self.current_iteration_runner, "inject_message"):
                await self.current_iteration_runner.inject_message(instruction_prompt)

            return {
                "status": "relayed",
                "approval_id": approval_id,
                "message": "Instruction sent to agent. Agent will propose a plan for approval.",
            }

        async def on_escalation_response(
            escalation_id: str, decision: str, message: str,
        ) -> dict[str, Any]:
            """Handle respond_escalation: unblock the Engine."""
            queue = self._escalation_queues.get(escalation_id)
            if not queue:
                return {"status": "error", "reason": f"no pending escalation {escalation_id}"}
            await queue.put({
                "decision": decision,
                "message": message,
            })
            if self._frontdesk:
                self._frontdesk.clear_escalation(escalation_id)
            return {"status": "delivered", "escalation_id": escalation_id}

        async def on_interrupt(reason: str) -> dict[str, Any]:
            """Handle request_interrupt: interrupt the main agent."""
            if not self.current_iteration_runner:
                return {"status": "error", "reason": "no agent running"}
            if hasattr(self.current_iteration_runner, "interrupt"):
                self.current_iteration_runner.interrupt()
            logger.info("Agent interrupted by human request: %s", reason)
            return {"status": "interrupted", "reason": reason}

        self._frontdesk = FrontDeskAgent(
            model=self._config.frontdesk_model,
            workspace_root=str(self._workspace.root) if self._workspace else ".",
            broadcast_fn=self._frontdesk_broadcast,
            on_relay=on_relay,
            on_escalation_response=on_escalation_response,
            on_interrupt=on_interrupt,
        )
        await self._frontdesk.start()
        logger.info("Front Desk Agent started")

    async def _stop_frontdesk(self) -> None:
        """Stop the Front Desk Agent if running."""
        if self._frontdesk is not None:
            await self._frontdesk.stop()
            self._frontdesk = None
            logger.info("Front Desk Agent stopped")

    async def _frontdesk_broadcast(self, data: dict[str, Any]) -> None:
        """Forward Front Desk events to the EventBus."""
        await self._bus.publish(Event(
            name=data.get("type", "frontdesk_event"),
            data=data.get("data", {}),
            source="frontdesk",
        ))

    async def handle_escalation(
        self,
        reason: str,
        suggestion: str = "",
    ) -> dict[str, Any]:
        """Handle a Supervisor escalation with the full blocking negotiation loop.

        1. Interrupt the main agent
        2. Notify Front Desk with escalation details
        3. Block until human responds via Front Desk
        4. Inject human's decision into agent
        5. Agent proposes plan → human approves/modifies/rejects
        6. Loop until approved or rejected

        Returns the final decision.
        """
        from aurora.human.frontdesk import Escalation

        escalation_id = f"esc_{uuid.uuid4().hex[:8]}"
        response_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._escalation_queues[escalation_id] = response_queue

        # Step 1: Interrupt the agent
        if self.current_iteration_runner and hasattr(self.current_iteration_runner, "interrupt"):
            self.current_iteration_runner.interrupt()
            logger.info("Agent interrupted for escalation %s", escalation_id)
        self._update_frontdesk_status(
            agent_status="interrupted",
            pending_escalations=len(self._escalation_queues),
        )

        # Step 2: Notify Front Desk
        if self._frontdesk:
            escalation = Escalation(
                escalation_id=escalation_id,
                context="Supervisor escalation",
                supervisor_reason=reason,
                supervisor_suggestion=suggestion,
                response_queue=response_queue,
            )
            await self._frontdesk.notify_escalation(escalation)

        # Step 3: Block until human responds
        logger.info("Escalation %s: waiting for human response", escalation_id)
        response = await response_queue.get()
        decision = response.get("decision", "approve")
        human_message = response.get("message", "")

        # Clean up
        self._escalation_queues.pop(escalation_id, None)

        if decision == "reject":
            logger.info("Escalation %s: human rejected — stopping agent", escalation_id)
            return {"decision": "reject", "escalation_id": escalation_id}

        # Steps 4-6: Negotiation loop — agent proposes, human approves
        while True:
            # Inject human's decision into agent and get agent's proposed plan
            instruction = (
                f"[Escalation Resolution — Human Decision]\n\n"
                f"The system was paused because: {reason}\n"
                f"Human's decision: {decision}\n"
            )
            if human_message:
                instruction += f"Human's guidance: {human_message}\n"
            instruction += (
                f"\nYou MUST:\n"
                f"1. Acknowledge this decision\n"
                f"2. Propose how you will adjust your approach\n"
                f"3. Wait for approval before continuing"
            )

            # Set flag so next agent text is forwarded to Front Desk
            self._awaiting_agent_response = True

            if self.current_iteration_runner and hasattr(
                self.current_iteration_runner, "inject_message"
            ):
                await self.current_iteration_runner.inject_message(instruction)

            # Wait for human approval of agent's proposed plan
            # Agent's response text is captured via text_callback →
            # _awaiting_agent_response → Front Desk.notify_agent_response()
            approval_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
            approval_id = f"approval_{uuid.uuid4().hex[:8]}"
            self._approval_queues[approval_id] = approval_queue

            approval = await approval_queue.get()
            self._approval_queues.pop(approval_id, None)
            approval_decision = approval.get("decision", "approve")

            if approval_decision == "approve":
                logger.info("Escalation %s: human approved agent's plan", escalation_id)
                return {"decision": "approve", "escalation_id": escalation_id}
            elif approval_decision == "reject":
                logger.info("Escalation %s: human rejected agent's plan", escalation_id)
                return {"decision": "reject", "escalation_id": escalation_id}
            else:
                # modify — loop back with new instructions
                decision = approval_decision
                human_message = approval.get("message", "")
                logger.info(
                    "Escalation %s: human requests modification — looping",
                    escalation_id,
                )

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
            custom_system_prompt=self._config.analysis_system_prompt,
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
                target = action.payload.get("target_phase", "research_loop")
                try:
                    loop_back_to = CampaignPhase(target)
                except ValueError:
                    loop_back_to = CampaignPhase.RESEARCH_LOOP
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
        """Run analysis via Claude Code agent session."""
        assert self._workspace is not None

        artifacts = self._store.load() if self._store else None
        if artifacts is not None:
            experiments_summary = "\n".join(
                f"- {e.job_id}: {e.status} (skill: {e.skill_used})"
                for e in artifacts.experiments
            ) or "(no experiments recorded)"
            observations_summary = "\n".join(
                f"- {o.observation_id}: {o.description[:100]}"
                for o in artifacts.observations
            ) or "(no observations)"
            goal_text = artifacts.current_goal
        else:
            experiments_summary = "(artifact store disabled)"
            observations_summary = "(artifact store disabled)"
            goal_text = self._config.objective

        user_msg = (
            f"## Research Goal\n{goal_text}\n\n"
            f"## Experiments Run\n{experiments_summary}\n\n"
            f"## Observations\n{observations_summary}\n\n"
            f"## Instructions\n"
            f"Analyze the results in the workspace. You can read result files, "
            f"run analysis scripts, and inspect outputs.\n\n"
            f"After analysis, write your verdict as a JSON file to: "
            f"{self._workspace.analysis_verdict_path(self._current_round)}\n\n"
            f"The JSON should have:\n"
            f'{{"sufficient": true/false, "target_phase": "research_loop"|"planning"|"screening", '
            f'"reason": "...", "findings": ["..."]}}\n\n'
            f'Set "sufficient": true if results meet the goal (proceed to writing).\n'
            f'Set "sufficient": false and specify "target_phase" to loop back.\n\n'
            f"When done, call task_complete."
        )

        result = await self._run_agent_session(
            "analysis",
            self._config.analysis_system_prompt,
            user_msg,
            max_turns=self._config.max_agentic_turns,
        )
        self._last_phase_session_id = result.get("session_id")

        # Read the verdict file
        verdict_path = self._workspace.analysis_verdict_path(self._current_round)
        try:
            verdict = json.loads(verdict_path.read_text())
            if verdict.get("sufficient", True):
                return None
            target = verdict.get("target_phase", "research_loop")
            try:
                return CampaignPhase(target)
            except ValueError:
                return CampaignPhase.RESEARCH_LOOP
        except Exception:
            logger.warning("Could not read analysis verdict, proceeding to writing")
            return None

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

        await self._publish(
            "writing_complete",
            version=version,
            path=ms_path,
        )
        return ms_path

    async def _run_writing_api(self) -> str | None:
        """Run writing via single-shot API call (fallback)."""
        assert self._router is not None
        assert self._workspace is not None

        artifacts = self._store.load() if self._store else None
        ctx = self._build_agent_context(artifacts)

        writer = WriterAgent(
            self._router,
            custom_system_prompt=self._config.writing_system_prompt,
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

        artifacts = self._store.load() if self._store else None
        experiments_summary = "\n".join(
            f"- {e.job_id}: {e.status} (skill: {e.skill_used})"
            for e in artifacts.experiments
        ) or "(no experiments recorded)"
        observations_summary = "\n".join(
            f"- {o.observation_id}: {o.description[:100]}"
            for o in artifacts.observations
        ) or "(no observations)"
        claims_summary = "\n".join(
            f"- [{c.status}] {c.statement[:100]}"
            for c in artifacts.claims
        ) or "(no claims)"

        versions = self._list_manuscript_versions()
        version = max(versions, default=0) + 1
        ms_path = self._workspace.manuscript_path(version)

        user_msg = (
            f"## Research Goal\n{artifacts.current_goal}\n\n"
            f"## Experiments\n{experiments_summary}\n\n"
            f"## Observations\n{observations_summary}\n\n"
            f"## Claims\n{claims_summary}\n\n"
            f"## Instructions\n"
            f"Read the experiment results and data files in the workspace. "
            f"Write a comprehensive report based on the research findings.\n\n"
            f"Save the report to: {ms_path}\n\n"
            f"When done, call task_complete."
        )

        result = await self._run_agent_session(
            "writing",
            self._config.writing_system_prompt,
            user_msg,
            max_turns=self._config.max_agentic_turns,
        )
        self._last_phase_session_id = result.get("session_id")

        if ms_path.exists() if isinstance(ms_path, Path) else Path(str(ms_path)).exists():
            return str(ms_path)
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
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
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

        if result.job_result and result.job_result.metrics:
            from aurora.models.artifacts import Observation
            obs = Observation(
                observation_id=f"obs_{result.iteration_id}",
                source_job_id=job.job_id,
                description=(
                    f"Job {job.job_id}: reward={result.job_result.metrics.get('reward', 'N/A')}, "
                    f"turns={result.job_result.metrics.get('turns', 'N/A')}, "
                    f"wall_time={result.wall_time_seconds:.1f}s"
                ),
                numerical_values={
                    k: float(v) for k, v in result.job_result.metrics.items()
                    if isinstance(v, (int, float))
                },
                timestamp=datetime.now(timezone.utc),
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
    ) -> None:
        if self._workspace is None:
            return
        cp = Checkpoint(
            campaign_id=self._config.campaign_id,
            phase=phase.value,
            iteration=iteration,
            research_cycle=research_cycle,
            proposal_id=proposal_id,
            plan_id=plan_id,
            completed_jobs=completed_jobs or [],
            current_iteration_id=current_iteration_id,
            timestamp=datetime.now(timezone.utc),
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
            rd = self._workspace.phases_dir / "roundtable" / f"round_{rn}"
            if not rd.exists():
                continue
            jsons = sorted(
                p for p in rd.glob("*.json") if p.name != "transcript.jsonl"
            )
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

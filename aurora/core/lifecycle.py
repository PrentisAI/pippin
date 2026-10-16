"""Research iteration lifecycle (Section C, Figure 1).

Three execution modes:

**Legacy** (:class:`ResearchIteration`):
  Engine-mediated: prompt → parse JSON → engine executes skill → LLM assesses.

**Agentic** (:class:`AgenticResearchIteration`):
  Claude autonomously calls tools (DFT, ASE, MLIP, …), records observations,
  proposes claims, and decides next steps — all within a single tool_use loop.

**Duplex** (:class:`DuplexResearchIteration`):
  Cerebellum-Brain architecture: lightweight Cerebellum handles real-time user
  chat while the Brain runs heavy computation as background asyncio.Tasks.
  The user can chat, modify, or cancel tasks at any time — even during DFT.

The engine chooses the mode based on ``config.use_agentic_researcher``
and ``config.use_duplex_mode``.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from aurora.config import AuroraConfig, PipelineStage
from aurora.core.events import Event, EventBus
from aurora.llm.base import Message
from aurora.llm.router import LLMRouter
from aurora.models.artifacts import ExperimentRecord, Observation, ResearchArtifacts
from aurora.models.audit import AuditOutcome, AuditRecord, AuditTrigger
from aurora.models.experiment import ExperimentJob, ExperimentPlan, JobResult
from aurora.hooks.base import HookContext, HookPoint, HookRegistry, HookResult
from aurora.human.checkpoint import CheckpointDecision
from aurora.human.web_checkpoint import WebCheckpointHandler
from aurora.state.artifacts import ArtifactStore
from aurora.state.context import ContextReconstructor
from aurora.state.mental_model import MentalModelManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class IterationDecision(str, Enum):
    """What the researcher decides after one iteration."""
    CONTINUE = "continue"          # pick next ready job
    AUDIT_REQUESTED = "audit"      # ask for JIT audit
    REPLAN = "replan"              # revise experiment plan
    FOLLOW_UP = "follow_up"        # writer triggered a new cycle
    DONE = "done"                  # all jobs complete
    BLOCKED = "blocked"            # no ready jobs, not all done
    ERROR = "error"                # unrecoverable failure


class IterationStage(str, Enum):
    """Stages within a single research iteration for checkpoint granularity."""
    CONTEXT = "context"
    MENTAL_MODEL = "mental_model"
    EXECUTION = "execution"
    ASSESSMENT = "assessment"
    RECORDING = "recording"
    DECISION = "decision"


class IterationResult(BaseModel):
    """Outcome of a single research iteration."""
    iteration_id: str
    job_id: str
    decision: str  # IterationDecision value
    job_result: JobResult | None = None
    observations: list[Observation] = Field(default_factory=list)
    audit_record: AuditRecord | None = None
    error: str | None = None
    wall_time_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Structured LLM outputs for the researcher's inner loop
# ---------------------------------------------------------------------------

class SkillSelection(BaseModel):
    """LLM-generated skill and parameter selection."""
    skill_name: str = "structure_construction"
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class ResultAssessment(BaseModel):
    """LLM-generated assessment of a job's results."""
    success: bool = True
    observations: list[dict[str, Any]] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    next_action: str = "continue"  # continue | audit | replan | done
    reasoning: str = ""


# ---------------------------------------------------------------------------
# ResearchIteration
# ---------------------------------------------------------------------------

class ResearchIteration:
    """Execute one step of the researcher's inner loop.

    Parameters
    ----------
    config : AuroraConfig
        Campaign configuration.
    router : LLMRouter
        Multi-model LLM router.
    artifact_store : ArtifactStore
        Persistent research artifact gateway.
    context_builder : ContextReconstructor
        On-demand context assembly.
    mental_model_mgr : MentalModelManager
        Reconstructs the scientific mental model.
    event_bus : EventBus
        Publish events for hooks and UI.
    skill_registry : Any
        ``SkillRegistry`` instance (typed as Any to avoid circular imports).
    tool_registry : Any
        ``ToolRegistry`` instance.
    hook_registry : HookRegistry | None
        Optional hook registry for firing JIT audit hooks during the
        iteration lifecycle.
    """

    def __init__(
        self,
        config: AuroraConfig,
        router: LLMRouter,
        artifact_store: ArtifactStore,
        context_builder: ContextReconstructor,
        mental_model_mgr: MentalModelManager,
        event_bus: EventBus,
        skill_registry: Any = None,
        tool_registry: Any = None,
        hook_registry: HookRegistry | None = None,
        checkpoint_handler: WebCheckpointHandler | None = None,
    ) -> None:
        self._config = config
        self._router = router
        self._store = artifact_store
        self._ctx = context_builder
        self._mm = mental_model_mgr
        self._bus = event_bus
        self._skills = skill_registry
        self._tools = tool_registry
        self._hooks = hook_registry
        self._checkpoint_handler = checkpoint_handler

    def _save_stage_checkpoint(self, iter_id: str, stage: IterationStage, data: dict) -> None:
        """Save a stage-level checkpoint within an iteration."""
        if not hasattr(self._store, '_ws') or self._store._ws is None:
            return
        checkpoint_dir = self._store._ws.campaign_dir / "iterations" / iter_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_data = {
            "iteration_id": iter_id,
            "stage": stage.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        checkpoint_path = checkpoint_dir / "stage_checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data, default=str), encoding="utf-8")

    def _load_stage_checkpoint(self, iter_id: str) -> dict | None:
        """Load a stage checkpoint if one exists."""
        if not hasattr(self._store, '_ws') or self._store._ws is None:
            return None
        checkpoint_path = self._store._ws.campaign_dir / "iterations" / iter_id / "stage_checkpoint.json"
        if not checkpoint_path.exists():
            return None
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))

    async def _fire_hooks(
        self,
        hook_point: HookPoint,
        data: dict[str, Any],
        iteration: int,
    ) -> list[HookResult]:
        """Fire hooks and check for critical failures or intervention requests.

        Parameters
        ----------
        hook_point:
            The pipeline point being fired.
        data:
            Payload specific to the hook point.
        iteration:
            Current iteration counter.

        Returns
        -------
        list[HookResult]
            Results from all matching hooks.

        Side Effects
        ------------
        Sets ``self._hook_decision_override`` to
        :data:`IterationDecision.AUDIT_REQUESTED` if any result has
        ``passed=False`` and ``severity="critical"``, or to
        :data:`IterationDecision.BLOCKED` if any result has
        ``human_intervention_required=True``.
        """
        if self._hooks is None:
            return []

        ctx = HookContext(
            hook_point=hook_point,
            iteration=iteration,
            data=data,
            metadata={"campaign_id": self._config.campaign_id},
        )
        results = await self._hooks.fire(hook_point, ctx)

        for r in results:
            if r.human_intervention_required:
                self._hook_decision_override = IterationDecision.BLOCKED
                logger.warning(
                    "Hook %r requires human intervention at %s",
                    r.hook_name, hook_point.value,
                )
            elif not r.passed and r.severity == "critical":
                # Only upgrade to AUDIT_REQUESTED if not already BLOCKED
                if self._hook_decision_override != IterationDecision.BLOCKED:
                    self._hook_decision_override = IterationDecision.AUDIT_REQUESTED
                    logger.warning(
                        "Hook %r triggered audit at %s: %s",
                        r.hook_name, hook_point.value, r.message,
                    )

        return results

    async def run(
        self,
        job: ExperimentJob,
        plan: ExperimentPlan,
        iteration_number: int = 0,
        *,
        resume_iter_id: str | None = None,
    ) -> IterationResult:
        """Execute one research iteration for *job*.

        Parameters
        ----------
        resume_iter_id:
            If set, attempt to resume a partially completed iteration
            from its last stage checkpoint.

        Stages
        ------
        1. Reconstruct context
        2. Load current artifacts & mental model
        3. Execute the job (invoke skill)
        4. Parse & assess results via LLM
        5. Record observations
        6. Decide next action
        """
        # Resume support: reload stage checkpoint if available
        resume_stage: IterationStage | None = None
        resume_data: dict[str, Any] = {}
        if resume_iter_id:
            saved = self._load_stage_checkpoint(resume_iter_id)
            if saved:
                resume_stage = IterationStage(saved["stage"])
                resume_data = saved
                logger.info(
                    "Resuming iteration %s from stage %s",
                    resume_iter_id, resume_stage.value,
                )

        iter_id = resume_iter_id or f"iter_{uuid.uuid4().hex[:8]}"
        start = datetime.now(timezone.utc)
        self._hook_decision_override: IterationDecision | None = None

        # Stage ordering for skip logic
        _stage_order = list(IterationStage)

        def _past_stage(stage: IterationStage) -> bool:
            """True if *stage* was already completed in a prior run."""
            if resume_stage is None:
                return False
            return _stage_order.index(stage) <= _stage_order.index(resume_stage)

        try:
            # Stage 1-2: Context reconstruction
            artifacts = self._store.load()

            # Reconstruct mental model from evidence (Section E.3)
            async def _llm_generate(prompt: str) -> str:
                resp = await self._router.generate(
                    PipelineStage.RESEARCHER, [Message.user(prompt)],
                    temperature=0.3,
                )
                return resp.content

            mental_model = await self._mm.reconstruct_from_evidence(
                artifacts, _llm_generate
            )
            context_str = self._ctx.reconstruct(
                task_description=f"Execute job {job.job_id} using skill {job.skill_name}",
                artifacts=artifacts,
                mental_model=mental_model,
                relevant_job_ids=[dep for dep in job.dependencies],
            )

            # Fire MENTAL_MODEL_UPDATED hook
            await self._fire_hooks(
                HookPoint.MENTAL_MODEL_UPDATED,
                {
                    "original_objective": artifacts.initial_objective,
                    "current_goal": artifacts.current_goal,
                },
                iteration_number,
            )

            self._save_stage_checkpoint(iter_id, IterationStage.CONTEXT, {
                "job_id": job.job_id,
            })

            # Stage 3–4: Execute skill (skip if resuming past EXECUTION)
            if _past_stage(IterationStage.EXECUTION):
                logger.info("Skipping execution — already completed in prior run")
                job_result = JobResult(
                    job_id=job.job_id,
                    success=resume_data.get("success", True),
                    metrics=resume_data.get("metrics", {}),
                    wall_time_seconds=0.0,
                )
            else:
                exp_record = ExperimentRecord(
                    job_id=job.job_id,
                    skill_used=job.skill_name,
                    parameters=job.parameters,
                    status="active",
                    started_at=start,
                )
                artifacts = self._store.record_experiment(artifacts, exp_record)

                await self._bus.publish(Event(
                    name="experiment_started",
                    data={"job_id": job.job_id, "skill": job.skill_name},
                    source="research_iteration",
                ))

                self._save_stage_checkpoint(iter_id, IterationStage.MENTAL_MODEL, {
                    "job_id": job.job_id,
                })

                # Fire PRE_EXPERIMENT hook
                await self._fire_hooks(
                    HookPoint.PRE_EXPERIMENT,
                    {
                        "job_id": job.job_id,
                        "skill_name": job.skill_name,
                        "parameters": job.parameters,
                    },
                    iteration_number,
                )

                job_result = await self._execute_skill(job, context_str)

                # Fire POST_EXPERIMENT hook
                await self._fire_hooks(
                    HookPoint.POST_EXPERIMENT,
                    {
                        "success": job_result.success,
                        "job_id": job.job_id,
                        "errors": job_result.errors,
                        "metrics": job_result.metrics,
                    },
                    iteration_number,
                )

                # Fire ENERGY_COMPUTED hook if energy-related metrics are present
                _energy_keys = {"total_energy", "delta_e", "segregation_energy"}
                if job_result.metrics and _energy_keys & set(job_result.metrics):
                    await self._fire_hooks(
                        HookPoint.ENERGY_COMPUTED,
                        {
                            "delta_e": job_result.metrics.get("delta_e", 0.0),
                            "reference_energy": job_result.metrics.get("total_energy"),
                            "computed_energy": job_result.metrics.get("total_energy"),
                        },
                        iteration_number,
                    )

                self._save_stage_checkpoint(iter_id, IterationStage.EXECUTION, {
                    "job_id": job.job_id,
                    "success": job_result.success,
                    "metrics": job_result.metrics,
                })

            # Stage 5: Assess results via LLM (skip if resuming past ASSESSMENT)
            if _past_stage(IterationStage.ASSESSMENT):
                logger.info("Skipping assessment — already completed in prior run")
                assessment = ResultAssessment(
                    success=resume_data.get("assessment_success", True),
                    next_action=resume_data.get("next_action", "continue"),
                )
            else:
                assessment = await self._assess_results(job, job_result, context_str)

                # Fire ANOMALY_DETECTED hook if anomalies were found
                if assessment.anomalies:
                    await self._fire_hooks(
                        HookPoint.ANOMALY_DETECTED,
                        {
                            "anomalies": assessment.anomalies,
                            "job_id": job.job_id,
                            "assessment_reasoning": assessment.reasoning,
                        },
                        iteration_number,
                    )

                self._save_stage_checkpoint(iter_id, IterationStage.ASSESSMENT, {
                    "job_id": job.job_id,
                    "assessment_success": assessment.success,
                    "next_action": assessment.next_action,
                })

            # Stage 6: Record observations
            observations: list[Observation] = []
            for obs_data in assessment.observations:
                obs = Observation(
                    observation_id=f"obs_{uuid.uuid4().hex[:8]}",
                    source_job_id=job.job_id,
                    description=obs_data.get("description", ""),
                    numerical_values=obs_data.get("numerical_values", {}),
                    timestamp=datetime.now(timezone.utc),
                )
                observations.append(obs)
                artifacts = self._store.record_observation(artifacts, obs)

            # Stage 7: Update experiment record as completed
            end = datetime.now(timezone.utc)
            exp_record = ExperimentRecord(
                job_id=job.job_id,
                skill_used=job.skill_name,
                parameters=job.parameters,
                status="completed" if job_result.success else "failed",
                started_at=start,
                completed_at=end,
                result_path=f"experiments/{job.job_id}/result.json",
                failure_diagnosis="; ".join(job_result.errors) if not job_result.success else None,
            )
            if job_result.success:
                artifacts = self._store.record_experiment(artifacts, exp_record)
            else:
                artifacts = self._store.record_failure(
                    artifacts, exp_record, "; ".join(job_result.errors)
                )

            # Fire ITERATION_COMPLETE hook
            await self._fire_hooks(
                HookPoint.ITERATION_COMPLETE,
                {
                    "current_goal": artifacts.current_goal,
                    "claims": [
                        {"status": c.status, "statement": c.statement}
                        for c in artifacts.claims
                    ],
                    "observations": [
                        {"description": o.description}
                        for o in observations
                    ],
                    "current_plan": f"Job {job.job_id} ({job.skill_name})",
                    "recent_results": [
                        {"job_id": job.job_id, "success": job_result.success}
                    ],
                },
                iteration_number,
            )

            self._save_stage_checkpoint(iter_id, IterationStage.RECORDING, {
                "job_id": job.job_id,
                "observations_count": len(observations),
            })

            # Stage 8: Determine decision
            decision = self._map_assessment_to_decision(assessment, job_result)

            # Apply hook-driven decision override if set
            if self._hook_decision_override is not None:
                logger.info(
                    "Hook override: decision changed from %s to %s",
                    decision.value, self._hook_decision_override.value,
                )
                decision = self._hook_decision_override

                # When BLOCKED, request human approval via the checkpoint handler
                if (
                    decision == IterationDecision.BLOCKED
                    and self._checkpoint_handler is not None
                ):
                    request = WebCheckpointHandler.build_request(
                        event_type="hook_intervention",
                        summary=(
                            f"A hook requires human intervention for job "
                            f"'{job.job_id}' ({job.skill_name}). "
                            f"The iteration decision has been set to BLOCKED."
                        ),
                        proposed_action="Review findings and decide: approve to continue, reject to replan, or terminate.",
                        risk_assessment="high",
                        recommendation="review",
                        artifacts=artifacts,
                    )
                    response = await self._checkpoint_handler.request_approval(request)
                    cp_decision = CheckpointDecision(response.decision)

                    if cp_decision == CheckpointDecision.APPROVE:
                        decision = IterationDecision.CONTINUE
                        logger.info("Human approved continuing past BLOCKED state")
                    elif cp_decision == CheckpointDecision.REJECT:
                        decision = IterationDecision.REPLAN
                        logger.info("Human rejected — decision changed to REPLAN")
                    elif cp_decision == CheckpointDecision.TERMINATE:
                        decision = IterationDecision.ERROR
                        logger.info("Human terminated campaign at BLOCKED checkpoint")
                    elif cp_decision == CheckpointDecision.MODIFY:
                        decision = IterationDecision.CONTINUE
                        logger.info(
                            "Human approved with modifications: %s",
                            response.comments[:200],
                        )

            self._save_stage_checkpoint(iter_id, IterationStage.DECISION, {
                "job_id": job.job_id,
                "decision": decision.value,
            })

            await self._bus.publish(Event(
                name="experiment_complete" if job_result.success else "experiment_failed",
                data={
                    "job_id": job.job_id,
                    "success": job_result.success,
                    "decision": decision.value,
                    "observations": len(observations),
                },
                source="research_iteration",
            ))

            wall_time = (end - start).total_seconds()
            return IterationResult(
                iteration_id=iter_id,
                job_id=job.job_id,
                decision=decision.value,
                job_result=job_result,
                observations=observations,
                wall_time_seconds=wall_time,
            )

        except Exception as exc:
            logger.exception("Iteration %s failed for job %s", iter_id, job.job_id)
            return IterationResult(
                iteration_id=iter_id,
                job_id=job.job_id,
                decision=IterationDecision.ERROR.value,
                error=str(exc),
                wall_time_seconds=(datetime.now(timezone.utc) - start).total_seconds(),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_skill(
        self,
        job: ExperimentJob,
        context_str: str,
    ) -> JobResult:
        """Invoke the skill named in *job* and return a JobResult."""
        if self._skills is None:
            logger.warning("No skill registry — returning mock success for %s", job.job_id)
            return JobResult(
                job_id=job.job_id,
                success=True,
                metrics={"mock": 1.0},
                wall_time_seconds=0.1,
            )

        skill = self._skills.get(job.skill_name)
        if skill is None:
            return JobResult(
                job_id=job.job_id,
                success=False,
                errors=[f"Skill '{job.skill_name}' not found in registry"],
            )

        try:
            # Build a skill input and context from the job parameters
            from aurora.skills.base import SkillInput, SkillContext
            skill_input = SkillInput(
                parameters=job.parameters,
                context=context_str,
            )
            skill_ctx = SkillContext(
                workspace_dir=str(self._store._ws.campaign_dir) if hasattr(self._store, '_ws') else ".",
                tool_registry=self._tools,
            )
            result = await skill.run(skill_input, skill_ctx)
            # Extract only numeric metrics from the skill output data
            raw_data = result.output.data if result.output else {}
            numeric_metrics = {
                k: float(v) for k, v in raw_data.items()
                if isinstance(v, (int, float)) and v is not None
            }
            return JobResult(
                job_id=job.job_id,
                success=result.success,
                output_files=getattr(result.output, "files", []),
                metrics=numeric_metrics,
                errors=[result.remediation_hint] if result.remediation_hint else [],
                wall_time_seconds=result.wall_time_seconds,
            )
        except Exception as exc:
            logger.error("Skill execution failed for %s: %s", job.job_id, exc)
            return JobResult(
                job_id=job.job_id,
                success=False,
                errors=[f"Skill execution error: {exc}"],
            )

    async def _assess_results(
        self,
        job: ExperimentJob,
        result: JobResult,
        context_str: str,
    ) -> ResultAssessment:
        """Ask the researcher LLM to assess the job results."""
        prompt = f"""\
You are a computational materials science researcher assessing experiment results.

## Research Context
{context_str}

## Job Executed
- Job ID: {job.job_id}
- Skill: {job.skill_name}
- Parameters: {job.parameters}

## Results
- Success: {result.success}
- Metrics: {result.metrics}
- Errors: {result.errors}
- Output files: {result.output_files}

## Task
Assess these results and extract scientific observations.

Return a JSON object with:
- "success": bool (your assessment of whether results are scientifically valid)
- "observations": list of {{"description": str, "numerical_values": dict}}
- "anomalies": list of strings describing any anomalies
- "next_action": "continue" | "audit" | "replan" | "done"
- "reasoning": brief explanation of your assessment
"""
        try:
            assessment = await self._router.generate_structured(
                PipelineStage.RESEARCHER,
                [Message.user(prompt)],
                ResultAssessment,
                temperature=0.3,
            )
            return assessment
        except Exception as exc:
            logger.warning("LLM assessment failed, using fallback: %s", exc)
            return ResultAssessment(
                success=result.success,
                observations=[{
                    "description": f"Job {job.job_id} {'succeeded' if result.success else 'failed'}",
                    "numerical_values": result.metrics,
                }] if result.success else [],
                anomalies=result.errors,
                next_action="continue" if result.success else "audit",
                reasoning=f"Fallback assessment (LLM unavailable): {exc}",
            )

    @staticmethod
    def _map_assessment_to_decision(
        assessment: ResultAssessment,
        result: JobResult,
    ) -> IterationDecision:
        """Map the LLM's assessment to an iteration decision."""
        action_map = {
            "continue": IterationDecision.CONTINUE,
            "audit": IterationDecision.AUDIT_REQUESTED,
            "replan": IterationDecision.REPLAN,
            "done": IterationDecision.DONE,
            "follow_up": IterationDecision.FOLLOW_UP,
        }
        return action_map.get(
            assessment.next_action,
            IterationDecision.ERROR if not result.success else IterationDecision.CONTINUE,
        )


# ---------------------------------------------------------------------------
# Agentic research iteration (tool_use loop)
# ---------------------------------------------------------------------------


class AgenticResearchIteration:
    """Execute one research iteration via an autonomous tool-use loop.

    Instead of the legacy prompt→parse→execute→assess pipeline, this class
    delegates the entire iteration to an :class:`AgenticAgent` researcher
    that calls tools directly and decides its own workflow.

    The engine still controls:
    - Context reconstruction (mental model, evidence)
    - Hook firing (via tool-call interceptor)
    - Checkpoint/human-in-the-loop approval
    - Provenance tracking
    """

    def __init__(
        self,
        config: AuroraConfig,
        router: LLMRouter,
        artifact_store: ArtifactStore,
        context_builder: ContextReconstructor,
        mental_model_mgr: MentalModelManager,
        event_bus: EventBus,
        tool_registry: Any = None,
        hook_registry: HookRegistry | None = None,
        checkpoint_handler: WebCheckpointHandler | None = None,
        chat_handler: Any = None,
    ) -> None:
        self._config = config
        self._router = router
        self._store = artifact_store
        self._ctx = context_builder
        self._mm = mental_model_mgr
        self._bus = event_bus
        self._tools = tool_registry
        self._hooks = hook_registry
        self._checkpoint_handler = checkpoint_handler
        self._chat_handler = chat_handler

    def _save_stage_checkpoint(self, iter_id: str, stage: IterationStage, data: dict) -> None:
        if not hasattr(self._store, '_ws') or self._store._ws is None:
            return
        checkpoint_dir = self._store._ws.campaign_dir / "iterations" / iter_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_data = {
            "iteration_id": iter_id,
            "stage": stage.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        checkpoint_path = checkpoint_dir / "stage_checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data, default=str), encoding="utf-8")

    def _load_stage_checkpoint(self, iter_id: str) -> dict | None:
        if not hasattr(self._store, '_ws') or self._store._ws is None:
            return None
        checkpoint_path = self._store._ws.campaign_dir / "iterations" / iter_id / "stage_checkpoint.json"
        if not checkpoint_path.exists():
            return None
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))

    async def run(
        self,
        job: ExperimentJob,
        plan: ExperimentPlan,
        iteration_number: int = 0,
        *,
        resume_iter_id: str | None = None,
    ) -> IterationResult:
        """Execute one research iteration via the agentic researcher.

        Parameters
        ----------
        resume_iter_id:
            If set, attempt to resume from a prior stage checkpoint.
            For agentic iterations, only CONTEXT stage can be skipped
            (the agentic loop itself is atomic).

        The researcher agent autonomously:
        1. Reviews current artifacts and mental model
        2. Designs and runs experiments using computational tools
        3. Analyzes results and handles failures
        4. Records observations and proposes claims
        5. Decides the next action
        """
        from aurora.agents.base import AgentContext
        from aurora.agents.researcher import ResearcherAgent
        from aurora.llm.tool_runner import HookInterceptor

        iter_id = resume_iter_id or f"iter_{uuid.uuid4().hex[:8]}"
        start = datetime.now(timezone.utc)

        try:
            # Stage 1: Reconstruct context and mental model
            artifacts = self._store.load()

            async def _llm_generate(prompt: str) -> str:
                resp = await self._router.generate(
                    PipelineStage.RESEARCHER, [Message.user(prompt)],
                    temperature=0.3,
                )
                return resp.content

            mental_model = await self._mm.reconstruct_from_evidence(
                artifacts, _llm_generate
            )
            context_str = self._ctx.reconstruct(
                task_description=(
                    f"Research iteration #{iteration_number}. "
                    f"Current job context: {job.job_id} ({job.skill_name}). "
                    f"You have full autonomy to design and execute experiments."
                ),
                artifacts=artifacts,
                mental_model=mental_model,
                relevant_job_ids=[dep for dep in job.dependencies],
            )

            self._save_stage_checkpoint(iter_id, IterationStage.CONTEXT, {
                "job_id": job.job_id,
            })

            # Stage 2: Build the hook interceptor
            hook_decision_override: IterationDecision | None = None

            async def _hook_interceptor(
                tool_name: str,
                tool_input: dict[str, Any],
                tool_result: str,
            ) -> None:
                nonlocal hook_decision_override
                if self._hooks is None:
                    return

                # Map tool calls to hook points
                if tool_name in ("mock_dft", "dft", "mock_mlip", "mlip", "mock_ase", "ase"):
                    # Fire POST_EXPERIMENT after each computational tool call
                    import json as _json
                    try:
                        parsed = _json.loads(tool_result)
                    except (ValueError, TypeError):
                        parsed = {}

                    ctx = HookContext(
                        hook_point=HookPoint.POST_EXPERIMENT,
                        iteration=iteration_number,
                        data={
                            "job_id": job.job_id,
                            "tool_name": tool_name,
                            "success": parsed.get("success", True),
                            "metrics": parsed.get("output", {}),
                        },
                        metadata={"campaign_id": self._config.campaign_id},
                    )
                    results = await self._hooks.fire(HookPoint.POST_EXPERIMENT, ctx)

                    for r in results:
                        if r.human_intervention_required:
                            hook_decision_override = IterationDecision.BLOCKED
                        elif not r.passed and r.severity == "critical":
                            if hook_decision_override != IterationDecision.BLOCKED:
                                hook_decision_override = IterationDecision.AUDIT_REQUESTED

                    # Check for energy-related hooks
                    energy_keys = {"total_energy", "delta_e", "segregation_energy"}
                    output = parsed.get("output", {})
                    if isinstance(output, dict) and energy_keys & set(output):
                        e_ctx = HookContext(
                            hook_point=HookPoint.ENERGY_COMPUTED,
                            iteration=iteration_number,
                            data={
                                "delta_e": output.get("delta_e", 0.0),
                                "reference_energy": output.get("total_energy"),
                                "computed_energy": output.get("total_energy"),
                            },
                            metadata={"campaign_id": self._config.campaign_id},
                        )
                        await self._hooks.fire(HookPoint.ENERGY_COMPUTED, e_ctx)

            # Stage 3: Create and run the agentic researcher
            workspace_dir = "."
            if hasattr(self._store, '_ws') and self._store._ws is not None:
                workspace_dir = str(self._store._ws.campaign_dir)

            # Resolve chat handler for real-time interaction (A+B+C)
            _user_queue = None
            _stream_cb = None
            if self._chat_handler is not None:
                _user_queue = self._chat_handler.user_queue
                _stream_cb = self._chat_handler.stream_event
                self._chat_handler.set_active(True)

            researcher = ResearcherAgent(
                router=self._router,
                tool_registry=self._tools,
                artifact_store=self._store,
                workspace_dir=workspace_dir,
                max_turns=self._config.max_agentic_turns
                if hasattr(self._config, 'max_agentic_turns')
                else 30,
                hook_interceptor=_hook_interceptor,
                user_message_queue=_user_queue,
                stream_callback=_stream_cb,
            )

            agent_context = AgentContext(
                campaign_id=self._config.campaign_id,
                current_goal=artifacts.current_goal or artifacts.initial_objective,
                research_summary=context_str,
                mental_model_summary=str(mental_model) if mental_model else "",
                recent_experiments=[
                    {"experiment_id": e.job_id, "description": f"{e.skill_used}: {e.status}"}
                    for e in artifacts.experiments[-5:]
                ],
                active_claims=[
                    {"status": c.status, "statement": c.statement, "confidence": c.confidence}
                    for c in artifacts.claims
                    if c.status in ("tentative", "accepted")
                ],
                contradictions=artifacts.contradictions if hasattr(artifacts, 'contradictions') else [],
                iteration_number=iteration_number,
            )

            await self._bus.publish(Event(
                name="agentic_iteration_started",
                data={
                    "iteration_id": iter_id,
                    "job_id": job.job_id,
                    "iteration_number": iteration_number,
                },
                source="agentic_research_iteration",
            ))

            actions = await researcher.run(agent_context)
            agentic_result = researcher.last_agentic_result

            self._save_stage_checkpoint(iter_id, IterationStage.EXECUTION, {
                "job_id": job.job_id,
                "success": bool(agentic_result and agentic_result.decision != "error"),
                "tool_calls": len(agentic_result.tool_calls) if agentic_result else 0,
            })

            # Stage 4: Extract results from the agentic run
            observations: list[Observation] = []
            if agentic_result:
                for obs_data in agentic_result.observations:
                    obs = Observation(
                        observation_id=obs_data.get("observation_id", f"obs_{uuid.uuid4().hex[:8]}"),
                        source_job_id=obs_data.get("source_job_id", job.job_id),
                        description=obs_data.get("description", ""),
                        numerical_values=obs_data.get("numerical_values", {}),
                        timestamp=datetime.now(timezone.utc),
                    )
                    observations.append(obs)

            # Stage 5: Determine decision
            decision_str = "continue"
            if agentic_result:
                decision_str = agentic_result.decision

            decision_map = {
                "continue": IterationDecision.CONTINUE,
                "replan": IterationDecision.REPLAN,
                "audit": IterationDecision.AUDIT_REQUESTED,
                "done": IterationDecision.DONE,
                "follow_up": IterationDecision.FOLLOW_UP,
                "error": IterationDecision.ERROR,
            }
            decision = decision_map.get(decision_str, IterationDecision.CONTINUE)
            if agentic_result is None:
                decision = IterationDecision.ERROR

            # Apply hook-driven override
            if hook_decision_override is not None:
                logger.info(
                    "Hook override: decision changed from %s to %s",
                    decision.value, hook_decision_override.value,
                )
                decision = hook_decision_override

                if (
                    decision == IterationDecision.BLOCKED
                    and self._checkpoint_handler is not None
                ):
                    request = WebCheckpointHandler.build_request(
                        event_type="hook_intervention",
                        summary=(
                            f"A hook requires human intervention during agentic "
                            f"iteration #{iteration_number}. "
                            f"The agent made {len(agentic_result.tool_calls) if agentic_result else 0} "
                            f"tool calls before being blocked."
                        ),
                        proposed_action="Review findings and decide: approve, reject, or terminate.",
                        risk_assessment="high",
                        recommendation="review",
                        artifacts=artifacts,
                    )
                    response = await self._checkpoint_handler.request_approval(request)
                    cp_decision = CheckpointDecision(response.decision)

                    if cp_decision == CheckpointDecision.APPROVE:
                        decision = IterationDecision.CONTINUE
                    elif cp_decision == CheckpointDecision.REJECT:
                        decision = IterationDecision.REPLAN
                    elif cp_decision == CheckpointDecision.TERMINATE:
                        decision = IterationDecision.ERROR
                    elif cp_decision == CheckpointDecision.MODIFY:
                        decision = IterationDecision.CONTINUE

            self._save_stage_checkpoint(iter_id, IterationStage.DECISION, {
                "job_id": job.job_id,
                "decision": decision.value,
            })

            # Fire ITERATION_COMPLETE hook
            if self._hooks is not None:
                iter_ctx = HookContext(
                    hook_point=HookPoint.ITERATION_COMPLETE,
                    iteration=iteration_number,
                    data={
                        "current_goal": artifacts.current_goal,
                        "claims": [
                            {"status": c.status, "statement": c.statement}
                            for c in artifacts.claims
                        ],
                        "observations": [
                            {"description": o.description} for o in observations
                        ],
                        "agentic_tool_calls": len(agentic_result.tool_calls) if agentic_result else 0,
                        "agentic_turns": agentic_result.turns if agentic_result else 0,
                    },
                    metadata={"campaign_id": self._config.campaign_id},
                )
                await self._hooks.fire(HookPoint.ITERATION_COMPLETE, iter_ctx)

            end = datetime.now(timezone.utc)
            wall_time = (end - start).total_seconds()

            await self._bus.publish(Event(
                name="agentic_iteration_complete",
                data={
                    "iteration_id": iter_id,
                    "job_id": job.job_id,
                    "decision": decision.value,
                    "observations": len(observations),
                    "tool_calls": len(agentic_result.tool_calls) if agentic_result else 0,
                    "turns": agentic_result.turns if agentic_result else 0,
                    "wall_time": wall_time,
                },
                source="agentic_research_iteration",
            ))

            # Build a synthetic JobResult from the agentic run
            tool_call_count = len(agentic_result.tool_calls) if agentic_result else 0
            job_result = JobResult(
                job_id=job.job_id,
                success=decision != IterationDecision.ERROR,
                metrics={"agentic_tool_calls": tool_call_count},
                wall_time_seconds=wall_time,
            )

            if self._chat_handler is not None:
                self._chat_handler.set_active(False)

            if decision == IterationDecision.ERROR:
                job_result.errors.append(
                    (agentic_result.error if agentic_result and agentic_result.error else None)
                    or "Agentic research iteration failed")
            return IterationResult(
                iteration_id=iter_id,
                job_id=job.job_id,
                decision=decision.value,
                job_result=job_result,
                observations=observations,
                wall_time_seconds=wall_time,
                error=(agentic_result.error if agentic_result else "no agentic result")
                if decision == IterationDecision.ERROR else None,
            )

        except Exception as exc:
            if self._chat_handler is not None:
                self._chat_handler.set_active(False)
            logger.exception("Agentic iteration %s failed", iter_id)
            return IterationResult(
                iteration_id=iter_id,
                job_id=job.job_id,
                decision=IterationDecision.ERROR.value,
                error=str(exc),
                wall_time_seconds=(datetime.now(timezone.utc) - start).total_seconds(),
            )


# ---------------------------------------------------------------------------
# Duplex research iteration (Cerebellum-Brain)
# ---------------------------------------------------------------------------


class DuplexResearchIteration:
    """Execute one research iteration via the Cerebellum-Brain duplex architecture.

    The Cerebellum (lightweight model) handles real-time user interaction
    while the Brain (full model) runs heavy computation as background tasks.
    The user can chat, modify tasks, or cancel computation at any time.
    """

    def __init__(
        self,
        config: AuroraConfig,
        router: LLMRouter,
        artifact_store: ArtifactStore,
        context_builder: ContextReconstructor,
        mental_model_mgr: MentalModelManager,
        event_bus: EventBus,
        tool_registry: Any = None,
        hook_registry: HookRegistry | None = None,
        checkpoint_handler: WebCheckpointHandler | None = None,
        chat_handler: Any = None,
    ) -> None:
        self._config = config
        self._router = router
        self._store = artifact_store
        self._ctx = context_builder
        self._mm = mental_model_mgr
        self._bus = event_bus
        self._tools = tool_registry
        self._hooks = hook_registry
        self._checkpoint_handler = checkpoint_handler
        self._chat_handler = chat_handler

    def _save_stage_checkpoint(self, iter_id: str, stage: IterationStage, data: dict) -> None:
        if not hasattr(self._store, '_ws') or self._store._ws is None:
            return
        checkpoint_dir = self._store._ws.campaign_dir / "iterations" / iter_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_data = {
            "iteration_id": iter_id,
            "stage": stage.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        checkpoint_path = checkpoint_dir / "stage_checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint_data, default=str), encoding="utf-8")

    def _load_stage_checkpoint(self, iter_id: str) -> dict | None:
        if not hasattr(self._store, '_ws') or self._store._ws is None:
            return None
        checkpoint_path = self._store._ws.campaign_dir / "iterations" / iter_id / "stage_checkpoint.json"
        if not checkpoint_path.exists():
            return None
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))

    async def run(
        self,
        job: ExperimentJob,
        plan: ExperimentPlan,
        iteration_number: int = 0,
        *,
        resume_iter_id: str | None = None,
    ) -> IterationResult:
        """Execute one research iteration via the Claude Code SDK duplex runtime.

        Interactive mode (chat_handler present): Cerebellum + Brain sessions.
        Autonomous mode (no chat_handler): Brain session only.

        Parameters
        ----------
        resume_iter_id:
            If set, resume from a prior checkpoint. The HarnessRuntime
            will reload completed Brain tasks from disk and skip them.
        """
        from aurora.agents.researcher import RESEARCHER_SYSTEM_PROMPT
        from aurora.llm.duplex import HarnessRuntime

        iter_id = resume_iter_id or f"iter_{uuid.uuid4().hex[:8]}"
        start = datetime.now(timezone.utc)

        try:
            # Stage 1: Reconstruct context
            artifacts = self._store.load()

            async def _llm_generate(prompt: str) -> str:
                resp = await self._router.generate(
                    PipelineStage.RESEARCHER, [Message.user(prompt)],
                    temperature=0.3,
                )
                return resp.content

            mental_model = await self._mm.reconstruct_from_evidence(
                artifacts, _llm_generate
            )
            context_str = self._ctx.reconstruct(
                task_description=(
                    f"Research iteration #{iteration_number}. "
                    f"Current job context: {job.job_id} ({job.skill_name}). "
                    f"You have full autonomy to design and execute experiments."
                ),
                artifacts=artifacts,
                mental_model=mental_model,
                relevant_job_ids=[dep for dep in job.dependencies],
            )

            # Stage 2: Resolve workspace and models
            workspace_dir = "."
            if hasattr(self._store, '_ws') and self._store._ws is not None:
                workspace_dir = str(self._store._ws.campaign_dir)

            cerebellum_model = self._config.duplex_cerebellum_model
            _, brain_model = self._router.get_provider_and_model(
                PipelineStage.RESEARCHER
            )

            # Stage 3: Resolve chat handler for stream callback
            _stream_cb = None
            interactive = self._chat_handler is not None
            if interactive:
                _stream_cb = self._chat_handler.stream_event
                self._chat_handler.set_active(True)

            self._save_stage_checkpoint(iter_id, IterationStage.CONTEXT, {
                "job_id": job.job_id,
            })

            # Stage 4: Create HarnessRuntime (Claude Code SDK sessions)
            runtime = HarnessRuntime(
                cerebellum_model=cerebellum_model,
                brain_model=brain_model,
                brain_system_prompt=RESEARCHER_SYSTEM_PROMPT,
                workspace_root=workspace_dir,
                max_brain_turns=getattr(self._config, "max_agentic_turns", 30),
                stream_callback=_stream_cb,
                research_context=context_str,
            )

            # Load runtime checkpoint if resuming — restores completed Brain tasks
            if resume_iter_id:
                if runtime.load_checkpoint():
                    logger.info(
                        "Resumed HarnessRuntime: %d completed tasks restored",
                        sum(
                            1 for s in runtime.brain_states.values()
                            if s.status.value == "completed"
                        ),
                    )

            # Wire user message queue from chat handler
            if interactive:
                runtime.user_queue  # ensure queue exists
                self._chat_handler._runtime_user_queue = runtime.user_queue

            initial_goal = artifacts.current_goal or artifacts.initial_objective

            await self._bus.publish(Event(
                name="duplex_iteration_started",
                data={
                    "iteration_id": iter_id,
                    "job_id": job.job_id,
                    "iteration_number": iteration_number,
                    "cerebellum_model": cerebellum_model,
                    "brain_model": brain_model,
                    "mode": "interactive" if interactive else "autonomous",
                },
                source="duplex_research_iteration",
            ))

            # Stage 5: Run — mode depends on whether a chat handler is present
            if interactive:
                all_results = await runtime.run(initial_goal)
            else:
                all_results = await runtime.run_autonomous(initial_goal)

            self._save_stage_checkpoint(iter_id, IterationStage.EXECUTION, {
                "job_id": job.job_id,
                "brain_tasks": len(runtime.brain_states),
                "results_count": len(all_results),
            })

            # Stage 6: Extract results
            observations: list[Observation] = []
            for result in all_results:
                for obs_data in result.observations:
                    obs = Observation(
                        observation_id=obs_data.get(
                            "observation_id", f"obs_{uuid.uuid4().hex[:8]}"
                        ),
                        source_job_id=obs_data.get("source_job_id", job.job_id),
                        description=obs_data.get("description", ""),
                        numerical_values=obs_data.get("numerical_values", {}),
                        timestamp=datetime.now(timezone.utc),
                    )
                    observations.append(obs)

            # Stage 7: Determine decision from combined results
            decision = IterationDecision.CONTINUE
            total_tool_calls = 0
            for result in all_results:
                total_tool_calls += len(result.tool_calls)
                d = result.decision
                if d == "done":
                    decision = IterationDecision.DONE
                elif d == "replan" and decision != IterationDecision.DONE:
                    decision = IterationDecision.REPLAN
                elif d == "audit" and decision not in (
                    IterationDecision.DONE, IterationDecision.REPLAN
                ):
                    decision = IterationDecision.AUDIT_REQUESTED

            end = datetime.now(timezone.utc)
            wall_time = (end - start).total_seconds()

            await self._bus.publish(Event(
                name="duplex_iteration_complete",
                data={
                    "iteration_id": iter_id,
                    "job_id": job.job_id,
                    "decision": decision.value,
                    "observations": len(observations),
                    "brain_tasks": len(runtime.brain_states),
                    "tool_calls": total_tool_calls,
                    "wall_time": wall_time,
                },
                source="duplex_research_iteration",
            ))

            job_result = JobResult(
                job_id=job.job_id,
                success=decision != IterationDecision.ERROR,
                metrics={
                    "brain_tasks": len(runtime.brain_states),
                    "tool_calls": total_tool_calls,
                },
                wall_time_seconds=wall_time,
            )

            if interactive:
                self._chat_handler.set_active(False)

            await runtime.close()

            return IterationResult(
                iteration_id=iter_id,
                job_id=job.job_id,
                decision=decision.value,
                job_result=job_result,
                observations=observations,
                wall_time_seconds=wall_time,
            )

        except Exception as exc:
            if self._chat_handler is not None:
                self._chat_handler.set_active(False)
            logger.exception("Duplex iteration %s failed", iter_id)
            return IterationResult(
                iteration_id=iter_id,
                job_id=job.job_id,
                decision=IterationDecision.ERROR.value,
                error=str(exc),
                wall_time_seconds=(datetime.now(timezone.utc) - start).total_seconds(),
            )

"""Scripted research iteration used in mock mode.

:class:`ResearchIteration` is the engine-mediated loop (prompt, parse the JSON
action, run a skill with the mock tools, assess the result).  It drives offline
demos and tests; real runs use :class:`aurora.core.terminal_iteration.TerminalIteration`,
where the research agent is a Claude Code session.
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

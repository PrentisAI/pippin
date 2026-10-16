"""Research phase: one Claude Code session per research job.

The research agent works in the campaign's execution backend (normally the
Docker container).  Each job of the experiment plan gets a fresh session whose
task message carries the objective, the chosen direction and the job itself.
When acceptance tests are configured (``sandbox.tests_dir``) they run after the
session, and a failing job is resumed with the test output until it passes or
its time runs out.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from aurora.config import AuroraConfig
from aurora.core.events import Event, EventBus
from aurora.core.execution_backend import ExecutionBackend
from aurora.core.lifecycle import IterationDecision, IterationResult
from aurora.llm.stream import StreamEvent
from aurora.models.experiment import ExperimentJob, ExperimentPlan, JobResult
from aurora.state.artifacts import ArtifactStore

logger = logging.getLogger(__name__)

# Sessions resumed after failing acceptance tests, per job
_MAX_TEST_RETRIES = 3


class TerminalIteration:
    """Runs research jobs as Claude Code sessions in the execution backend."""

    def __init__(
        self,
        config: AuroraConfig,
        backend: ExecutionBackend,
        event_bus: EventBus,
        *,
        system_prompt: str = "",
        task_instruction: str = "",
        campaign_brief: str = "",
        artifact_store: ArtifactStore | None = None,
        prior_phase_context: str = "",
        transcript_dir: str | Path | None = None,
        text_callback: Any = None,
        cwd: str | None = None,
    ) -> None:
        self._config = config
        self._backend = backend
        self._bus = event_bus
        self._system_prompt = system_prompt
        self._task_instruction = task_instruction
        self._campaign_brief = campaign_brief
        self._artifact_store = artifact_store
        self._current_loop: Any = None
        self._prior_phase_context = prior_phase_context
        self._transcript_dir = Path(transcript_dir) if transcript_dir else None
        self._cwd = cwd
        self.transcript_path: str | None = None
        self._text_callback = text_callback
        self.session_id: str | None = None
        self.cost_usd: float = 0.0
        self._pending_messages: list[str] = []
        self._stop_requested = False

    async def inject_message(self, message: str) -> None:
        """Send *message* to the running session, or queue it for the next one."""
        if self._current_loop is not None:
            await self._current_loop.inject_message(message)
        else:
            self._pending_messages.append(message)

    def interrupt(self) -> None:
        """Interrupt the current tool call of the running session (SIGINT)."""
        if self._current_loop is not None:
            self._current_loop.interrupt()

    def request_stop(self) -> None:
        """Stop the running job and do not start further sessions."""
        self._stop_requested = True
        self.interrupt()

    async def run(self, job: ExperimentJob, plan: ExperimentPlan, iteration_number: int = 0) -> IterationResult:
        iter_id = f"job_{job.job_id}_{uuid.uuid4().hex[:6]}"
        start = time.time()
        deadline = start + self._config.agent.research_timeout_minutes * 60
        await self._publish("iteration_started", iteration_id=iter_id, job_id=job.job_id)

        turns, cost, errors = 0, 0.0, []
        passed = False
        message = self._job_message(job, plan)
        resume_id: str | None = None
        try:
            for attempt in range(1 + (_MAX_TEST_RETRIES if self._config.sandbox.tests_dir else 0)):
                if self._stop_requested:
                    errors.append("stopped by request")
                    break
                remaining = deadline - time.time()
                if remaining < 30:
                    errors.append("research time limit reached")
                    break
                if self._pending_messages:
                    message += "\n\n" + "\n\n".join(self._pending_messages)
                    self._pending_messages.clear()

                result = await self._run_session(message, remaining, resume_id)
                turns += result.get("turns", 0)
                cost += result.get("cost_usd", 0.0)
                if result.get("error"):
                    errors.append(result["error"])
                    break
                resume_id = self.session_id

                if not self._config.sandbox.tests_dir:
                    passed = True
                    break
                passed, test_output = await self._run_tests(attempt)
                if passed:
                    break
                message = ("The acceptance tests failed. Fix the problems and run your own checks "
                           f"before finishing. Last lines of the test output:\n\n{test_output[-3000:]}")
        except Exception as exc:
            logger.exception("Research job %s failed", job.job_id)
            errors.append(str(exc))

        self.cost_usd += cost
        wall = time.time() - start
        job_result = JobResult(
            job_id=job.job_id,
            success=passed,
            metrics={"turns": float(turns), "cost_usd": round(cost, 4)},
            errors=errors,
            wall_time_seconds=wall,
        )
        decision = IterationDecision.DONE.value if passed else IterationDecision.ERROR.value
        self._record_to_artifact_store(job, job_result, iter_id)
        await self._publish("iteration_complete", iteration_id=iter_id, job_id=job.job_id,
                            decision=decision, cost_usd=round(cost, 4), errors=errors)
        return IterationResult(
            iteration_id=iter_id,
            job_id=job.job_id,
            decision=decision,
            job_result=job_result,
            error="; ".join(errors) or None,
            wall_time_seconds=wall,
        )

    async def _run_session(self, message: str, time_left: float, resume_id: str | None) -> dict[str, Any]:
        loop = self._create_loop(self._config.agent.research_max_turns)
        if resume_id:
            loop._resume_session_id = resume_id
        self._current_loop = loop
        try:
            return await asyncio.wait_for(loop.run(self._build_system_prompt(), message), timeout=time_left)
        except asyncio.TimeoutError:
            loop.interrupt()
            return {"error": "research time limit reached", "turns": 0}
        finally:
            self._current_loop = None
            if getattr(loop, "session_id", None):
                self.session_id = loop.session_id
            await loop.close()

    async def _run_tests(self, attempt: int) -> tuple[bool, str]:
        await self._publish("verifier_started", attempt=attempt)
        try:
            passed, score, output = await self._backend.run_verifier(timeout=600.0)
        except Exception as exc:
            logger.warning("Acceptance tests could not run: %s", exc)
            passed, score, output = False, 0.0, str(exc)
        await self._publish("verifier_result", attempt=attempt, passed=passed, score=round(score, 4))
        return passed, output

    async def _publish(self, name: str, **data: Any) -> None:
        await self._bus.publish(Event(name=name, data=data, source="terminal_iteration"))

    def _job_message(self, job: ExperimentJob, plan: ExperimentPlan) -> str:
        parts = []
        if self._campaign_brief:
            parts.append(self._campaign_brief)
        parts.append(f"## Your job: {job.job_id}\n\n{job.skill_name}")
        if job.parameters:
            parts.append("Details:\n```json\n" + json.dumps(job.parameters, indent=2, ensure_ascii=False) + "\n```")
        if job.expected_outputs:
            parts.append("Expected outputs:\n" + "\n".join(f"- {o}" for o in job.expected_outputs))
        if job.dependencies:
            parts.append("This job builds on: " + ", ".join(job.dependencies)
                         + ". Their outputs are in the working directory.")
        others = [j.job_id for j in plan.jobs if j.job_id != job.job_id]
        if others:
            parts.append("Other jobs of the plan (done separately, not by you): " + ", ".join(others))
        if self._task_instruction:
            parts.append(f"## Instructions from the researcher\n\n{self._task_instruction}")
        parts.append("Explore the working directory first, then do the job. Verify your results, and "
                     "end with a short summary of what you did, where the outputs are, and what is "
                     "uncertain.")
        return "\n\n".join(parts)

    def _build_system_prompt(self) -> str:
        return f"{self._system_prompt}\n{self._prior_phase_context}"

    def _create_loop(self, max_turns: int) -> Any:
        from aurora.core.agent_session import make_claude_code_loop

        async def stream_callback(event: StreamEvent) -> None:
            await self._bus.publish(Event(
                name=event.event_type,
                data=event.data,
                source="terminal_iteration",
            ))

        live_path = None
        if self._transcript_dir:
            self._transcript_dir.mkdir(parents=True, exist_ok=True)
            live_path = str(self._transcript_dir / "research_loop.jsonl")
            self.transcript_path = live_path
        return make_claude_code_loop(
            self._config,
            model=self._config.models.agent,
            max_turns=max_turns,
            docker_container=getattr(self._backend, "container_id", None),
            cwd=self._cwd,
            stream_callback=stream_callback,
            live_transcript_path=live_path,
            text_callback=self._text_callback,
        )

    def _record_to_artifact_store(
        self,
        job: ExperimentJob,
        job_result: JobResult,
        iter_id: str,
    ) -> None:
        """Write experiment results to ArtifactStore for downstream phases."""
        if self._artifact_store is None:
            return
        try:
            from aurora.models.artifacts import ExperimentRecord, Observation
            from datetime import datetime, timezone

            artifacts = self._artifact_store.load()
            now = datetime.now(timezone.utc)

            record = ExperimentRecord(
                job_id=job.job_id,
                skill_used=job.skill_name or "terminal_execution",
                parameters=job.parameters,
                status="completed" if job_result.success else "failed",
                completed_at=now,
            )
            artifacts = self._artifact_store.record_experiment(artifacts, record)

            obs = Observation(
                observation_id=f"obs_{iter_id}",
                source_job_id=job.job_id,
                description=(
                    f"Research job {job.job_id} {'completed' if job_result.success else 'failed'}. "
                    f"Turns: {job_result.metrics.get('turns', 0)}, "
                    f"Wall time: {job_result.wall_time_seconds:.1f}s"
                ),
                numerical_values={
                    k: float(v)
                    for k, v in job_result.metrics.items()
                    if isinstance(v, (int, float))
                },
                timestamp=now,
            )
            self._artifact_store.record_observation(artifacts, obs)

        except Exception as exc:
            logger.warning("Failed to record to ArtifactStore: %s", exc)


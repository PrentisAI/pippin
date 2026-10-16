"""Terminal iteration runner — agent loop in a terminal environment.

Fourth execution mode alongside Legacy, Agentic, and Duplex.
The agent runs autonomously in a terminal environment (local or Docker),
with optional verifier-based retry until timeout.

This is a general-purpose runner, not LHTB-specific. Any task that
involves autonomous agent execution with shell access can use it.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aurora.config import AuroraConfig
from aurora.core.events import Event, EventBus
from aurora.core.execution_backend import ExecutionBackend
from aurora.core.lifecycle import IterationDecision, IterationResult
from aurora.llm.tool_runner import StreamEvent, ToolSchema
from aurora.models.experiment import ExperimentJob, ExperimentPlan, JobResult
from aurora.state.artifacts import ArtifactStore

logger = logging.getLogger(__name__)


class TerminalIteration:
    """Execute a task via an autonomous agent loop in a terminal environment.

    The agent loop driver (ClaudeCodeLoop, OpenAIAgenticLoop, or
    AnthropicAgenticLoop) is selected based on config. The backend
    provides tools and verifier.
    """

    def __init__(
        self,
        config: AuroraConfig,
        backend: ExecutionBackend,
        event_bus: EventBus,
        *,
        system_prompt: str = "",
        task_instruction: str = "",
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
        self._artifact_store = artifact_store
        self._current_loop: Any = None
        self._prior_phase_context = prior_phase_context
        self._transcript_dir = Path(transcript_dir) if transcript_dir else None
        self._cwd = cwd
        self.transcript_path: str | None = None
        self._all_trajectory: list[Any] = []
        self._text_callback = text_callback
        self.session_id: str | None = None
        self._pending_supervisor_advice: list[str] = []

    async def inject_message(self, message: str) -> None:
        """Inject a message into the currently running agent loop.

        If no loop is active (between phases), the message is buffered
        and prepended to the next phase's user message.
        """
        if self._current_loop and hasattr(self._current_loop, "inject_message"):
            await self._current_loop.inject_message(message)
        else:
            self._pending_supervisor_advice.append(message)

    def interrupt(self) -> None:
        """Interrupt the currently running agent loop (SIGINT to subprocess)."""
        if self._current_loop and hasattr(self._current_loop, "interrupt"):
            self._current_loop.interrupt()

    async def run(
        self,
        job: ExperimentJob,
        plan: ExperimentPlan,
        iteration_number: int = 0,
    ) -> IterationResult:
        """Execute the terminal iteration for *job*."""
        iter_id = f"term_{uuid.uuid4().hex[:8]}"
        start_time = time.time()

        await self._bus.publish(Event(
            name="iteration_started",
            data={"iteration_id": iter_id, "job_id": job.job_id, "mode": "terminal"},
            source="terminal_iteration",
        ))

        phase = 0
        best_reward = 0.0
        prev_feedback = ""
        total_turns = 0
        total_tokens = 0
        verifier_passed = False
        timeout_sec = self._config.terminal_timeout_sec
        checkpoint_interval = 1800.0
        last_checkpoint = start_time

        try:
            while True:
                elapsed = time.time() - start_time
                remaining = timeout_sec - elapsed

                if remaining <= 30:
                    logger.info("Timeout reached after %d phases", phase)
                    break

                # Determine max turns for this phase — cap by checkpoint interval
                time_until_checkpoint = checkpoint_interval - (time.time() - last_checkpoint)
                phase_timeout = min(remaining, max(time_until_checkpoint, 60.0))
                turns_this_phase = min(
                    self._config.terminal_max_turns,
                    int(phase_timeout / 5),
                )
                if turns_this_phase < 5:
                    break

                await self._bus.publish(Event(
                    name="phase_started",
                    data={"phase": phase, "elapsed": round(elapsed), "remaining": round(remaining)},
                    source="terminal_iteration",
                ))

                system_prompt = self._build_system_prompt(phase, prev_feedback)
                user_message = self._build_user_message(phase, prev_feedback)

                if self._pending_supervisor_advice:
                    advice = "\n\n".join(self._pending_supervisor_advice)
                    user_message = f"{user_message}\n\n{advice}"
                    self._pending_supervisor_advice.clear()

                loop = self._create_loop(turns_this_phase)
                self._current_loop = loop

                try:
                    loop_result = await loop.run(system_prompt, user_message, temperature=0.3)
                finally:
                    self._current_loop = None
                    trajectory = loop_result.get("trajectory", [])
                    self._all_trajectory.extend(trajectory)
                    if hasattr(loop, "session_id") and loop.session_id:
                        self.session_id = loop.session_id
                    await loop.close()

                tokens = loop_result.get("tokens", {})
                phase_tokens = (
                    tokens.get("prompt", 0) + tokens.get("completion", 0)
                    if isinstance(tokens, dict) else 0
                )
                phase_turns = loop_result.get("turns", 0)
                total_turns += phase_turns
                total_tokens += phase_tokens

                # Run verifier on task_complete OR checkpoint interval
                task_completed = loop_result.get("task_completed", False)
                checkpoint_due = (time.time() - last_checkpoint) >= checkpoint_interval
                should_verify = task_completed or checkpoint_due

                if should_verify:
                    verify_reason = "task_complete" if task_completed else "checkpoint"
                    logger.info(
                        "Running verifier (reason=%s, phase=%d)", verify_reason, phase
                    )
                    await self._bus.publish(Event(
                        name="verifier_started",
                        data={"phase": phase, "reason": verify_reason},
                        source="terminal_iteration",
                    ))
                    try:
                        passed, reward, v_output = await self._backend.run_verifier(
                            timeout=600.0,
                        )
                        await self._bus.publish(Event(
                            name="verifier_result",
                            data={"phase": phase, "passed": passed, "reward": round(reward, 4)},
                            source="terminal_iteration",
                        ))

                        if reward > best_reward:
                            best_reward = reward

                        if passed:
                            verifier_passed = True
                            break

                    except Exception as exc:
                        logger.warning("Verifier failed: %s", exc)

                    last_checkpoint = time.time()

                if not self._config.continue_until_timeout:
                    break

                phase += 1
                # Binary rejection — no reward, no test details (LHTB-compatible)
                prev_feedback = (
                    "Your submitted solution did not pass verification. "
                    "No verifier details, test output, scalar reward, or "
                    "hidden requirements are available. Continue working "
                    "from the current project state. Re-read the original "
                    "requirements, inspect your implementation, and run "
                    "your own checks before submitting again. "
                    f"This is continuation {phase}; approximately "
                    f"{int(timeout_sec - (time.time() - start_time))} seconds remain."
                )

        except Exception as exc:
            logger.exception("Terminal iteration failed")
            return IterationResult(
                iteration_id=iter_id,
                job_id=job.job_id,
                decision=IterationDecision.ERROR.value,
                error=str(exc),
                wall_time_seconds=time.time() - start_time,
            )

        wall_time = time.time() - start_time

        self._save_transcript()

        job_result = JobResult(
            job_id=job.job_id,
            success=verifier_passed,
            metrics={"reward": best_reward, "turns": total_turns, "tokens": total_tokens},
            wall_time_seconds=wall_time,
        )

        decision = (
            IterationDecision.DONE.value if verifier_passed
            else IterationDecision.CONTINUE.value
        )

        self._record_to_artifact_store(job, job_result, iter_id)

        await self._bus.publish(Event(
            name="iteration_complete",
            data={
                "iteration_id": iter_id,
                "job_id": job.job_id,
                "decision": decision,
                "reward": best_reward,
            },
            source="terminal_iteration",
        ))

        return IterationResult(
            iteration_id=iter_id,
            job_id=job.job_id,
            decision=decision,
            job_result=job_result,
            wall_time_seconds=wall_time,
        )

    def _save_transcript(self) -> None:
        """Backfill transcript if live writing was not used (non-Claude-Code drivers)."""
        if self._transcript_dir is None or not self._all_trajectory:
            return
        self._transcript_dir.mkdir(parents=True, exist_ok=True)
        path = self._transcript_dir / "research_loop.jsonl"
        if path.exists() and path.stat().st_size > 0:
            self.transcript_path = str(path)
            return
        with open(path, "w") as f:
            for entry in self._all_trajectory:
                if dataclasses.is_dataclass(entry):
                    row = dataclasses.asdict(entry)
                elif hasattr(entry, "__dict__"):
                    row = entry.__dict__
                else:
                    row = entry
                f.write(json.dumps(row, default=str) + "\n")
        self.transcript_path = str(path)

    def _create_loop(self, max_turns: int) -> Any:
        """Instantiate the appropriate agent loop driver."""
        driver = self._resolve_driver()

        async def stream_callback(event: StreamEvent) -> None:
            await self._bus.publish(Event(
                name=event.event_type,
                data=event.data,
                source="terminal_iteration",
            ))

        if driver == "claude-code":
            from aurora.llm.agent_loops import ClaudeCodeLoop
            container_id = getattr(self._backend, 'container_id', None)
            base_url = ""
            if self._config.terminal_base_url and "openrouter.ai" in self._config.terminal_base_url:
                base_url = "https://openrouter.ai/api"
            live_path = None
            if self._transcript_dir:
                self._transcript_dir.mkdir(parents=True, exist_ok=True)
                live_path = str(self._transcript_dir / "research_loop.jsonl")
                self.transcript_path = live_path
            return ClaudeCodeLoop(
                model=self._config.terminal_model or self._config.llm_assignment.researcher,
                api_key=self._config.terminal_api_key or os.environ.get("OPENROUTER_API_KEY", ""),
                base_url=base_url,
                max_turns=max_turns,
                docker_container=container_id,
                cwd=self._cwd,
                stream_callback=stream_callback,
                live_transcript_path=live_path,
                text_callback=self._text_callback,
                allow_host_bash=self._config.allow_host_bash,
            )
        elif driver == "anthropic":
            from aurora.llm.agent_loops import AnthropicAgenticLoop
            tools = self._backend.get_tool_registry()
            tools.append(self._build_task_complete_tool())
            return AnthropicAgenticLoop(
                api_key=self._config.terminal_api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
                model=self._config.terminal_model or self._config.llm_assignment.researcher,
                tools=tools,
                max_turns=max_turns,
                stream_callback=stream_callback,
            )
        else:
            from aurora.llm.agent_loops import OpenAIAgenticLoop
            tools = self._backend.get_tool_registry()
            tools.append(self._build_task_complete_tool())
            return OpenAIAgenticLoop(
                api_key=self._config.terminal_api_key or os.environ.get("OPENROUTER_API_KEY", ""),
                model=self._config.terminal_model or self._config.llm_assignment.researcher,
                tools=tools,
                base_url=self._config.terminal_base_url,
                max_turns=max_turns,
                stream_callback=stream_callback,
            )

    def _resolve_driver(self) -> str:
        driver = self._config.terminal_driver
        if driver != "auto":
            return driver
        base = self._config.terminal_base_url
        if not base or "anthropic.com" in base:
            return "anthropic"
        return "claude-code"

    def _build_task_complete_tool(self) -> ToolSchema:
        async def handler(params: dict[str, Any]) -> str:
            return json.dumps({
                "acknowledged": True,
                "message": "Task marked as complete. Running verification...",
            })
        return ToolSchema(
            name="task_complete",
            description=(
                "Signal that you have finished working on the task. "
                "Call this when all required output files have been produced "
                "and you believe the solution is correct."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what was done",
                    },
                },
            },
            handler=handler,
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
                    f"Terminal iteration {'passed' if job_result.success else 'failed'}. "
                    f"Reward: {job_result.metrics.get('reward', 0):.2%}, "
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

    def _build_system_prompt(self, phase: int, prev_feedback: str) -> str:
        phase_info = ""
        if phase > 0 and prev_feedback:
            phase_info = f"\n## Previous Attempt Feedback\nThis is attempt #{phase + 1}.\n{prev_feedback}\n"

        return f"""{self._system_prompt}
{self._prior_phase_context}
{phase_info}
## Your Approach

1. **Understand**: Read the task instruction carefully. Explore the environment.
2. **Plan**: Break the task into subtasks.
3. **Execute**: Work systematically, verifying after each step.
4. **Complete**: When done, call task_complete.
"""

    def _build_user_message(self, phase: int, prev_feedback: str) -> str:
        if phase == 0:
            return (
                f"Here is your task instruction:\n\n"
                f"---\n{self._task_instruction}\n---\n\n"
                f"Begin by exploring the task environment, then work on the task."
            )
        return prev_feedback

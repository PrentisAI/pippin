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
from aurora.state.sessions import CONTINUE_NOTE, RESTART_NOTE

logger = logging.getLogger(__name__)

# Sessions resumed after failing acceptance tests, per job
_MAX_TEST_RETRIES = 3


CRASH_NOTE = ("[Resumed] Your session stopped unexpectedly; if you ran something big in the container, "
              "it may have run out of memory and taken your session with it. Check what you had done, "
              "then continue the job; keep memory use within the container's limit.")


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
        budget_fn: Any = None,
        sessions: Any = None,
        round_n: int = 0,
        resuming: bool = False,
    ) -> None:
        self._sessions = sessions        # SessionRegistry: continue interrupted job sessions
        self._round = round_n
        self._resuming = resuming
        self._session_callback: Any = None
        self._budget_fn = budget_fn
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
        # time the agent spent paused (waiting for a decision) does not count against its job
        self._paused_since: float | None = None
        self._paused_total = 0.0
        # paused until the researcher decides: other messages wait for the decision
        self._awaiting_decision = False
        self._held_messages: list[str] = []

    async def inject_message(self, message: str) -> None:
        """Send *message* to the running session (resuming it if paused), or queue it for the next one.
        While the agent waits for the researcher's decision, messages wait with it."""
        if self._awaiting_decision:
            self._held_messages.append(message)
            return
        self._resume_clock()
        if self._current_loop is not None:
            await self._current_loop.inject_message(message)
        else:
            self._pending_messages.append(message)

    def interrupt(self) -> None:
        """Pause the running session: its current action stops and it waits for the next message
        (:meth:`inject_message`).  The job's clock stands still meanwhile."""
        if self._current_loop is not None:
            self._current_loop.interrupt()
            if self._paused_since is None:
                self._paused_since = time.time()

    @property
    def paused(self) -> bool:
        return self._paused_since is not None

    def pause_for_decision(self) -> None:
        """Pause the agent until :meth:`resume_after_decision`; nothing else wakes it."""
        self.interrupt()
        self._awaiting_decision = self._current_loop is not None

    async def resume_after_decision(self, text: str) -> None:
        """Give the agent the decision (and any message that came meanwhile); it goes on."""
        self._awaiting_decision = False
        messages, self._held_messages = [text, *self._held_messages], []
        await self.inject_message("\n\n".join(messages))

    def request_stop(self) -> None:
        """Stop the running job and do not start further sessions."""
        self._stop_requested = True
        self._awaiting_decision = False
        self._resume_clock()
        if self._current_loop is not None:
            self._current_loop.terminate()

    def _resume_clock(self) -> None:
        if self._paused_since is not None:
            self._paused_total += time.time() - self._paused_since
            self._paused_since = None

    def _paused_for(self) -> float:
        """Seconds paused so far in this job."""
        return self._paused_total + (time.time() - self._paused_since if self._paused_since else 0.0)

    async def _wait_working(self, task: asyncio.Task, seconds: float) -> bool:
        """Wait until *task* is done or the agent has worked *seconds* (paused time not counted)."""
        end = time.time() + seconds
        counted = self._paused_for()
        while True:
            paused = self._paused_for()
            end += paused - counted          # move the end by the time spent paused
            counted = paused
            left = end - time.time()
            if left <= 0 and not self.paused:
                return task.done()
            done, _ = await asyncio.wait({task}, timeout=max(min(left, 5.0), 0.5))
            if done:
                return True

    async def run(self, job: ExperimentJob, plan: ExperimentPlan, iteration_number: int = 0, *,
                  concurrent: list[str] | None = None) -> IterationResult:
        """Run *job*; *concurrent* names jobs that run at the same time in the same /app."""
        self._job_id = job.job_id
        self._concurrent = list(concurrent or [])
        iter_id = f"job_{job.job_id}_{uuid.uuid4().hex[:6]}"
        start = time.time()
        deadline = start + self._config.agent.research_timeout_minutes * 60
        await self._publish("iteration_started", iteration_id=iter_id, job_id=job.job_id)

        turns, cost, errors = 0, 0.0, []
        summary = ""
        passed = False
        message = self._job_message(job, plan)
        key = f"research:{job.job_id}:round{self._round}"
        resume_id = self._sessions.open_session(key) if (self._resuming and self._sessions) else None
        continuing = resume_id is not None
        if continuing:
            logger.info("Continuing the interrupted session %s of job %s", resume_id, job.job_id)
            message = f"{CONTINUE_NOTE}\n\n{message}"
        if self._sessions is not None:
            self._session_callback = lambda sid: self._sessions.record(key, sid)
        try:
            for attempt in range(1 + (_MAX_TEST_RETRIES if self._config.sandbox.tests_dir else 0)):
                if self._stop_requested:
                    errors.append("stopped by request")
                    break
                remaining = deadline + self._paused_for() - time.time()
                if remaining < 30:
                    errors.append("research time limit reached")
                    break
                if self._pending_messages:
                    message += "\n\n" + "\n\n".join(self._pending_messages)
                    self._pending_messages.clear()

                result = await self._run_session(message, remaining, resume_id)
                if continuing and result.get("error") and not result.get("turns"):
                    # the old session could not be continued: start over from the files it left
                    logger.warning("Could not continue session %s: %s", resume_id, result["error"])
                    continuing, resume_id = False, None
                    message = message.replace(CONTINUE_NOTE, RESTART_NOTE)
                    result = await self._run_session(message, deadline + self._paused_for() - time.time(), None)
                continuing = False
                if (str(result.get("error") or "").startswith("Claude Code exited without a result")
                        and self.session_id and not self._stop_requested):
                    # the agent's process died (in the container: perhaps out of memory); go on with
                    # the same session once rather than lose the job
                    logger.warning("Research session of job %s ended abruptly; continuing it", job.job_id)
                    turns += result.get("turns", 0)
                    cost += result.get("cost_usd", 0.0)
                    result = await self._run_session(CRASH_NOTE, deadline + self._paused_for() - time.time(),
                                                     self.session_id)
                if result.get("final_text"):
                    summary = result["final_text"]
                turns += result.get("turns", 0)
                cost += result.get("cost_usd", 0.0)
                if result.get("timed_out"):
                    errors.append("time limit reached; the results may be incomplete")
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
        if passed and self._sessions is not None:
            self._sessions.done(key)
        wall = time.time() - start
        job_result = JobResult(
            job_id=job.job_id,
            success=passed,
            metrics={"turns": float(turns), "cost_usd": round(cost, 4)},
            errors=errors,
            summary=summary.strip()[-4000:],
            wall_time_seconds=wall,
        )
        decision = IterationDecision.DONE.value if passed else IterationDecision.ERROR.value
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
        """One research session, with a warning before the time limit and a short grace period after it."""
        loop = self._create_loop(self._config.agent.research_max_turns)
        if resume_id:
            loop._resume_session_id = resume_id
        self._current_loop = loop
        grace = min(300.0, max(60.0, time_left * 0.1))
        task = asyncio.create_task(loop.run(self._build_system_prompt(), message))
        timed_out = False
        try:
            done = await self._wait_working(task, max(time_left * 0.8, 1.0))
            if not done:
                await loop.inject_message(
                    f"[Time] About {time_left * 0.2 / 60:.0f} minutes are left for this job. Finish the "
                    "current step, save your results and write the summary; do not start new analyses.")
                done = await self._wait_working(task, time_left * 0.2)
            if not done:
                timed_out = True
                loop.interrupt()
                await loop.inject_message(
                    "[Time is up] Stop now. Save what you have and reply with a short summary of what is "
                    "done, where the outputs are, and what is unfinished.")
                done = await self._wait_working(task, grace)
            if not done:
                task.cancel()
                return {"error": "research time limit reached", "turns": 0, "timed_out": True}
            result = task.result()
            result["timed_out"] = timed_out
            return result
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
        if getattr(self, "_concurrent", None):
            parts.append(
                "These jobs run AT THE SAME TIME as yours, by other agents in the same /app and container: "
                + ", ".join(self._concurrent) + f". Put your files under /app/{job.job_id}/ (or name them "
                "after your job), do not change or delete their files, and share the container's CPUs "
                "and memory with them.")
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

        job_id = getattr(self, "_job_id", "")

        async def stream_callback(event: StreamEvent) -> None:
            await self._bus.publish(Event(
                name=event.event_type,
                data={**event.data, "job_id": job_id},
                source="terminal_iteration",
            ))

        live_path = None
        if self._transcript_dir:
            self._transcript_dir.mkdir(parents=True, exist_ok=True)
            live_path = str(self._transcript_dir / "research_loop.jsonl")
            self.transcript_path = live_path
        return make_claude_code_loop(
            self._config,
            model=self._config.models.for_phase("research"),
            max_turns=max_turns,
            max_budget_usd=self._budget_fn() if self._budget_fn else None,
            docker_container=getattr(self._backend, "container_id", None),
            container_agent=({"container": self._backend.container_id, **self._backend.agent_env}
                             if getattr(self._backend, "agent_env", None) else None),
            cwd=self._cwd,
            stream_callback=stream_callback,
            live_transcript_path=live_path,
            text_callback=self._text_callback,
            session_callback=self._session_callback,
            transcript_tag=job_id,
        )


class RunningJobs:
    """The research jobs running now, one agent each.

    Messages, pauses and stops go to one job (by ID) or, without one, to all of them.  A message
    sent while no job runs is given to the next one that starts.
    """

    def __init__(self) -> None:
        self.active: dict[str, Any] = {}
        self._waiting: list[str] = []

    def add(self, job_id: str, runner: Any) -> None:
        self.active[job_id] = runner
        if self._waiting and hasattr(runner, "_pending_messages"):
            runner._pending_messages.extend(self._waiting)
            self._waiting.clear()

    def remove(self, job_id: str) -> None:
        self.active.pop(job_id, None)

    def _targets(self, job: str | None = None) -> list[Any]:
        if job and job in self.active:
            return [self.active[job]]
        if job:
            logger.info("No running job %r; addressing all running jobs", job)
        return list(self.active.values())

    @property
    def job_ids(self) -> list[str]:
        return list(self.active)

    @property
    def paused(self) -> bool:
        return any(getattr(r, "paused", False) for r in self.active.values())

    async def inject_message(self, message: str, job: str | None = None) -> None:
        targets = [r for r in self._targets(job) if hasattr(r, "inject_message")]
        if not targets:
            self._waiting.append(message)
        for r in targets:
            await r.inject_message(message)

    def interrupt(self, job: str | None = None) -> None:
        for r in self._targets(job):
            if hasattr(r, "interrupt"):
                r.interrupt()

    def request_stop(self) -> None:
        for r in self.active.values():
            if hasattr(r, "request_stop"):
                r.request_stop()

    def pause_for_decision(self, job: str | None = None) -> list[str]:
        """Pause *job* (or all running jobs) for a decision; the IDs of those paused."""
        paused = []
        for jid, r in list(self.active.items()):
            if (not job or job == jid or job not in self.active) and hasattr(r, "pause_for_decision"):
                r.pause_for_decision()
                paused.append(jid)
        return paused

    async def resume_after_decision(self, text: str, jobs: list[str]) -> None:
        for jid in jobs:
            r = self.active.get(jid)
            if r is not None and hasattr(r, "resume_after_decision"):
                await r.resume_after_decision(text)

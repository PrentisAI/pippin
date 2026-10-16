"""Unified Supervisor — Claude Code instance that monitors the main agent.

Replaces the separate Auditor (agents/auditor.py) and Watchdog
(lhtb/watchdog.py, lhtb/watchdog_cc.py) with a single component.

Architecture
------------
Main Agent (Claude Code #1)
  │  writes trajectory to transcript.jsonl in real-time
  ▼
transcript.jsonl
  │
  │  Engine (Python) tails the file, pushes new lines via stdin
  ▼
Supervisor (Claude Code #2)
  │
  │  writes verdicts + interrupts
  ▼
supervisor_verdicts.jsonl ──► Engine reads
control.jsonl ─────────────► Engine reads

The Engine (pure Python) does the reliable file tailing. It reads
new lines from the transcript and pushes them to the Supervisor
via stdin.  The Supervisor just receives pushed content — it doesn't
need to track file positions itself.

The Supervisor can still use its tools (bash, read, docker exec, etc.)
to proactively investigate when it sees something suspicious.

Two modes of operation, same instance:
  - Continuous monitoring: Engine pushes transcript lines periodically
  - Checkpoint audit: Engine pushes checkpoint request via stdin

Output is a unified verdict with one of 7 AuditOutcome values:
  continue / validate / replan / reframe / reject / recover / escalate
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from aurora.models.audit import AuditOutcome

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

@dataclass
class SupervisorVerdict:
    outcome: AuditOutcome = AuditOutcome.CONTINUE
    reason: str = ""
    suggestion: str = ""
    source: str = "supervisor"
    timestamp: float = field(default_factory=time.time)


@dataclass
class SupervisorTrajectoryEntry:
    timestamp: float = 0.0
    event_type: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Transcript Tailer (runs inside Engine process)
# ---------------------------------------------------------------------------

class TranscriptTailer:
    """Reliably tails a transcript JSONL file and pushes new lines to the Supervisor.

    Runs as an asyncio task in the Engine process. Handles file position
    tracking, partial line buffering, and phase switches.
    """

    def __init__(
        self,
        supervisor: Supervisor,
        *,
        poll_interval: float = 5.0,
        batch_max_lines: int = 50,
    ) -> None:
        self._supervisor = supervisor
        self._poll_interval = poll_interval
        self._batch_max_lines = batch_max_lines
        self._transcript_path: Path | None = None
        self._file_pos: int = 0
        self._partial_line: str = ""
        self._running = False
        self._task: asyncio.Task | None = None
        self._lines_pushed: int = 0

    async def start(self, transcript_path: str | Path) -> None:
        """Start tailing a transcript file."""
        self._transcript_path = Path(transcript_path)
        self._file_pos = 0
        self._partial_line = ""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("TranscriptTailer started: %s", self._transcript_path)

    def switch(self, transcript_path: str | Path) -> None:
        """Switch to a new transcript file (phase change)."""
        old = self._transcript_path
        self._transcript_path = Path(transcript_path)
        self._file_pos = 0
        self._partial_line = ""
        logger.info("TranscriptTailer switched: %s → %s", old, self._transcript_path)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("TranscriptTailer stopped — %d lines pushed", self._lines_pushed)

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._poll_interval)
                await self._read_and_push()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("TranscriptTailer error")

    async def _read_and_push(self) -> None:
        if self._transcript_path is None or not self._transcript_path.exists():
            return

        try:
            with open(self._transcript_path) as f:
                f.seek(self._file_pos)
                raw = f.read()
                self._file_pos = f.tell()
        except Exception:
            return

        if not raw:
            return

        # Handle partial lines: prepend leftover from last read
        raw = self._partial_line + raw
        self._partial_line = ""

        # Split into complete lines only
        if not raw.endswith("\n"):
            last_newline = raw.rfind("\n")
            if last_newline == -1:
                self._partial_line = raw
                return
            self._partial_line = raw[last_newline + 1:]
            raw = raw[:last_newline + 1]

        lines = [l for l in raw.splitlines() if l.strip()]
        if not lines:
            return

        # Batch into chunks to avoid overwhelming the supervisor
        for i in range(0, len(lines), self._batch_max_lines):
            batch = lines[i:i + self._batch_max_lines]
            content = "\n".join(batch)
            msg = (
                f"## Agent Transcript Update ({len(batch)} new entries)\n"
                f"```jsonl\n{content}\n```\n"
                f"Analyze these events. If you see issues, write a verdict to "
                f"`{self._supervisor._verdicts_file.name}`."
            )
            await self._supervisor._push_message(msg)
            self._lines_pushed += len(batch)


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

class Supervisor:
    """Claude Code instance that monitors the main agent.

    Parameters
    ----------
    run_dir:
        Directory for supervisor's own files (verdicts, control, logs).
    task_description:
        What the monitored agent is doing (injected into supervisor prompt).
    model:
        Model to back the Claude Code supervisor instance.
    api_key:
        OpenRouter API key (or via OPENROUTER_API_KEY env var).
    interrupt_callback:
        Called when the supervisor decides to interrupt the agent.
    broadcast_callback:
        Called to push supervisor events to frontend via WebSocket.
    """

    def __init__(
        self,
        *,
        run_dir: str | Path,
        task_description: str = "",
        model: str = "deepseek/deepseek-v4-flash",
        api_key: str = "",
        interrupt_callback: Callable[[str], Awaitable[None]] | None = None,
        broadcast_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._run_dir = Path(run_dir)
        self._control_file = self._run_dir / "control.jsonl"
        self._verdicts_file = self._run_dir / "supervisor_verdicts.jsonl"
        self._log_file = self._run_dir / "supervisor.log"
        self._task_desc = task_description
        self._model = model
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._interrupt = interrupt_callback
        self._broadcast = broadcast_callback

        self._process: asyncio.subprocess.Process | None = None
        self._log_fd = None
        self._running = False
        self._watchers: list[asyncio.Task] = []
        self._verdicts: list[SupervisorVerdict] = []
        self._trajectory: list[SupervisorTrajectoryEntry] = []
        self._stdout_events: list[dict[str, Any]] = []

        self._tailer: TranscriptTailer | None = None
        self._cycles: int = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def verdicts(self) -> list[SupervisorVerdict]:
        return list(self._verdicts)

    @property
    def trajectory(self) -> list[SupervisorTrajectoryEntry]:
        return list(self._trajectory)

    @property
    def running_summary(self) -> str:
        if self._verdicts:
            v = self._verdicts[-1]
            return f"[{v.outcome.value.upper()}] {v.reason}"
        return ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, transcript_path: str | Path) -> None:
        """Start the supervisor and begin tailing the transcript file.

        Parameters
        ----------
        transcript_path:
            Path to the main agent's live transcript file (JSONL).
            The Engine tails this file and pushes new content to the
            supervisor via stdin.
        """
        if self._running:
            return

        if not shutil.which("claude"):
            logger.error("claude CLI not found — cannot start supervisor")
            return

        self._running = True
        self._run_dir.mkdir(parents=True, exist_ok=True)

        prompt = self._build_prompt()

        env = os.environ.copy()
        env["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
        env["ANTHROPIC_AUTH_TOKEN"] = self._api_key
        env["ANTHROPIC_API_KEY"] = ""
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = self._model

        self._log_fd = open(self._log_file, "w")

        self._process = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )

        await self._push_message(prompt)

        # Start file watchers for supervisor output
        self._watchers = [
            asyncio.create_task(self._watch_control()),
            asyncio.create_task(self._watch_verdicts()),
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._wait_process()),
        ]

        # Start transcript tailer
        self._tailer = TranscriptTailer(self)
        await self._tailer.start(transcript_path)

        self._trajectory.append(SupervisorTrajectoryEntry(
            timestamp=time.time(), event_type="started",
            data={"model": self._model, "pid": self._process.pid,
                   "transcript": str(transcript_path)},
        ))

        logger.info(
            "Supervisor started: model=%s pid=%d transcript=%s",
            self._model, self._process.pid, transcript_path,
        )

    def watch(self, transcript_path: str | Path) -> None:
        """Switch the transcript file being tailed (phase change)."""
        if self._tailer:
            self._tailer.switch(transcript_path)

    async def stop(self) -> None:
        """Stop the supervisor subprocess and tailer."""
        self._running = False

        if self._tailer:
            await self._tailer.stop()

        if self._process and self._process.returncode is None:
            if self._process.stdin:
                self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=30)
            except asyncio.TimeoutError:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    self._process.kill()
        for task in self._watchers:
            task.cancel()
        if self._log_fd:
            self._log_fd.close()
        logger.info(
            "Supervisor stopped — %d verdicts, %d cycles",
            len(self._verdicts), self._cycles,
        )

    # ------------------------------------------------------------------
    # Checkpoint audit
    # ------------------------------------------------------------------

    async def checkpoint(
        self,
        context: str,
        timeout: float = 120.0,
    ) -> SupervisorVerdict:
        """Push a checkpoint audit request and wait for the verdict.

        The supervisor Claude Code instance receives the checkpoint as
        a direct stdin message. It has full conversation context from
        the transcript updates it has been receiving.
        """
        ckpt_id = f"ckpt_{int(time.time())}_{self._cycles}"

        checkpoint_message = (
            f">>> CHECKPOINT AUDIT REQUIRED (id: {ckpt_id}) <<<\n"
            f"The main agent is PAUSED and waiting for your verdict.\n"
            f"Checkpoint context: {context}\n\n"
            f"Analyze the agent's recent progress. You may use tools to investigate.\n"
            f"Then write your verdict to {self._verdicts_file.name}:\n"
            f'{{"checkpoint_id": "{ckpt_id}", '
            f'"outcome": "continue|advise|escalate", '
            f'"reason": "...", "suggestion": "..."}}'
        )

        await self._push_message(checkpoint_message)

        self._trajectory.append(SupervisorTrajectoryEntry(
            timestamp=time.time(), event_type="checkpoint_requested",
            data={"checkpoint_id": ckpt_id, "context": context},
        ))

        verdict = await self._poll_checkpoint_response(ckpt_id, timeout)

        self._verdicts.append(verdict)
        self._cycles += 1
        self._trajectory.append(SupervisorTrajectoryEntry(
            timestamp=time.time(), event_type="checkpoint_response",
            data={
                "checkpoint_id": ckpt_id,
                "outcome": verdict.outcome.value,
                "reason": verdict.reason,
            },
        ))

        if self._broadcast:
            await self._broadcast({
                "event_type": "supervisor_checkpoint",
                "data": {
                    "checkpoint_id": ckpt_id,
                    "context": context,
                    "outcome": verdict.outcome.value,
                    "reason": verdict.reason,
                    "suggestion": verdict.suggestion,
                },
                "timestamp": time.time(),
            })

        await self._handle_verdict(verdict)

        logger.info(
            "Checkpoint [%s] verdict: %s — %s",
            context, verdict.outcome.value, verdict.reason,
        )
        return verdict

    async def _poll_checkpoint_response(
        self, checkpoint_id: str, timeout: float
    ) -> SupervisorVerdict:
        """Poll verdicts file for a response matching the checkpoint_id."""
        hard_deadline = time.time() + 600
        deadline = time.time() + timeout
        last_pos = 0

        if self._verdicts_file.exists():
            last_pos = self._verdicts_file.stat().st_size

        while time.time() < min(deadline, hard_deadline):
            await asyncio.sleep(2)

            if not self._verdicts_file.exists():
                continue

            try:
                with open(self._verdicts_file) as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    last_pos = f.tell()
            except Exception:
                continue

            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    v = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if v.get("checkpoint_id") != checkpoint_id:
                    continue

                if v.get("type") == "heartbeat":
                    deadline = max(deadline, time.time() + 30)
                    continue

                return SupervisorVerdict(
                    outcome=_parse_outcome(v.get("outcome", "continue")),
                    reason=v.get("reason", ""),
                    suggestion=v.get("suggestion", ""),
                    source="checkpoint",
                )

        logger.warning("Checkpoint %s timed out after %.0fs", checkpoint_id, timeout)
        return SupervisorVerdict(
            outcome=AuditOutcome.CONTINUE,
            reason=f"Checkpoint {checkpoint_id} timed out — supervisor did not respond",
            source="checkpoint_timeout",
        )

    # ------------------------------------------------------------------
    # Verdict handling
    # ------------------------------------------------------------------

    async def _handle_verdict(self, verdict: SupervisorVerdict) -> None:
        """Handle a verdict based on its outcome.

        - continue: no action
        - advise: inject suggestion into the main agent's conversation
        - escalate: notify human via broadcast (Engine/frontend handles)
        """
        if verdict.outcome == AuditOutcome.CONTINUE:
            return

        if verdict.outcome == AuditOutcome.ADVISE and self._interrupt:
            msg = (
                f"[Supervisor Advice]\n\n"
                f"{verdict.suggestion or verdict.reason}"
            )
            logger.info("Supervisor advise: %s", verdict.reason)
            await self._interrupt(msg)

        if verdict.outcome == AuditOutcome.ESCALATE:
            logger.warning("Supervisor escalate: %s", verdict.reason)
            if self._broadcast:
                await self._broadcast({
                    "event_type": "supervisor_escalate",
                    "data": {
                        "reason": verdict.reason,
                        "suggestion": verdict.suggestion,
                    },
                    "timestamp": time.time(),
                })

    # ------------------------------------------------------------------
    # File watchers (for supervisor's OUTPUT files)
    # ------------------------------------------------------------------

    async def _watch_control(self) -> None:
        """Poll control.jsonl for interrupt commands from the supervisor."""
        last_pos = 0
        while self._running:
            try:
                await asyncio.sleep(2)
                if not self._control_file.exists():
                    continue
                with open(self._control_file) as f:
                    f.seek(last_pos)
                    lines = f.readlines()
                    last_pos = f.tell()

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        cmd = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if cmd.get("action") == "escalate":
                        msg = cmd.get("message", "Supervisor requests human attention")
                        outcome_str = "escalate"
                        logger.warning("Supervisor escalate via control: %s", msg[:300])
                        self._trajectory.append(SupervisorTrajectoryEntry(
                            timestamp=time.time(), event_type="interrupt",
                            data={"message": msg, "outcome": outcome_str},
                        ))
                        verdict = SupervisorVerdict(
                            outcome=_parse_outcome(outcome_str),
                            reason=msg,
                            source="control",
                        )
                        self._verdicts.append(verdict)
                        if self._broadcast:
                            await self._broadcast({
                                "event_type": "supervisor_escalate",
                                "data": {"message": msg, "outcome": outcome_str},
                                "timestamp": time.time(),
                            })
                        await self._handle_verdict(verdict)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Control watcher error")

    async def _watch_verdicts(self) -> None:
        """Poll supervisor_verdicts.jsonl for new verdicts (continuous monitoring).

        The supervisor sometimes rewrites the whole file instead of appending, so
        new verdicts are detected by line content rather than file offset.
        """
        seen: set[str] = set()
        while self._running:
            try:
                await asyncio.sleep(3)
                if not self._verdicts_file.exists():
                    continue
                lines = self._verdicts_file.read_text().splitlines()

                for line in lines:
                    line = line.strip()
                    if not line or line in seen:
                        continue
                    seen.add(line)
                    try:
                        v = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if v.get("checkpoint_id"):
                        continue

                    verdict = SupervisorVerdict(
                        outcome=_parse_outcome(v.get("outcome", "continue")),
                        reason=v.get("reason", ""),
                        suggestion=v.get("suggestion", ""),
                    )
                    self._verdicts.append(verdict)
                    self._cycles += 1
                    self._trajectory.append(SupervisorTrajectoryEntry(
                        timestamp=time.time(), event_type="verdict",
                        data={
                            "outcome": verdict.outcome.value,
                            "reason": verdict.reason,
                            "cycle": self._cycles,
                        },
                    ))
                    if self._broadcast:
                        await self._broadcast({
                            "event_type": "supervisor_verdict",
                            "data": {
                                "outcome": verdict.outcome.value,
                                "reason": verdict.reason,
                                "suggestion": verdict.suggestion,
                                "cycle": self._cycles,
                            },
                            "timestamp": time.time(),
                        })
                    await self._handle_verdict(verdict)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Verdicts watcher error")

    async def _read_stdout(self) -> None:
        """Parse the supervisor's stream-json stdout."""
        if not self._process or not self._process.stdout:
            return
        while self._running:
            try:
                line = await self._process.stdout.readline()
                if not line:
                    break
                text = line.decode().strip()
                if not text:
                    continue

                if self._log_fd:
                    self._log_fd.write(text + "\n")
                    self._log_fd.flush()

                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue

                self._stdout_events.append(event)

                if event.get("type") == "result":
                    break

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Stdout reader error")

    async def _wait_process(self) -> None:
        """Wait for the Claude Code subprocess to exit."""
        if self._process:
            await self._process.wait()
            self._trajectory.append(SupervisorTrajectoryEntry(
                timestamp=time.time(), event_type="process_exited",
                data={"returncode": self._process.returncode},
            ))
            logger.info("Supervisor process exited: code=%s", self._process.returncode)

    # ------------------------------------------------------------------
    # Stdin push
    # ------------------------------------------------------------------

    async def _push_message(self, message: str) -> None:
        """Push a user message into the supervisor's conversation via stdin."""
        if self._process and self._process.stdin and self._process.returncode is None:
            try:
                msg = json.dumps({
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": message}],
                    },
                })
                self._process.stdin.write(msg.encode() + b"\n")
                await self._process.stdin.drain()
            except Exception as e:
                logger.warning("Failed to push message to supervisor: %s", e)

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def _build_prompt(self) -> str:
        return f"""You are a supervisor agent monitoring another AI agent solving a research task.

You are a **consultant**, not a controller. You cannot stop the agent or change its execution flow. You can only observe, advise, and escalate.

## Task
{self._task_desc}

## How You Receive Information

The main agent's live transcript is pushed to you periodically as JSONL entries. Each entry has:
- `event_type`: "thinking", "tool_call", "tool_result", "text", "error"
- `data`: the event payload
- `timestamp`, `turn`

You can also use your tools (bash, read files, etc.) to investigate the agent's workspace.

## Your Verdicts

Append verdicts to `{self._verdicts_file}` (one JSON per line). Always append — never overwrite earlier verdicts:
```
echo '{{"outcome": "continue|advise|escalate", "reason": "...", "suggestion": "..."}}' >> {self._verdicts_file}
```

## Three Outcomes

- **continue** — Agent making reasonable progress. No action needed.
- **advise** — You see a potential issue. Your `suggestion` will be injected as a message into the agent's conversation. The agent may or may not follow it — that's fine, you're advising, not commanding. Use this when:
  - Agent seems to be going down an unproductive path
  - Agent is missing something obvious
  - You have a better approach to suggest
  - Agent is repeating similar attempts without progress
- **escalate** — You cannot resolve this by advising. The issue needs human attention. Write to `{self._control_file}`: `{{"action": "escalate", "message": "..."}}`. Use this when:
  - Agent has been ignoring your advice repeatedly
  - The situation requires a judgment call beyond your scope
  - Something fundamentally wrong that advising won't fix

## Guidelines

- Default to `continue`. Most of the time the agent is fine.
- `advise` sparingly — too many suggestions are noise. Only advise when you're fairly confident the agent is heading in a wrong direction.
- Write clear, actionable suggestions. "Try X instead of Y because Z" is better than "something seems wrong".
- `escalate` is rare. It means "I, the supervisor, don't know what to do either."
- Early exploration (reading files, trying approaches) is normal. Don't advise too early.

## Checkpoints

When you receive a checkpoint message, the agent is PAUSED. Respond by writing a verdict with the `checkpoint_id`:
```
{{"checkpoint_id": "<id>", "outcome": "continue|advise|escalate", "reason": "...", "suggestion": "..."}}
```

Wait for the first transcript update."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_outcome(value: str) -> AuditOutcome:
    v = value.lower().strip()
    # Map legacy outcomes to the new three
    legacy_to_advise = {"validate", "replan", "reframe", "reject", "recover"}
    if v in legacy_to_advise:
        return AuditOutcome.ADVISE
    try:
        return AuditOutcome(v)
    except ValueError:
        return AuditOutcome.ESCALATE

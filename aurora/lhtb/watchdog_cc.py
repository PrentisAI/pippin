"""Claude Code agentic watchdog — a separate Claude Code instance monitors the main agent.

Architecture
------------
Main Agent (Claude Code #1)
  │  writes events to events.jsonl in real-time
  ▼
events.jsonl  ←── tail / periodic read ──  Watchdog (Claude Code #2)
  ▲                                           │
  │  reads interrupt commands                  │  writes verdicts + interrupts
  └──── control.jsonl  ◄──────────────────────┘

The watchdog runs as a fully agentic Claude Code instance backed by a cheap
model (e.g. DeepSeek V4 Flash via OpenRouter).  It has bash, file read/write,
and all standard Claude Code tools — giving it the ability to inspect the
Docker container, search code, read logs, etc.

Communication is file-based (JSONL), decoupled, observable, and crash-safe.
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

logger = logging.getLogger(__name__)


@dataclass
class WatchdogVerdict:
    level: str = "normal"
    reason: str = ""
    suggestion: str = ""
    confidence: str = "high"
    source: str = "claude-code"
    timestamp: float = field(default_factory=time.time)


@dataclass
class WatchdogTrajectoryEntry:
    timestamp: float = 0.0
    event_type: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class ClaudeCodeWatchdog:
    """Watchdog that runs as a separate Claude Code instance via OpenRouter.

    Interface-compatible with the API-based WatchdogAgent so routes can
    use either interchangeably.
    """

    def __init__(
        self,
        *,
        run_dir: str | Path,
        task_description: str = "",
        model: str = "deepseek/deepseek-v4-flash",
        openrouter_api_key: str = "",
        interrupt_callback: Callable[[str], Awaitable[None]] | None = None,
        broadcast_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._run_dir = Path(run_dir)
        self._events_file = self._run_dir / "events.jsonl"
        self._control_file = self._run_dir / "control.jsonl"
        self._verdicts_file = self._run_dir / "watchdog_verdicts.jsonl"
        self._log_file = self._run_dir / "watchdog.log"
        self._task_desc = task_description
        self._model = model
        self._api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._interrupt = interrupt_callback
        self._broadcast = broadcast_callback
        self._orchestrator: Any = None

        self._process: asyncio.subprocess.Process | None = None
        self._log_fd = None
        self._running = False
        self._watchers: list[asyncio.Task] = []
        self._verdicts: list[WatchdogVerdict] = []
        self._trajectory: list[WatchdogTrajectoryEntry] = []
        self._stdout_events: list[dict[str, Any]] = []

        # Compat with WatchdogAgent interface (used by routes/lhtb.py)
        self._container_id: str | None = None
        self._cycles: int = 0
        self._l2_escalations: int = 0

    # ------------------------------------------------------------------
    # Properties (interface-compatible with WatchdogAgent)
    # ------------------------------------------------------------------

    @property
    def verdicts(self) -> list[WatchdogVerdict]:
        return list(self._verdicts)

    @property
    def trajectory(self) -> list[WatchdogTrajectoryEntry]:
        return list(self._trajectory)

    @property
    def running_summary(self) -> str:
        if self._verdicts:
            v = self._verdicts[-1]
            return f"[{v.level.upper()}] {v.reason}"
        return ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return

        if not shutil.which("claude"):
            logger.error("claude CLI not found — cannot start Claude Code watchdog")
            return

        self._running = True
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._events_file.touch()

        prompt = self._build_prompt()

        env = os.environ.copy()
        env["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
        env["ANTHROPIC_AUTH_TOKEN"] = self._api_key
        env["ANTHROPIC_API_KEY"] = ""
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = self._model

        self._log_fd = open(self._log_file, "w")

        # Launch with stream-json I/O — process stays alive until stdin is closed
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

        # Send initial prompt via stdin (agent-watcher pattern)
        await self.push_message(prompt)

        self._watchers = [
            asyncio.create_task(self._watch_control()),
            asyncio.create_task(self._watch_verdicts()),
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._wait_process()),
        ]

        self._trajectory.append(WatchdogTrajectoryEntry(
            timestamp=time.time(), event_type="started",
            data={"model": self._model, "pid": self._process.pid},
        ))
        # Start orchestrator for checkpoint coordination
        from aurora.lhtb.orchestrator import Orchestrator
        self._orchestrator = Orchestrator(
            watchdog=self,
            api_key=self._api_key,
            model=self._model,
        )

        logger.info(
            "Claude Code watchdog started: model=%s pid=%d run_dir=%s",
            self._model, self._process.pid, self._run_dir,
        )

    async def stop(self) -> None:
        self._running = False
        if self._process and self._process.returncode is None:
            # Close stdin to signal EOF — this is the clean shutdown path
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
        if self._orchestrator:
            await self._orchestrator.close()
        if self._log_fd:
            self._log_fd.close()
        logger.info(
            "Watchdog stopped — %d verdicts, %d cycles",
            len(self._verdicts), self._cycles,
        )

    async def push_message(self, message: str) -> None:
        """Push a user message directly into the watchdog's conversation via stdin.

        Uses the stream-json wire format confirmed by agent-watcher:
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "..."}]}}
        """
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
                logger.warning("Failed to push message to watchdog: %s", e)

    async def on_event(self, event: dict[str, Any]) -> None:
        """Append an event to events.jsonl for the watchdog to read."""
        try:
            with open(self._events_file, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except Exception:
            logger.exception("Failed to write event to %s", self._events_file)

        # Write container ID to file when Docker starts
        if event.get("event_type") == "docker_started":
            cid = event.get("data", {}).get("container_id")
            if cid:
                self._container_id = cid
                (self._run_dir / "container_id").write_text(cid)

    async def on_engine_event(self, event: Any) -> None:
        """Adapter for core engine EventBus — converts Event to dict.

        Use with ``bus.subscribe_all(watchdog.on_engine_event)`` to wire
        the watchdog into the core engine's EventBus.
        """
        ws_event: dict[str, Any] = {
            "type": "lhtb_event",
            "event_type": getattr(event, "name", "unknown"),
            "data": getattr(event, "data", {}),
            "timestamp": time.time(),
        }
        await self.on_event(ws_event)

    # ------------------------------------------------------------------
    # File watchers
    # ------------------------------------------------------------------

    async def _watch_control(self) -> None:
        """Poll control.jsonl for interrupt commands from the watchdog."""
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

                    if cmd.get("action") == "interrupt":
                        msg = cmd.get("message", "Watchdog detected a critical issue")
                        logger.warning("Watchdog interrupt: %s", msg[:300])
                        self._trajectory.append(WatchdogTrajectoryEntry(
                            timestamp=time.time(), event_type="interrupt",
                            data={"message": msg},
                        ))
                        if self._broadcast:
                            await self._broadcast({
                                "event_type": "watchdog_interrupt",
                                "data": {"message": msg},
                                "timestamp": time.time(),
                            })
                        if self._interrupt:
                            await self._interrupt(msg)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Control watcher error")

    async def _watch_verdicts(self) -> None:
        """Poll watchdog_verdicts.jsonl for new verdicts."""
        last_pos = 0
        while self._running:
            try:
                await asyncio.sleep(3)
                if not self._verdicts_file.exists():
                    continue
                with open(self._verdicts_file) as f:
                    f.seek(last_pos)
                    lines = f.readlines()
                    last_pos = f.tell()

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        v = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if v.get("checkpoint_id"):
                        continue

                    verdict = WatchdogVerdict(
                        level=v.get("level", "normal"),
                        reason=v.get("reason", ""),
                        suggestion=v.get("suggestion", ""),
                        confidence=v.get("confidence", "high"),
                    )
                    self._verdicts.append(verdict)
                    self._cycles += 1
                    self._trajectory.append(WatchdogTrajectoryEntry(
                        timestamp=time.time(), event_type="verdict",
                        data={
                            "level": verdict.level,
                            "reason": verdict.reason,
                            "cycle": self._cycles,
                        },
                    ))
                    if self._broadcast:
                        await self._broadcast({
                            "event_type": "watchdog_verdict",
                            "data": {
                                "level": verdict.level,
                                "reason": verdict.reason,
                                "suggestion": verdict.suggestion,
                                "confidence": verdict.confidence,
                                "source": "claude-code",
                                "cycle": self._cycles,
                            },
                            "timestamp": time.time(),
                        })

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Verdicts watcher error")

    # ------------------------------------------------------------------
    # Checkpoint — notify the running watchdog to audit NOW
    # ------------------------------------------------------------------

    async def checkpoint(
        self,
        context: str,
        recent_events: list[dict[str, Any]],
        timeout: float = 120.0,
    ) -> WatchdogVerdict:
        """Push a checkpoint audit request directly into the watchdog's conversation.

        Uses stdin (stream-json) to inject a user message into the running
        watchdog Claude Code instance.  The watchdog is stateful — it has
        full conversation history from continuous monitoring — and receives
        the checkpoint as a direct message, not via file polling.

        Also writes to events.jsonl as backup (in case stdin push fails).
        Falls back to a default verdict on timeout.
        """
        ckpt_id = f"ckpt_{int(time.time())}_{self._cycles}"

        checkpoint_message = (
            f">>> CHECKPOINT AUDIT REQUIRED (id: {ckpt_id}) <<<\n"
            f"The main agent is PAUSED and waiting for your verdict.\n"
            f"Checkpoint context: {context}\n\n"
            f"Analyze the agent's recent progress. You may use tools to investigate.\n"
            f"Then write your verdict to {self._verdicts_file.name}:\n"
            f'{{"checkpoint_id": "{ckpt_id}", "level": "normal|warning|critical", '
            f'"reason": "...", "suggestion": "..."}}'
        )

        # Push directly into watchdog's conversation via stdin (stream-json format)
        if self._process and self._process.stdin and self._process.returncode is None:
            try:
                msg = json.dumps({
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": checkpoint_message}],
                    },
                })
                self._process.stdin.write(msg.encode() + b"\n")
                await self._process.stdin.drain()
                logger.info("Checkpoint %s pushed to watchdog stdin", ckpt_id)
            except Exception as e:
                logger.warning("Failed to push checkpoint via stdin: %s", e)

        # Also write to events.jsonl as backup
        request_event = {
            "type": "CHECKPOINT_REQUEST",
            "checkpoint_id": ckpt_id,
            "context": context,
            "message": checkpoint_message,
            "timestamp": time.time(),
        }
        try:
            with open(self._events_file, "a") as f:
                f.write(json.dumps(request_event, default=str) + "\n")
        except Exception:
            logger.exception("Failed to write checkpoint request to events file")

        self._trajectory.append(WatchdogTrajectoryEntry(
            timestamp=time.time(), event_type="checkpoint_requested",
            data={"checkpoint_id": ckpt_id, "context": context},
        ))

        # Start orchestrator monitoring (heartbeats + stuck detection)
        monitor_task = None
        if self._orchestrator:
            monitor_task = asyncio.create_task(
                self._orchestrator.monitor_checkpoint(ckpt_id, self._verdicts_file)
            )

        # Poll watchdog_verdicts.jsonl for the response (with heartbeat support)
        try:
            verdict = await self._poll_checkpoint_response(ckpt_id, timeout)
        finally:
            if monitor_task and not monitor_task.done():
                monitor_task.cancel()

        self._verdicts.append(verdict)
        self._cycles += 1
        self._trajectory.append(WatchdogTrajectoryEntry(
            timestamp=time.time(), event_type="checkpoint_response",
            data={
                "checkpoint_id": ckpt_id,
                "context": context,
                "level": verdict.level,
                "reason": verdict.reason,
            },
        ))
        if self._broadcast:
            await self._broadcast({
                "event_type": "watchdog_checkpoint",
                "data": {
                    "checkpoint_id": ckpt_id,
                    "context": context,
                    "level": verdict.level,
                    "reason": verdict.reason,
                    "suggestion": verdict.suggestion,
                    "source": "checkpoint",
                    "cycle": self._cycles,
                },
                "timestamp": time.time(),
            })
        logger.info("Checkpoint [%s] verdict: %s — %s", context, verdict.level, verdict.reason)
        return verdict

    async def _poll_checkpoint_response(
        self, checkpoint_id: str, timeout: float
    ) -> WatchdogVerdict:
        """Poll verdicts file for a response matching the checkpoint_id.

        Supports heartbeat messages from the orchestrator: when a heartbeat
        with the matching checkpoint_id arrives, the deadline is extended
        so the main agent keeps waiting while the watchdog is active.
        """
        hard_deadline = time.time() + 600  # absolute max: 10 minutes
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

                # Heartbeat from orchestrator — extend deadline
                if v.get("type") == "heartbeat":
                    deadline = max(deadline, time.time() + 30)
                    logger.debug(
                        "Checkpoint %s heartbeat received, deadline extended",
                        checkpoint_id,
                    )
                    continue

                # Actual verdict — return it
                return WatchdogVerdict(
                    level=v.get("level", "normal"),
                    reason=v.get("reason", ""),
                    suggestion=v.get("suggestion", ""),
                    confidence=v.get("confidence", "high"),
                    source="checkpoint",
                )

        logger.warning("Checkpoint %s timed out after %.0fs", checkpoint_id, timeout)
        return WatchdogVerdict(
            level="normal",
            reason=f"Checkpoint {checkpoint_id} timed out — watchdog did not respond",
            source="checkpoint_timeout",
        )

    async def _read_stdout(self) -> None:
        """Parse the watchdog's stream-json stdout in real-time.

        Mirrors agent-watcher's main loop: read each JSON event, log it,
        detect verdicts embedded in assistant messages, and stop on 'result'.
        """
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

                # Log raw output
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
            self._trajectory.append(WatchdogTrajectoryEntry(
                timestamp=time.time(), event_type="process_exited",
                data={"returncode": self._process.returncode},
            ))
            logger.info("Watchdog process exited: code=%s", self._process.returncode)

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def _build_prompt(self) -> str:
        return f"""You are an autonomous watchdog agent monitoring another AI agent that is solving a terminal-based task in real-time.

## Task the Monitored Agent is Working On
{self._task_desc}

## Your Communication Files

All files are in: {self._run_dir}

1. **Event stream** (READ-ONLY): `{self._events_file}`
   - JSONL format — one JSON object per line, appended in real-time
   - Each line: {{"type": "lhtb_event", "event_type": "...", "data": {{...}}, "timestamp": ...}}
   - Key event_types: `reasoning` (agent thinking), `tool_call_start` (agent using a tool), `tool_call_result` (tool output), `phase_started`, `verifier_result` (task scoring), `run_finished`

2. **Your verdicts** (WRITE): `{self._verdicts_file}`
   - Append one JSON per line when you have a judgment:
   - Format: {{"level": "normal|warning|critical", "reason": "one-sentence explanation", "suggestion": "what to do differently", "confidence": "high|medium|low"}}

3. **Interrupt commands** (WRITE): `{self._control_file}`
   - ONLY for critical issues that require stopping the agent:
   - Format: {{"action": "interrupt", "message": "detailed description of the problem and suggested fix"}}

4. **Container ID** (READ, appears after Docker starts): `{self._run_dir / "container_id"}`
   - Contains the Docker container ID — use `docker exec <id> <command>` to inspect the agent's environment

## Monitoring Protocol

1. Read `{self._events_file.name}` to understand what has happened so far
2. Count the lines to know your current position
3. Enter a monitoring loop:
   a. Wait ~20 seconds
   b. Read new lines from the events file (use `tail -n +<line_number>` to read from where you left off)
   c. Analyze the new events for concerning patterns
   d. Write a verdict to `{self._verdicts_file.name}`
   e. If critical, also write to `{self._control_file.name}`
   f. Repeat from (a)
4. When you see a `run_finished` event, write a final summary verdict and stop

## ⚠️ CHECKPOINT REQUESTS (HIGHEST PRIORITY)

When you see an event with `"type": "CHECKPOINT_REQUEST"` in the events file, the main agent
is **PAUSED and waiting for YOUR verdict**. You MUST respond immediately:

1. Stop whatever you're doing and focus on the checkpoint
2. Read the checkpoint context and analyze the agent's recent progress
3. Investigate if needed (docker exec, read files, etc.)
4. Write your verdict to `{self._verdicts_file.name}` — you MUST include the `checkpoint_id` from the request:
   `{{"checkpoint_id": "<from request>", "level": "normal|warning|critical", "reason": "...", "suggestion": "..."}}`
5. The main agent **cannot continue** until you respond. Be fast but thorough.

If you miss a checkpoint request, the main agent will be stuck waiting. Always prioritize
checkpoint requests over routine monitoring.

## What to Watch For

**Critical (interrupt immediately):**
- Agent stuck in an infinite loop — repeating the exact same commands 3+ times
- Agent destroying its own progress (deleting work, overwriting correct solutions)
- Agent completely ignoring persistent errors without changing approach
- Agent running dangerous commands that could break the environment

**Warning (note but don't interrupt):**
- Agent trying the same general approach repeatedly with minor variations
- Agent exploring irrelevant paths but may self-correct
- Agent not making visible progress for several turns
- Agent making inefficient choices but still moving forward

**Normal:**
- Agent making steady progress, even if the approach is unconventional
- Agent recovering from errors and adapting its strategy
- Agent exploring before committing to an approach (early in the task)

## Important Rules
- You are READ-ONLY on the events file — never modify it
- Only interrupt on genuinely critical issues — false alarms disrupt the agent's work
- Your verdicts should include enough context to be useful on their own
- If the container_id file exists, you can use docker exec for deeper investigation
- Keep your analysis focused and efficient — you're running on a budget model

Start monitoring now."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from text that may contain reasoning around it."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None

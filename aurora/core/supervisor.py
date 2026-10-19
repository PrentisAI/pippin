"""Live auditor (supervisor): a Claude Code session that watches the research agent.

The research agent writes its trajectory to a JSONL transcript as it works.
The engine tails that file and pushes new entries into the supervisor's
session; the supervisor may also look at the workspace and, read-only, into
the container.  After each update it answers with a verdict line::

    VERDICT {"outcome": "advise", "reason": "...", "suggestion": "..."}

Outcomes (Section 3.3 of the paper):

* ``continue``  — nothing to do;
* ``advise``    — the suggestion is sent to the research agent, which keeps working;
* ``interrupt`` — the agent's current action is stopped and the correction sent;
* ``escalate``  — the engine opens a human gate.

The supervisor advises; it cannot change the pipeline itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from aurora.llm.agent_loops import claude_code_env, permission_args
from aurora.models.audit import AuditOutcome

logger = logging.getLogger(__name__)

_VERDICT_RE = re.compile(r"VERDICT\s*:?\s*(\{.*?\})\s*$", re.MULTILINE)


@dataclass
class SupervisorVerdict:
    outcome: AuditOutcome = AuditOutcome.CONTINUE
    reason: str = ""
    suggestion: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome.value, "reason": self.reason,
                "suggestion": self.suggestion, "timestamp": self.timestamp}


def parse_verdicts(text: str) -> list[SupervisorVerdict]:
    """Verdict lines in a supervisor reply; unknown outcomes count as advise."""
    verdicts = []
    for match in _VERDICT_RE.finditer(text):
        try:
            v = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        raw = str(v.get("outcome", "continue")).lower().strip()
        try:
            outcome = AuditOutcome(raw)
        except ValueError:
            outcome = AuditOutcome.ADVISE
        if outcome not in (AuditOutcome.CONTINUE, AuditOutcome.ADVISE,
                           AuditOutcome.INTERRUPT, AuditOutcome.ESCALATE):
            outcome = AuditOutcome.ADVISE
        verdicts.append(SupervisorVerdict(outcome, str(v.get("reason", "")), str(v.get("suggestion", ""))))
    return verdicts


class TranscriptTailer:
    """Tails the research agent's transcript and pushes complete new lines to the supervisor."""

    def __init__(self, push: Callable[[str], Awaitable[None]], *, poll_interval: float = 5.0,
                 batch_max_lines: int = 50) -> None:
        self._push = push
        self._poll_interval = poll_interval
        self._batch_max_lines = batch_max_lines
        self._path: Path | None = None
        self._pos = 0
        self._partial = ""
        self._task: asyncio.Task | None = None
        self.lines_pushed = 0

    async def start(self, path: str | Path) -> None:
        self._path = Path(path)
        self._pos = self._path.stat().st_size if self._path.exists() else 0  # only new activity
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                await self._read_and_push()
            except Exception:
                logger.exception("Transcript tailer error")

    async def _read_and_push(self) -> None:
        if self._path is None or not self._path.exists():
            return
        with open(self._path) as fh:
            fh.seek(self._pos)
            raw = fh.read()
            self._pos = fh.tell()
        raw, self._partial = self._partial + raw, ""
        if not raw:
            return
        if not raw.endswith("\n"):
            cut = raw.rfind("\n")
            raw, self._partial = (raw[:cut + 1], raw[cut + 1:]) if cut >= 0 else ("", raw)
        lines = [line for line in raw.splitlines() if line.strip()]
        for i in range(0, len(lines), self._batch_max_lines):
            batch = lines[i:i + self._batch_max_lines]
            await self._push(f"## Transcript update ({len(batch)} new entries)\n```jsonl\n"
                             + "\n".join(batch) + "\n```\nEnd your reply with one VERDICT line.")
            self.lines_pushed += len(batch)


class Supervisor:
    """Runs the live auditor next to the research agent.

    Parameters
    ----------
    run_dir:
        Where the supervisor's log and verdicts are written.
    task_description:
        The research objective and direction, shown to the supervisor.
    model, api_key, base_url:
        Model and Anthropic-compatible endpoint of the Claude Code session.
    cwd:
        The campaign workspace; the supervisor may read files under it.
    docker_container:
        The campaign container, for read-only ``docker exec`` inspection.
    advise_callback(message):
        Sends a message to the research agent.
    interrupt_callback(message):
        Stops the research agent's current action, then sends the message.
    escalate_callback(verdict):
        Opens a human gate.
    broadcast_callback(event):
        Publishes supervisor events (``{"event_type", "data"}``).
    """

    def __init__(
        self,
        *,
        run_dir: str | Path,
        task_description: str,
        model: str,
        api_key: str,
        base_url: str,
        cwd: str | Path,
        docker_container: str | None = None,
        advise_callback: Callable[[str], Awaitable[None]] | None = None,
        interrupt_callback: Callable[[str], Awaitable[None]] | None = None,
        escalate_callback: Callable[[SupervisorVerdict], Awaitable[None]] | None = None,
        broadcast_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._run_dir = Path(run_dir)
        self._task_desc = task_description
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._cwd = Path(cwd)
        self._container = docker_container
        self._advise = advise_callback
        self._interrupt = interrupt_callback
        self._escalate = escalate_callback
        self._broadcast = broadcast_callback
        self._process: asyncio.subprocess.Process | None = None
        self._tailer: TranscriptTailer | None = None
        self._reader: asyncio.Task | None = None
        self._log_fd: Any = None
        self.verdicts: list[SupervisorVerdict] = []
        self.cost_usd = 0.0

    @property
    def running_summary(self) -> str:
        if not self.verdicts:
            return ""
        v = self.verdicts[-1]
        return f"{v.outcome.value}: {v.reason}"

    async def start(self, transcript_path: str | Path) -> None:
        if self._process is not None:
            return
        if not shutil.which("claude"):
            logger.error("Claude Code CLI not found; the live auditor is not running")
            return
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._log_fd = open(self._run_dir / "supervisor.log", "a")
        self._process = await asyncio.create_subprocess_exec(
            "claude", "-p", "--output-format", "stream-json", "--input-format", "stream-json", "--verbose",
            *permission_args(self._cwd, self._container),
            "--disallowedTools", "Edit", "Write", "NotebookEdit",
            "--model", self._model,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=str(self._cwd),
            env=claude_code_env(self._api_key, self._base_url),
            limit=64 * 1024 * 1024,
        )
        await self._push(self._build_prompt())
        self._reader = asyncio.create_task(self._read_stdout())
        self._tailer = TranscriptTailer(self._push)
        await self._tailer.start(transcript_path)
        logger.info("Live auditor started (model=%s)", self._model)

    async def stop(self) -> None:
        if self._tailer:
            await self._tailer.stop()
            self._tailer = None
        if self._process and self._process.returncode is None:
            if self._process.stdin:
                self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=20)
            except TimeoutError:
                self._process.kill()
        if self._reader:
            self._reader.cancel()
        if self._log_fd:
            self._log_fd.close()
            self._log_fd = None
        self._process = None
        logger.info("Live auditor stopped after %d verdicts", len(self.verdicts))

    async def _push(self, message: str) -> None:
        if self._process and self._process.stdin and self._process.returncode is None:
            line = json.dumps({"type": "user", "message": {
                "role": "user", "content": [{"type": "text", "text": message}]}})
            try:
                self._process.stdin.write(line.encode() + b"\n")
                await self._process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                logger.warning("Could not reach the live auditor: %s", exc)

    async def _read_stdout(self) -> None:
        """Read the supervisor's stream-json output for as long as it runs."""
        assert self._process and self._process.stdout
        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if self._log_fd and text:
                self._log_fd.write(text + "\n")
                self._log_fd.flush()
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                # total_cost_usd is the session's running total, not the cost of this turn
                self.cost_usd = max(self.cost_usd, float(event.get("total_cost_usd") or 0.0))
            if event.get("type") != "assistant":
                continue
            for block in event.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    for verdict in parse_verdicts(block.get("text", "")):
                        try:
                            await self._handle(verdict)
                        except Exception:
                            logger.exception("Handling a live-auditor verdict failed")

    async def _handle(self, verdict: SupervisorVerdict) -> None:
        self.verdicts.append(verdict)
        with open(self._run_dir / "verdicts.jsonl", "a") as fh:
            fh.write(json.dumps(verdict.to_dict(), ensure_ascii=False) + "\n")
        if self._broadcast:
            await self._broadcast({"event_type": f"supervisor_{verdict.outcome.value}",
                                   "data": verdict.to_dict()})
        text = verdict.suggestion or verdict.reason
        if verdict.outcome == AuditOutcome.ADVISE and self._advise:
            logger.info("Live auditor advises: %s", verdict.reason)
            await self._advise(f"[Live auditor — advice]\n{text}")
        elif verdict.outcome == AuditOutcome.INTERRUPT and self._interrupt:
            logger.warning("Live auditor interrupts: %s", verdict.reason)
            await self._interrupt(f"[Live auditor — your last action was stopped]\n{text}\n"
                                  "Correct course before you continue.")
        elif verdict.outcome == AuditOutcome.ESCALATE and self._escalate:
            logger.warning("Live auditor escalates: %s", verdict.reason)
            await self._escalate(verdict)

    def _build_prompt(self) -> str:
        container = (f"\nThe agent works in Docker container `{self._container}`; you may inspect it "
                     f"read-only with `docker exec {self._container} ...` (ls, cat, head, python -c to "
                     f"load a result). Never change files or state in it." if self._container else "")
        return f"""You are the live auditor of a research agent working on this task:

{self._task_desc}

You receive the agent's live transcript (JSONL entries: thinking, tool_call, tool_result,
text, error) in updates. You may read the campaign workspace ({self._cwd}).{container}

After every update, end your reply with exactly one verdict line:
VERDICT {{"outcome": "continue|advise|interrupt|escalate", "reason": "...", "suggestion": "..."}}

- continue — reasonable progress. The default; early exploration is normal.
- advise — a likely problem the agent can fix while it keeps working: a wrong method or
  assumption, a silent failure, results that do not support the claims made, a loop
  without progress. The suggestion is sent to the agent: make it concrete
  ("do X instead of Y because Z").
- interrupt — the current action is harmful or wasteful (it destroys data, burns a lot of
  compute on a known-wrong setup, fabricates results). The action is stopped and your
  suggestion sent.
- escalate — a decision only a human should make (the objective looks wrong or infeasible,
  repeated advice is ignored, a risky or costly step). The human is asked.

Advise sparingly and only when fairly confident; most updates should be continue.
Wait for the first transcript update."""

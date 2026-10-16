"""Full-duplex runtime using Claude Code SDK sessions.

Two execution modes:

**Interactive** (human-in-the-loop):
  Cerebellum (Claude Code session) handles user chat while Brain
  (Claude Code session) runs computation in the background.  The
  Harness coordinates both, injecting Brain state into Cerebellum
  context so the user always gets up-to-date information.

**Autonomous** (no human):
  Brain runs directly, no Cerebellum.  The Harness dispatches jobs
  and collects results.

Both Cerebellum and Brain are full Claude Code sessions with built-in
file I/O, shell access, and automatic context management.

Architecture::

    Interactive mode:
      User <-> Harness (Python event loop) <-> Frontend
                  |                    |
         Cerebellum session      Brain session(s)
         (Claude Code)           (Claude Code)

    Autonomous mode:
      Harness -> Brain session(s) (Claude Code)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ClaudeSDKClient,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
)

from aurora.llm.tool_runner import AgenticResult, StreamCallback, StreamEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BrainEvent:
    """A single event from a Brain session."""

    event_type: str  # text, tool_use, tool_result, thinking, done, error
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class BrainState:
    """Tracks the current state of one Brain session."""

    task_id: str
    description: str
    task_type: str = "research"
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    events: list[BrainEvent] = field(default_factory=list)
    result_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    num_turns: int = 0
    cost_usd: float = 0.0
    session_id: str = ""

    def summarize(self) -> str:
        """One-paragraph summary for Cerebellum context injection."""
        lines = [
            f"**{self.task_id}** [{self.status.value}] "
            f"({self.task_type}): {self.description[:200]}"
        ]
        if self.tool_calls:
            last = self.tool_calls[-1]
            lines.append(
                f"  Last tool: {last.get('name', '?')} "
                f"({len(self.tool_calls)} total calls)"
            )
        if self.status == TaskStatus.COMPLETED and self.result_text:
            lines.append(f"  Result: {self.result_text[:300]}")
        if self.error:
            lines.append(f"  Error: {self.error[:200]}")
        return "\n".join(lines)


@dataclass
class _HarnessCommand:
    """Internal command from Cerebellum MCP tools to Harness."""

    command: str  # start_brain, send_to_brain, cancel_brain
    task_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

CEREBELLUM_SYSTEM_PROMPT = """\
You are the interaction coordinator for AURORA, a computational \
materials science research platform. You manage real-time communication \
with the human researcher while background computation runs asynchronously.

## Your Role
- **Respond to the user immediately** — never make them wait for computation
- **Dispatch heavy tasks** to the background Brain when computation is needed
- **Monitor progress** and keep the user informed
- **Summarize results** when background tasks complete
- **Modify or cancel tasks** if the user changes their mind

## Tools
You have 4 task management tools (prefixed with aurora__):
- `aurora__task_start` — start a background computation task
- `aurora__task_send` — send a directive to a running task
- `aurora__task_cancel` — cancel a running task
- `aurora__check_status` — get current status of all tasks

You also have full Claude Code capabilities — you can read files, explore \
the workspace, and run analysis directly for quick tasks that don't need \
heavy computation.

## Guidelines
- Quick questions (concepts, plans, reading files) → handle directly
- Anything needing heavy computation (DFT, MLIP, structure relaxation) → task_start
- When a task finishes → read the result files and summarize key findings
- If the user wants changes mid-execution → task_send or task_cancel
- Be concise and scientifically precise
- Tell the user what's happening ("DFT started — you can keep chatting")
"""


# ---------------------------------------------------------------------------
# MCP tool builders
# ---------------------------------------------------------------------------


def _build_cerebellum_mcp_tools(
    command_queue: asyncio.Queue[_HarnessCommand],
    get_status_fn: Any,
) -> list[SdkMcpTool]:
    """Build MCP tools for the Cerebellum session.

    These tools let the Cerebellum dispatch work to the Harness,
    which in turn starts/manages Brain sessions.
    """

    async def task_start(params: dict[str, Any]) -> dict[str, Any]:
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        await command_queue.put(
            _HarnessCommand(
                command="start_brain",
                task_id=task_id,
                params=params,
            )
        )
        return {
            "status": "started",
            "task_id": task_id,
            "description": params.get("description", ""),
        }

    async def task_send(params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("task_id", "")
        message = params.get("message", "")
        await command_queue.put(
            _HarnessCommand(
                command="send_to_brain",
                task_id=task_id,
                params={"message": message},
            )
        )
        return {"status": "sent", "task_id": task_id}

    async def task_cancel(params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("task_id", "")
        await command_queue.put(
            _HarnessCommand(
                command="cancel_brain",
                task_id=task_id,
                params={"reason": params.get("reason", "")},
            )
        )
        return {"status": "cancelled", "task_id": task_id}

    async def check_status(params: dict[str, Any]) -> dict[str, Any]:
        return {"status_report": get_status_fn()}

    return [
        SdkMcpTool(
            name="task_start",
            description=(
                "Start a new background computation task. Use for DFT, MLIP, "
                "ASE, literature search, analysis, or any heavy work."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What the task should accomplish",
                    },
                    "task_type": {
                        "type": "string",
                        "enum": [
                            "research", "dft", "mlip", "ase",
                            "literature", "analysis",
                        ],
                        "description": "Category of computation",
                    },
                    "instructions": {
                        "type": "string",
                        "description": (
                            "Detailed instructions for the Brain agent. "
                            "Include specific parameters, file paths, and "
                            "expected outputs."
                        ),
                    },
                },
                "required": ["description", "task_type", "instructions"],
            },
            handler=task_start,
        ),
        SdkMcpTool(
            name="task_send",
            description="Send a directive to a running background task.",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "message": {
                        "type": "string",
                        "description": "The directive or question",
                    },
                },
                "required": ["task_id", "message"],
            },
            handler=task_send,
        ),
        SdkMcpTool(
            name="task_cancel",
            description="Cancel a running background task.",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["task_id"],
            },
            handler=task_cancel,
        ),
        SdkMcpTool(
            name="check_status",
            description="Get current status of all background tasks.",
            input_schema={"type": "object", "properties": {}},
            handler=check_status,
        ),
    ]


# ---------------------------------------------------------------------------
# HarnessRuntime
# ---------------------------------------------------------------------------


class HarnessRuntime:
    """Coordinates Cerebellum and Brain Claude Code sessions.

    Two modes controlled by the caller:

    - :meth:`run` — interactive mode with Cerebellum + Brain(s).
    - :meth:`run_autonomous` — autonomous mode with Brain only.

    Communication flow (interactive)::

        User ──→ Harness ──→ Cerebellum (Claude Code)
                    │              ↓ task_start (MCP tool)
                    │         _command_queue
                    │              ↓
                    ├──────→ Brain (Claude Code, background asyncio.Task)
                    │              ↓ events
                    │         _brain_states
                    │              ↓ (injected as context)
                    └──────→ Cerebellum (next turn, with brain state)
                    └──────→ Frontend (stream events via callback)
    """

    def __init__(
        self,
        *,
        cerebellum_model: str = "claude-sonnet-4-20250514",
        brain_model: str = "claude-opus-4-8",
        brain_system_prompt: str = "",
        workspace_root: str | Path = ".",
        max_brain_turns: int = 30,
        stream_callback: StreamCallback | None = None,
        research_context: str = "",
    ) -> None:
        self._cb_model = cerebellum_model
        self._brain_model = brain_model
        self._brain_system_prompt = brain_system_prompt
        self._workspace_root = Path(workspace_root)
        self._max_brain_turns = max_brain_turns
        self._stream_callback = stream_callback
        self._research_context = research_context

        # State
        self._brain_states: dict[str, BrainState] = {}
        self._brain_tasks: dict[str, asyncio.Task[None]] = {}
        self._command_queue: asyncio.Queue[_HarnessCommand] = asyncio.Queue()
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._user_queue: asyncio.Queue[str] = asyncio.Queue()
        self._running = False
        self._all_results: list[AgenticResult] = []
        self._cerebellum_client: ClaudeSDKClient | None = None
        self._cerebellum_session_id: str | None = None
        self._brain_session_id: str | None = None

        (self._workspace_root / "tasks").mkdir(parents=True, exist_ok=True)

    @property
    def user_queue(self) -> asyncio.Queue[str]:
        """External queue for the API/WebSocket layer to push user messages."""
        return self._user_queue

    @property
    def brain_states(self) -> dict[str, BrainState]:
        return self._brain_states

    # ------------------------------------------------------------------
    # Checkpoint persistence
    # ------------------------------------------------------------------

    def save_checkpoint(self) -> None:
        """Persist current runtime state to disk for crash recovery.

        Saves:
        - Each BrainState (status, description, task_type, result summary)
        - Completed AgenticResults (already on disk as task outputs)
        - Runtime metadata (which tasks exist, their statuses)

        On resume, completed tasks are skipped and their results reloaded
        from the task directories.
        """
        cp_path = self._workspace_root / "tasks" / "_runtime_checkpoint.json"
        task_states = {}
        for tid, state in self._brain_states.items():
            task_states[tid] = {
                "task_id": state.task_id,
                "description": state.description,
                "task_type": state.task_type,
                "status": state.status.value,
                "created_at": state.created_at,
                "completed_at": state.completed_at,
                "result_text": state.result_text[:2000],
                "num_turns": state.num_turns,
                "cost_usd": state.cost_usd,
                "error": state.error,
            }
        checkpoint = {
            "task_states": task_states,
            "cerebellum_session_id": self._cerebellum_session_id,
            "brain_session_id": self._brain_session_id,
            "timestamp": time.time(),
        }
        cp_path.write_text(
            json.dumps(checkpoint, indent=2, default=str), encoding="utf-8"
        )
        logger.info(
            "Runtime checkpoint saved: %d tasks (%d completed)",
            len(task_states),
            sum(1 for s in task_states.values() if s["status"] == "completed"),
        )

    def load_checkpoint(self) -> bool:
        """Restore runtime state from a prior checkpoint.

        Returns True if a checkpoint was loaded. Completed tasks are
        marked in ``_brain_states`` so ``_start_brain_task`` can skip
        them, and their results are reloaded from task directories.
        """
        cp_path = self._workspace_root / "tasks" / "_runtime_checkpoint.json"
        if not cp_path.exists():
            return False

        try:
            checkpoint = json.loads(cp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt runtime checkpoint — starting fresh")
            return False

        self._cerebellum_session_id = checkpoint.get("cerebellum_session_id")
        self._brain_session_id = checkpoint.get("brain_session_id")
        task_states = checkpoint.get("task_states", {})
        restored = 0
        for tid, sdata in task_states.items():
            status = TaskStatus(sdata.get("status", "pending"))
            state = BrainState(
                task_id=tid,
                description=sdata.get("description", ""),
                task_type=sdata.get("task_type", "research"),
                status=status,
                created_at=sdata.get("created_at", time.time()),
                completed_at=sdata.get("completed_at"),
                result_text=sdata.get("result_text", ""),
                num_turns=sdata.get("num_turns", 0),
                cost_usd=sdata.get("cost_usd", 0.0),
                error=sdata.get("error"),
            )
            self._brain_states[tid] = state

            # Reload results for completed tasks
            if status == TaskStatus.COMPLETED:
                task_dir = self._workspace_root / "tasks" / tid
                if task_dir.exists():
                    observations = _read_jsonl(task_dir / "observations.jsonl")
                    claims = _read_jsonl(task_dir / "claims.jsonl")
                    decision = _read_json(task_dir / "decision.json")
                    self._all_results.append(AgenticResult(
                        final_text=state.result_text,
                        tool_calls=[],
                        observations=observations,
                        claims=claims,
                        decision=(
                            decision.get("decision", "continue")
                            if decision else "continue"
                        ),
                        token_usage={},
                        turns=state.num_turns,
                    ))
                    restored += 1

        logger.info(
            "Runtime checkpoint loaded: %d tasks, %d completed results restored",
            len(task_states), restored,
        )
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _emit(
        self, event_type: str, data: dict[str, Any] | None = None
    ) -> None:
        if self._stream_callback is not None:
            event = StreamEvent(event_type=event_type, data=data or {})
            try:
                await self._stream_callback(event)
            except Exception:
                logger.debug("Stream callback error", exc_info=True)

    def _get_brain_summary(self) -> str:
        if not self._brain_states:
            return "No background tasks."
        return "\n\n".join(s.summarize() for s in self._brain_states.values())

    def _has_running_brains(self) -> bool:
        return any(
            s.status == TaskStatus.RUNNING for s in self._brain_states.values()
        )

    # ==================================================================
    # Interactive mode
    # ==================================================================

    async def run(self, initial_goal: str) -> list[AgenticResult]:
        """Run in interactive mode: Cerebellum handles chat, Brains compute.

        The Cerebellum runs as a **persistent Claude Code session** —
        all turns happen within the same conversation, so it remembers
        prior exchanges with the user (just like a normal chat).

        Returns all :class:`AgenticResult` objects from completed Brain
        sessions.
        """
        self._running = True
        await self._emit(
            "duplex_started",
            {
                "cerebellum_model": self._cb_model,
                "brain_model": self._brain_model,
                "mode": "interactive",
            },
        )

        # Build Cerebellum MCP server for task management
        cb_tools = _build_cerebellum_mcp_tools(
            self._command_queue, self._get_brain_summary
        )
        mcp_server = create_sdk_mcp_server(cb_tools)

        cb_system = (
            f"{CEREBELLUM_SYSTEM_PROMPT}\n\n"
            f"## Research Context\n{self._research_context}\n\n"
            f"## Workspace\nRoot: `{self._workspace_root}`\n"
        )

        cb_options = ClaudeCodeOptions(
            model=self._cb_model,
            system_prompt=cb_system,
            cwd=str(self._workspace_root),
            permission_mode="bypassPermissions",
            max_turns=10,
            mcp_servers={
                "aurora": {
                    "type": "sdk",
                    "name": "aurora",
                    "instance": mcp_server,
                }
            },
        )

        # Resume prior Cerebellum session if checkpoint has a session ID
        if self._cerebellum_session_id:
            cb_options = ClaudeCodeOptions(
                **{
                    **{
                        k: v for k, v in cb_options.__dict__.items()
                        if v is not None and k != "resume"
                    },
                    "resume": self._cerebellum_session_id,
                }
            )
            logger.info(
                "Resuming Cerebellum session %s", self._cerebellum_session_id
            )

        # Create persistent Cerebellum session
        self._cerebellum_client = ClaudeSDKClient(cb_options)

        # Relay user messages from WebSocket into the event queue
        relay = asyncio.create_task(self._relay_user_messages())

        try:
            if self._cerebellum_session_id:
                # Resuming — reconnect and inform Cerebellum of the restart
                await self._cerebellum_client.connect()
                await self._cerebellum_turn(
                    "[System] Session resumed after interruption. "
                    "Check the status of background tasks and continue."
                )
            else:
                # Fresh start
                initial_prompt = (
                    f"## Goal\n\n{initial_goal}\n\n"
                    f"Analyze the goal and start appropriate background "
                    f"computation tasks."
                )
                await self._cerebellum_client.connect(initial_prompt)
                await self._drain_cerebellum_response()
            await self._process_commands()

            # Main event loop
            while self._running:
                try:
                    event = await asyncio.wait_for(
                        self._event_queue.get(), timeout=300.0
                    )
                except asyncio.TimeoutError:
                    if not self._has_running_brains():
                        break
                    continue

                await self._handle_event(event)

        finally:
            self._running = False
            relay.cancel()
            if self._cerebellum_client is not None:
                self._cerebellum_client.disconnect()
                self._cerebellum_client = None
            for task in self._brain_tasks.values():
                if not task.done():
                    task.cancel()
            await self._emit(
                "duplex_finished",
                {
                    "tasks_completed": sum(
                        1
                        for s in self._brain_states.values()
                        if s.status == TaskStatus.COMPLETED
                    ),
                    "tasks_total": len(self._brain_states),
                },
            )

        return self._all_results

    async def _handle_event(self, event: dict[str, Any]) -> None:
        """Dispatch a single event from the main loop."""
        etype = event.get("type", "")

        if etype == "user_message":
            text = event.get("text", "")
            await self._emit("user_message_received", {"text": text})
            brain_ctx = self._get_brain_summary()
            msg = (
                f"[Background Tasks]\n{brain_ctx}\n\n"
                f"[User Message]\n{text}"
            )
            await self._cerebellum_turn(msg)
            await self._process_commands()

        elif etype == "brain_completed":
            task_id = event.get("task_id", "")
            brain_ctx = self._get_brain_summary()
            msg = (
                f"[Background Tasks]\n{brain_ctx}\n\n"
                f"[System Notification]\n"
                f"Task '{task_id}' completed. "
                f"Results saved in `tasks/{task_id}/`. "
                f"Read the files to summarize findings for the user."
            )
            await self._cerebellum_turn(msg)
            await self._process_commands()

            if not self._has_running_brains() and self._event_queue.empty():
                self._running = False

        elif etype == "brain_failed":
            task_id = event.get("task_id", "")
            error = event.get("error", "unknown")
            brain_ctx = self._get_brain_summary()
            msg = (
                f"[Background Tasks]\n{brain_ctx}\n\n"
                f"[System Notification]\n"
                f"Task '{task_id}' failed: {error}"
            )
            await self._cerebellum_turn(msg)
            await self._process_commands()

    # ==================================================================
    # Autonomous mode
    # ==================================================================

    async def run_autonomous(self, instructions: str) -> list[AgenticResult]:
        """Run a persistent Brain session without Cerebellum.

        The Brain runs as a single long-lived Claude Code session.
        Used when no human is present — the Harness dispatches the
        job directly and collects results.
        """
        self._running = True
        await self._emit(
            "duplex_started",
            {"brain_model": self._brain_model, "mode": "autonomous"},
        )

        try:
            result = await self._brain_turn(instructions)
            self._all_results.append(result)
            self.save_checkpoint()
        except Exception as exc:
            logger.exception("Autonomous brain session failed")
            self._all_results.append(
                AgenticResult(
                    final_text=f"Brain session failed: {exc}",
                    decision="error",
                )
            )
            self.save_checkpoint()
        finally:
            self._running = False
            await self._emit(
                "duplex_finished",
                {"tasks_completed": len(self._all_results), "mode": "autonomous"},
            )

        return self._all_results

    # ==================================================================
    # Cerebellum
    # ==================================================================

    async def _cerebellum_turn(self, prompt: str) -> None:
        """Send a message to the persistent Cerebellum session.

        The Cerebellum maintains full conversation history — each turn
        sees all prior exchanges, just like a normal chat session.
        """
        assert self._cerebellum_client is not None
        try:
            await self._cerebellum_client.query(prompt)
            await self._drain_cerebellum_response()
        except Exception as exc:
            logger.error("Cerebellum turn failed: %s", exc)
            await self._emit(
                "error", {"source": "cerebellum", "error": str(exc)}
            )

    async def _drain_cerebellum_response(self) -> None:
        """Read all messages from the current Cerebellum turn until done."""
        assert self._cerebellum_client is not None
        async for msg in self._cerebellum_client.receive_response():
            await self._process_sdk_message(msg, source="cerebellum")

    async def _process_sdk_message(self, msg: Any, source: str) -> None:
        """Process a message from a Claude Code SDK session."""
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    await self._emit(
                        f"{source}_response", {"text": block.text}
                    )
                elif isinstance(block, ToolUseBlock):
                    await self._emit(
                        "tool_call_start" if source == "cerebellum" else "brain_tool_call_start",
                        {
                            "tool_name": block.name,
                            "input": _safe_truncate(block.input),
                        },
                    )
                elif isinstance(block, ToolResultBlock):
                    await self._emit(
                        "tool_call_result" if source == "cerebellum" else "brain_tool_call_result",
                        {
                            "tool_name": "",
                            "content": str(block.content)[:500],
                            "success": True,
                        },
                    )
                elif isinstance(block, ThinkingBlock):
                    await self._emit(
                        "reasoning" if source == "cerebellum" else "brain_reasoning",
                        {"text": block.text[:500] if block.text else ""},
                    )
        elif isinstance(msg, ResultMessage):
            # Capture session IDs for checkpoint persistence
            if source == "cerebellum" and msg.session_id:
                self._cerebellum_session_id = msg.session_id
            elif source == "brain" and msg.session_id:
                self._brain_session_id = msg.session_id
            logger.info(
                "%s turn done: turns=%s cost=$%.4f session=%s",
                source,
                msg.num_turns,
                msg.total_cost_usd or 0,
                msg.session_id or "?",
            )

    # ==================================================================
    # Brain — persistent session
    # ==================================================================

    async def _ensure_brain_client(self) -> ClaudeSDKClient:
        """Get or create the persistent Brain session.

        The Brain is a single long-lived Claude Code session that
        maintains full conversation history — it remembers every
        experiment it ran, every structure it built, and every result
        it analysed.  Instructions from the Cerebellum are sent as
        messages within this same session.

        On resume, the ``resume`` option reconnects to the prior
        session so the Brain keeps its reasoning chain.
        """
        if hasattr(self, "_brain_client") and self._brain_client is not None:
            return self._brain_client

        brain_system = (
            f"{self._brain_system_prompt}\n\n"
            f"## Output Convention\n"
            f"Save your findings to the workspace:\n"
            f"- Write observations to `observations.jsonl` "
            f"(one JSON object per line with keys: description, "
            f"numerical_values, source_job_id)\n"
            f"- Write claims to `claims.jsonl` "
            f"(one JSON object per line with keys: statement, "
            f"confidence, supporting_evidence)\n"
            f"- Write your final decision to `decision.json` "
            f"(keys: decision [continue|replan|audit|done], reasoning)\n"
            f"- Save any generated data, structures, or plots here too\n"
        )

        brain_options = ClaudeCodeOptions(
            model=self._brain_model,
            system_prompt=brain_system,
            cwd=str(self._workspace_root),
            permission_mode="bypassPermissions",
            max_turns=self._max_brain_turns,
        )

        # Resume prior Brain session if checkpoint has a session ID
        if self._brain_session_id:
            brain_options = ClaudeCodeOptions(
                **{
                    **{
                        k: v for k, v in brain_options.__dict__.items()
                        if v is not None and k != "resume"
                    },
                    "resume": self._brain_session_id,
                }
            )
            logger.info(
                "Resuming Brain session %s", self._brain_session_id
            )

        self._brain_client = ClaudeSDKClient(brain_options)
        return self._brain_client

    async def _brain_turn(self, instructions: str) -> AgenticResult:
        """Send instructions to the persistent Brain session.

        Each call is a new turn in the same conversation — the Brain
        remembers everything from prior turns.

        Returns an :class:`AgenticResult` assembled from the Brain's
        response and any structured output files it wrote.
        """
        client = await self._ensure_brain_client()

        tool_calls: list[dict[str, Any]] = []
        final_text = ""

        try:
            # First turn: connect; subsequent turns: query
            if not hasattr(self, "_brain_connected") or not self._brain_connected:
                await client.connect(instructions)
                self._brain_connected = True
            else:
                await client.query(instructions)

            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            final_text = block.text
                            await self._emit(
                                "brain_progress",
                                {
                                    "task_id": getattr(self, "_current_task_id", ""),
                                    "result_preview": block.text[:300],
                                },
                            )
                        elif isinstance(block, ToolUseBlock):
                            tc = {
                                "name": block.name,
                                "input": _safe_truncate(block.input),
                            }
                            tool_calls.append(tc)
                            await self._emit(
                                "brain_tool_call_start",
                                {
                                    "tool_name": block.name,
                                    "task_id": getattr(self, "_current_task_id", ""),
                                },
                            )
                        elif isinstance(block, ToolResultBlock):
                            await self._emit(
                                "brain_tool_call_result",
                                {
                                    "tool_name": tool_calls[-1]["name"] if tool_calls else "?",
                                    "content": str(block.content)[:500],
                                    "success": True,
                                    "task_id": getattr(self, "_current_task_id", ""),
                                },
                            )
                        elif isinstance(block, ThinkingBlock):
                            await self._emit(
                                "brain_reasoning",
                                {
                                    "text": block.text[:500] if block.text else "",
                                    "task_id": getattr(self, "_current_task_id", ""),
                                },
                            )
                elif isinstance(msg, ResultMessage):
                    if msg.session_id:
                        self._brain_session_id = msg.session_id
                    num_turns = msg.num_turns or 0
                    cost = msg.total_cost_usd or 0
                    logger.info(
                        "Brain turn done: turns=%s cost=$%.4f session=%s",
                        num_turns, cost, msg.session_id or "?",
                    )
                    await self._emit(
                        "brain_agent_done",
                        {
                            "task_id": getattr(self, "_current_task_id", ""),
                            "text": final_text[:300] if final_text else "",
                            "turns": num_turns,
                            "tool_calls": len(tool_calls),
                        },
                    )

        except Exception as exc:
            logger.error("Brain turn failed: %s", exc)
            return AgenticResult(
                final_text=f"Brain turn failed: {exc}",
                decision="error",
            )

        # Read structured outputs from workspace
        observations = _read_jsonl(self._workspace_root / "observations.jsonl")
        claims = _read_jsonl(self._workspace_root / "claims.jsonl")
        decision = _read_json(self._workspace_root / "decision.json")

        return AgenticResult(
            final_text=final_text,
            tool_calls=[
                {"tool_name": tc["name"], "tool_input": tc.get("input", {})}
                for tc in tool_calls
            ],
            observations=observations,
            claims=claims,
            decision=(
                decision.get("decision", "continue") if decision else "continue"
            ),
            token_usage={},
        )

    # ------------------------------------------------------------------
    # Brain task dispatch (from Cerebellum commands)
    # ------------------------------------------------------------------

    async def _dispatch_brain_instruction(self, cmd: _HarnessCommand) -> None:
        """Send an instruction to the persistent Brain session.

        The instruction is sent as a new turn in the same conversation.
        The Brain runs it and writes results to the workspace.  Since
        Brain turns can be long (DFT etc.), this runs as a background
        asyncio.Task.
        """
        task_id = cmd.task_id
        description = cmd.params.get("description", "")
        task_type = cmd.params.get("task_type", "research")
        instructions = cmd.params.get("instructions", description)

        # Skip if already completed (checkpoint resume)
        existing = self._brain_states.get(task_id)
        if existing and existing.status == TaskStatus.COMPLETED:
            logger.info(
                "Skipping completed task %s (restored from checkpoint)", task_id
            )
            await self._event_queue.put(
                {"type": "brain_completed", "task_id": task_id}
            )
            return

        state = BrainState(
            task_id=task_id,
            description=description,
            task_type=task_type,
            status=TaskStatus.RUNNING,
        )
        self._brain_states[task_id] = state
        await self._emit(
            "brain_task_started",
            {
                "task_id": task_id,
                "description": description,
                "task_type": task_type,
            },
        )

        async def _run() -> None:
            try:
                self._current_task_id = task_id
                result = await self._brain_turn(
                    f"[Task {task_id} — {task_type}]\n\n{instructions}"
                )
                state.status = TaskStatus.COMPLETED
                state.completed_at = time.time()
                state.result_text = result.final_text[:2000]
                self._all_results.append(result)
                self.save_checkpoint()
                await self._event_queue.put(
                    {"type": "brain_completed", "task_id": task_id}
                )
            except asyncio.CancelledError:
                state.status = TaskStatus.CANCELLED
                state.completed_at = time.time()
                self.save_checkpoint()
            except Exception as exc:
                state.status = TaskStatus.FAILED
                state.error = str(exc)
                state.completed_at = time.time()
                self.save_checkpoint()
                await self._event_queue.put(
                    {
                        "type": "brain_failed",
                        "task_id": task_id,
                        "error": str(exc),
                    }
                )

        self._brain_tasks[task_id] = asyncio.create_task(_run())
        logger.info(
            "Brain instruction dispatched: %s (%s): %s",
            task_id, task_type, description[:200],
        )

    async def _send_brain_directive(self, task_id: str, message: str) -> None:
        """Send a follow-up directive to the Brain within the same session."""
        state = self._brain_states.get(task_id)
        if state:
            state.events.append(
                BrainEvent(
                    event_type="directive",
                    data={"message": message},
                )
            )
        await self._brain_turn(
            f"[Directive for task {task_id}]\n\n{message}"
        )

    async def _interrupt_brain(self) -> None:
        """Interrupt the Brain's current turn."""
        if hasattr(self, "_brain_client") and self._brain_client is not None:
            self._brain_client.interrupt()
            logger.info("Brain session interrupted")

    # ------------------------------------------------------------------
    # Command processing
    # ------------------------------------------------------------------

    async def _process_commands(self) -> None:
        """Process all queued commands from Cerebellum MCP tools.

        Called after each Cerebellum turn completes.  Commands are
        enqueued by the MCP tool handlers during the turn.
        """
        while not self._command_queue.empty():
            try:
                cmd = self._command_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if cmd.command == "start_brain":
                await self._dispatch_brain_instruction(cmd)

            elif cmd.command == "send_to_brain":
                logger.info(
                    "Brain directive for %s: %s",
                    cmd.task_id,
                    cmd.params.get("message", "")[:200],
                )
                await self._send_brain_directive(
                    cmd.task_id, cmd.params.get("message", "")
                )

            elif cmd.command == "cancel_brain":
                task = self._brain_tasks.get(cmd.task_id)
                if task and not task.done():
                    task.cancel()
                state = self._brain_states.get(cmd.task_id)
                if state:
                    state.status = TaskStatus.CANCELLED
                    state.completed_at = time.time()
                logger.info(
                    "Brain task %s cancelled: %s",
                    cmd.task_id, cmd.params.get("reason", ""),
                )

    # ------------------------------------------------------------------
    # User message relay
    # ------------------------------------------------------------------

    async def _relay_user_messages(self) -> None:
        """Background coroutine: drain user_queue into event_queue."""
        try:
            while True:
                text = await self._user_queue.get()
                await self._event_queue.put(
                    {"type": "user_message", "text": text}
                )
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Cancel all running Brain tasks and disconnect both sessions."""
        if self._cerebellum_client is not None:
            self._cerebellum_client.disconnect()
            self._cerebellum_client = None
        if hasattr(self, "_brain_client") and self._brain_client is not None:
            self._brain_client.disconnect()
            self._brain_client = None
        for task in self._brain_tasks.values():
            if not task.done():
                task.cancel()


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, returning an empty list if absent."""
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("Skipping invalid JSON line in %s", path)
    return items


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON file, returning ``None`` if absent or invalid."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _safe_truncate(obj: Any, max_len: int = 200) -> Any:
    """Truncate large values for stream events."""
    if isinstance(obj, str) and len(obj) > max_len:
        return obj[:max_len] + "..."
    if isinstance(obj, dict):
        return {k: _safe_truncate(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list) and len(obj) > 10:
        return obj[:10] + ["..."]
    return obj

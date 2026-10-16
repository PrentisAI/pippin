"""Front Desk Agent — the human's single interaction entry point.

A persistent Claude Code SDK session that bridges the human researcher
and the AURORA system.  It handles:

- **Propose experiments**: researcher describes intent → Front Desk
  compiles an experiment proposal → researcher reviews/refines → confirms.
- **Status queries**: human asks "where are we?" → Front Desk answers
  using injected system state + its own file-reading capability.
- **Relay instructions**: human gives an instruction → Front Desk calls
  ``relay_to_agent`` MCP tool → Engine interrupts and injects into main
  agent → agent must acknowledge and propose plan → human approves.
- **Escalation handling**: Supervisor escalates → Engine notifies Front
  Desk → Front Desk presents to human → human decides → Front Desk
  calls ``respond_escalation`` → Engine unblocks.
- **Interrupt**: human wants to stop the agent → Front Desk calls
  ``request_interrupt`` → Engine interrupts the agent.

Architecture::

    Human (frontend)
      ↕  WebSocket / REST
    Harness (Python event loop)
      ↕  Claude Code SDK (persistent session)
    Front Desk Agent (Claude Code)
      ↕  MCP tools
    Engine
      ↕
    Main Agent / Supervisor
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ClaudeSDKClient,
    McpSdkServerConfig,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)

# Monkey-patch the SDK's message parser to skip unknown message types
# instead of crashing. The CLI may emit events (e.g. rate_limit_event)
# that the SDK doesn't handle yet.
try:
    import claude_code_sdk._internal.message_parser as _mp
    _orig_parse = _mp.parse_message

    def _patched_parse(data: dict) -> Any:
        try:
            return _orig_parse(data)
        except _mp.MessageParseError as exc:
            if "Unknown message type" in str(exc):
                logger.debug("Skipping unknown message type: %s", data.get("type"))
                from claude_code_sdk.types import SystemMessage
                return SystemMessage(subtype="unknown", data=data)
            raise

    _mp.parse_message = _patched_parse
except Exception:
    pass

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Escalation:
    """A pending escalation waiting for human response."""
    escalation_id: str
    context: str
    supervisor_reason: str
    supervisor_suggestion: str
    created_at: float = field(default_factory=time.time)
    response_queue: asyncio.Queue | None = None


@dataclass
class AgentProposal:
    """Agent's proposed plan after receiving human instruction, awaiting approval."""
    proposal_id: str
    instruction: str
    agent_response: str
    created_at: float = field(default_factory=time.time)
    approval_queue: asyncio.Queue | None = None


@dataclass
class SystemStatus:
    """Snapshot of the current system state for context injection."""
    phase: str = ""
    iteration: int = 0
    max_iterations: int = 0
    agent_status: str = "idle"
    last_tool_call: str = ""
    last_tool_time: float = 0.0
    supervisor_status: str = ""
    pending_escalations: int = 0
    verifier_score: float | None = None

    def format(self) -> str:
        lines = [
            f"Phase: {self.phase}",
            f"Iteration: {self.iteration}/{self.max_iterations}",
            f"Agent: {self.agent_status}",
        ]
        if self.last_tool_call:
            ago = time.time() - self.last_tool_time if self.last_tool_time else 0
            lines.append(f"Last tool: {self.last_tool_call} ({ago:.0f}s ago)")
        if self.supervisor_status:
            lines.append(f"Supervisor: {self.supervisor_status}")
        if self.pending_escalations > 0:
            lines.append(f"Pending escalations: {self.pending_escalations}")
        if self.verifier_score is not None:
            lines.append(f"Verifier score: {self.verifier_score}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

FRONTDESK_SYSTEM_PROMPT = """\
You are the Front Desk agent for AURORA, a research automation platform.
Think of yourself as the researcher's personal secretary — your job is to \
understand what they want, help them shape it, and get it running.

## How You Work
- Talk naturally. The researcher doesn't need to fill out a form — they just \
tell you what they want, like talking to a colleague.
- Ask clarifying questions when something is unclear. Don't rush to propose.
- When you think you understand enough, propose an experiment. The researcher \
will see a card with your proposal and can confirm, or tell you what to change.
- **This is iterative.** The researcher might say "instruction 不太对" or \
"加上 analysis" or "model 换一个" — just update and re-propose. Keep going \
until they're happy and click confirm.
- You never start experiments directly. You propose, they confirm.

## CRITICAL: Never Fabricate Information
- **NEVER make up task names, descriptions, or details.** Only mention tasks \
that the `list_tasks` tool returned. If you haven't called `list_tasks` yet, \
call it first. If you need to filter tasks, filter from the actual results.
- If the researcher asks about tasks or categories you're unsure about, call \
`list_tasks` again rather than guessing.
- The same applies to task metadata (difficulty, time estimates, GPU requirements, \
Docker images) — only use data from the tool results or from reading the actual \
task.toml files in the workspace.

## Proposing Experiments
Two paths:
- **Predefined task**: researcher picks from benchmarks → `propose_experiment`
- **Custom/free-form**: researcher describes what they want → you compile a \
clear instruction → `propose_custom_experiment`

Tips for writing good instructions:
- Include everything the agent needs to know: what to do, what data is available, \
where it is, what the expected output looks like.
- Write it as if briefing a smart colleague who has never seen this project.
- The researcher may refine it over several rounds — that's normal.

## Handling Feedback on Proposals
When the researcher gives feedback after seeing a proposal:
- If they say the instruction needs changes → update the instruction and re-propose
- If they want different phases → adjust skip_phases and re-propose
- If they want a different model → update model and re-propose
- If they say "可以了" / "好的" / "开始吧" → tell them to click the confirm button
- Your new proposal automatically replaces the old one.

## Phase Pipeline
Each experiment runs through phases. Suggest based on task complexity:

Phases (in order): roundtable → screening → planning → **research_loop** → analysis → writing
- research_loop is always included (the actual execution)
- The others are optional and configurable

Presets (what to pass as `skip_phases`):
- **quick**: skip everything except research_loop. For simple single-step tasks.
- **standard**: skip roundtable and writing. For benchmark tasks, multi-step work.
- **full**: skip nothing. For complex open-ended research.

Don't lecture about phases — just pick a sensible default. If the researcher \
asks about phases, explain briefly.

## Running Experiments
When experiments are already running, you can:
- `relay_to_agent` — send instructions to the running agent
- `respond_escalation` — handle escalations from the Supervisor
- `request_interrupt` — stop the agent if the researcher wants

You also have full Claude Code capabilities — you can read files in the workspace, \
run shell commands, and investigate the agent's outputs directly.

## Guidelines
- Speak the same language as the researcher. Chinese in, Chinese out.
- Be concise. Don't repeat what they already know.
- Act like a good secretary: attentive, efficient, no unnecessary formality.
"""


# ---------------------------------------------------------------------------
# MCP tool builders
# ---------------------------------------------------------------------------

def _build_frontdesk_mcp_tools(
    relay_fn: Callable[[str], Awaitable[dict[str, Any]]],
    escalation_fn: Callable[[str, str, str], Awaitable[dict[str, Any]]],
    interrupt_fn: Callable[[str], Awaitable[dict[str, Any]]],
    list_tasks_fn: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
    propose_experiment_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    propose_custom_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> list[SdkMcpTool]:
    """Build MCP tools for the Front Desk Agent."""

    def _mcp_result(data: Any) -> dict[str, Any]:
        """Wrap a result as MCP tool response format."""
        text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
        return {"content": [{"type": "text", "text": text}]}

    async def relay_to_agent(params: dict[str, Any]) -> dict[str, Any]:
        message = params.get("message", "")
        if not message:
            return _mcp_result({"status": "error", "reason": "message is required"})
        result = await relay_fn(message)
        return _mcp_result(result)

    async def respond_escalation(params: dict[str, Any]) -> dict[str, Any]:
        escalation_id = params.get("escalation_id", "")
        decision = params.get("decision", "")
        message = params.get("message", "")
        if not escalation_id or not decision:
            return _mcp_result({"status": "error", "reason": "escalation_id and decision are required"})
        result = await escalation_fn(escalation_id, decision, message)
        return _mcp_result(result)

    async def request_interrupt(params: dict[str, Any]) -> dict[str, Any]:
        reason = params.get("reason", "Human requested interrupt")
        result = await interrupt_fn(reason)
        return _mcp_result(result)

    async def list_tasks(params: dict[str, Any]) -> dict[str, Any]:
        if not list_tasks_fn:
            return _mcp_result({"status": "error", "reason": "list_tasks not configured"})
        tasks = await list_tasks_fn()
        return _mcp_result({"tasks": tasks})

    async def propose_experiment(params: dict[str, Any]) -> dict[str, Any]:
        if not propose_experiment_fn:
            return _mcp_result({"status": "error", "reason": "propose_experiment not configured"})
        result = await propose_experiment_fn(params)
        return _mcp_result(result)

    async def propose_custom_experiment(params: dict[str, Any]) -> dict[str, Any]:
        if not propose_custom_fn:
            return _mcp_result({"status": "error", "reason": "propose_custom_experiment not configured"})
        result = await propose_custom_fn(params)
        return _mcp_result(result)

    tools = [
        SdkMcpTool(
            name="relay_to_agent",
            description=(
                "Send a human instruction to the main agent. The agent MUST "
                "acknowledge receipt, propose how it will adjust, and wait for "
                "human approval before continuing. Use this whenever the human "
                "wants to guide, correct, or redirect the agent."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The human's instruction to relay to the agent",
                    },
                },
                "required": ["message"],
            },
            handler=relay_to_agent,
        ),
        SdkMcpTool(
            name="respond_escalation",
            description=(
                "Respond to a pending Supervisor escalation. This unblocks the "
                "Engine which is waiting for a human decision."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "escalation_id": {
                        "type": "string",
                        "description": "The ID of the escalation to respond to",
                    },
                    "decision": {
                        "type": "string",
                        "enum": ["approve", "modify", "reject"],
                        "description": (
                            "approve: let the agent continue with guidance; "
                            "modify: provide modified instructions; "
                            "reject: stop the agent"
                        ),
                    },
                    "message": {
                        "type": "string",
                        "description": "Additional guidance or instructions for the agent",
                    },
                },
                "required": ["escalation_id", "decision"],
            },
            handler=respond_escalation,
        ),
        SdkMcpTool(
            name="request_interrupt",
            description=(
                "Request the Engine to interrupt the main agent immediately. "
                "Use when the human wants to stop the agent's current work."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why the interrupt is being requested",
                    },
                },
                "required": ["reason"],
            },
            handler=request_interrupt,
        ),
    ]

    if list_tasks_fn:
        tools.append(SdkMcpTool(
            name="list_tasks",
            description=(
                "List available predefined benchmark tasks that can be run as experiments. "
                "Call this to show the human what tasks are available before starting one."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
            handler=list_tasks,
        ))

    if propose_experiment_fn:
        tools.append(SdkMcpTool(
            name="propose_experiment",
            description=(
                "Propose a predefined benchmark experiment to the researcher for "
                "confirmation. This does NOT start the experiment — it creates a "
                "proposal card that the researcher can review, adjust phases, and "
                "confirm. Call this when you've gathered enough information."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_dir": {
                        "type": "string",
                        "description": "Path to the task directory (from list_tasks)",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what this experiment will do",
                    },
                    "model": {
                        "type": "string",
                        "description": "Model to use (default: deepseek/deepseek-v4-flash)",
                    },
                    "driver": {
                        "type": "string",
                        "enum": ["claude-code", "openai-agentic", "anthropic-agentic"],
                        "description": "Agent driver (default: claude-code)",
                    },
                    "max_turns": {
                        "type": "integer",
                        "description": "Maximum agent turns (default: 200)",
                    },
                    "skip_phases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Phases to skip based on task complexity",
                    },
                },
                "required": ["task_dir", "summary"],
            },
            handler=propose_experiment,
        ))

    if propose_custom_fn:
        tools.append(SdkMcpTool(
            name="propose_custom_experiment",
            description=(
                "Propose a free-form experiment to the researcher for confirmation. "
                "Compile the researcher's intent into a clear instruction, suggest a "
                "phase pipeline, and let them review before starting."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "Compiled instruction for the agent",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary for the researcher",
                    },
                    "name": {
                        "type": "string",
                        "description": "Short name for this experiment",
                    },
                    "model": {
                        "type": "string",
                        "description": "Model to use (default: deepseek/deepseek-v4-flash)",
                    },
                    "max_turns": {
                        "type": "integer",
                        "description": "Maximum agent turns (default: 200)",
                    },
                    "data_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Paths to data/code the researcher mentioned",
                    },
                    "skip_phases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Phases to skip based on task complexity",
                    },
                },
                "required": ["instruction", "summary"],
            },
            handler=propose_custom_experiment,
        ))

    return tools


# ---------------------------------------------------------------------------
# MCP server factory (compatible with mcp v2.x)
# ---------------------------------------------------------------------------

def _create_mcp_server(
    name: str, tools: list[SdkMcpTool],
) -> "McpSdkServerConfig":
    """Build an in-process MCP server from SdkMcpTool definitions.

    ``claude_code_sdk.create_sdk_mcp_server`` relies on mcp v1's
    ``@server.list_tools()`` decorator which no longer exists in mcp v2.
    The SDK's internal bridge (``Query._handle_sdk_mcp_request``) accesses
    ``server.request_handlers[ListToolsRequest]`` and
    ``server.request_handlers[CallToolRequest]`` as async callables, plus
    ``server.name`` and ``server.version``.  We build a minimal shim that
    satisfies these requirements without depending on any specific mcp
    ``Server`` class.
    """
    from mcp.types import (
        CallToolRequest,
        CallToolRequestParams,
        CallToolResult,
        ListToolsRequest,
        ListToolsResult,
        TextContent,
    )

    tool_map = {t.name: t for t in tools}

    # The SDK's _handle_sdk_mcp_request accesses tool.inputSchema (camelCase)
    # but mcp v2 Tool uses tool.input_schema (snake_case). We use a simple
    # object that exposes the camelCase attribute the SDK expects.
    class _ToolDef:
        __slots__ = ("name", "description", "inputSchema")

        def __init__(self, name: str, description: str, schema: dict) -> None:
            self.name = name
            self.description = description
            self.inputSchema = schema

    tool_objects = []
    for t in tools:
        if isinstance(t.input_schema, dict) and "type" in t.input_schema:
            schema = t.input_schema
        else:
            schema = {"type": "object", "properties": {}}
        tool_objects.append(_ToolDef(t.name, t.description, schema))

    async def handle_list_tools(request: ListToolsRequest) -> Any:
        logger.info("MCP list_tools called — returning %d tools", len(tool_objects))
        # The SDK accesses result.root.tools (mcp v1 style).  mcp v2's
        # ListToolsResult has .tools directly but no .root.  Return a
        # wrapper that satisfies both access patterns.
        class _ListResult:
            def __init__(self, tools: list) -> None:
                self.tools = tools
                self.root = self  # result.root.tools → self.tools
        return _ListResult(tool_objects)

    async def handle_call_tool(request: CallToolRequest) -> Any:
        tool_name = request.params.name
        arguments = request.params.arguments or {}
        logger.info("MCP call_tool: %s(%s)", tool_name, list(arguments.keys()))
        if tool_name not in tool_map:
            raise ValueError(f"Tool '{tool_name}' not found")
        raw = await tool_map[tool_name].handler(arguments)
        content: list[TextContent] = []
        if isinstance(raw, dict) and "content" in raw:
            for item in raw["content"]:
                if item.get("type") == "text":
                    content.append(TextContent(type="text", text=item["text"]))
        # SDK accesses result.root.content and result.root.is_error
        class _CallResult:
            def __init__(self, content: list, is_error: bool = False) -> None:
                self.content = content
                self.is_error = is_error
                self.root = self
        return _CallResult(content)

    class _McpServerShim:
        """Minimal object satisfying ``claude_code_sdk``'s server interface."""

        def __init__(self) -> None:
            self.name = name
            self.version = "1.0.0"
            self.request_handlers: dict[type, Any] = {
                ListToolsRequest: handle_list_tools,
                CallToolRequest: handle_call_tool,
            }

    return McpSdkServerConfig(type="sdk", name=name, instance=_McpServerShim())


# ---------------------------------------------------------------------------
# FrontDeskAgent
# ---------------------------------------------------------------------------

class FrontDeskAgent:
    """Persistent Claude Code session that bridges human and system.

    Uses the Claude Code SDK to run a full Claude Code instance with
    file reading, shell access, and MCP tools for experiment management.

    Parameters
    ----------
    model:
        Model for the Front Desk session (supports OpenRouter models).
    api_key:
        API key (OpenRouter or Anthropic).
    base_url:
        Base URL for the API (e.g. https://openrouter.ai/api/v1).
    workspace_root:
        Root directory of the research workspace (for file access).
    broadcast_fn:
        Async callable to push events to frontend via WebSocket.
    campaign_context_fn:
        Callable returning campaign context string for system prompt.
    on_relay:
        Called when Front Desk relays a human instruction to the agent.
    on_escalation_response:
        Called when Front Desk responds to an escalation.
    on_interrupt:
        Called when Front Desk requests an agent interrupt.
    on_list_tasks:
        Called to list available benchmark tasks.
    on_propose_experiment:
        Called when Front Desk proposes a predefined experiment.
        Returns dict with proposal data.
    on_propose_custom_experiment:
        Called when Front Desk proposes a custom experiment.
        Returns dict with proposal data.
    """

    def __init__(
        self,
        *,
        model: str = "deepseek/deepseek-chat",
        api_key: str = "",
        base_url: str = "",
        workspace_root: str | Path = ".",
        broadcast_fn: BroadcastFn | None = None,
        campaign_context_fn: Callable[[], str] | None = None,
        on_relay: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        on_escalation_response: Callable[[str, str, str], Awaitable[dict[str, Any]]] | None = None,
        on_interrupt: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        on_list_tasks: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
        on_propose_experiment: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        on_propose_custom_experiment: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._base_url = base_url or os.environ.get("FRONTDESK_BASE_URL", "")
        self._workspace_root = Path(workspace_root)
        self._broadcast = broadcast_fn
        self._campaign_context_fn = campaign_context_fn

        self._on_relay = on_relay or self._noop_relay
        self._on_escalation_response = on_escalation_response or self._noop_escalation
        self._on_interrupt = on_interrupt or self._noop_interrupt
        self._on_list_tasks = on_list_tasks
        self._on_propose_experiment = on_propose_experiment
        self._on_propose_custom_experiment = on_propose_custom_experiment

        self._client: ClaudeSDKClient | None = None
        self._session_id: str | None = None
        self._running = False
        self._status = SystemStatus()
        self._user_queue: asyncio.Queue[str] = asyncio.Queue()
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._relay_task: asyncio.Task | None = None
        self._pending_escalations: dict[str, Escalation] = {}
        self._history: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

        self._turn_text_parts: list[str] = []
        self._turn_proposal: dict[str, Any] | None = None

    @property
    def user_queue(self) -> asyncio.Queue[str]:
        return self._user_queue

    @property
    def status(self) -> SystemStatus:
        return self._status

    def set_proposal(self, proposal: dict[str, Any]) -> None:
        """Called by MCP tool handlers to attach a proposal to the current turn."""
        self._turn_proposal = proposal

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the Front Desk Agent session in streaming mode.

        Uses ``connect()`` without a prompt so the CLI starts in
        ``--input-format stream-json`` mode with stdin kept open.
        Subsequent messages are sent via ``query()``.
        """
        if self._running:
            return

        self._running = True

        mcp_tools = _build_frontdesk_mcp_tools(
            relay_fn=self._on_relay,
            escalation_fn=self._on_escalation_response,
            interrupt_fn=self._on_interrupt,
            list_tasks_fn=self._on_list_tasks,
            propose_experiment_fn=self._on_propose_experiment,
            propose_custom_fn=self._on_propose_custom_experiment,
        )
        mcp_server = _create_mcp_server("aurora", mcp_tools)

        campaign_ctx = ""
        if self._campaign_context_fn:
            campaign_ctx = f"\n\n## Current Campaign\n{self._campaign_context_fn()}"

        # Pre-load task list so the model never needs to guess
        task_catalog = ""
        if self._on_list_tasks:
            try:
                tasks = await self._on_list_tasks()
                if tasks:
                    lines = []
                    for t in tasks:
                        cat = t.get("category", "")
                        diff = t.get("difficulty", "")
                        expert = t.get("expert_time_min", "")
                        desc = t.get("description", "")[:80]
                        lines.append(
                            f"- **{t['name']}** ({cat}, {diff}, ~{expert}min): {desc}"
                        )
                    task_catalog = (
                        "\n\n## Available LHTB Tasks (ground truth — use ONLY these)\n"
                        + "\n".join(lines)
                        + "\n\nDo NOT mention any task not in this list."
                    )
                    logger.info("Injected %d tasks into system prompt", len(tasks))
            except Exception as exc:
                logger.warning("Failed to pre-load task list: %s", exc)

        system_prompt = (
            f"{FRONTDESK_SYSTEM_PROMPT}"
            f"{campaign_ctx}"
            f"{task_catalog}\n\n"
            f"## Workspace\nRoot: `{self._workspace_root}`\n"
        )

        env: dict[str, str] = {}
        if self._base_url and "openrouter.ai" in self._base_url:
            env["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
        if self._api_key:
            env["ANTHROPIC_AUTH_TOKEN"] = self._api_key
            env["ANTHROPIC_API_KEY"] = ""

        options = ClaudeCodeOptions(
            model=self._model,
            system_prompt=system_prompt,
            cwd=str(self._workspace_root),
            permission_mode="bypassPermissions",
            max_turns=10,
            env=env,
            mcp_servers={
                "aurora": mcp_server,
            },
        )

        if self._session_id:
            options = ClaudeCodeOptions(
                **{
                    **{
                        k: v for k, v in options.__dict__.items()
                        if v is not None and k != "resume"
                    },
                    "resume": self._session_id,
                }
            )
            logger.info("Resuming Front Desk session %s", self._session_id)

        self._client = ClaudeSDKClient(options)
        await self._client.connect()

        logger.info("Front Desk Agent started: model=%s", self._model)

    async def stop(self) -> None:
        """Stop the Front Desk Agent session."""
        self._running = False
        if self._relay_task:
            self._relay_task.cancel()
        if self._client:
            await self._client.disconnect()
            self._client = None
        logger.info("Front Desk Agent stopped")

    # ------------------------------------------------------------------
    # Human → Front Desk (message handling)
    # ------------------------------------------------------------------

    async def handle_user_message(self, text: str) -> dict[str, Any]:
        """Process a user message and return response with optional proposal.

        Returns
        -------
        dict with keys:
            text: str — the assistant's text response
            proposal: dict | None — experiment proposal if one was created
        """
        async with self._lock:
            if not self._running:
                await self.start()

            if not self._client:
                return {"text": "Front Desk is not available.", "proposal": None}

            self._turn_text_parts = []
            self._turn_proposal = None

            self._history.append({
                "role": "user", "text": text, "timestamp": time.time(),
            })

            status_block = self._status.format()
            escalation_block = self._format_pending_escalations()

            prompt_parts = [f"[System Status]\n{status_block}"]
            if escalation_block:
                prompt_parts.append(f"[Pending Escalations]\n{escalation_block}")
            if self._campaign_context_fn:
                prompt_parts.append(f"[Campaign Context]\n{self._campaign_context_fn()}")
            prompt_parts.append(f"[User Message]\n{text}")

            prompt = "\n\n".join(prompt_parts)

            try:
                await self._client.query(prompt)
                async for msg in self._client.receive_response():
                    await self._process_response(msg)

            except Exception as exc:
                # MessageParseError for unknown event types (e.g. rate_limit_event)
                # is non-fatal — we may already have partial text/tool results
                exc_name = type(exc).__name__
                if "MessageParseError" in exc_name or "rate_limit" in str(exc).lower():
                    logger.warning("Non-fatal parse error (partial response kept): %s", exc)
                else:
                    logger.error("Front Desk turn failed: %s", exc)
                    if self._broadcast:
                        await self._broadcast({
                            "type": "frontdesk_error",
                            "data": {"error": str(exc)},
                        })
                    if not self._turn_text_parts:
                        return {"text": f"Front Desk error: {exc}", "proposal": None}

            response_text = "\n".join(self._turn_text_parts) if self._turn_text_parts else ""

            result: dict[str, Any] = {"text": response_text}
            if self._turn_proposal is not None:
                result["proposal"] = self._turn_proposal
            return result

    # ------------------------------------------------------------------
    # System → Front Desk (events)
    # ------------------------------------------------------------------

    async def notify_escalation(self, escalation: Escalation) -> None:
        """Notify the Front Desk about a new Supervisor escalation."""
        self._pending_escalations[escalation.escalation_id] = escalation

        prompt = (
            f"[ESCALATION — Action Required]\n"
            f"Escalation ID: {escalation.escalation_id}\n"
            f"Supervisor reason: {escalation.supervisor_reason}\n"
            f"Suggestion: {escalation.supervisor_suggestion}\n\n"
            f"The main agent has been interrupted and is waiting. "
            f"Please present this to the human and help them decide:\n"
            f"- approve: let the agent continue (optionally with guidance)\n"
            f"- modify: provide modified instructions for the agent\n"
            f"- reject: stop the agent\n\n"
            f"Use the `respond_escalation` tool with the escalation ID once "
            f"the human has decided."
        )

        if self._client and self._running:
            try:
                await self._client.query(prompt)
                async for msg in self._client.receive_response():
                    await self._process_response(msg)
            except Exception as exc:
                logger.error("Front Desk escalation notification failed: %s", exc)

    async def notify_agent_response(self, response_text: str) -> None:
        """Notify the Front Desk that the agent has responded."""
        prompt = (
            f"[Agent Response — Approval Required]\n"
            f"The agent has received the instruction and responds:\n\n"
            f"{response_text}\n\n"
            f"Present this to the human. The agent is waiting for approval "
            f"before proceeding. Ask the human if they approve this plan, "
            f"want to modify it, or want to reject it."
        )

        if self._client and self._running:
            try:
                await self._client.query(prompt)
                async for msg in self._client.receive_response():
                    await self._process_response(msg)
            except Exception as exc:
                logger.error("Front Desk agent response notification failed: %s", exc)

    def update_status(self, **kwargs: Any) -> None:
        """Update the system status snapshot."""
        for k, v in kwargs.items():
            if hasattr(self._status, k):
                setattr(self._status, k, v)

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    async def _process_response(self, msg: Any) -> None:
        """Process a message from the Front Desk Claude Code session."""
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    self._turn_text_parts.append(block.text)
                    self._history.append({
                        "role": "assistant",
                        "text": block.text,
                        "timestamp": time.time(),
                    })
                    if self._broadcast:
                        await self._broadcast({
                            "type": "frontdesk_response",
                            "data": {"text": block.text},
                        })
                elif isinstance(block, ToolUseBlock):
                    if self._broadcast:
                        await self._broadcast({
                            "type": "frontdesk_tool_call",
                            "data": {
                                "tool_name": block.name,
                                "input": _safe_truncate(block.input),
                            },
                        })
                elif isinstance(block, ToolResultBlock):
                    if self._broadcast:
                        await self._broadcast({
                            "type": "frontdesk_tool_result",
                            "data": {"content": str(block.content)[:500]},
                        })

        elif isinstance(msg, ResultMessage):
            if msg.session_id:
                self._session_id = msg.session_id
            logger.info(
                "Front Desk turn done: turns=%s cost=$%.4f session=%s",
                msg.num_turns, msg.total_cost_usd or 0, msg.session_id or "?",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_pending_escalations(self) -> str:
        if not self._pending_escalations:
            return ""
        lines = []
        for esc in self._pending_escalations.values():
            ago = time.time() - esc.created_at
            lines.append(
                f"- [{esc.escalation_id}] ({ago:.0f}s ago): {esc.supervisor_reason}"
            )
        return "\n".join(lines)

    def clear_escalation(self, escalation_id: str) -> None:
        """Remove a resolved escalation."""
        self._pending_escalations.pop(escalation_id, None)

    async def _relay_user_messages(self) -> None:
        """Background task: drain user_queue and handle each message."""
        try:
            while self._running:
                text = await self._user_queue.get()
                await self.handle_user_message(text)
        except asyncio.CancelledError:
            pass

    def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._history[-limit:]

    # ------------------------------------------------------------------
    # No-op defaults
    # ------------------------------------------------------------------

    @staticmethod
    async def _noop_relay(message: str) -> dict[str, Any]:
        logger.warning("relay_to_agent called but no handler configured")
        return {"status": "error", "reason": "no relay handler configured"}

    @staticmethod
    async def _noop_escalation(
        escalation_id: str, decision: str, message: str
    ) -> dict[str, Any]:
        logger.warning("respond_escalation called but no handler configured")
        return {"status": "error", "reason": "no escalation handler configured"}

    @staticmethod
    async def _noop_interrupt(reason: str) -> dict[str, Any]:
        logger.warning("request_interrupt called but no handler configured")
        return {"status": "error", "reason": "no interrupt handler configured"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_truncate(obj: Any, max_len: int = 200) -> Any:
    if isinstance(obj, str) and len(obj) > max_len:
        return obj[:max_len] + "..."
    if isinstance(obj, dict):
        return {k: _safe_truncate(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list) and len(obj) > 10:
        return obj[:10] + ["..."]
    return obj

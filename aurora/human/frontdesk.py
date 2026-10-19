"""Front desk agent: the human's conversation partner while a campaign runs.

The front desk makes the interaction *duplex*: the human can talk to the system
at any time without stopping the research agent.  It

* answers questions about the campaign from the live status and the files in
  the workspace;
* relays instructions to the research agent, which receives them mid-session;
* interrupts the research agent when the human wants it to stop what it does;
* answers human gates when the human decides in the conversation;
* at the project level (web console, before any campaign runs) helps the
  human turn an idea into an experiment proposal they confirm.

It is a small tool-use loop over an OpenAI-compatible chat API (OpenRouter by
default), separate from the Claude Code sessions that do the research, so it
stays responsive while they work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]
_MAX_TOOL_ROUNDS = 8
_HISTORY_MESSAGES = 40
_MAX_FILE_CHARS = 20_000


@dataclass
class FrontDeskTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass
class SystemStatus:
    """Snapshot of the running campaign, shown to the front desk on every turn."""

    phase: str = ""
    iteration: int = 0
    max_iterations: int = 0
    agent_status: str = "idle"
    supervisor_status: str = ""
    open_gates: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float = 0.0
    current_job: str = ""
    recent_actions: list[str] = field(default_factory=list)   # newest last

    def format(self) -> str:
        lines = [f"Phase: {self.phase or 'not started'}",
                 f"Research jobs: {self.iteration}/{self.max_iterations or '?'}",
                 f"Research agent: {self.agent_status}",
                 f"API cost so far: ${self.cost_usd:.2f}"]
        if self.current_job:
            lines.append(f"Current job: {self.current_job}")
        if self.recent_actions:
            lines.append("Recent actions of the research agent (newest last):")
            lines += [f"  {a}" for a in self.recent_actions[-8:]]
        if self.supervisor_status:
            lines.append(f"Live auditor: {self.supervisor_status}")
        for g in self.open_gates:
            lines.append(f"OPEN HUMAN GATE {g['gate_id']} ({g['source']}): {g['title']}")
        return "\n".join(lines)


CAMPAIGN_PROMPT = """\
You are the front desk of AURORA, a system that runs long research campaigns with AI agents.
You talk with the researcher while the campaign runs. The research agent works in parallel;
you never do the research yourself.

What you can do:
- Answer questions about progress. Use the status block and read files in the workspace
  (list_files, read_file) instead of guessing. Say where things are (file paths).
- Relay instructions: when the researcher wants the research agent to do something
  differently, call relay_to_agent with a clear, self-contained instruction. The agent
  receives it while it works; you do not need to stop it first.
- Interrupt: when the researcher wants the agent to stop its current action, call
  interrupt_agent (and relay the new instruction, if any).
- Human gates: when the status shows an OPEN HUMAN GATE, explain it briefly and help the
  researcher decide. Once they have clearly decided, call answer_gate with
  continue, instruct (with their instruction) or stop. Never answer a gate on your own.

Be brief and concrete. Reply in the researcher's language.
"""

PROJECT_PROMPT = """\
You are the front desk of AURORA, a system that runs long research campaigns with AI agents.
You help the researcher turn what they want into an experiment proposal for this project.

- Talk naturally; ask a clarifying question when something important is unclear
  (goal, data, success criteria, constraints), but do not interrogate.
- When you understand enough, call propose_experiment. The researcher sees a card with
  your proposal and confirms it or asks for changes; then call propose_experiment again
  with the update. You never start experiments yourself.
- Write the instruction as a self-contained brief for a capable colleague who has never
  seen the project: what to find out or build, what data exists and where, what a good
  result looks like, constraints.
- Phases: ideation → screening → planning → research → analysis → writing. Skip
  ideation and screening when the researcher already knows exactly what to do; skip
  writing when no report is wanted. Research is always run.

Be brief. Reply in the researcher's language.
"""


class FrontDeskAgent:
    """Chat agent with tools; one instance per running campaign or per web project."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        system_prompt: str = CAMPAIGN_PROMPT,
        workspace_root: str | Path | None = None,
        tools: list[FrontDeskTool] | None = None,
        status_fn: Callable[[], str] | None = None,
        broadcast_fn: BroadcastFn | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._system_prompt = system_prompt
        self._root = Path(workspace_root).resolve() if workspace_root else None
        self._tools = {t.name: t for t in (tools or [])}
        if self._root is not None:
            for t in self._file_tools():
                self._tools.setdefault(t.name, t)
        self._status_fn = status_fn
        self._broadcast = broadcast_fn
        self._messages: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._client: Any = None
        self._turn_proposal: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []

    # -- public API ------------------------------------------------------------

    def set_proposal(self, proposal: dict[str, Any]) -> None:
        """Tool handlers call this to attach a proposal card to the current reply."""
        self._turn_proposal = proposal

    async def handle_user_message(self, text: str) -> dict[str, Any]:
        """Answer a message from the human; returns ``{"text", "proposal"?}``."""
        async with self._lock:
            self.history.append({"role": "user", "text": text, "timestamp": time.time()})
            self._turn_proposal = None
            status = self._status_fn() if self._status_fn else ""
            content = f"[Status]\n{status}\n\n[Researcher]\n{text}" if status else text
            reply = await self._turn(content)
            result: dict[str, Any] = {"text": reply}
            if self._turn_proposal is not None:
                result["proposal"] = self._turn_proposal
            return result

    async def notify(self, text: str, *, explain: bool = False) -> None:
        """Tell the front desk about an event (a gate opened, the agent answered, ...).

        With *explain* the front desk writes a short message to the human about it;
        otherwise the note only becomes context for the next conversation turn.
        """
        async with self._lock:
            if explain:
                await self._turn(f"[System event]\n{text}\n\nTell the researcher briefly what this means "
                                 "and what they can do. Do not call answer_gate.")
            else:
                self._messages.append({"role": "user", "content": f"[System event]\n{text}"})
                self._messages.append({"role": "assistant", "content": "Noted."})

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # -- the tool loop -----------------------------------------------------------

    async def _turn(self, content: str) -> str:
        self._messages.append({"role": "user", "content": content})
        self._messages = self._messages[-_HISTORY_MESSAGES:]
        while self._messages and self._messages[0]["role"] != "user":
            self._messages.pop(0)   # never start the window with a dangling tool reply
        try:
            client = self._get_client()
            for _ in range(_MAX_TOOL_ROUNDS):
                response = await client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "system", "content": self._system_prompt}, *self._messages],
                    tools=[t.schema() for t in self._tools.values()] or None,
                )
                msg = response.choices[0].message
                calls = msg.tool_calls or []
                self._messages.append({
                    "role": "assistant", "content": msg.content or "",
                    **({"tool_calls": [c.model_dump() for c in calls]} if calls else {}),
                })
                if not calls:
                    text = (msg.content or "").strip()
                    await self._emit_text(text)
                    return text
                for call in calls:
                    result = await self._call_tool(call.function.name, call.function.arguments)
                    self._messages.append({"role": "tool", "tool_call_id": call.id,
                                           "content": json.dumps(result, ensure_ascii=False, default=str)})
            text = "(The front desk stopped after too many tool calls.)"
        except Exception as exc:
            logger.error("Front desk turn failed: %s", exc)
            text = f"The front desk could not answer: {exc}"
            if self._broadcast:
                await self._broadcast({"type": "frontdesk_error", "data": {"error": str(exc)}})
        await self._emit_text(text)
        return text

    async def _call_tool(self, name: str, arguments: str) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"unknown tool {name}"}
        try:
            params = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return {"error": f"arguments are not valid JSON: {exc}"}
        if self._broadcast:
            await self._broadcast({"type": "frontdesk_tool_call",
                                   "data": {"tool_name": name, "input": _truncate(params)}})
        try:
            result = await tool.handler(params)
        except Exception as exc:
            logger.warning("Front desk tool %s failed: %s", name, exc)
            result = {"error": str(exc)}
        if self._broadcast:
            await self._broadcast({"type": "frontdesk_tool_result",
                                   "data": {"tool_name": name, "content": str(result)[:500]}})
        return result

    async def _emit_text(self, text: str) -> None:
        if not text:
            return
        self.history.append({"role": "assistant", "text": text, "timestamp": time.time()})
        if self._broadcast:
            await self._broadcast({"type": "frontdesk_response", "data": {"text": text}})

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    # -- workspace file tools ------------------------------------------------------

    def _file_tools(self) -> list[FrontDeskTool]:
        async def list_files(params: dict[str, Any]) -> Any:
            base = self._resolve(params.get("path", "."))
            if not base.is_dir():
                return {"error": f"not a directory: {params.get('path')}"}
            entries = sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name))[:200]
            return [f"{p.name}/" if p.is_dir() else f"{p.name} ({p.stat().st_size} bytes)" for p in entries]

        async def read_file(params: dict[str, Any]) -> Any:
            path = self._resolve(params.get("path", ""))
            if not path.is_file():
                return {"error": f"not a file: {params.get('path')}"}
            text = path.read_text(errors="replace")
            if len(text) > _MAX_FILE_CHARS:
                text = text[-_MAX_FILE_CHARS:]
                return {"note": f"showing the last {_MAX_FILE_CHARS} characters", "content": text}
            return {"content": text}

        path_param = {"type": "object", "properties": {"path": {
            "type": "string", "description": "Path relative to the workspace root"}}, "required": ["path"]}
        return [
            FrontDeskTool("list_files", "List a directory of the campaign workspace.", path_param, list_files),
            FrontDeskTool("read_file", "Read a text file of the campaign workspace (the end of long files).",
                          path_param, read_file),
        ]

    def _resolve(self, rel: str) -> Path:
        assert self._root is not None
        path = (self._root / rel).resolve()
        if not path.is_relative_to(self._root):
            raise PermissionError("paths outside the workspace are not accessible")
        return path


def campaign_tools(
    *,
    relay: Callable[[str], Awaitable[dict[str, Any]]],
    interrupt: Callable[[str], Awaitable[dict[str, Any]]],
    answer_gate: Callable[[str, str, str], Awaitable[dict[str, Any]]],
) -> list[FrontDeskTool]:
    """Tools of the front desk of a running campaign."""
    async def _relay(p: dict[str, Any]) -> Any:
        return await relay(str(p.get("message", "")))

    async def _interrupt(p: dict[str, Any]) -> Any:
        return await interrupt(str(p.get("reason", "requested by the researcher")))

    async def _answer(p: dict[str, Any]) -> Any:
        return await answer_gate(str(p.get("gate_id", "")), str(p.get("action", "")), str(p.get("message", "")))

    return [
        FrontDeskTool(
            "relay_to_agent",
            "Send an instruction from the researcher to the research agent; it receives the message "
            "while it works and adjusts.",
            {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
            _relay),
        FrontDeskTool(
            "interrupt_agent",
            "Stop the research agent's current action (it then waits for the next message).",
            {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
            _interrupt),
        FrontDeskTool(
            "answer_gate",
            "Record the researcher's decision on an open human gate. Only after they decided.",
            {"type": "object", "properties": {
                "gate_id": {"type": "string"},
                "action": {"type": "string", "enum": ["continue", "instruct", "stop"]},
                "message": {"type": "string", "description": "the instruction, for 'instruct'"},
            }, "required": ["gate_id", "action"]},
            _answer),
    ]


def _truncate(obj: Any, max_len: int = 200) -> Any:
    if isinstance(obj, str) and len(obj) > max_len:
        return obj[:max_len] + "..."
    if isinstance(obj, dict):
        return {k: _truncate(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list) and len(obj) > 10:
        return obj[:10] + ["..."]
    return obj

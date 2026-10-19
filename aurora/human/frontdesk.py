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
    activity: str = ""          # what is happening right now, in a few words
    iteration: int = 0
    max_iterations: int = 0
    agent_status: str = "idle"
    supervisor_status: str = ""
    open_gates: list[dict[str, Any]] = field(default_factory=list)
    container: str = ""     # e.g. "4 CPUs, 8 GB memory"
    cost_usd: float = 0.0
    budget_usd: float | None = None
    current_job: str = ""
    running_jobs: dict[str, str] = field(default_factory=dict)   # job ID -> description
    recent_actions: list[str] = field(default_factory=list)   # newest last
    ideation_log: list[str] = field(default_factory=list)     # meeting progress, newest last

    def format(self) -> str:
        budget = f" of a US${self.budget_usd:g} budget" if self.budget_usd else ""
        lines = [f"Phase: {self.phase or 'not started'}",
                 f"Now: {self.activity or 'waiting'}",
                 f"API cost so far (all agents and models): ${self.cost_usd:.2f}{budget}"]
        if self.ideation_log and self.phase == "ideation":
            lines.append("Ideation progress (newest last):")
            lines += [f"  {x}" for x in self.ideation_log[-10:]]
        if self.phase in ("research", "analysis", "writing") or self.iteration:
            lines.append(f"Research jobs finished: {self.iteration}/{self.max_iterations or '?'}")
        if self.current_job and self.phase == "research":
            lines.append(f"Running job(s): {self.current_job}")
        if self.recent_actions and self.phase == "research":
            lines.append("Recent actions of the research agent (newest last):")
            lines += [f"  {a}" for a in self.recent_actions[-8:]]
        if self.supervisor_status:
            lines.append(f"Live auditor's last verdict: {self.supervisor_status}")
        if self.container:
            lines.append(f"Container limits: {self.container}")
        for g in self.open_gates:
            lines.append(f"OPEN HUMAN GATE {g['gate_id']} ({g['source']}): {g['title']}\n"
                         f"  {g.get('detail', '')}\n  actions: {', '.join(g.get('options', []))}")
        return "\n".join(lines)


SYSTEM_OVERVIEW = """## How AURORA works (explain it from this when asked)
A campaign runs these phases in order; any can be skipped:
1. Ideation: several models from different families (the "seats") each prepare one research
   direction. In "roundtable" mode they hold a group meeting: each presents, the others ask
   questions, raise concerns or build on it, the presenter answers, and a chair from another
   model family takes notes and then decides the direction (no voting, no ranking). In
   "independent" mode the chair picks one of the ideas without a meeting. The chosen seat
   writes the proposal. No code runs in this phase; its cost is the models' API calls.
2. Screening: an agent scores the proposal for novelty, feasibility and significance; a
   low score sends the campaign back to ideation.
3. Planning: an agent turns the proposal into a plan of jobs with dependencies.
4. Pre-flight: an agent checks every job before it runs at full scale: inputs, data,
   software and parameters, plus a short smoke run on a small input that also estimates
   time and memory. Problems go back to planning; if they remain, the researcher decides.
5. Research: for each job a research agent (a Claude Code session) works in the
   campaign's Docker container: writes and runs code, saves results. Independent jobs may
   run at the same time. A live auditor watches the transcripts and may advise, interrupt
   or escalate to the researcher.
6. Analysis: an agent checks the results against the objective and may ask for another
   research round.
7. Writing: an agent writes the report.
Between phases a milestone auditor reviews the output: pass, reject (the phase revises its
work) or escalate (the researcher decides). A human gate is a point where the campaign waits
for the researcher's decision: continue, instruct, or stop. The gate that opens when
screening keeps rejecting proposals also offers propose: the researcher says in their own
words which direction to pursue (one of the proposals, with or without changes, or their own
idea) and whether it goes through ideation and/or screening again first; by default neither,
and planning starts from it.
To start an experiment from the researcher's own idea, skip ideation: the instruction itself
is then the direction.
"""

CAMPAIGN_PROMPT = """You are the front desk of AURORA, a system that runs long research campaigns with AI agents.
You talk with the researcher while the campaign runs. The agents work in parallel; you never
do the research yourself.

What you can do:
- Answer questions about progress from the status block, which is current, and by reading
  files in the workspace (list_files, read_file). Say where things are (file paths). Do not
  guess; if the status says what is happening, trust it.
- Relay instructions: when the researcher wants the research agent to do something
  differently, call relay_to_agent with a clear, self-contained instruction. The agent
  receives it while it works. Before the research phase there is no research agent yet:
  say so, and the instruction will be passed on when research starts.
- Container resources: when the researcher wants more (or fewer) CPUs or memory for the
  experiment's container, call set_container_resources; it takes effect at once, nothing restarts.
- Interrupt: when the researcher wants the research agent to stop its current action, call
  interrupt_agent (and relay the new instruction, if any). To stop the whole campaign, the
  researcher uses the Stop button.
- Human gates: when the status shows an OPEN HUMAN GATE, explain it briefly and help the
  researcher decide. Once they have clearly decided, call answer_gate with one of the
  actions the gate offers: continue (optionally with a note), instruct (with their
  instruction), propose (where offered: their choice of direction) or stop. Never answer a
  gate on your own. For propose, pass what the researcher said about the direction in their
  own words, complete; it is interpreted against the proposals on file. Ask whether it should
  go through ideation (the seats develop it), screening (it is scored), both, or neither
  (planning starts from it at once) unless they said so, and pass that as rerun.

Be brief and concrete. Reply in the researcher's language.

""" + SYSTEM_OVERVIEW

PROJECT_PROMPT = """You are the front desk of AURORA, a system that runs long research campaigns with AI agents.
You help the researcher turn what they want into an experiment proposal for this project.

- Talk naturally; ask a clarifying question when something important is unclear
  (goal, data, success criteria, constraints), but do not interrogate. If the researcher
  wants you to suggest the question, propose concrete options or draft one yourself.
- When you understand enough, call propose_experiment. The researcher sees a card with
  your proposal, its phases and its budget, and confirms it or asks for changes; then call
  propose_experiment again with the update. You never start experiments yourself.
- Write the instruction as a self-contained brief for a capable colleague who has never
  seen the project: what to find out or build, what data exists and where, what a good
  result looks like, constraints.
- Choose the phases: skip ideation and screening when the question is already concrete;
  keep them when the researcher wants the agents to explore directions; skip writing when
  no report is wanted. Research is always run. Mention in your reply which phases run.
- With ideation, choose its mode: "roundtable" (a group meeting; richer discussion, about four
  times the cost) or "independent" (each model proposes on its own and the chair picks one;
  cheaper, and in our evaluation at least as good). Ask the researcher if they have a preference.

Be brief. Reply in the researcher's language.

""" + SYSTEM_OVERVIEW


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
    relay: Callable[[str, str], Awaitable[dict[str, Any]]],
    interrupt: Callable[[str, str], Awaitable[dict[str, Any]]],
    answer_gate: Callable[..., Awaitable[dict[str, Any]]],
    set_resources: Callable[[float | None, int | None], Awaitable[dict[str, Any]]] | None = None,
) -> list[FrontDeskTool]:
    """Tools of the front desk of a running campaign."""
    async def _relay(p: dict[str, Any]) -> Any:
        return await relay(str(p.get("message", "")), str(p.get("job_id") or ""))

    async def _interrupt(p: dict[str, Any]) -> Any:
        return await interrupt(str(p.get("reason", "requested by the researcher")), str(p.get("job_id") or ""))

    async def _answer(p: dict[str, Any]) -> Any:
        rerun = p.get("rerun") if isinstance(p.get("rerun"), list) else []
        return await answer_gate(str(p.get("gate_id", "")), str(p.get("action", "")), str(p.get("message", "")),
                                 [str(x) for x in rerun])

    tools = [
        FrontDeskTool(
            "relay_to_agent",
            "Send an instruction from the researcher to the research agent; it receives the message "
            "while it works and adjusts. When several jobs run, give job_id to address one of them "
            "(otherwise all running jobs receive it).",
            {"type": "object", "properties": {"message": {"type": "string"}, "job_id": {"type": "string"}},
             "required": ["message"]},
            _relay),
        FrontDeskTool(
            "interrupt_agent",
            "Stop the research agent's current action (it then waits for the next message). With job_id, "
            "only that job's agent; otherwise all running jobs.",
            {"type": "object", "properties": {"reason": {"type": "string"}, "job_id": {"type": "string"}},
             "required": ["reason"]},
            _interrupt),
        FrontDeskTool(
            "answer_gate",
            "Record the researcher's decision on an open human gate. Only after they decided.",
            {"type": "object", "properties": {
                "gate_id": {"type": "string"},
                "action": {"type": "string", "enum": ["continue", "instruct", "propose", "stop"]},
                "message": {"type": "string",
                            "description": "the instruction, for 'instruct'; the researcher's words on the direction, for 'propose'; an optional note, for 'continue'"},
                "rerun": {"type": "array", "items": {"type": "string", "enum": ["ideation", "screening"]},
                          "description": "for 'propose': the phases the researcher wants the direction to go "
                                         "through again before planning; empty (the default) for neither"},
            }, "required": ["gate_id", "action"]},
            _answer),
    ]
    if set_resources is not None:
        async def _resources(p: dict[str, Any]) -> Any:
            cpus = p.get("cpus")
            mem = p.get("memory_gb")
            return await set_resources(float(cpus) if cpus else None, int(float(mem) * 1024) if mem else None)

        tools.append(FrontDeskTool(
            "set_container_resources",
            "Change the CPU and memory limits of the experiment's container while it runs (nothing "
            "restarts). Only when the researcher asks for it.",
            {"type": "object", "properties": {
                "cpus": {"type": "number", "description": "number of CPUs"},
                "memory_gb": {"type": "number", "description": "memory in GB"},
            }},
            _resources))
    return tools


def _truncate(obj: Any, max_len: int = 200) -> Any:
    if isinstance(obj, str) and len(obj) > max_len:
        return obj[:max_len] + "..."
    if isinstance(obj, dict):
        return {k: _truncate(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list) and len(obj) > 10:
        return obj[:10] + ["..."]
    return obj

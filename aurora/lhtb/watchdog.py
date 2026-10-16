"""Two-tier agentic watchdog — L1 fast triage + L2 deep analysis.

Architecture
------------
L1 (fast cheap model via API, e.g. DeepSeek V4 Flash):
  - Runs every event window, very low API cost (~$0.09/M input)
  - Fast triage: NORMAL/WARNING/CRITICAL + confidence score
  - Escalates to L2 when confidence is low or situation is ambiguous

L2 (same or stronger model via API):
  - Stateful agent with its own conversation history and running summary
  - Triggered only on L1 escalation — rare, so API cost stays low
  - Has deeper reasoning and can use tools to investigate
  - Its verdict overrides L1's judgment

Both tiers maintain:
  - Persistent running summary across cycles
  - Read-only inspection tools
  - Full trajectory logging
  - Context compaction for long runs
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import httpx

logger = logging.getLogger(__name__)

MAX_CONTEXT_MESSAGES = 40
COMPACTION_TRIGGER = 30


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

@dataclass
class WatchdogVerdict:
    level: str = "normal"
    reason: str = ""
    suggestion: str = ""
    confidence: str = "high"  # high | medium | low
    source: str = "L1"  # L1 | L2
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Trajectory entry for watchdog's own actions
# ---------------------------------------------------------------------------

@dataclass
class WatchdogTrajectoryEntry:
    timestamp: float = 0.0
    event_type: str = ""  # thinking | tool_call | tool_result | verdict | compaction | escalation
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# L2 Deep-Analysis Agent
# ---------------------------------------------------------------------------

class L2Agent:
    """Stateful deep-analysis agent backed by a strong API model.

    Activated only when L1 escalates.  Maintains its own conversation
    history and running summary across escalation calls.
    """

    def __init__(
        self,
        *,
        model: str = "deepseek/deepseek-v4-flash",
        api_key: str = "",
        base_url: str = "https://openrouter.ai/api/v1",
        task_description: str = "",
        container_id: str | None = None,
        max_turns: int = 5,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._base_url = base_url
        self._task_desc = task_description
        self._container_id = container_id
        self._max_turns = max_turns

        self._messages: list[dict[str, Any]] = []
        self._running_summary: str = ""
        self._trajectory: list[WatchdogTrajectoryEntry] = []
        self._verdicts: list[WatchdogVerdict] = []
        self._calls: int = 0
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=120.0)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def trajectory(self) -> list[WatchdogTrajectoryEntry]:
        return list(self._trajectory)

    @property
    def verdicts(self) -> list[WatchdogVerdict]:
        return list(self._verdicts)

    @property
    def running_summary(self) -> str:
        return self._running_summary

    async def analyze(
        self,
        *,
        l1_verdict: WatchdogVerdict,
        l1_summary: str,
        recent_events: list[dict[str, Any]],
        all_events: list[dict[str, Any]],
    ) -> WatchdogVerdict:
        """Run a deep-analysis cycle.  Returns the L2 verdict."""
        self._calls += 1

        if len(self._messages) > COMPACTION_TRIGGER:
            await self._compact_context()

        events_text = json.dumps(recent_events[-30:], default=str, ensure_ascii=False)

        tool_descriptions = self._prompt_tool_descriptions()

        user_msg = f"""## Escalation from L1 Watchdog
L1 assessed: **{l1_verdict.level.upper()}** (confidence: {l1_verdict.confidence})
L1 reason: {l1_verdict.reason}
L1 suggestion: {l1_verdict.suggestion}

## L1's Running Summary
{l1_summary or "(none)"}

## Your Running Summary
{self._running_summary or "(first L2 call — no summary yet)"}

## Recent Agent Events ({len(recent_events[-30:])} events)
{events_text}

The L1 watchdog (a smaller local model) was not confident in its assessment and escalated to you for deeper analysis. You are a stronger model with better reasoning. Review the situation carefully.

{tool_descriptions}

To call a tool, respond with JSON:
{{"action": "tool_call", "tool": "<tool_name>", "args": {{...}}}}

When done, respond with your verdict:
{{"action": "verdict", "level": "normal|warning|critical", "reason": "detailed explanation", "suggestion": "specific actionable advice", "summary_update": "your updated understanding of agent progress"}}

Respond with exactly ONE JSON object per message."""

        self._messages.append({"role": "user", "content": user_msg})

        # Store references for tool handlers
        self._all_events_ref = all_events

        pending_verdict: WatchdogVerdict | None = None

        for turn in range(self._max_turns + 1):
            try:
                headers = {"Authorization": f"Bearer {self._api_key}"}
                resp = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": self._system_prompt()},
                            *self._messages,
                        ],
                        "temperature": 0.2,
                        "max_tokens": 2048,
                    },
                )
                resp.raise_for_status()
                result = resp.json()
            except Exception as e:
                logger.warning("L2 API call failed: %s", e)
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="error",
                    data={"error": str(e), "source": "L2"},
                ))
                break

            text = result["choices"][0]["message"].get("content") or ""
            self._messages.append({"role": "assistant", "content": text})

            # Extract JSON from response (may contain thinking/reasoning text before JSON)
            parsed = _extract_json(text)
            if parsed is None:
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="thinking",
                    data={"text": text[:500], "source": "L2", "parse_error": "no JSON found"},
                ))
                break

            action = parsed.get("action", "")

            if action == "verdict":
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="verdict",
                    data={
                        "level": parsed.get("level", "normal"),
                        "reason": parsed.get("reason", ""),
                        "source": "L2",
                    },
                ))
                summary_update = parsed.get("summary_update", "")
                if summary_update:
                    self._running_summary = summary_update
                pending_verdict = WatchdogVerdict(
                    level=parsed.get("level", "normal"),
                    reason=parsed.get("reason", ""),
                    suggestion=parsed.get("suggestion", ""),
                    confidence="high",
                    source="L2",
                )
                self._verdicts.append(pending_verdict)
                break

            elif action == "tool_call":
                tool_name = parsed.get("tool", "")
                tool_args = parsed.get("args", {})
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="tool_call",
                    data={"tool_name": tool_name, "args": _summarize_event_data(tool_args), "source": "L2"},
                ))
                tool_result = await self._handle_tool(tool_name, tool_args, all_events)
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="tool_result",
                    data={"tool_name": tool_name, "result": tool_result[:1000], "source": "L2"},
                ))
                self._messages.append({
                    "role": "user",
                    "content": f"Tool `{tool_name}` result:\n{tool_result}\n\nContinue your analysis. Call another tool or issue your verdict.",
                })
            else:
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="thinking",
                    data={"text": text[:500], "source": "L2"},
                ))
                break

        if pending_verdict is None:
            pending_verdict = WatchdogVerdict(
                level=l1_verdict.level,
                reason=f"L2 deferred to L1: {l1_verdict.reason}",
                confidence="medium",
                source="L2",
            )
            self._verdicts.append(pending_verdict)

        return pending_verdict

    async def _handle_tool(
        self, name: str, args: dict[str, Any], all_events: list[dict[str, Any]]
    ) -> str:
        if name == "get_event_history":
            last_n = min(args.get("last_n", 30), 100)
            type_filter = args.get("event_type_filter", "")
            events = list(all_events)
            if type_filter:
                events = [e for e in events if e.get("event_type") == type_filter]
            return json.dumps(events[-last_n:], default=str, ensure_ascii=False)

        elif name == "get_agent_status":
            recent_tools = []
            for e in reversed(all_events):
                if e.get("event_type") == "tool_call_start":
                    recent_tools.append(e.get("data", {}).get("tool_name", "?"))
                    if len(recent_tools) >= 15:
                        break
            return json.dumps({
                "total_events": len(all_events),
                "recent_tool_calls": recent_tools,
            })

        elif name == "inspect_container":
            return await _inspect_container(self._container_id, args.get("command", ""))

        elif name == "get_l1_history":
            return json.dumps({
                "l1_verdicts": [
                    {"level": v.level, "reason": v.reason, "confidence": v.confidence, "ts": v.timestamp}
                    for v in (self._verdicts or [])
                ][-10:],
            })

        return json.dumps({"error": f"Unknown tool: {name}"})

    def _prompt_tool_descriptions(self) -> str:
        lines = ["## Available Tools"]
        lines.append('- `get_event_history`: Retrieve events. Args: {"last_n": 30, "event_type_filter": ""}')
        lines.append('- `get_agent_status`: Current status and recent tool calls. Args: {}')
        lines.append('- `get_l1_history`: See all previous L1 watchdog verdicts. Args: {}')
        if self._container_id:
            lines.append('- `inspect_container`: Read-only command in Docker. Args: {"command": "ls -la /app"}')
        return "\n".join(lines)

    def _system_prompt(self) -> str:
        return f"""You are an expert L2 supervisor agent performing deep analysis of another AI agent's behavior.

## Task the Monitored Agent is Working On
{self._task_desc}

## Your Role
You are called when the L1 watchdog (a fast local model) is not confident in its assessment.
Your job is to do a thorough analysis and make a definitive judgment.

You have stronger reasoning capabilities than L1. Use them to:
- Detect subtle patterns (slow drift, strategy regression, wrong approach)
- Verify whether the agent's changes actually make progress
- Distinguish creative problem-solving from aimless exploration
- Assess whether the agent's strategy is viable given the task constraints

## Verdict Levels
- **normal**: Agent is making genuine progress, even if the approach is unconventional.
- **warning**: Concerning pattern, but give the agent 1-2 more cycles to self-correct.
- **critical**: Agent must be interrupted — stuck, destructive, or fundamentally wrong approach.

## Running Summary
Your summary persists across L2 calls. Include your deeper understanding of:
- Agent's overall strategy and whether it's viable
- Specific progress markers (files changed, tests passing, etc.)
- Risks or failure modes you're watching for"""

    async def _compact_context(self) -> None:
        if len(self._messages) <= MAX_CONTEXT_MESSAGES:
            return
        keep = self._messages[-(MAX_CONTEXT_MESSAGES - 5):]
        dropped = len(self._messages) - len(keep)
        self._messages = [{
            "role": "user",
            "content": f"[CONTEXT COMPACTED] {dropped} earlier messages removed. Your running summary has the key context.",
        }] + keep
        self._trajectory.append(WatchdogTrajectoryEntry(
            timestamp=time.time(), event_type="compaction",
            data={"messages_compacted": dropped, "source": "L2"},
        ))


# ---------------------------------------------------------------------------
# L1 Triage Agent (WatchdogAgent — the main entry point)
# ---------------------------------------------------------------------------

class WatchdogAgent:
    """Two-tier watchdog: L1 fast triage + L2 deep analysis on escalation.

    Parameters
    ----------
    judge_base_url / judge_model / judge_api_key:
        L1 model endpoint (default: DeepSeek V4 Flash via OpenRouter).
    l2_model / l2_api_key / l2_base_url:
        L2 model for deep analysis on escalation.
        If l2_model is empty, L2 escalation is disabled.
    task_description:
        What the monitored agent is supposed to be doing.
    interrupt_callback:
        Called with a message string when CRITICAL is issued.
    container_id:
        Docker container ID for inspection tools.
    """

    def __init__(
        self,
        *,
        judge_base_url: str = "https://openrouter.ai/api/v1",
        judge_model: str = "deepseek/deepseek-v4-flash",
        judge_api_key: str = "",
        task_description: str = "",
        interrupt_callback: Callable[[str], Awaitable[None]] | None = None,
        broadcast_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        container_id: str | None = None,
        window_size: int = 15,
        window_timeout: float = 30.0,
        cooldown: float = 10.0,
        max_inspect_turns: int = 3,
        # L2 config
        l2_model: str = "deepseek/deepseek-v4-flash",
        l2_api_key: str = "",
        l2_base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        self._base_url = judge_base_url
        self._model = judge_model
        self._api_key = judge_api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._task_desc = task_description
        self._interrupt = interrupt_callback
        self._broadcast = broadcast_callback
        self._container_id = container_id
        self._window_size = window_size
        self._window_timeout = window_timeout
        self._cooldown = cooldown
        self._max_inspect_turns = max_inspect_turns

        # L2 agent
        self._l2: L2Agent | None = None
        if l2_model:
            self._l2 = L2Agent(
                model=l2_model,
                api_key=l2_api_key,
                base_url=l2_base_url,
                task_description=task_description,
                container_id=container_id,
            )

        # Event buffer
        self._buffer: list[dict[str, Any]] = []

        # L1 conversation history
        self._messages: list[dict[str, Any]] = []

        # Running summary
        self._running_summary: str = ""

        # Verdict history (from both L1 and L2)
        self._history: list[WatchdogVerdict] = []
        self._consecutive_warnings: int = 0

        # Trajectory (combined L1 + L2)
        self._trajectory: list[WatchdogTrajectoryEntry] = []

        # Stats
        self._cycles: int = 0
        self._l2_escalations: int = 0
        self._total_events_seen: int = 0
        self._last_cycle_time: float = 0.0

        # Runtime
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None
        self._event_signal = asyncio.Event()
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Tool handlers (L1)
    # ------------------------------------------------------------------

    async def _handle_tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "get_event_history":
            last_n = min(args.get("last_n", 20), 50)
            type_filter = args.get("event_type_filter", "")
            events = list(self._all_events)
            if type_filter:
                events = [e for e in events if e.get("event_type") == type_filter]
            return json.dumps(events[-last_n:], default=str, ensure_ascii=False)

        elif name == "get_agent_status":
            recent_tools = []
            for e in reversed(self._all_events):
                if e.get("event_type") == "tool_call_start":
                    recent_tools.append(e.get("data", {}).get("tool_name", "?"))
                    if len(recent_tools) >= 10:
                        break
            return json.dumps({
                "total_events": len(self._all_events),
                "total_events_seen_by_watchdog": self._total_events_seen,
                "watchdog_cycles_completed": self._cycles,
                "l2_escalations": self._l2_escalations,
                "elapsed_seconds": round(time.time() - self._start_time, 1) if hasattr(self, '_start_time') else 0,
                "recent_tool_calls": recent_tools,
                "verdicts_issued": len(self._history),
                "warnings": sum(1 for v in self._history if v.level == "warning"),
                "criticals": sum(1 for v in self._history if v.level == "critical"),
            })

        elif name == "inspect_container":
            return await _inspect_container(self._container_id, args.get("command", ""))

        elif name == "issue_verdict":
            return self._tool_issue_verdict(args)

        return json.dumps({"error": f"Unknown tool: {name}"})

    def _tool_issue_verdict(self, args: dict[str, Any]) -> str:
        level = args.get("level", "normal")
        reason = args.get("reason", "")
        suggestion = args.get("suggestion", "")
        summary_update = args.get("summary_update", "")
        confidence = args.get("confidence", "high")

        verdict = WatchdogVerdict(
            level=level, reason=reason, suggestion=suggestion,
            confidence=confidence, source="L1",
        )
        self._history.append(verdict)
        self._trajectory.append(WatchdogTrajectoryEntry(
            timestamp=time.time(), event_type="verdict",
            data={"level": level, "reason": reason, "confidence": confidence, "source": "L1"},
        ))
        if summary_update:
            self._running_summary = summary_update
        self._pending_verdict = verdict
        asyncio.ensure_future(self._emit("watchdog_verdict", {
            "level": level, "reason": reason, "suggestion": suggestion,
            "confidence": confidence, "source": "L1", "cycle": self._cycles,
        }))
        return json.dumps({"status": "verdict_recorded", "level": level})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    _all_events: list[dict[str, Any]]

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._all_events = []
        self._pending_verdict = None
        self._client = httpx.AsyncClient(timeout=120.0)
        if self._l2:
            await self._l2.start()
        self._loop_task = asyncio.create_task(self._main_loop())
        logger.info(
            "Watchdog started — L1: %s, L2: %s",
            self._model, self._l2._model if self._l2 else "disabled",
        )

    async def stop(self) -> None:
        self._running = False
        self._event_signal.set()
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        if self._l2:
            await self._l2.stop()
        logger.info(
            "Watchdog stopped — %d cycles, %d L2 escalations, %d verdicts (%d W, %d C)",
            self._cycles, self._l2_escalations,
            len(self._history),
            sum(1 for v in self._history if v.level == "warning"),
            sum(1 for v in self._history if v.level == "critical"),
        )

    async def on_event(self, event: dict[str, Any]) -> None:
        summarized = {
            "event_type": event.get("event_type", ""),
            "data": _summarize_event_data(event.get("data", {})),
            "timestamp": event.get("timestamp", time.time()),
        }
        self._all_events.append(summarized)
        self._buffer.append(summarized)
        if len(self._buffer) >= self._window_size:
            self._event_signal.set()

    @property
    def verdicts(self) -> list[WatchdogVerdict]:
        return list(self._history)

    @property
    def trajectory(self) -> list[WatchdogTrajectoryEntry]:
        combined = list(self._trajectory)
        if self._l2:
            combined.extend(self._l2.trajectory)
        combined.sort(key=lambda t: t.timestamp)
        return combined

    @property
    def running_summary(self) -> str:
        return self._running_summary

    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast a watchdog event to the frontend via WebSocket."""
        if self._broadcast:
            try:
                await self._broadcast({
                    "event_type": event_type,
                    "data": data,
                    "timestamp": time.time(),
                })
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        while self._running:
            try:
                await asyncio.wait_for(
                    self._event_signal.wait(),
                    timeout=self._window_timeout,
                )
            except asyncio.TimeoutError:
                pass
            self._event_signal.clear()

            if not self._buffer:
                continue

            elapsed = time.time() - self._last_cycle_time
            if elapsed < self._cooldown:
                await asyncio.sleep(self._cooldown - elapsed)

            window = self._buffer[:]
            self._buffer.clear()
            self._total_events_seen += len(window)

            try:
                await self._run_cycle(window)
            except Exception:
                logger.exception("Watchdog cycle failed")

    # ------------------------------------------------------------------
    # Single cycle: L1 triage → optional L2 escalation
    # ------------------------------------------------------------------

    async def _run_cycle(self, window: list[dict[str, Any]]) -> None:
        self._cycles += 1
        self._pending_verdict = None

        await self._emit("watchdog_cycle_start", {
            "cycle": self._cycles,
            "events_in_window": len(window),
            "source": "L1",
            "model": self._model,
        })

        if len(self._messages) > COMPACTION_TRIGGER:
            await self._compact_context()

        # DeepSeek V4 Flash has large context — allow more event data
        max_events_chars = 16000
        events_text = json.dumps(window, default=str, ensure_ascii=False)
        if len(events_text) > max_events_chars:
            # Keep only the most recent events that fit
            trimmed = list(window)
            while len(json.dumps(trimmed, default=str, ensure_ascii=False)) > max_events_chars and len(trimmed) > 1:
                trimmed.pop(0)
            events_text = json.dumps(trimmed, default=str, ensure_ascii=False)
            if len(events_text) > max_events_chars:
                events_text = events_text[:max_events_chars] + "...(truncated)"

        tool_descriptions = self._prompt_tool_descriptions()

        confidence_instruction = ""
        if self._l2:
            confidence_instruction = """
Also include a "confidence" field: "high" (you're sure), "medium" (somewhat uncertain), or "low" (unsure, need deeper analysis).
If your confidence is "low", a stronger L2 model will be called for deeper analysis."""

        user_msg = f"""## New Events ({len(window)} events since last check)
{events_text}

## Your Running Summary
{self._running_summary or "(first cycle — no summary yet)"}

Analyze these events. You may call tools to investigate further.

{tool_descriptions}

To call a tool, respond with JSON:
{{"action": "tool_call", "tool": "<tool_name>", "args": {{...}}}}

When done, respond with your verdict:
{{"action": "verdict", "level": "normal|warning|critical", "reason": "one sentence", "suggestion": "what to do differently", "confidence": "high|medium|low", "summary_update": "updated running summary"}}
{confidence_instruction}
Respond with exactly ONE JSON object per message."""

        self._messages.append({"role": "user", "content": user_msg})

        for turn in range(self._max_inspect_turns + 1):
            try:
                headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
                resp = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": self._system_prompt()},
                            *self._messages,
                        ],
                        "temperature": 0.2,
                        "max_tokens": 2048,
                    },
                )
                resp.raise_for_status()
                result = resp.json()
            except Exception as e:
                logger.warning("L1 API call failed: %s", e)
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="error",
                    data={"error": str(e), "source": "L1"},
                ))
                break

            text = result["choices"][0]["message"].get("content") or ""
            self._messages.append({"role": "assistant", "content": text})

            parsed = _extract_json(text)
            if parsed is None:
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="thinking",
                    data={"text": text[:500], "source": "L1", "parse_error": "no JSON found"},
                ))
                break

            action = parsed.get("action", "")

            if action == "verdict":
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="thinking",
                    data={"text": text[:500], "source": "L1"},
                ))
                self._tool_issue_verdict({
                    "level": parsed.get("level", "normal"),
                    "reason": parsed.get("reason", ""),
                    "suggestion": parsed.get("suggestion", ""),
                    "summary_update": parsed.get("summary_update", ""),
                    "confidence": parsed.get("confidence", "high"),
                })
                break

            elif action == "tool_call":
                tool_name = parsed.get("tool", "")
                tool_args = parsed.get("args", {})
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="tool_call",
                    data={"tool_name": tool_name, "args": _summarize_event_data(tool_args), "source": "L1"},
                ))
                await self._emit("watchdog_tool_call", {
                    "tool_name": tool_name, "source": "L1", "cycle": self._cycles,
                })
                tool_result = await self._handle_tool(tool_name, tool_args)
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="tool_result",
                    data={"tool_name": tool_name, "result": tool_result[:1000], "source": "L1"},
                ))
                self._messages.append({
                    "role": "user",
                    "content": f"Tool `{tool_name}` result:\n{tool_result}\n\nContinue your analysis. Call another tool or issue your verdict.",
                })
            else:
                self._trajectory.append(WatchdogTrajectoryEntry(
                    timestamp=time.time(), event_type="thinking",
                    data={"text": text[:500], "source": "L1", "unknown_action": action},
                ))
                break

        # Default verdict if none issued
        if self._pending_verdict is None:
            self._pending_verdict = WatchdogVerdict(
                level="normal", reason="No explicit verdict issued",
                confidence="low", source="L1",
            )
            self._history.append(self._pending_verdict)

        verdict = self._pending_verdict
        self._last_cycle_time = time.time()

        # --- L2 escalation decision ---
        should_escalate = self._should_escalate(verdict)
        if should_escalate and self._l2:
            self._l2_escalations += 1
            self._l2._container_id = self._container_id
            self._trajectory.append(WatchdogTrajectoryEntry(
                timestamp=time.time(), event_type="escalation",
                data={
                    "reason": f"L1 confidence={verdict.confidence}, level={verdict.level}",
                    "l1_verdict": verdict.reason,
                },
            ))
            logger.info(
                "Escalating to L2 — L1 confidence=%s, level=%s",
                verdict.confidence, verdict.level,
            )
            await self._emit("watchdog_escalation", {
                "l1_level": verdict.level, "l1_confidence": verdict.confidence,
                "l1_reason": verdict.reason, "l2_model": self._l2._model,
                "escalation_count": self._l2_escalations,
            })

            l2_verdict = await self._l2.analyze(
                l1_verdict=verdict,
                l1_summary=self._running_summary,
                recent_events=list(self._all_events),
                all_events=list(self._all_events),
            )

            # L2 verdict overrides L1
            verdict = l2_verdict
            self._history.append(verdict)
            logger.info(
                "L2 verdict: %s — %s",
                verdict.level.upper(), verdict.reason,
            )
            await self._emit("watchdog_verdict", {
                "level": verdict.level, "reason": verdict.reason,
                "suggestion": verdict.suggestion, "confidence": verdict.confidence,
                "source": "L2", "cycle": self._cycles,
            })

        # Warning escalation (3 consecutive → critical)
        if verdict.level == "warning":
            self._consecutive_warnings += 1
            logger.warning(
                "Watchdog WARNING (%d consecutive): %s [%s]",
                self._consecutive_warnings, verdict.reason, verdict.source,
            )
            if self._consecutive_warnings >= 3 and self._interrupt:
                verdict.level = "critical"
                verdict.reason = (
                    f"Escalated: {self._consecutive_warnings} consecutive "
                    f"warnings. Latest: {verdict.reason}"
                )
        elif verdict.level == "critical":
            self._consecutive_warnings = 0
        else:
            self._consecutive_warnings = 0

        # Interrupt on CRITICAL
        if verdict.level == "critical" and self._interrupt:
            logger.critical(
                "Watchdog CRITICAL [%s] — interrupting: %s",
                verdict.source, verdict.reason,
            )
            msg = (
                f"[WATCHDOG INTERRUPT] The supervisor agent ({verdict.source}) has detected "
                f"a critical issue and is pausing your execution.\n\n"
                f"Issue: {verdict.reason}\n"
                f"Suggestion: {verdict.suggestion}\n\n"
                f"Please address this before continuing."
            )
            await self._interrupt(msg)

    def _should_escalate(self, verdict: WatchdogVerdict) -> bool:
        """Decide whether to escalate to L2."""
        if not self._l2:
            return False
        # Low confidence — L1 is unsure
        if verdict.confidence == "low":
            return True
        # Medium confidence + warning — ambiguous situation
        if verdict.confidence == "medium" and verdict.level == "warning":
            return True
        # 2 consecutive warnings — escalate before the auto-critical at 3
        if self._consecutive_warnings >= 2 and verdict.level == "warning":
            return True
        return False

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------

    def _prompt_tool_descriptions(self) -> str:
        lines = ["## Available Tools"]
        lines.append('- `get_event_history`: Retrieve recent events. Args: {"last_n": 20, "event_type_filter": ""}')
        lines.append('- `get_agent_status`: Current status and recent tool calls. Args: {}')
        if self._container_id:
            lines.append('- `inspect_container`: Read-only command in Docker. Args: {"command": "ls -la /app"}')
        return "\n".join(lines)

    def _system_prompt(self) -> str:
        l2_note = ""
        if self._l2:
            l2_note = """

## Confidence Assessment
You MUST include a "confidence" field in your verdict:
- **high**: You are certain of your assessment.
- **medium**: Somewhat uncertain — the situation is ambiguous.
- **low**: You are unsure and need deeper analysis from a stronger model.

When you report low confidence, a more capable L2 model will review the situation.
Use low confidence when:
- The agent's strategy is unusual and you can't tell if it's creative or wrong
- There are subtle patterns you're not sure about
- The situation requires deeper domain knowledge to assess"""

        return f"""You are a real-time L1 supervisor agent (fast triage) monitoring another AI agent solving a task.

## Task the Monitored Agent is Working On
{self._task_desc}

## Your Role
Fast triage of the agent's event stream. Classify behavior and flag issues.
You have tools to look at event history, agent status, and the Docker container.

## How to Work
1. Review the new events in context of your running summary.
2. Use tools if you need more context before judging.
3. Issue your verdict with an updated running summary.

## Verdict Levels
- **normal**: Reasonable progress toward the task goal.
- **warning**: Concerning patterns, but agent may self-correct.
- **critical**: Must interrupt — stuck, destructive, or lost.
{l2_note}

## Summary Guidelines
Your summary_update is your memory. Include: accomplishments, current approach, concerns, key changes.
Keep it under 500 words."""

    # ------------------------------------------------------------------
    # Context compaction
    # ------------------------------------------------------------------

    async def _compact_context(self) -> None:
        if len(self._messages) <= MAX_CONTEXT_MESSAGES:
            return

        old_msgs = self._messages[:MAX_CONTEXT_MESSAGES - 10]
        old_text = json.dumps(old_msgs, default=str, ensure_ascii=False)[:4000]

        try:
            headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            resp = await self._client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "user", "content": f"Summarize this watchdog conversation into a concise paragraph. Focus on: key findings, verdicts, patterns, concerns.\n\n{old_text}"},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 512,
                },
            )
            resp.raise_for_status()
            summary = resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("L1 context compaction failed: %s", e)
            summary = f"(compaction failed, {len(old_msgs)} messages dropped)"

        self._messages = [{
            "role": "user",
            "content": f"[CONTEXT COMPACTED] Previous {len(old_msgs)} messages summarized:\n{summary}",
        }] + self._messages[MAX_CONTEXT_MESSAGES - 10:]

        self._trajectory.append(WatchdogTrajectoryEntry(
            timestamp=time.time(), event_type="compaction",
            data={"messages_compacted": len(old_msgs), "summary": summary[:500], "source": "L1"},
        ))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _inspect_container(container_id: str | None, command: str) -> str:
    """Run a read-only command inside a Docker container."""
    if not container_id:
        return json.dumps({"error": "No container available"})

    dangerous = ["rm ", "mv ", "cp ", "> ", ">> ", "chmod", "chown", "kill",
                  "pkill", "dd ", "mkfs", "apt ", "pip ", "npm ", "curl ", "wget "]
    if any(d in command for d in dangerous):
        return json.dumps({"error": "Command rejected — watchdog only has read-only access"})

    import subprocess
    try:
        result = subprocess.run(
            ["docker", "exec", container_id, "bash", "-c", command],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout[:3000]
        if result.returncode != 0:
            output += f"\n[stderr]: {result.stderr[:1000]}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "(command timed out after 30s)"
    except Exception as e:
        return json.dumps({"error": str(e)})


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from text that may contain reasoning before/after it."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find JSON in the text (models sometimes wrap JSON in markdown or thinking)
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


def _summarize_event_data(data: dict[str, Any]) -> dict[str, Any]:
    """Truncate large fields to keep prompts small."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, str) and len(v) > 500:
            out[k] = v[:500] + "..."
        elif isinstance(v, dict):
            out[k] = _summarize_event_data(v)
        elif isinstance(v, list) and len(v) > 10:
            out[k] = v[:10]
        else:
            out[k] = v
    return out

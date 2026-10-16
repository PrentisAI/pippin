"""Agentic tool-use loop for AURORA agents.

Runs a Claude Messages API loop with tool_use, enabling agents to
autonomously call tools, observe results, and decide next steps.

Real-time interaction features:
- **Cooperative interrupt**: checks for user messages between tool calls
- **Chat steering**: injects user messages into the conversation so the
  agent sees and responds to them on the next turn
- **Streaming**: fires callbacks on every significant event (reasoning,
  tool start/result, decision) for live frontend rendering
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from aurora.llm.base import TokenUsage, UsageTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schema (matches Claude API tool format)
# ---------------------------------------------------------------------------

@dataclass
class ToolSchema:
    """A single tool exposed to the agentic loop."""
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class ToolCall:
    """Record of a single tool invocation during the loop."""
    tool_name: str
    tool_input: dict[str, Any]
    tool_result: str
    timestamp: float = field(default_factory=time.time)
    duration_seconds: float = 0.0


class AgenticResult(BaseModel):
    """Outcome of a full agentic loop run."""
    final_text: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    decision: str = "continue"
    token_usage: dict[str, int] = Field(default_factory=lambda: {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    })
    turns: int = 0
    reasoning_trace: list[str] = Field(default_factory=list)
    chat_exchanges: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Callback types
# ---------------------------------------------------------------------------

HookInterceptor = Callable[[str, dict[str, Any], str], Awaitable[None]]
"""Fires after each tool call so the engine can run audit hooks."""


@dataclass
class StreamEvent:
    """A real-time event emitted during the agentic loop."""
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
        }


StreamCallback = Callable[[StreamEvent], Awaitable[None]]
"""Fires on every significant event for live frontend rendering."""


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

class AgenticLoop:
    """Runs Claude with tool_use in an autonomous loop.

    Supports three real-time interaction modes:

    **A. Cooperative interrupt** — between each tool call, the loop checks
    ``user_message_queue`` for pending messages and pauses if found.

    **B. Chat steering** — user messages are injected into the Claude
    conversation so the agent sees them on the next turn and adjusts
    its behavior.

    **C. Streaming** — every reasoning block, tool call, and tool result
    fires a ``StreamEvent`` via ``stream_callback`` for live rendering.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        tools: list[ToolSchema],
        *,
        max_turns: int = 30,
        max_tokens_per_turn: int = 4096,
        usage_tracker: UsageTracker | None = None,
        hook_interceptor: HookInterceptor | None = None,
        user_message_queue: asyncio.Queue[str] | None = None,
        stream_callback: StreamCallback | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._tools = {t.name: t for t in tools}
        self._tool_defs = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]
        self._max_turns = max_turns
        self._max_tokens = max_tokens_per_turn
        self._usage = usage_tracker or UsageTracker()
        self._hook_interceptor = hook_interceptor
        self._user_queue = user_message_queue
        self._stream = stream_callback
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                max_retries=2,
            )
        return self._client

    async def _emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Fire a stream event if a callback is registered."""
        if self._stream is not None:
            event = StreamEvent(event_type=event_type, data=data or {})
            try:
                await self._stream(event)
            except Exception:
                logger.debug("Stream callback error", exc_info=True)

    async def _drain_user_messages(self) -> list[str]:
        """Non-blocking drain of all pending user messages from the queue."""
        messages: list[str] = []
        if self._user_queue is None:
            return messages
        while True:
            try:
                msg = self._user_queue.get_nowait()
                messages.append(msg)
            except asyncio.QueueEmpty:
                break
        return messages

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.4,
    ) -> AgenticResult:
        """Execute the agentic loop with real-time interaction support."""
        client = self._get_client()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_message},
        ]

        result = AgenticResult()
        total_prompt = 0
        total_completion = 0

        await self._emit("loop_started", {"model": self._model, "max_turns": self._max_turns})

        for turn in range(self._max_turns):
            # --- A. Cooperative interrupt: check for user messages ---
            user_msgs = await self._drain_user_messages()
            if user_msgs:
                for um in user_msgs:
                    result.chat_exchanges += 1
                    await self._emit("user_message_injected", {"text": um, "turn": turn})

                    # Inject as a user message with clear framing
                    inject_text = (
                        f"[RESEARCHER NOTE: The human operator has sent you a message "
                        f"during your research iteration. Please acknowledge it and "
                        f"adjust your approach if appropriate.]\n\n"
                        f"Human message: {um}"
                    )

                    # If the last message is from the user (tool results),
                    # we need to add an assistant message first to maintain
                    # the alternating pattern
                    if messages and messages[-1].get("role") == "user":
                        messages.append({
                            "role": "assistant",
                            "content": "[Acknowledged — reading operator message.]",
                        })
                    messages.append({"role": "user", "content": inject_text})

            # --- C. Streaming: emit turn start ---
            await self._emit("turn_start", {"turn": turn, "total_turns": self._max_turns})

            try:
                response, text_parts, tool_use_blocks = await self._stream_api_call(
                    client, system_prompt, messages, temperature, turn,
                )
            except Exception as exc:
                logger.error("Agentic loop API call failed at turn %d: %s", turn, exc)
                await self._emit("error", {"turn": turn, "error": str(exc)})
                result.final_text += f"\n[API error at turn {turn}: {exc}]"
                # a failed call is a failed iteration, never a silent "continue"
                result.error = f"API call failed at turn {turn}: {exc}"
                result.decision = "error"
                break

            total_prompt += response.usage.input_tokens
            total_completion += response.usage.output_tokens

            # Reasoning was already streamed token-by-token; record full trace
            if text_parts:
                combined = "\n".join(text_parts)
                result.reasoning_trace.append(combined)

            # If the model stopped without tool calls, we're done
            if response.stop_reason == "end_turn" and not tool_use_blocks:
                result.final_text = "\n".join(text_parts)
                await self._emit("agent_done", {
                    "text": result.final_text[:500],
                    "turn": turn,
                })
                break

            # Process tool calls
            if tool_use_blocks:
                messages.append({"role": "assistant", "content": response.content})
                tool_results: list[dict[str, Any]] = []

                for block in tool_use_blocks:
                    tool_name = block.name
                    tool_input = block.input
                    call_id = block.id

                    # --- C. Streaming: emit tool call start ---
                    await self._emit("tool_call_start", {
                        "tool_name": tool_name,
                        "tool_input": _safe_truncate(tool_input),
                        "turn": turn,
                    })

                    # --- A. Cooperative interrupt: check between tool calls ---
                    mid_msgs = await self._drain_user_messages()
                    for um in mid_msgs:
                        result.chat_exchanges += 1
                        await self._emit("user_message_injected", {
                            "text": um,
                            "turn": turn,
                            "mid_tool_call": True,
                        })
                        # Queue for injection after this tool batch
                        if self._user_queue is not None:
                            self._user_queue.put_nowait(um)

                    call_start = time.time()
                    tool_output = await self._dispatch_tool(tool_name, tool_input)
                    call_duration = time.time() - call_start

                    call_record = {
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "tool_result": tool_output[:2000],
                        "duration_seconds": round(call_duration, 3),
                    }
                    result.tool_calls.append(call_record)

                    # --- C. Streaming: emit tool result ---
                    await self._emit("tool_call_result", {
                        "tool_name": tool_name,
                        "success": "error" not in tool_output.lower()[:50],
                        "result_preview": tool_output[:500],
                        "duration_seconds": round(call_duration, 3),
                        "turn": turn,
                    })

                    self._extract_side_effects(tool_name, tool_input, result)

                    if self._hook_interceptor:
                        try:
                            await self._hook_interceptor(tool_name, tool_input, tool_output)
                        except Exception as hook_exc:
                            logger.warning("Hook interceptor failed: %s", hook_exc)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": tool_output,
                    })

                messages.append({"role": "user", "content": tool_results})

            if response.stop_reason == "end_turn" and text_parts and not tool_use_blocks:
                result.final_text = "\n".join(text_parts)
                await self._emit("agent_done", {
                    "text": result.final_text[:500],
                    "turn": turn,
                })
                break

            result.turns = turn + 1

        # Finalize
        result.token_usage = {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
        }
        self._usage.record("anthropic", self._model, TokenUsage(
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            total_tokens=total_prompt + total_completion,
        ))

        if not result.decision or result.decision == "continue":
            result.decision = self._extract_decision(result)

        await self._emit("loop_finished", {
            "turns": result.turns,
            "tool_calls": len(result.tool_calls),
            "decision": result.decision,
            "chat_exchanges": result.chat_exchanges,
            "tokens": result.token_usage,
        })

        return result

    async def _stream_api_call(
        self,
        client: Any,
        system_prompt: str,
        messages: list[dict[str, Any]],
        temperature: float,
        turn: int,
    ) -> tuple[Any, list[str], list[Any]]:
        """Call Claude with streaming, emitting text tokens in real time.

        Returns (final_message, text_parts, tool_use_blocks) — the same
        shape the old non-streaming call produced, but the frontend sees
        tokens as they arrive.
        """
        text_parts: list[str] = []
        tool_use_blocks: list[Any] = []

        # Buffer for accumulating the current text block's streamed tokens.
        # We emit a 'reasoning_delta' for each chunk and a full 'reasoning'
        # at the end of each text block.
        text_buffer: list[str] = []
        _current_block_type: str | None = None

        async with client.messages.stream(
            model=self._model,
            system=system_prompt,
            messages=messages,
            tools=self._tool_defs,
            max_tokens=self._max_tokens,
        ) as stream:
            async for event in stream:
                etype = event.type

                if etype == "content_block_start":
                    block = event.content_block
                    if hasattr(block, "text"):
                        _current_block_type = "text"
                        text_buffer = []
                    elif getattr(block, "type", None) == "tool_use":
                        _current_block_type = "tool_use"

                elif etype == "content_block_delta":
                    delta = event.delta
                    if hasattr(delta, "text"):
                        chunk = delta.text
                        text_buffer.append(chunk)
                        await self._emit("reasoning_delta", {
                            "text": chunk,
                            "turn": turn,
                        })
                    # tool_use input_json_delta is handled by the SDK internally

                elif etype == "content_block_stop":
                    if _current_block_type == "text" and text_buffer:
                        full_text = "".join(text_buffer)
                        text_parts.append(full_text)
                        await self._emit("reasoning", {"text": full_text, "turn": turn})
                        text_buffer = []
                    _current_block_type = None

            # Get the final assembled message (contains complete tool_use blocks)
            final_message = await stream.get_final_message()

        # Extract tool_use blocks from the final assembled message
        for block in final_message.content:
            if getattr(block, "type", None) == "tool_use":
                tool_use_blocks.append(block)

        return final_message, text_parts, tool_use_blocks

    async def _dispatch_tool(self, name: str, input_data: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            return await tool.handler(input_data)
        except Exception as exc:
            logger.error("Tool '%s' execution failed: %s", name, exc)
            return json.dumps({"error": str(exc), "tool": name})

    @staticmethod
    def _extract_side_effects(
        tool_name: str,
        tool_input: dict[str, Any],
        result: AgenticResult,
    ) -> None:
        if tool_name == "record_observation":
            result.observations.append(tool_input)
        elif tool_name == "record_claim":
            result.claims.append(tool_input)
        elif tool_name == "decide_next_step":
            result.decision = tool_input.get("decision", "continue")

    @staticmethod
    def _extract_decision(result: AgenticResult) -> str:
        text = result.final_text.lower()
        if "replan" in text:
            return "replan"
        if "request audit" in text or "request_audit" in text:
            return "audit"
        if "terminate" in text or "research complete" in text:
            return "done"
        if "follow up" in text or "follow_up" in text:
            return "follow_up"
        return "continue"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


def _safe_truncate(obj: Any, max_len: int = 200) -> Any:
    """Truncate large values for stream events."""
    if isinstance(obj, str) and len(obj) > max_len:
        return obj[:max_len] + "..."
    if isinstance(obj, dict):
        return {k: _safe_truncate(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list) and len(obj) > 10:
        return obj[:10] + ["..."]
    return obj

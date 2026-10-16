"""General-purpose agent loop drivers.

Three agent loop implementations for driving LLM agents:

- ``ClaudeCodeLoop`` — drives Claude Code CLI via subprocess + stream-json
- ``OpenAIAgenticLoop`` — OpenAI-compatible chat completions loop
- ``AnthropicAgenticLoop`` — thin wrapper around Anthropic Messages API

These are general LLM drivers, not LHTB-specific. Any task that needs
an autonomous agent loop can use them.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aurora.llm.tool_runner import ToolSchema, StreamEvent

logger = logging.getLogger(__name__)


def _safe_serialize(obj: Any) -> Any:
    """Convert an object to a JSON-safe form, truncating large strings."""
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v) for v in obj]
    if isinstance(obj, str):
        return obj[:5000] + "..." if len(obj) > 5000 else obj
    if isinstance(obj, (int, float, bool, type(None))):
        return obj
    return str(obj)[:2000]


@dataclass
class TrajectoryEntry:
    """A single event in the agent trajectory."""
    timestamp: float = 0.0
    phase: int = 0
    turn: int = 0
    event_type: str = ""  # thinking | tool_call | tool_result | text | error
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# OpenAI-compatible agentic loop
# ---------------------------------------------------------------------------

class OpenAIAgenticLoop:
    """Agentic tool-use loop using the OpenAI chat completions API.

    Compatible with OpenRouter, Together, and any OpenAI-format endpoint.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        tools: list[ToolSchema],
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        max_turns: int = 200,
        max_tokens_per_turn: int = 16384,
        stream_callback: Any = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._tools = {t.name: t for t in tools}
        self._tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]
        self._base_url = base_url
        self._max_turns = max_turns
        self._max_tokens = max_tokens_per_turn
        self._stream = stream_callback
        self._client: Any = None
        self._pending_injections: list[str] = []

    async def inject_message(self, message: str) -> None:
        """Queue a user message to be injected before the next API call."""
        self._pending_injections.append(message)

    def _get_client(self) -> Any:
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                max_retries=3,
            )
        return self._client

    async def _emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self._stream is not None:
            event = StreamEvent(event_type=event_type, data=data or {})
            try:
                await self._stream(event)
            except Exception:
                pass

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """Execute the agentic loop until the model stops calling tools."""
        client = self._get_client()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        total_prompt = 0
        total_completion = 0
        all_tool_calls: list[dict[str, Any]] = []
        trajectory: list[TrajectoryEntry] = []
        task_completed = False

        await self._emit("loop_started", {"model": self._model})

        for turn in range(self._max_turns):
            while self._pending_injections:
                injected = self._pending_injections.pop(0)
                messages.append({"role": "user", "content": injected})

            await self._emit("turn_start", {"turn": turn})

            try:
                response = await client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=self._tool_defs,
                    temperature=temperature,
                    max_tokens=self._max_tokens,
                )
            except Exception as exc:
                logger.error("API call failed at turn %d: %s", turn, exc)
                await self._emit("error", {"turn": turn, "error": str(exc)})
                trajectory.append(TrajectoryEntry(
                    timestamp=time.time(), turn=turn, event_type="error",
                    data={"error": str(exc)},
                ))
                return {
                    "final_text": f"API error at turn {turn}: {exc}",
                    "tool_calls": all_tool_calls,
                    "turns": turn,
                    "tokens": {"prompt": total_prompt, "completion": total_completion},
                    "task_completed": task_completed,
                    "error": str(exc),
                    "trajectory": trajectory,
                }

            choice = response.choices[0]
            usage = response.usage
            if usage:
                total_prompt += usage.prompt_tokens
                total_completion += usage.completion_tokens

            text = choice.message.content or ""
            if text:
                await self._emit("reasoning", {"text": text[:500], "turn": turn})
                logger.info("Turn %d text: %s", turn, text[:200])
                trajectory.append(TrajectoryEntry(
                    timestamp=time.time(), turn=turn, event_type="thinking",
                    data={"text": text},
                ))

            tool_calls = choice.message.tool_calls
            if not tool_calls:
                await self._emit("agent_done", {"text": text[:500], "turn": turn})
                return {
                    "final_text": text,
                    "tool_calls": all_tool_calls,
                    "turns": turn + 1,
                    "tokens": {"prompt": total_prompt, "completion": total_completion},
                    "task_completed": task_completed,
                    "trajectory": trajectory,
                }

            messages.append(choice.message.model_dump())

            for tc in tool_calls:
                fn = tc.function
                tool_name = fn.name
                try:
                    tool_input = json.loads(fn.arguments) if isinstance(fn.arguments, str) else fn.arguments
                except json.JSONDecodeError:
                    tool_input = {"raw": fn.arguments}

                await self._emit("tool_call_start", {"tool_name": tool_name, "turn": turn})
                trajectory.append(TrajectoryEntry(
                    timestamp=time.time(), turn=turn, event_type="tool_call",
                    data={"tool_name": tool_name, "tool_input": _safe_serialize(tool_input)},
                ))

                call_start = time.time()
                tool_output = await self._dispatch_tool(tool_name, tool_input)
                duration = time.time() - call_start

                if tool_name == "task_complete":
                    task_completed = True

                all_tool_calls.append({
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "result_preview": tool_output[:500],
                    "duration": round(duration, 3),
                })

                await self._emit("tool_call_result", {
                    "tool_name": tool_name,
                    "duration": round(duration, 3),
                    "turn": turn,
                })
                trajectory.append(TrajectoryEntry(
                    timestamp=time.time(), turn=turn, event_type="tool_result",
                    data={
                        "tool_name": tool_name,
                        "output": tool_output[:5000],
                        "duration": round(duration, 3),
                    },
                ))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_output,
                })

            if task_completed:
                await self._emit("agent_done", {"text": "task_complete called", "turn": turn})
                return {
                    "final_text": "Task completed by agent",
                    "tool_calls": all_tool_calls,
                    "turns": turn + 1,
                    "tokens": {"prompt": total_prompt, "completion": total_completion},
                    "task_completed": True,
                    "trajectory": trajectory,
                }

        return {
            "final_text": "Max turns reached",
            "tool_calls": all_tool_calls,
            "turns": self._max_turns,
            "tokens": {"prompt": total_prompt, "completion": total_completion},
            "task_completed": task_completed,
            "trajectory": trajectory,
        }

    async def _dispatch_tool(self, name: str, input_data: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            return await tool.handler(input_data)
        except Exception as exc:
            logger.error("Tool '%s' failed: %s", name, exc)
            return json.dumps({"error": str(exc)})

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


# ---------------------------------------------------------------------------
# Anthropic-native agentic loop
# ---------------------------------------------------------------------------

class AnthropicAgenticLoop:
    """Thin wrapper around the existing AgenticLoop for Anthropic-native calls."""

    def __init__(
        self,
        api_key: str,
        model: str,
        tools: list[ToolSchema],
        *,
        max_turns: int = 200,
        max_tokens_per_turn: int = 16384,
        stream_callback: Any = None,
    ) -> None:
        from aurora.llm.tool_runner import AgenticLoop
        self._loop = AgenticLoop(
            api_key=api_key,
            model=model,
            tools=tools,
            max_turns=max_turns,
            max_tokens_per_turn=max_tokens_per_turn,
            stream_callback=stream_callback,
        )

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        result = await self._loop.run(system_prompt, user_message, temperature=temperature)
        return {
            "final_text": result.final_text,
            "tool_calls": result.tool_calls,
            "turns": result.turns,
            "tokens": result.token_usage,
            "decision": result.decision,
            "task_completed": result.decision == "done",
        }

    async def close(self) -> None:
        await self._loop.close()


# ---------------------------------------------------------------------------
# Claude Code driver
# ---------------------------------------------------------------------------

class ClaudeCodeLoop:
    """Agentic loop driven by Claude Code CLI via subprocess + stream-json.

    Launches ``claude -p`` with ``--input-format stream-json`` so that
    messages can be injected into the running session via stdin at any time
    (e.g. watchdog interrupts).  Claude Code brings its own tools (Bash,
    Read, Edit, Write, etc.) — we just give it the task prompt and let it
    work inside the Docker container via ``docker exec``.

    Supports OpenRouter via ANTHROPIC_BASE_URL pointing to
    ``https://openrouter.ai/api`` (Anthropic-compatible endpoint).
    """

    def __init__(
        self,
        *,
        model: str = "sonnet",
        api_key: str = "",
        base_url: str = "",
        max_turns: int = 200,
        docker_container: str | None = None,
        cwd: str | None = None,
        stream_callback: Any = None,
        resume_session_id: str | None = None,
        live_transcript_path: str | Path | None = None,
        text_callback: Any = None,
        allow_host_bash: bool = False,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._max_turns = max_turns
        self._docker_container = docker_container
        self._cwd = cwd or os.getcwd()
        self._stream = stream_callback
        self._process: asyncio.subprocess.Process | None = None
        self._resume_session_id = resume_session_id
        self.session_id: str | None = None
        self._live_transcript_path = Path(live_transcript_path) if live_transcript_path else None
        self._live_transcript_fd: Any = None
        self._text_callback = text_callback
        self._allow_host_bash = allow_host_bash

    def _permission_args(self) -> list[str]:
        """Confine the session to its working directory instead of skipping permissions.

        Files can be read and edited only under ``cwd``.  With a container,
        Bash is limited to ``docker exec`` into that container; without one,
        Bash is denied unless ``allow_host_bash`` is set.  Anything not allowed
        is refused without prompting (``dontAsk``).
        """
        root = str(Path(self._cwd).resolve())
        allowed = [
            f"Read(/{root}/**)",
            f"Edit(/{root}/**)",
            "WebSearch", "WebFetch", "TodoWrite", "Skill",
        ]
        if self._docker_container:
            c = self._docker_container
            allowed += [
                f"Bash(docker exec {c} *)",
                f"Bash(docker exec -i {c} *)",
                f"Bash(docker exec -w /app {c} *)",
                f"Bash(docker exec -i -w /app {c} *)",
            ]
        elif self._allow_host_bash:
            allowed.append("Bash")
        return ["--permission-mode", "dontAsk", "--allowedTools", *allowed]

    async def inject_message(self, message: str) -> None:
        """Inject a user message into the running agent's conversation via stdin."""
        if self._process and self._process.stdin and self._process.returncode is None:
            try:
                msg = json.dumps({
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": message}],
                    },
                })
                self._process.stdin.write((msg + "\n").encode())
                await self._process.stdin.drain()
            except Exception as e:
                logger.warning("Failed to inject message into agent: %s", e)

    def interrupt(self) -> None:
        """Send SIGINT to the Claude Code subprocess (equivalent to Ctrl+C).

        The process handles SIGINT gracefully — it stops the current
        tool call, emits a result event, and the session remains usable
        for subsequent ``inject_message`` calls.
        """
        if self._process and self._process.returncode is None:
            import signal
            try:
                self._process.send_signal(signal.SIGINT)
                logger.info("SIGINT sent to Claude Code process (pid=%d)", self._process.pid)
            except OSError as e:
                logger.warning("Failed to interrupt Claude Code process: %s", e)

    async def _emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self._stream is not None:
            event = StreamEvent(event_type=event_type, data=data or {})
            try:
                await self._stream(event)
            except Exception:
                pass

    def _write_live(self, entry: TrajectoryEntry) -> None:
        """Append a trajectory entry to the live transcript file."""
        if self._live_transcript_fd is not None:
            try:
                row = dataclasses.asdict(entry)
                self._live_transcript_fd.write(json.dumps(row, default=str) + "\n")
                self._live_transcript_fd.flush()
            except Exception:
                pass

    async def run(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        docker_preamble = ""
        if self._docker_container:
            docker_preamble = (
                f"\n## Docker Container\n"
                f"You are working inside Docker container `{self._docker_container}`.\n"
                f"ALL commands must be executed via:\n"
                f"```\n"
                f"docker exec -w /app {self._docker_container} bash -c '...'\n"
                f"```\n"
                f"To read files: `docker exec {self._docker_container} cat /app/path`\n"
                f"To write files: use `docker exec {self._docker_container} bash -c 'cat > /app/path << '\\''EOF'\\''\\n...\\nEOF'`\n"
                f"To list files: `docker exec {self._docker_container} ls -la /app/`\n"
                f"Never run commands directly on the host — always use docker exec.\n"
            )
        elif not self._allow_host_bash:
            docker_preamble = (
                f"\n## Sandbox\n"
                f"Shell commands are disabled in this session. You can read and edit "
                f"files only under `{Path(self._cwd).resolve()}`.\n"
            )

        full_system = system_prompt + docker_preamble

        env = os.environ.copy()
        if self._base_url and "openrouter.ai" in self._base_url:
            env["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
        if self._api_key:
            env["ANTHROPIC_AUTH_TOKEN"] = self._api_key
            env["ANTHROPIC_API_KEY"] = ""

        cmd = [
            "claude", "-p",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            *self._permission_args(),
            "--model", self._model,
        ]

        if self._resume_session_id:
            cmd.extend(["--resume", self._resume_session_id])
        else:
            cmd.extend(["--append-system-prompt", full_system])

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self._cwd,
            limit=1024 * 1024 * 1024,  # 1GB buffer — never be the bottleneck
        )

        if self._resume_session_id:
            await self.inject_message(
                f"You are resuming from a previous session. The Docker container has been "
                f"restored with your previous workspace. Continue working on the task from "
                f"where you left off.\n\n{user_message}"
            )
        else:
            await self.inject_message(user_message)

        await self._emit("loop_started", {"model": self._model, "driver": "claude-code"})

        turns = 0
        final_text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        trajectory: list[TrajectoryEntry] = []
        pending_tool_name: str | None = None
        pending_tool_start: float = 0.0

        if self._live_transcript_path:
            self._live_transcript_path.parent.mkdir(parents=True, exist_ok=True)
            self._live_transcript_fd = open(self._live_transcript_path, "a")

        def _track(entry: TrajectoryEntry) -> None:
            """Append to in-memory list and live transcript file."""
            trajectory.append(entry)
            self._write_live(entry)

        async def _drain_stderr() -> None:
            """Read stderr in background and emit as stream events."""
            assert self._process and self._process.stderr
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if text:
                    logger.warning("[claude-code stderr] %s", text)
                    await self._emit("agent_stderr", {"text": text, "turn": turns})

        stderr_task = asyncio.create_task(_drain_stderr())

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                text = line.decode().strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")

                if event_type == "system":
                    sid = event.get("session_id")
                    if sid:
                        self.session_id = sid
                    continue

                if event_type == "assistant":
                    sid = event.get("session_id")
                    if sid and not self.session_id:
                        self.session_id = sid
                    msg = event.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type")
                            if btype == "text":
                                t = block.get("text", "")
                                final_text_parts.append(t)
                                await self._emit("reasoning", {"text": t[:500], "turn": turns})
                                _track(TrajectoryEntry(
                                    timestamp=time.time(), turn=turns,
                                    event_type="thinking", data={"text": t},
                                ))
                                if self._text_callback:
                                    try:
                                        await self._text_callback(t)
                                    except Exception:
                                        pass
                            elif btype == "tool_use":
                                tool_name = block.get("name", "")
                                tool_input = block.get("input", {})
                                pending_tool_start = time.time()
                                input_preview = ""
                                if tool_name == "Bash" or tool_name == "bash":
                                    input_preview = (tool_input.get("command") or "")[:200]
                                elif tool_name in ("Read", "read"):
                                    input_preview = tool_input.get("file_path") or tool_input.get("path", "")
                                elif tool_name in ("Edit", "edit"):
                                    input_preview = tool_input.get("file_path") or tool_input.get("path", "")
                                elif tool_name in ("Write", "write"):
                                    input_preview = tool_input.get("file_path") or tool_input.get("path", "")
                                await self._emit("tool_call_start", {
                                    "tool_name": tool_name,
                                    "turn": turns,
                                    "input_preview": input_preview,
                                })
                                tool_calls.append({
                                    "tool_name": tool_name,
                                    "tool_input": tool_input,
                                })
                                _track(TrajectoryEntry(
                                    timestamp=time.time(), turn=turns,
                                    event_type="tool_call",
                                    data={"tool_name": tool_name, "tool_input": _safe_serialize(tool_input)},
                                ))
                                pending_tool_name = tool_name
                            elif btype == "tool_result":
                                result_content = block.get("content", "")
                                is_error = block.get("is_error", False)
                                if isinstance(result_content, list):
                                    result_text = " ".join(
                                        b.get("text", str(b)) if isinstance(b, dict) else str(b)
                                        for b in result_content
                                    )
                                else:
                                    result_text = str(result_content)
                                duration = round(time.time() - pending_tool_start, 2) if pending_tool_start else 0
                                await self._emit("tool_call_result", {
                                    "tool_name": pending_tool_name or "tool",
                                    "success": not is_error,
                                    "duration_seconds": duration,
                                    "turn": turns,
                                    "result_preview": result_text[:300],
                                })
                                _track(TrajectoryEntry(
                                    timestamp=time.time(), turn=turns,
                                    event_type="tool_result",
                                    data={
                                        "tool_name": pending_tool_name or "tool",
                                        "output": result_text[:5000],
                                        "is_error": is_error,
                                        "duration": duration,
                                    },
                                ))
                                pending_tool_name = None
                                pending_tool_start = 0.0
                    turns += 1

                elif event_type == "result":
                    result_text = event.get("result", "")
                    usage = event.get("usage") or {}
                    logger.info(
                        "[claude-code result] subtype=%s stop_reason=%s is_error=%s num_turns=%s "
                        "empty_result=%s input=%s cache_read=%s output=%s",
                        event.get("subtype"), event.get("stop_reason"), event.get("is_error"),
                        event.get("num_turns"), not result_text.strip(),
                        usage.get("input_tokens"), usage.get("cache_read_input_tokens"),
                        usage.get("output_tokens"),
                    )
                    final_text_parts.append(result_text)
                    _track(TrajectoryEntry(
                        timestamp=time.time(), turn=turns,
                        event_type="text", data={"text": result_text},
                    ))
                    if self._text_callback and result_text:
                        try:
                            await self._text_callback(result_text)
                        except Exception:
                            pass
                    break

        except Exception as exc:
            logger.error("Claude Code subprocess failed: %s", exc)
            _track(TrajectoryEntry(
                timestamp=time.time(), turn=turns,
                event_type="error", data={"error": str(exc)},
            ))
            return {
                "final_text": f"Claude Code error: {exc}",
                "tool_calls": tool_calls,
                "turns": turns,
                "tokens": {"prompt": 0, "completion": 0},
                "task_completed": False,
                "error": str(exc),
                "trajectory": trajectory,
            }
        finally:
            stderr_task.cancel()
            if self._live_transcript_fd:
                self._live_transcript_fd.close()
                self._live_transcript_fd = None

        await self._emit("agent_done", {"text": "Claude Code finished", "turn": turns})

        return {
            "final_text": "\n".join(final_text_parts),
            "tool_calls": tool_calls,
            "turns": turns,
            "tokens": {"prompt": 0, "completion": 0},
            "task_completed": True,
            "trajectory": trajectory,
        }

    async def close(self) -> None:
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

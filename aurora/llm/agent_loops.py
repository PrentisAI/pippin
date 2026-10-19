"""Claude Code as the agent driver.

``ClaudeCodeLoop`` runs the Claude Code CLI as a subprocess with
``--input-format stream-json``, so messages can be injected while it works
(duplex interaction, supervisor advice) and SIGINT interrupts the current tool
call.  Every phase agent, the milestone auditor and the research agent are
Claude Code sessions driven by this class.
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

from aurora.llm.stream import StreamEvent

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

def claude_code_env(api_key: str, base_url: str) -> dict[str, str]:
    """Environment for a Claude Code subprocess that talks to *base_url* with *api_key*.

    *base_url* is the Anthropic-compatible endpoint (for OpenRouter
    ``https://openrouter.ai/api``).  Only variables Claude Code needs are
    passed on, so other credentials in the parent environment do not leak
    into agent sessions.
    """
    keep = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR", "SHELL",
            "DOCKER_HOST", "DOCKER_CONFIG", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    if api_key:
        env["ANTHROPIC_AUTH_TOKEN"] = api_key
        env["ANTHROPIC_API_KEY"] = ""
    return env


def permission_args(cwd: str | Path, docker_container: str | None = None,
                    allow_host_bash: bool = False) -> list[str]:
    """Claude Code flags that confine a session to its working directory.

    Files can be read and edited only under *cwd*.  With a container, Bash is
    limited to ``docker exec`` into that container; without one, Bash is denied
    unless *allow_host_bash* is set.  Anything else is refused without
    prompting (``dontAsk``).
    """
    root = str(Path(cwd).resolve())
    allowed = [
        f"Read(/{root}/**)",
        f"Edit(/{root}/**)",
        "WebSearch", "WebFetch", "TodoWrite", "Skill",
    ]
    if docker_container:
        c = docker_container
        # any docker exec options (-i, -w, -e, -d, ...) but only into this container
        allowed += [f"Bash(docker exec {c} *)", f"Bash(docker exec * {c} *)"]
    elif allow_host_bash:
        allowed.append("Bash")
    return ["--permission-mode", "dontAsk", "--allowedTools", *allowed]


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
        max_budget_usd: float | None = None,
        session_callback: Any = None,
    ) -> None:
        self._session_callback = session_callback
        self._max_budget_usd = max_budget_usd
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
        return permission_args(self._cwd, self._docker_container, self._allow_host_bash)

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
        root = Path(self._cwd).resolve()
        if self._docker_container:
            c = self._docker_container
            docker_preamble = (
                f"\n## Where you work\n"
                f"Run every command inside Docker container `{c}`, working directory /app:\n"
                f"  docker exec -w /app {c} bash -lc '<command>'\n"
                f"Options such as -i, -e KEY=VALUE or -d are fine. Put pipes, redirects and multi-step "
                f"commands inside the bash -lc '...' string. There is no shell on the host.\n"
                f"You can read and edit files on the host only under {root} (with the Read/Edit/Write "
                f"tools). The container does not see that directory. To move a file into the "
                f"container: docker exec -i {c} bash -lc 'cat > /app/x.py' < relative/path/x.py (or a "
                f"heredoc). Keep your code, data and results in /app.\n"
            )
        elif self._allow_host_bash:
            docker_preamble = f"\n## Where you work\nYour working directory is {root}.\n"
        else:
            docker_preamble = (
                f"\n## Where you work\n"
                f"Shell commands are disabled in this session. You can read and edit files only "
                f"under {root}.\n"
            )

        full_system = system_prompt + docker_preamble

        env = claude_code_env(self._api_key, self._base_url)

        cmd = [
            "claude", "-p",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            *self._permission_args(),
            "--model", self._model,
        ]
        if self._max_budget_usd is not None:
            cmd += ["--max-budget-usd", f"{max(self._max_budget_usd, 0.01):.2f}"]

        if self._resume_session_id:
            cmd.extend(["--resume", self._resume_session_id])
        # also on resume: the container and the rules may have changed since
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

        await self.inject_message(user_message)

        await self._emit("loop_started", {"model": self._model, "driver": "claude-code"})

        turns = 0
        cost_usd = 0.0
        result_error: str | None = None
        hit_turn_limit = False
        got_result = False
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

                if event_type == "system" and event.get("subtype") != "permission_denied":
                    sid = event.get("session_id")
                    if sid and sid != self.session_id:
                        self.session_id = sid
                        if self._session_callback:
                            try:
                                self._session_callback(sid)
                            except Exception:
                                logger.debug("session callback failed", exc_info=True)
                    continue

                if event_type == "system" and event.get("subtype") == "permission_denied":
                    await self._emit("permission_denied", {"turn": turns, "detail": _safe_serialize(event)})
                    _track(TrajectoryEntry(
                        timestamp=time.time(), turn=turns,
                        event_type="permission_denied", data=_safe_serialize(event),
                    ))
                    continue

                if event_type in ("assistant", "user"):
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
                            if btype == "text" and event_type == "assistant":
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
                    if event_type == "assistant":
                        turns += 1
                        if turns == self._max_turns:
                            await self.inject_message(
                                "[Turn limit reached] Stop working now. Save what you have and reply "
                                "with a short summary of what is done and what is left.")
                        elif turns >= self._max_turns + 10:
                            logger.warning("Claude Code session exceeded its turn limit; stopping it")
                            hit_turn_limit = True
                            break

                elif event_type == "result":
                    result_text = event.get("result", "")
                    usage = event.get("usage") or {}
                    cost_usd = float(event.get("total_cost_usd") or 0.0)
                    if event.get("is_error"):
                        result_error = f"{event.get('subtype', 'error')}: {result_text[:500]}"
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
                    got_result = True
                    break

        except asyncio.CancelledError:
            # stopped from outside: close() must end the process now, not let it finish its turn
            self._cancelled = True
            raise
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
                "cost_usd": cost_usd,
                "task_completed": False,
                "error": str(exc),
                "trajectory": trajectory,
            }
        finally:
            stderr_task.cancel()
            if self._live_transcript_fd:
                self._live_transcript_fd.close()
                self._live_transcript_fd = None

        if not got_result and not result_error:
            result_error = ("turn limit exceeded" if hit_turn_limit
                            else f"Claude Code exited without a result (exit code {self._process.returncode})")
        await self._emit("agent_done", {"text": "Claude Code finished", "turn": turns,
                                        "cost_usd": round(cost_usd, 4), "error": result_error})

        return {
            "final_text": "\n".join(final_text_parts),
            "tool_calls": tool_calls,
            "turns": turns,
            "cost_usd": cost_usd,
            "task_completed": result_error is None,
            "error": result_error,
            "trajectory": trajectory,
        }

    async def close(self) -> None:
        if self._process and self._process.returncode is None:
            if getattr(self, "_cancelled", False):
                self.interrupt()
            if self._process.stdin:
                self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5 if getattr(self, "_cancelled", False) else 30)
            except TimeoutError:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=10)
                except TimeoutError:
                    self._process.kill()

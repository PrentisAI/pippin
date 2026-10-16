"""LHTB task runner — executes benchmark tasks using AURORA's agentic loop.

Supports both Anthropic and OpenAI-compatible APIs (including OpenRouter).
Runs tasks inside Docker containers pulled from the LHTB task image, with
an optional continue-until-timeout verification loop.

Usage
-----
::

    python -m aurora.lhtb.runner \\
        --task-dir ./tasks/materials-phase-diagram-audit \\
        --model deepseek/deepseek-v4-flash \\
        --api-key "$OPENROUTER_API_KEY"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import dataclasses
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aurora.lhtb.task_loader import LHTBTask, load_task
from aurora.llm.tool_runner import ToolSchema, StreamEvent

# Agent loop drivers live in llm.agent_loops — re-export for backward compat
from aurora.llm.agent_loops import (  # noqa: F401
    ClaudeCodeLoop,
    OpenAIAgenticLoop,
    AnthropicAgenticLoop,
    TrajectoryEntry,
    _safe_serialize,
)

logger = logging.getLogger(__name__)


@dataclass
class PhaseResult:
    """Result of a single agent phase (one attempt before verification)."""
    phase: int = 0
    turns: int = 0
    tokens: int = 0
    wall_time_seconds: float = 0.0
    verifier_passed: bool = False
    verifier_reward: float = 0.0
    verifier_output: str = ""
    trajectory: list[TrajectoryEntry] = field(default_factory=list)


@dataclass
class LHTBResult:
    """Result of an LHTB task run."""
    task_name: str = ""
    status: str = "incomplete"  # completed | partial | incomplete | error | timeout
    artifacts_found: list[str] = field(default_factory=list)
    artifacts_missing: list[str] = field(default_factory=list)
    phases: list[PhaseResult] = field(default_factory=list)
    total_turns: int = 0
    total_tokens: int = 0
    wall_time_seconds: float = 0.0
    final_reward: float = 0.0
    error: str | None = None


# Agent loop classes (OpenAIAgenticLoop, AnthropicAgenticLoop, ClaudeCodeLoop)
# have been extracted to aurora.llm.agent_loops and re-exported above.


class DockerEnvironment:
    """Manages a Docker container for LHTB task execution."""

    def __init__(self, task: LHTBTask) -> None:
        self._task = task
        self._container_id: str | None = None
        self._tests_dir: Path | None = task.task_dir / "tests" if (task.task_dir / "tests").is_dir() else None

    @property
    def container_id(self) -> str | None:
        return self._container_id

    def start(self) -> str:
        """Start the task container and return its ID."""
        if not self._task.docker_image:
            raise ValueError("No docker_image specified in task.toml")

        logger.info("Pulling image %s (may take a while)...", self._task.docker_image)
        pull = subprocess.run(
            ["docker", "pull", self._task.docker_image],
            capture_output=True, text=True, timeout=self._task.build_timeout_sec,
        )
        if pull.returncode != 0:
            logger.warning("Docker pull warning: %s", pull.stderr.strip())

        cmd = [
            "docker", "run", "-d",
            "--cpus", str(self._task.cpus),
            "--memory", f"{self._task.memory_mb}m",
        ]
        if self._task.allow_internet:
            cmd.extend(["--network", "bridge"])
        else:
            cmd.extend(["--network", "none"])

        if self._tests_dir:
            cmd.extend(["-v", f"{self._tests_dir.resolve()}:/tests:ro"])

        cmd.extend([self._task.docker_image, "sleep", "infinity"])

        logger.info("Starting container: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=self._task.build_timeout_sec,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Docker run failed: {result.stderr}")

        self._container_id = result.stdout.strip()[:12]
        logger.info("Container started: %s", self._container_id)
        return self._container_id

    def exec(self, command: str, *, timeout: float = 120.0, workdir: str = "/app") -> tuple[int, str, str]:
        """Run a command inside the container."""
        if not self._container_id:
            raise RuntimeError("Container not started")
        result = subprocess.run(
            ["docker", "exec", "-w", workdir, self._container_id, "bash", "-c", command],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr

    def run_verifier(self, *, timeout: float = 600.0) -> tuple[bool, float, str]:
        """Run the task's verifier inside the container.

        LHTB verifiers use ``/tests/test.sh`` which runs ``grade.py`` and
        writes a numeric reward to ``/logs/verifier/reward.txt``.  Falls back
        to a pytest-based approach when ``test.sh`` is absent.

        Returns (passed, reward, output).
        """
        if not self._container_id:
            raise RuntimeError("Container not started")

        # Prefer LHTB-style test.sh → grade.py → reward.txt
        probe_code, probe_out, _ = self.exec("test -f /tests/test.sh && echo yes || echo no")
        has_test_sh = "yes" in probe_out

        if has_test_sh:
            code, stdout, stderr = self.exec(
                "bash /tests/test.sh 2>&1 || true",
                timeout=timeout,
            )
            output = stdout + stderr

            # Read reward from /logs/verifier/reward.txt
            _, reward_str, _ = self.exec("cat /logs/verifier/reward.txt 2>/dev/null || echo 0")
            try:
                reward = float(reward_str.strip())
            except (ValueError, TypeError):
                reward = 0.0

            # Append grade log if available
            _, grade_log, _ = self.exec("cat /logs/verifier/grade.log 2>/dev/null || true")
            if grade_log.strip():
                output += "\n--- grade.log ---\n" + grade_log

            passed = reward >= 0.95
            return passed, reward, output

        # Fallback: pytest-based verifier
        code, stdout, stderr = self.exec(
            "cd /app && python -m pytest tests/ -v --tb=short 2>&1 || true",
            timeout=timeout,
        )

        output = stdout + stderr
        passed_count = 0
        failed_count = 0

        for line in output.splitlines():
            if " passed" in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "passed" and i > 0:
                        try:
                            passed_count = int(parts[i - 1])
                        except ValueError:
                            pass
                    if p == "failed" and i > 0:
                        try:
                            failed_count = int(parts[i - 1])
                        except ValueError:
                            pass

        total_count = passed_count + failed_count
        reward = passed_count / total_count if total_count > 0 else 0.0
        all_passed = failed_count == 0 and passed_count > 0

        return all_passed, reward, output

    def extract_workspace(self, dest: Path, src: str = "/app") -> bool:
        """Copy the container workspace to a local directory before teardown."""
        if not self._container_id:
            return False
        dest.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["docker", "cp", f"{self._container_id}:{src}/.", str(dest)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.warning("docker cp failed: %s", result.stderr.strip())
                return False
            logger.info("Workspace extracted to %s", dest)
            return True
        except Exception as exc:
            logger.warning("Failed to extract workspace: %s", exc)
            return False

    def stop(self) -> None:
        if self._container_id:
            subprocess.run(
                ["docker", "stop", "-t", "5", self._container_id],
                capture_output=True, timeout=30,
            )
            subprocess.run(
                ["docker", "rm", "-f", self._container_id],
                capture_output=True, timeout=30,
            )
            logger.info("Container stopped: %s", self._container_id)
            self._container_id = None


# ---------------------------------------------------------------------------
# LHTB Runner
# ---------------------------------------------------------------------------

class LHTBRunner:
    """Runs a single LHTB task using AURORA's agentic loop.

    When ``use_docker=True`` (default if the task has a docker_image):
      1. Pulls and starts the task's Docker container
      2. Runs the agentic loop with commands executed via ``docker exec``
      3. When the agent calls ``task_complete``, runs the pytest verifier
      4. If ``continue_until_timeout`` and reward < 1.0, tells the agent
         "verification failed" and continues (mimicking Harbor's loop)
      5. Repeats until timeout or full pass
    """

    def __init__(
        self,
        task: LHTBTask,
        *,
        model: str = "deepseek/deepseek-v4-flash",
        api_key: str = "",
        base_url: str = "https://openrouter.ai/api/v1",
        driver: str = "auto",  # "auto" | "claude-code" | "openai"
        use_docker: bool | None = None,
        working_dir: str | None = None,
        max_turns: int = 200,
        timeout_sec: float | None = None,
        stream_callback: Any = None,
        workspace_dir: str | Path | None = None,
        allow_host_bash: bool = False,
    ) -> None:
        self._task = task
        self._allow_host_bash = allow_host_bash
        self._model = model
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url
        self._driver = driver
        self._use_docker = use_docker if use_docker is not None else bool(task.docker_image)
        self._working_dir = working_dir or str(task.task_dir / "environment")
        self._max_turns = max_turns
        self._timeout_sec = timeout_sec or task.agent_timeout_sec
        self._stream = stream_callback
        self._docker_env: DockerEnvironment | None = None
        self._workspace_dir = Path(workspace_dir) if workspace_dir else None
        self._resume_session_id: str | None = None
        self.session_id: str | None = None
        self._current_loop: Any = None

    async def inject_message(self, message: str) -> None:
        """Inject a message into the currently running agent loop."""
        if self._current_loop and hasattr(self._current_loop, "inject_message"):
            await self._current_loop.inject_message(message)

    def _build_tools(self) -> list[ToolSchema]:
        """Build the tool set for the LHTB task."""
        from aurora.tools.shell import build_shell_tool
        from aurora.tools.file_ops import build_all_file_tools

        docker_id = self._docker_env.container_id if self._docker_env else None
        work_dir = "/app" if docker_id else self._working_dir

        tools = [
            build_shell_tool(work_dir, docker_container=docker_id, timeout=120.0),
        ]

        if docker_id:
            # For Docker mode, file tools also go through docker exec
            # but shell tool handles this — file tools work on the host.
            # Instead, agent should use shell tool for file operations in container.
            # Add a convenience read_file that works via docker exec.
            tools.extend(_build_docker_file_tools(docker_id, work_dir))
        else:
            tools.extend(build_all_file_tools(work_dir))

        # task_complete signal
        self._task_complete_called = False

        async def task_complete_handler(params: dict[str, Any]) -> str:
            self._task_complete_called = True
            return json.dumps({
                "acknowledged": True,
                "message": "Task marked as complete. Running verification...",
            })

        tools.append(ToolSchema(
            name="task_complete",
            description=(
                "Signal that you have finished working on the task. "
                "Call this when all required output files have been produced "
                "and you believe the solution is correct. "
                "The verifier will then check your outputs."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what was done",
                    },
                },
            },
            handler=task_complete_handler,
        ))

        return tools

    def _build_system_prompt(self, phase: int = 0, prev_feedback: str = "") -> str:
        """Build the system prompt for the LHTB task agent."""
        artifacts_list = "\n".join(
            f"  - {a}" for a in self._task.artifacts
        ) if self._task.artifacts else "  (none specified)"

        phase_info = ""
        if phase > 0:
            phase_info = f"""
## Previous Attempt Feedback
This is attempt #{phase + 1}. Your previous submission was verified and **did NOT fully pass**.
{prev_feedback}
Re-examine your work, identify what's still wrong, and fix it.
"""

        return f"""You are an expert software engineer and scientist solving a benchmark task inside a Docker container.

## Task Information
- **Name**: {self._task.name}
- **Category**: {self._task.category}
- **Difficulty**: {self._task.difficulty}
- **Keywords**: {', '.join(self._task.keywords or self._task.tags)}

## Required Output Artifacts
{artifacts_list}
{phase_info}
## Your Approach

You are powered by AURORA, a structured research harness. Follow this methodology:

1. **Understand**: Read the task instruction carefully. Explore the codebase to understand existing code.
2. **Plan**: Break the task into subtasks. Identify the bugs described in the instruction.
3. **Execute**: Fix bugs one at a time. After each fix, run the relevant script to verify.
4. **Self-check**: Before calling task_complete, run the main CLI/script to generate ALL required output artifacts and verify they exist and contain valid data.

## Guidelines

- Read files before editing them.
- Run scripts after changes to verify correctness.
- Diagnose errors before retrying — don't repeat the same failed approach.
- Do NOT hardcode expected test outputs or fixture data.
- Work methodically — fix one bug at a time and verify.
- When all outputs look correct, call task_complete.

## Available Tools

- **shell**: Run any bash command in the container
- **read_file**: Read file contents
- **write_file**: Create or overwrite a file
- **edit_file**: Search-and-replace edit
- **list_files**: List directory contents
- **search_files**: Grep for patterns
- **task_complete**: Signal you're done (triggers verification)
"""

    async def run(self) -> LHTBResult:
        """Execute the LHTB task with optional verification loop."""
        start_time = time.time()
        result = LHTBResult(task_name=self._task.name)
        self._task_complete_called = False
        # Run-level trajectory for events outside any phase (docker, metadata)
        self._run_trajectory: list[TrajectoryEntry] = []

        self._run_trajectory.append(TrajectoryEntry(
            timestamp=time.time(), phase=-1, turn=0,
            event_type="run_start",
            data={
                "task_name": self._task.name,
                "model": self._model,
                "driver": self._driver,
                "docker_image": self._task.docker_image or "",
                "timeout_sec": self._timeout_sec,
                "max_turns": self._max_turns,
                "continue_until_timeout": self._task.continue_until_timeout,
                "artifacts": self._task.artifacts,
            },
        ))

        # Start Docker container
        if self._use_docker:
            self._docker_env = DockerEnvironment(self._task)
            try:
                self._docker_env.start()
                await self._emit_stream("docker_started", {
                    "container_id": self._docker_env.container_id,
                    "image": self._task.docker_image,
                })
                self._run_trajectory.append(TrajectoryEntry(
                    timestamp=time.time(), phase=-1, turn=0,
                    event_type="docker_started",
                    data={
                        "container_id": self._docker_env.container_id,
                        "image": self._task.docker_image,
                    },
                ))
            except Exception as exc:
                result.status = "error"
                result.error = f"Docker setup failed: {exc}"
                return result

        try:
            phase = 0
            prev_feedback = ""
            best_reward = 0.0

            while True:
                elapsed = time.time() - start_time
                remaining = self._timeout_sec - elapsed

                if remaining <= 30:
                    logger.info("Timeout reached after %d phases", phase)
                    result.status = "timeout"
                    break

                turns_this_phase = min(
                    self._max_turns,
                    int(remaining / 5),  # rough estimate: ~5s per turn
                )
                if turns_this_phase < 5:
                    result.status = "timeout"
                    break

                await self._emit_stream("phase_started", {
                    "phase": phase,
                    "elapsed": round(elapsed),
                    "remaining": round(remaining),
                })

                phase_start = time.time()
                phase_result = await self._run_phase(
                    phase=phase,
                    prev_feedback=prev_feedback,
                    max_turns=turns_this_phase,
                )
                phase_result.phase = phase
                phase_result.wall_time_seconds = time.time() - phase_start

                # Append phase boundary event
                phase_result.trajectory.append(TrajectoryEntry(
                    timestamp=time.time(), phase=phase, turn=phase_result.turns,
                    event_type="phase_ended",
                    data={
                        "phase": phase,
                        "turns": phase_result.turns,
                        "wall_time": round(phase_result.wall_time_seconds, 2),
                    },
                ))

                # Run verifier if Docker is available and agent called task_complete
                if self._docker_env and self._task_complete_called:
                    await self._emit_stream("verifier_started", {"phase": phase})
                    try:
                        passed, reward, v_output = self._docker_env.run_verifier(
                            timeout=self._task.verifier_timeout_sec,
                        )
                        phase_result.verifier_passed = passed
                        phase_result.verifier_reward = reward
                        phase_result.verifier_output = v_output[-2000:]

                        await self._emit_stream("verifier_result", {
                            "phase": phase,
                            "passed": passed,
                            "reward": round(reward, 4),
                        })

                        phase_result.trajectory.append(TrajectoryEntry(
                            timestamp=time.time(), phase=phase,
                            turn=phase_result.turns,
                            event_type="verifier_result",
                            data={
                                "passed": passed,
                                "reward": round(reward, 4),
                                "output": v_output[-3000:],
                            },
                        ))

                        if reward > best_reward:
                            best_reward = reward

                    except Exception as exc:
                        logger.warning("Verifier failed: %s", exc)
                        phase_result.verifier_output = str(exc)
                        phase_result.trajectory.append(TrajectoryEntry(
                            timestamp=time.time(), phase=phase,
                            turn=phase_result.turns,
                            event_type="verifier_error",
                            data={"error": str(exc)},
                        ))

                result.phases.append(phase_result)
                result.total_turns += phase_result.turns
                result.total_tokens += phase_result.tokens

                # Check if we're done
                if phase_result.verifier_passed:
                    result.status = "completed"
                    result.final_reward = 1.0
                    break

                if not self._task.continue_until_timeout:
                    # Single-shot mode — don't loop
                    result.final_reward = best_reward
                    result.status = "partial" if best_reward > 0 else "incomplete"
                    break

                # Continue-until-timeout: prepare feedback for next phase
                self._task_complete_called = False
                phase += 1
                prev_feedback = (
                    f"Verifier reward: {phase_result.verifier_reward:.2%}. "
                    f"Your solution did not fully pass. "
                    f"Review your outputs and fix the remaining issues."
                )
                # Binary feedback only (like LHTB) — don't leak verifier details

        except Exception as exc:
            logger.exception("Task execution failed")
            result.status = "error"
            result.error = str(exc)

        finally:
            result.wall_time_seconds = time.time() - start_time
            result.final_reward = max(
                result.final_reward,
                max((p.verifier_reward for p in result.phases), default=0.0),
            )

            # Check artifacts and extract workspace
            if self._docker_env:
                for artifact in self._task.artifacts:
                    code, _, _ = self._docker_env.exec(f"test -f /app/{artifact} && echo exists")
                    if code == 0:
                        result.artifacts_found.append(artifact)
                    else:
                        result.artifacts_missing.append(artifact)
                if self._workspace_dir:
                    self._docker_env.extract_workspace(self._workspace_dir)
                    await self._emit_stream("workspace_extracted", {
                        "path": str(self._workspace_dir),
                        "artifacts_found": result.artifacts_found,
                    })
                self._docker_env.stop()
            else:
                work_dir = Path(self._working_dir)
                for artifact in self._task.artifacts:
                    if (work_dir / artifact).exists():
                        result.artifacts_found.append(artifact)
                    else:
                        result.artifacts_missing.append(artifact)

        return result

    def _resolve_driver(self) -> str:
        """Resolve 'auto' driver to a concrete driver name."""
        if self._driver != "auto":
            return self._driver
        if self._is_anthropic_native():
            return "anthropic"
        return "claude-code"

    async def _run_phase(
        self,
        phase: int,
        prev_feedback: str,
        max_turns: int,
    ) -> PhaseResult:
        """Run a single agent phase."""
        system_prompt = self._build_system_prompt(phase=phase, prev_feedback=prev_feedback)

        if phase == 0:
            user_message = (
                f"Here is your task instruction:\n\n"
                f"---\n{self._task.instruction}\n---\n\n"
                f"Begin by exploring the task environment (list files, read the main source code). "
                f"Then systematically fix the issues described in the instruction."
            )
        else:
            user_message = (
                f"Your previous attempt did not fully pass verification. "
                f"{prev_feedback}\n\n"
                f"Continue working on the task. Review what you did before, "
                f"identify remaining issues, and fix them."
            )

        driver = self._resolve_driver()

        if driver == "claude-code":
            docker_id = self._docker_env.container_id if self._docker_env else None
            cc_base_url = ""
            if self._base_url and "openrouter.ai" in self._base_url:
                cc_base_url = "https://openrouter.ai/api"
            loop = ClaudeCodeLoop(
                model=self._model,
                api_key=self._api_key,
                base_url=cc_base_url,
                max_turns=max_turns,
                docker_container=docker_id,
                cwd=self._working_dir,
                stream_callback=self._stream,
                resume_session_id=self._resume_session_id,
                allow_host_bash=self._allow_host_bash,
            )
        elif driver == "anthropic":
            tools = self._build_tools()
            loop = AnthropicAgenticLoop(
                api_key=self._api_key,
                model=self._model,
                tools=tools,
                max_turns=max_turns,
                stream_callback=self._stream,
            )
        else:
            tools = self._build_tools()
            loop = OpenAIAgenticLoop(
                api_key=self._api_key,
                model=self._model,
                tools=tools,
                base_url=self._base_url,
                max_turns=max_turns,
                stream_callback=self._stream,
            )

        self._current_loop = loop
        try:
            loop_result = await loop.run(system_prompt, user_message, temperature=0.3)
        finally:
            if hasattr(loop, 'session_id') and loop.session_id:
                self.session_id = loop.session_id
            self._current_loop = None
            await loop.close()

        tokens = loop_result.get("tokens", {})
        total_tok = (
            tokens.get("prompt", 0) + tokens.get("completion", 0)
            if isinstance(tokens, dict)
            else tokens.get("total_tokens", 0)
        )

        trajectory = loop_result.get("trajectory", [])

        # Prepend system prompt and user message so the JSONL is self-contained
        trajectory.insert(0, TrajectoryEntry(
            timestamp=time.time(),
            phase=phase,
            turn=0,
            event_type="user_message",
            data={"message": user_message},
        ))
        trajectory.insert(0, TrajectoryEntry(
            timestamp=time.time(),
            phase=phase,
            turn=0,
            event_type="system_prompt",
            data={"prompt": system_prompt},
        ))

        # Set phase number on all entries
        for entry in trajectory:
            entry.phase = phase

        return PhaseResult(
            turns=loop_result.get("turns", 0),
            tokens=total_tok,
            trajectory=trajectory,
        )

    async def _emit_stream(self, event_type: str, data: dict[str, Any]) -> None:
        if self._stream is not None:
            try:
                await self._stream(StreamEvent(event_type=event_type, data=data))
            except Exception:
                pass

    def _is_anthropic_native(self) -> bool:
        return (
            not self._base_url
            or "anthropic.com" in self._base_url
            or "api.anthropic.com" in self._base_url
        )


# ---------------------------------------------------------------------------
# Docker file tools
# ---------------------------------------------------------------------------

def _build_docker_file_tools(container_id: str, workdir: str) -> list[ToolSchema]:
    """Build file tools that operate inside a Docker container via docker exec."""

    async def _docker_exec(command: str, timeout: float = 30.0) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-w", workdir, container_id, "bash", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        return proc.returncode, output

    async def read_handler(params: dict[str, Any]) -> str:
        path = params.get("path", "")
        if not path:
            return json.dumps({"error": "No path specified"})
        code, output = await _docker_exec(f"cat '{path}'")
        if code != 0:
            return json.dumps({"error": f"Cannot read: {output.strip()}"})
        if len(output) > 10000:
            output = output[:10000] + f"\n... (truncated)"
        return json.dumps({"content": output, "path": path})

    async def write_handler(params: dict[str, Any]) -> str:
        path = params.get("path", "")
        content = params.get("content", "")
        if not path:
            return json.dumps({"error": "No path specified"})
        # Write via heredoc
        escaped = content.replace("\\", "\\\\").replace("'", "'\\''")
        code, output = await _docker_exec(
            f"mkdir -p \"$(dirname '{path}')\" && cat > '{path}' << 'AURORA_EOF'\n{content}\nAURORA_EOF",
            timeout=30.0,
        )
        if code != 0:
            return json.dumps({"error": f"Write failed: {output.strip()}"})
        return json.dumps({"success": True, "path": path})

    async def edit_handler(params: dict[str, Any]) -> str:
        path = params.get("path", "")
        old = params.get("old_string", "")
        new = params.get("new_string", "")
        if not path or not old:
            return json.dumps({"error": "path and old_string required"})
        # Read, replace, write back
        code, content = await _docker_exec(f"cat '{path}'")
        if code != 0:
            return json.dumps({"error": f"Cannot read: {content.strip()}"})
        count = content.count(old)
        if count == 0:
            return json.dumps({"error": "old_string not found in file"})
        replace_all = params.get("replace_all", False)
        if not replace_all and count > 1:
            return json.dumps({"error": f"old_string found {count} times. Use replace_all=true or add more context."})
        new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        # Write back using python to handle special characters safely
        import base64
        b64 = base64.b64encode(new_content.encode()).decode()
        code2, out2 = await _docker_exec(
            f"echo '{b64}' | base64 -d > '{path}'",
            timeout=30.0,
        )
        if code2 != 0:
            return json.dumps({"error": f"Write-back failed: {out2.strip()}"})
        return json.dumps({"success": True, "replacements": count if replace_all else 1})

    async def list_handler(params: dict[str, Any]) -> str:
        path = params.get("path", ".")
        recursive = params.get("recursive", False)
        cmd = f"find '{path}' -maxdepth 1 -printf '%f\\n'" if not recursive else f"find '{path}' -printf '%P\\n' | head -200"
        code, output = await _docker_exec(cmd)
        entries = [e for e in output.strip().splitlines() if e]
        return json.dumps({"entries": entries, "path": path})

    async def search_handler(params: dict[str, Any]) -> str:
        pattern = params.get("pattern", "")
        path = params.get("path", ".")
        if not pattern:
            return json.dumps({"error": "No pattern"})
        file_pattern = params.get("file_pattern", "")
        cmd = f"grep -rn"
        if file_pattern:
            cmd += f" --include='{file_pattern}'"
        cmd += f" '{pattern}' '{path}' 2>/dev/null | head -100"
        code, output = await _docker_exec(cmd, timeout=30.0)
        return json.dumps({"matches": output, "match_count": output.count("\n")})

    return [
        ToolSchema(name="read_file", description="Read a file's contents inside the container.", input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File path"}},
            "required": ["path"],
        }, handler=read_handler),
        ToolSchema(name="write_file", description="Write content to a file inside the container.", input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        }, handler=write_handler),
        ToolSchema(name="edit_file", description="Search-and-replace edit a file inside the container.", input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "old_string": {"type": "string", "description": "String to find"},
                "new_string": {"type": "string", "description": "Replacement"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences"},
            },
            "required": ["path", "old_string", "new_string"],
        }, handler=edit_handler),
        ToolSchema(name="list_files", description="List files in a directory inside the container.", input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path"},
                "recursive": {"type": "boolean", "description": "List recursively"},
            },
        }, handler=list_handler),
        ToolSchema(name="search_files", description="Search for a pattern in files inside the container.", input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text pattern to search"},
                "path": {"type": "string", "description": "Directory to search"},
                "file_pattern": {"type": "string", "description": "File glob (e.g. '*.py')"},
            },
            "required": ["pattern"],
        }, handler=search_handler),
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="aurora-lhtb",
        description="Run an LHTB benchmark task using AURORA.",
    )
    parser.add_argument("--task-dir", required=True, help="LHTB task directory.")
    parser.add_argument("--model", default="deepseek/deepseek-v4-flash", help="Model ID.")
    parser.add_argument("--api-key", default="", help="API key.")
    parser.add_argument("--base-url", default="https://openrouter.ai/api/v1", help="API base URL.")
    parser.add_argument("--driver", default="auto", choices=["auto", "claude-code", "openai"],
                        help="Agent driver: claude-code (Claude Code CLI), openai (OpenAI-compatible loop), auto.")
    parser.add_argument("--no-docker", action="store_true", help="Run locally without Docker.")
    parser.add_argument("--working-dir", default=None, help="Override working dir for local mode.")
    parser.add_argument("--max-turns", type=int, default=200, help="Max turns per phase.")
    parser.add_argument("--timeout", type=float, default=None, help="Override timeout (seconds).")
    parser.add_argument("--output-dir", default=None,
                        help="Directory for result JSON and trajectory JSONL. Default: task-dir.")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    api_key = (
        args.api_key
        or os.environ.get("OPENROUTER_API_KEY", "")
        or os.environ.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not api_key:
        print("Error: No API key. Set --api-key, OPENROUTER_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY.", file=sys.stderr)
        sys.exit(1)

    task = load_task(args.task_dir)

    print(f"[AURORA LHTB] Task: {task.name}")
    print(f"  Category   : {task.category}")
    print(f"  Difficulty : {task.difficulty}")
    print(f"  Artifacts  : {len(task.artifacts)} required")
    print(f"  Docker     : {task.docker_image or '(none — local mode)'}")
    print(f"  Timeout    : {args.timeout or task.agent_timeout_sec}s")
    print(f"  Loop mode  : {'continue-until-timeout' if task.continue_until_timeout else 'single-shot'}")
    print(f"  Driver     : {args.driver}")
    print(f"  Model      : {args.model}")
    print(f"  API        : {args.base_url}")
    print()

    async def _stream(event: StreamEvent) -> None:
        d = event.data
        t = event.event_type
        if t == "reasoning":
            print(f"  [think] {d.get('text', '')[:200]}")
        elif t == "tool_call_start":
            print(f"  [tool]  {d.get('tool_name', '?')} ...", end="", flush=True)
        elif t == "tool_call_result":
            print(f" ({d.get('duration', 0):.1f}s)")
        elif t == "agent_done":
            print(f"  [done]  Agent finished")
        elif t == "phase_started":
            print(f"\n--- Phase {d.get('phase', 0)} (elapsed {d.get('elapsed', 0)}s, remaining {d.get('remaining', 0)}s) ---")
        elif t == "verifier_started":
            print(f"  [verify] Running verifier...")
        elif t == "verifier_result":
            status = "PASSED" if d.get("passed") else "FAILED"
            print(f"  [verify] {status} (reward: {d.get('reward', 0):.2%})")
        elif t == "docker_started":
            print(f"  [docker] Container {d.get('container_id', '?')} started")
        elif t == "error":
            print(f"  [ERR]   {d.get('error', '?')}")

    runner = LHTBRunner(
        task,
        model=args.model,
        api_key=api_key,
        base_url=args.base_url,
        driver=args.driver,
        use_docker=not args.no_docker,
        working_dir=args.working_dir,
        max_turns=args.max_turns,
        timeout_sec=args.timeout,
        stream_callback=_stream,
    )

    result = asyncio.run(runner.run())

    print()
    print("=" * 60)
    print(f"[AURORA LHTB] {result.status.upper()}")
    print(f"  Task       : {result.task_name}")
    print(f"  Phases     : {len(result.phases)}")
    print(f"  Turns      : {result.total_turns}")
    print(f"  Tokens     : {result.total_tokens:,}")
    print(f"  Wall time  : {result.wall_time_seconds:.1f}s")
    print(f"  Reward     : {result.final_reward:.2%}")
    print(f"  Artifacts  : {len(result.artifacts_found)}/{len(result.artifacts_found) + len(result.artifacts_missing)}")
    if result.artifacts_missing:
        print(f"  Missing    : {', '.join(result.artifacts_missing)}")
    if result.error:
        print(f"  Error      : {result.error}")
    print("=" * 60)

    # Write result JSON and trajectory JSONL
    output_dir = Path(args.output_dir) if args.output_dir else Path(args.task_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    run_id = f"{int(time.time())}_{args.model.replace('/', '_')}"

    result_path = output_dir / f"aurora_result_{run_id}.json"
    result_path.write_text(json.dumps({
        "task_name": result.task_name,
        "status": result.status,
        "final_reward": result.final_reward,
        "phases": [
            {
                "phase": p.phase,
                "turns": p.turns,
                "tokens": p.tokens,
                "wall_time": p.wall_time_seconds,
                "reward": p.verifier_reward,
                "passed": p.verifier_passed,
            }
            for p in result.phases
        ],
        "artifacts_found": result.artifacts_found,
        "artifacts_missing": result.artifacts_missing,
        "total_turns": result.total_turns,
        "total_tokens": result.total_tokens,
        "wall_time_seconds": result.wall_time_seconds,
        "error": result.error,
        "model": args.model,
        "driver": args.driver,
        "run_id": run_id,
    }, indent=2), encoding="utf-8")
    print(f"\nResult saved to: {result_path}")

    # Write trajectory JSONL — one line per event across all phases
    trajectory_path = output_dir / f"trajectory_{run_id}.jsonl"
    with open(trajectory_path, "w", encoding="utf-8") as f:
        # Run-level events first (metadata, docker start)
        for entry in getattr(runner, '_run_trajectory', []):
            f.write(json.dumps({
                "timestamp": entry.timestamp,
                "phase": entry.phase,
                "turn": entry.turn,
                "event_type": entry.event_type,
                "data": _safe_serialize(entry.data),
            }) + "\n")
        # Per-phase events
        for phase_result in result.phases:
            for entry in phase_result.trajectory:
                f.write(json.dumps({
                    "timestamp": entry.timestamp,
                    "phase": entry.phase or phase_result.phase,
                    "turn": entry.turn,
                    "event_type": entry.event_type,
                    "data": _safe_serialize(entry.data),
                }) + "\n")
        # Final run_end event
        f.write(json.dumps({
            "timestamp": time.time(),
            "phase": -1,
            "turn": 0,
            "event_type": "run_end",
            "data": {
                "status": result.status,
                "final_reward": result.final_reward,
                "total_turns": result.total_turns,
                "total_tokens": result.total_tokens,
                "wall_time_seconds": round(result.wall_time_seconds, 2),
                "artifacts_found": result.artifacts_found,
                "artifacts_missing": result.artifacts_missing,
                "error": result.error,
            },
        }) + "\n")
    print(f"Trajectory saved to: {trajectory_path}")

    # Also keep a latest symlink for convenience
    latest_result = output_dir / "aurora_result.json"
    latest_trajectory = output_dir / "trajectory.jsonl"
    for link, target in [(latest_result, result_path), (latest_trajectory, trajectory_path)]:
        try:
            link.unlink(missing_ok=True)
            link.symlink_to(target.name)
        except OSError:
            pass

    if result.status not in ("completed", "partial"):
        sys.exit(1)


if __name__ == "__main__":
    main()

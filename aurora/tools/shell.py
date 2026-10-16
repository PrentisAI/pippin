"""Shell command execution tool for agentic loops.

Allows agents to run arbitrary shell commands in a working directory,
with output capture, timeout control, and optional Docker container
execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from aurora.llm.tool_runner import ToolSchema

logger = logging.getLogger(__name__)

_MAX_OUTPUT = 8000


def build_shell_tool(
    working_dir: str,
    *,
    docker_container: str | None = None,
    timeout: float = 120.0,
) -> ToolSchema:
    """Build a shell execution tool for the agentic loop.

    Parameters
    ----------
    working_dir:
        Default working directory for commands.
    docker_container:
        If set, commands are executed via ``docker exec`` in this container.
    timeout:
        Per-command timeout in seconds.
    """

    async def handler(params: dict[str, Any]) -> str:
        command = params.get("command", "")
        if not command.strip():
            return json.dumps({"error": "Empty command"})

        cwd = params.get("working_dir", working_dir)
        cmd_timeout = min(params.get("timeout", timeout), 300.0)

        if docker_container:
            full_cmd = ["docker", "exec", "-w", cwd, docker_container, "bash", "-c", command]
            exec_cwd = None
        else:
            full_cmd = ["bash", "-c", command]
            exec_cwd = cwd

        try:
            proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=exec_cwd,
                env={**os.environ, "TERM": "dumb"},
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=cmd_timeout,
            )
            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")

            if len(stdout) > _MAX_OUTPUT:
                stdout = stdout[:_MAX_OUTPUT] + f"\n... (truncated, {len(stdout_bytes)} bytes total)"
            if len(stderr) > _MAX_OUTPUT:
                stderr = stderr[:_MAX_OUTPUT] + f"\n... (truncated, {len(stderr_bytes)} bytes total)"

            return json.dumps({
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
            })

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return json.dumps({"error": f"Command timed out after {cmd_timeout}s"})
        except Exception as exc:
            return json.dumps({"error": f"Execution failed: {exc}"})

    return ToolSchema(
        name="shell",
        description=(
            "Execute a shell command and return stdout/stderr. "
            "Use this to run Python scripts, install packages, inspect files, "
            "run tests, and perform any terminal operations. "
            "Commands run in bash."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory (optional, defaults to task directory)",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (max 300)",
                },
            },
            "required": ["command"],
        },
        handler=handler,
    )

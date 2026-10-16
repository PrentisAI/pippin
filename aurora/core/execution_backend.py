"""Pluggable execution backend abstraction.

Defines the ``ExecutionBackend`` protocol that all execution environments
must implement, plus ``LocalBackend`` for host-local subprocess execution.

Task-specific backends (e.g. Docker for LHTB) live under their own
packages and implement this same protocol.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


logger = logging.getLogger(__name__)


@runtime_checkable
class ExecutionBackend(Protocol):
    """Protocol for pluggable execution environments.

    Implementations provide command execution, tool registration,
    and lifecycle management for a task's runtime environment.
    """

    async def setup(self) -> None:
        """Prepare the execution environment (pull images, start containers, etc.)."""
        ...

    async def teardown(self) -> None:
        """Clean up the execution environment."""
        ...

    async def exec_command(
        self,
        command: str,
        *,
        timeout: float = 120.0,
        workdir: str | None = None,
    ) -> tuple[int, str, str]:
        """Run a command and return (exit_code, stdout, stderr)."""
        ...

    async def read_file(self, path: str) -> bytes:
        """Read a file from the execution environment."""
        ...

    async def write_file(self, path: str, content: bytes) -> None:
        """Write a file in the execution environment."""
        ...

    async def run_verifier(self, *, timeout: float = 600.0) -> tuple[bool, float, str]:
        """Run task verification. Returns (passed, reward, output).

        Backends that don't support verification should return (True, 1.0, "no verifier").
        """
        ...

    async def extract_workspace(self, dest: Path) -> bool:
        """Copy the task workspace to a local directory. Returns success."""
        ...


class LocalBackend:
    """Execute commands on the local host via subprocess."""

    def __init__(self, working_dir: str) -> None:
        self._working_dir = working_dir

    async def setup(self) -> None:
        Path(self._working_dir).mkdir(parents=True, exist_ok=True)

    async def teardown(self) -> None:
        pass

    async def exec_command(
        self,
        command: str,
        *,
        timeout: float = 120.0,
        workdir: str | None = None,
    ) -> tuple[int, str, str]:
        cwd = workdir or self._working_dir
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={**os.environ, "TERM": "dumb"},
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
            return (
                proc.returncode or 0,
                stdout_bytes.decode(errors="replace"),
                stderr_bytes.decode(errors="replace"),
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return 1, "", f"Command timed out after {timeout}s"
        except Exception as exc:
            return 1, "", f"Execution failed: {exc}"

    async def read_file(self, path: str) -> bytes:
        full = Path(self._working_dir) / path.lstrip("/")
        return full.read_bytes()

    async def write_file(self, path: str, content: bytes) -> None:
        full = Path(self._working_dir) / path.lstrip("/")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)

    async def run_verifier(self, *, timeout: float = 600.0) -> tuple[bool, float, str]:
        return True, 1.0, "no verifier"

    async def extract_workspace(self, dest: Path) -> bool:
        import shutil
        src = Path(self._working_dir)
        if not src.exists():
            return False
        dest.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(src, dest, dirs_exist_ok=True)
            return True
        except Exception as exc:
            logger.warning("Failed to extract workspace: %s", exc)
            return False

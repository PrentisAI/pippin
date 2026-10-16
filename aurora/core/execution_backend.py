"""Where the agents' commands run: the execution backend of a campaign.

``DockerBackend`` (:mod:`aurora.core.docker_backend`) gives every campaign its
own container; ``LocalBackend`` runs nothing itself and lets the Claude Code
sessions work directly in the campaign workspace on this machine (shell
commands only with ``sandbox.allow_host_bash``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ExecutionBackend(Protocol):
    """What the engine needs from an execution environment."""

    container_id: str | None

    async def setup(self) -> None:
        """Prepare the environment (pull the image, start the container, ...)."""
        ...

    async def teardown(self) -> None:
        """Release the environment."""
        ...

    async def exec_command(self, command: str, *, timeout: float = 120.0,
                           workdir: str | None = None) -> tuple[int, str, str]:
        """Run a shell command; returns (exit code, stdout, stderr)."""
        ...

    async def run_verifier(self, *, timeout: float = 600.0) -> tuple[bool, float, str]:
        """Run the acceptance tests; returns (passed, score, output)."""
        ...

    async def extract_workspace(self, dest: Path) -> bool:
        """Copy the agents' working directory to *dest* on the host."""
        ...


class LocalBackend:
    """No container: the agents work in the campaign workspace on this machine."""

    container_id: str | None = None

    def __init__(self, working_dir: str) -> None:
        self._working_dir = working_dir

    async def setup(self) -> None:
        Path(self._working_dir).mkdir(parents=True, exist_ok=True)

    async def teardown(self) -> None:
        pass

    async def exec_command(self, command: str, *, timeout: float = 120.0,
                           workdir: str | None = None) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", command, cwd=workdir or self._working_dir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "TERM": "dumb"},
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            return 124, "", f"timed out after {timeout:g}s"
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    async def run_verifier(self, *, timeout: float = 600.0) -> tuple[bool, float, str]:
        logger.warning("Acceptance tests (sandbox.tests_dir) need sandbox.docker; not run")
        return True, 1.0, "acceptance tests not run (no container)"

    async def extract_workspace(self, dest: Path) -> bool:
        # the agents already worked in the campaign workspace on the host
        return True

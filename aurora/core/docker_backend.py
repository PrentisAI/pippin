"""Docker execution backend: one container per campaign, shared by all its phases.

The container runs ``sleep infinity``; the agents work in ``/app`` through
``docker exec``.  When the campaign ends, ``/app`` is copied to
``<campaign>/workspace`` and the container is removed.

Acceptance tests (``sandbox.tests_dir``) are copied into ``/tests`` only while
they run and removed right after, so the agents never see them.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def _run(*args: str, timeout: float = 120.0, stdin: bytes | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return 124, "", f"`{' '.join(args[:3])} ...` timed out after {timeout:g}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class DockerBackend:
    """A container for one campaign.

    Parameters
    ----------
    docker_image:
        Image to run; pulled when it is not present locally.
    cpus, memory_mb:
        Resource limits of the container.
    allow_network:
        Internet access from the container.
    tests_dir:
        Host directory with acceptance tests (``test.sh`` or pytest files).
    """

    def __init__(self, docker_image: str = "python:3.11-slim", *, cpus: float = 4.0, memory_mb: int = 8192,
                 allow_network: bool = True, tests_dir: str = "", pull_timeout: float = 1800.0) -> None:
        self._image = docker_image
        self._cpus = cpus
        self._memory_mb = memory_mb
        self._allow_network = allow_network
        self._tests_dir = tests_dir
        self._pull_timeout = pull_timeout
        self.container_id: str | None = None

    async def setup(self) -> None:
        code, _, _ = await _run("docker", "image", "inspect", self._image, timeout=30)
        if code != 0:
            logger.info("Pulling image %s ...", self._image)
            code, _, err = await _run("docker", "pull", self._image, timeout=self._pull_timeout)
            if code != 0:
                raise RuntimeError(f"Could not pull Docker image {self._image}: {err.strip()}")
        code, out, err = await _run(
            "docker", "run", "-d", "--init",
            "--cpus", str(self._cpus), "--memory", f"{self._memory_mb}m",
            "--network", "bridge" if self._allow_network else "none",
            "--label", "aurora.campaign=1",
            "-w", "/app", self._image, "sleep", "infinity", timeout=120)
        if code != 0:
            raise RuntimeError(f"Could not start a container from {self._image}: {err.strip()}")
        self.container_id = out.strip()[:12]
        await self.exec_command("mkdir -p /app")
        logger.info("Container %s started from %s", self.container_id, self._image)

    async def teardown(self) -> None:
        if self.container_id:
            await _run("docker", "rm", "-f", self.container_id, timeout=60)
            logger.info("Container %s removed", self.container_id)
            self.container_id = None

    async def exec_command(self, command: str, *, timeout: float = 120.0,
                           workdir: str | None = None) -> tuple[int, str, str]:
        if not self.container_id:
            raise RuntimeError("Container not started")
        return await _run("docker", "exec", "-w", workdir or "/app", self.container_id, "sh", "-c", command,
                          timeout=timeout)

    async def run_verifier(self, *, timeout: float = 600.0) -> tuple[bool, float, str]:
        """Run the acceptance tests; passed means exit code 0."""
        if not self._tests_dir:
            return True, 1.0, "no acceptance tests configured"
        tests = Path(self._tests_dir).expanduser().resolve()
        if not tests.is_dir():
            return False, 0.0, f"tests_dir not found: {tests}"
        code, _, err = await _run("docker", "cp", f"{tests}/.", f"{self.container_id}:/tests", timeout=60)
        if code != 0:
            return False, 0.0, f"Could not copy the tests: {err}"
        try:
            cmd = ("if [ -f /tests/test.sh ]; then bash /tests/test.sh; "
                   "else python -m pytest -q /tests; fi")
            code, out, err = await self.exec_command(cmd + " 2>&1", timeout=timeout)
        finally:
            await self.exec_command("rm -rf /tests")
        passed = code == 0
        return passed, 1.0 if passed else 0.0, out + err

    async def extract_workspace(self, dest: Path) -> bool:
        if not self.container_id:
            return False
        dest.mkdir(parents=True, exist_ok=True)
        code, _, err = await _run("docker", "cp", f"{self.container_id}:/app/.", str(dest), timeout=600)
        if code != 0:
            logger.warning("Could not copy /app out of the container: %s", err.strip())
        return code == 0

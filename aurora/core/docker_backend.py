"""Generic Docker execution backend.

Provides a Docker container as the execution environment for any
experiment type. One container per experiment, shared across all
execution phases (Screening → Planning → Preflight → Research Loop →
Analysis). Purely LLM-inference phases (Roundtable, Writing) bypass
the container entirely.

Task-specific adapters (e.g. LHTBTaskAdapter) set ``docker_image``,
resource limits, and test directories via ``AuroraConfig``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from aurora.llm.tool_runner import ToolSchema

logger = logging.getLogger(__name__)


class DockerBackend:
    """ExecutionBackend backed by a Docker container.

    Parameters
    ----------
    docker_image:
        Docker image to use (e.g. ``python:3.11-slim``).
    cpus:
        CPU limit for the container.
    memory_mb:
        Memory limit in MB.
    allow_network:
        Whether the container has internet access.
    tests_dir:
        Optional host path containing verifier scripts. NOT mounted into
        the agent container — injected via docker-cp only during verification.
    build_timeout:
        Timeout for docker pull / docker run.
    """

    def __init__(
        self,
        docker_image: str = "python:3.11-slim",
        *,
        cpus: float = 4.0,
        memory_mb: int = 8192,
        allow_network: bool = True,
        tests_dir: str = "",
        build_timeout: float = 600.0,
    ) -> None:
        self._image = docker_image
        self._cpus = cpus
        self._memory_mb = memory_mb
        self._allow_network = allow_network
        self._tests_dir = tests_dir
        self._build_timeout = build_timeout
        self._container_id: str | None = None

    @property
    def container_id(self) -> str | None:
        return self._container_id

    async def setup(self) -> None:
        """Pull image and start the container.

        Tests are NOT mounted into the agent container. Verification
        uses docker-cp to inject tests temporarily — the agent never
        sees the grader during normal execution.
        """
        logger.info("Pulling image %s...", self._image)
        pull = subprocess.run(
            ["docker", "pull", self._image],
            capture_output=True, text=True,
            timeout=self._build_timeout,
        )
        if pull.returncode != 0:
            logger.warning("Docker pull warning: %s", pull.stderr.strip())

        cmd = [
            "docker", "run", "-d",
            "--cpus", str(self._cpus),
            "--memory", f"{self._memory_mb}m",
        ]
        if self._allow_network:
            cmd.extend(["--network", "bridge"])
        else:
            cmd.extend(["--network", "none"])

        # Tests are intentionally NOT mounted — agent cannot see grader
        cmd.extend([self._image, "sleep", "infinity"])

        logger.info("Starting container: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=self._build_timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Docker run failed: {result.stderr}")

        self._container_id = result.stdout.strip()[:12]
        logger.info("Container started: %s", self._container_id)

        subprocess.run(
            ["docker", "exec", self._container_id, "mkdir", "-p", "/app"],
            capture_output=True, timeout=10,
        )

    async def teardown(self) -> None:
        """Stop and remove the container."""
        if self._container_id:
            logger.info("Stopping container %s", self._container_id)
            subprocess.run(
                ["docker", "stop", "-t", "5", self._container_id],
                capture_output=True, timeout=30,
            )
            subprocess.run(
                ["docker", "rm", "-f", self._container_id],
                capture_output=True, timeout=30,
            )
            self._container_id = None

    async def exec_command(
        self,
        command: str,
        *,
        timeout: float = 120.0,
        workdir: str | None = None,
    ) -> tuple[int, str, str]:
        if not self._container_id:
            raise RuntimeError("Container not started")
        wd = workdir or "/app"
        result = subprocess.run(
            ["docker", "exec", "-w", wd, self._container_id, "bash", "-c", command],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr

    async def read_file(self, path: str) -> bytes:
        if not self._container_id:
            raise RuntimeError("Container not started")
        result = subprocess.run(
            ["docker", "exec", self._container_id, "cat", path],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            raise FileNotFoundError(
                f"Cannot read {path} in container: {result.stderr.decode(errors='replace')}"
            )
        return result.stdout

    async def write_file(self, path: str, content: bytes) -> None:
        if not self._container_id:
            raise RuntimeError("Container not started")
        subprocess.run(
            ["docker", "exec", self._container_id, "mkdir", "-p",
             str(Path(path).parent)],
            capture_output=True, timeout=10,
        )
        proc = subprocess.run(
            ["docker", "exec", "-i", self._container_id, "tee", path],
            input=content, capture_output=True, timeout=30,
        )
        if proc.returncode != 0:
            raise IOError(
                f"Cannot write {path}: {proc.stderr.decode(errors='replace')}"
            )

    def get_tool_registry(self) -> list[ToolSchema]:
        if not self._container_id:
            raise RuntimeError("Container not started")
        from aurora.tools.shell import build_shell_tool
        from aurora.lhtb.runner import _build_docker_file_tools
        tools: list[ToolSchema] = [
            build_shell_tool("/app", docker_container=self._container_id, timeout=120.0),
        ]
        tools.extend(_build_docker_file_tools(self._container_id, "/app"))
        return tools

    async def run_verifier(self, *, timeout: float = 600.0) -> tuple[bool, float, str]:
        """Run verifier with test isolation.

        When an external tests_dir is configured (LHTB), tests are
        docker-cp'd in just for verification and deleted immediately
        after. The agent never has /tests during normal execution.

        Falls back to in-container pytest when no external tests_dir
        is configured, and returns (True, 1.0, "no verifier") when
        no tests exist at all.
        """
        if not self._container_id:
            raise RuntimeError("Container not started")

        if self._tests_dir and Path(self._tests_dir).is_dir():
            return await self._run_sidecar_verifier(timeout=timeout)

        # Fallback: in-container pytest for non-LHTB tasks
        code, probe, _ = await self.exec_command(
            "test -d /app/tests && echo yes || echo no"
        )
        if "yes" in probe:
            _, stdout, stderr = await self.exec_command(
                "cd /app && python -m pytest tests/ -v --tb=short 2>&1 || true",
                timeout=timeout,
            )
            output = stdout + stderr
            passed = "failed" not in output.lower() and "passed" in output.lower()
            return passed, 1.0 if passed else 0.0, output

        return True, 1.0, "no verifier"

    async def _run_sidecar_verifier(
        self, *, timeout: float = 600.0,
    ) -> tuple[bool, float, str]:
        """Copy tests in, run verifier, delete tests — agent never sees them.

        We can't use a sidecar container because the grader may depend
        on packages the agent installed at runtime (pip install -e .).
        Instead we:
        1. docker cp tests into the agent container as /tests
        2. run test.sh
        3. rm -rf /tests immediately after
        """
        tests_path = Path(self._tests_dir).resolve()
        logger.info("Verifier: injecting /tests (agent cannot see grader at runtime)")

        # 1. Copy tests into container
        cp_result = subprocess.run(
            ["docker", "cp", f"{tests_path}/.", f"{self._container_id}:/tests"],
            capture_output=True, text=True, timeout=30,
        )
        if cp_result.returncode != 0:
            return False, 0.0, f"Failed to copy tests: {cp_result.stderr}"

        try:
            # 2. Run verifier
            _, stdout, stderr = await self.exec_command(
                "mkdir -p /logs/verifier && bash /tests/test.sh 2>&1 || true",
                timeout=timeout,
            )
            output = stdout + stderr

            _, reward_str, _ = await self.exec_command(
                "cat /logs/verifier/reward.txt 2>/dev/null || echo 0"
            )
            try:
                reward = float(reward_str.strip())
            except (ValueError, TypeError):
                reward = 0.0

            _, grade_log, _ = await self.exec_command(
                "cat /logs/verifier/grade.log 2>/dev/null || true"
            )
            if grade_log.strip():
                output += "\n--- grade.log ---\n" + grade_log
        finally:
            # 3. Always remove tests and verifier logs after verification
            await self.exec_command(
                "rm -rf /tests /logs/verifier /tmp/pytest-of-root 2>/dev/null || true"
            )
            logger.info("Verifier: /tests removed from container")

        return reward >= 0.95, reward, output

    async def extract_workspace(self, dest: Path) -> bool:
        if not self._container_id:
            return False
        dest.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["docker", "cp", f"{self._container_id}:/app/.", str(dest)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.warning("Failed to extract workspace: %s", result.stderr)
            return False
        return True

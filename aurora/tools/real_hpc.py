"""Real Slurm HPC job management tool wrapper.

Wraps ``sbatch``, ``squeue``, ``scancel``, and ``sacct`` for job
submission, monitoring, and cancellation on a Slurm-managed cluster.
Falls back gracefully when Slurm commands are not available.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from aurora.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


def _check_slurm_available() -> tuple[bool, str]:
    """Check whether Slurm's ``sbatch`` is on the PATH."""
    import shutil

    if shutil.which("sbatch") is not None:
        return True, "Slurm (sbatch) is available."
    return False, (
        "Slurm (sbatch) not found on PATH. "
        "Ensure you are on a Slurm-managed cluster."
    )


class RealHPCTool(Tool):
    """HPC job management via Slurm.

    Supported operations
    --------------------
    ``submit_job``
        Submit a batch script via ``sbatch``.
    ``check_status``
        Check job status via ``squeue`` / ``sacct``.
    ``cancel_job``
        Cancel a running or pending job via ``scancel``.
    ``wait_for_completion``
        Poll until a job finishes (with timeout).
    """

    name = "hpc"
    description = "Submit and manage compute jobs on a Slurm HPC cluster"

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        op = params.get("operation")
        valid_ops = ("submit_job", "check_status", "cancel_job", "wait_for_completion")
        if op not in valid_ops:
            return False, f"Unknown operation: {op!r}; choose from {valid_ops}"

        available, msg = _check_slurm_available()
        if not available:
            return False, msg

        if op == "submit_job":
            if "script_path" not in params:
                return False, "Missing required parameter: script_path"

        if op in ("check_status", "cancel_job", "wait_for_completion"):
            if "job_id" not in params:
                return False, "Missing required parameter: job_id"

        return True, "Parameters OK."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        op = params["operation"]
        t0 = time.monotonic()
        try:
            if op == "submit_job":
                result = await self._submit_job(params)
            elif op == "check_status":
                result = await self._check_status(params)
            elif op == "cancel_job":
                result = await self._cancel_job(params)
            elif op == "wait_for_completion":
                result = await self._wait_for_completion(params)
            else:
                result = ToolResult(success=False, error=f"Unhandled: {op}")
            result.wall_time_seconds = time.monotonic() - t0
            return result
        except Exception as exc:
            logger.exception("HPC operation %r failed", op)
            return ToolResult(
                success=False,
                error=f"HPC operation {op!r} failed: {exc}",
                wall_time_seconds=time.monotonic() - t0,
            )

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    async def _submit_job(self, params: dict[str, Any]) -> ToolResult:
        """Submit a batch job via ``sbatch``."""
        script_path: str = params["script_path"]
        job_name: str | None = params.get("job_name")
        partition: str | None = params.get("partition")
        nodes: int | None = params.get("nodes")
        gpus: int | str | None = params.get("gpus")
        time_limit: str | None = params.get("time_limit")

        if not Path(script_path).exists():
            return ToolResult(
                success=False,
                error=f"Script file not found: {script_path}",
            )

        cmd: list[str] = ["sbatch"]
        if job_name:
            cmd.extend(["--job-name", job_name])
        if partition:
            cmd.extend(["--partition", partition])
        if nodes is not None:
            cmd.extend(["--nodes", str(nodes)])
        if gpus is not None:
            cmd.extend(["--gpus", str(gpus)])
        if time_limit:
            cmd.extend(["--time", time_limit])
        cmd.append(script_path)

        logger.info("Submitting job: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            return ToolResult(
                success=False,
                error=f"sbatch failed (code {proc.returncode}): {stderr_text}",
                metadata={"returncode": proc.returncode},
            )

        # Parse job ID from sbatch output: "Submitted batch job 12345"
        job_id_match = re.search(r"Submitted batch job\s+(\d+)", stdout_text)
        job_id = job_id_match.group(1) if job_id_match else stdout_text

        return ToolResult(
            success=True,
            output={
                "job_id": job_id,
                "status": "submitted",
                "message": stdout_text,
            },
            metadata={"command": " ".join(cmd)},
        )

    async def _check_status(self, params: dict[str, Any]) -> ToolResult:
        """Check job status via ``squeue`` and ``sacct``."""
        job_id: str = str(params["job_id"])

        # First try squeue (for queued/running jobs)
        proc = await asyncio.create_subprocess_exec(
            "squeue", "-j", job_id, "--noheader",
            "--format=%i|%j|%T|%M|%l|%P|%R",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        stdout_text = stdout.decode("utf-8", errors="replace").strip()

        if stdout_text:
            parts = stdout_text.split("|")
            status_info = {
                "job_id": parts[0].strip() if len(parts) > 0 else job_id,
                "job_name": parts[1].strip() if len(parts) > 1 else "",
                "status": parts[2].strip() if len(parts) > 2 else "UNKNOWN",
                "elapsed": parts[3].strip() if len(parts) > 3 else "",
                "time_limit": parts[4].strip() if len(parts) > 4 else "",
                "partition": parts[5].strip() if len(parts) > 5 else "",
                "reason": parts[6].strip() if len(parts) > 6 else "",
            }
            return ToolResult(success=True, output=status_info)

        # Job not in squeue — try sacct for completed/failed jobs
        proc = await asyncio.create_subprocess_exec(
            "sacct", "-j", job_id, "--noheader", "--parsable2",
            "--format=JobID,JobName,State,Elapsed,ExitCode",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        stdout_text = stdout.decode("utf-8", errors="replace").strip()

        if stdout_text:
            # Take the first line (main job, not steps)
            first_line = stdout_text.splitlines()[0]
            parts = first_line.split("|")
            status_info = {
                "job_id": parts[0].strip() if len(parts) > 0 else job_id,
                "job_name": parts[1].strip() if len(parts) > 1 else "",
                "status": parts[2].strip() if len(parts) > 2 else "UNKNOWN",
                "elapsed": parts[3].strip() if len(parts) > 3 else "",
                "exit_code": parts[4].strip() if len(parts) > 4 else "",
            }
            return ToolResult(success=True, output=status_info)

        return ToolResult(
            success=False,
            error=f"Could not find job {job_id} in squeue or sacct.",
        )

    async def _cancel_job(self, params: dict[str, Any]) -> ToolResult:
        """Cancel a job via ``scancel``."""
        job_id: str = str(params["job_id"])

        proc = await asyncio.create_subprocess_exec(
            "scancel", job_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            return ToolResult(
                success=False,
                error=f"scancel failed (code {proc.returncode}): {stderr_text}",
            )

        return ToolResult(
            success=True,
            output={"job_id": job_id, "status": "cancelled"},
        )

    async def _wait_for_completion(self, params: dict[str, Any]) -> ToolResult:
        """Poll until a job completes or times out."""
        job_id: str = str(params["job_id"])
        poll_interval: float = params.get("poll_interval", 30.0)
        timeout: float = params.get("timeout", 86400.0)  # 24h default

        terminal_states = {
            "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT",
            "NODE_FAIL", "PREEMPTED", "OUT_OF_MEMORY",
        }

        elapsed = 0.0
        last_status = "UNKNOWN"

        while elapsed < timeout:
            status_result = await self._check_status({"job_id": job_id})
            if not status_result.success:
                # Job might have finished and left sacct
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
                continue

            last_status = status_result.output.get("status", "UNKNOWN")
            if last_status in terminal_states:
                return ToolResult(
                    success=last_status == "COMPLETED",
                    output={
                        "job_id": job_id,
                        "status": last_status,
                        "elapsed_wait_seconds": round(elapsed, 1),
                        **status_result.output,
                    },
                )

            logger.debug(
                "Job %s status: %s (waited %.0fs / %.0fs)",
                job_id, last_status, elapsed, timeout,
            )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        return ToolResult(
            success=False,
            error=(
                f"Timeout waiting for job {job_id} after {timeout}s. "
                f"Last status: {last_status}"
            ),
            output={"job_id": job_id, "status": last_status},
        )

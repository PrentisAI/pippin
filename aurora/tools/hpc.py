"""Mock HPC job submission and management.

Simulates a shared GPU pool (H100 / V100) with job queuing, execution,
and completion tracking.  Wall-time caps and concurrency limits mirror the
resource constraints described in Section H.3 of the paper.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aurora.tools.base import Tool, ToolResult


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class _MockJob:
    job_id: str
    status: JobStatus = JobStatus.QUEUED
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    wall_time: float = 0.0


class MockHPCTool(Tool):
    """Mock HPC cluster for job submission.

    Operations
    ----------
    ``submit``
        Queue a new job; returns a ``job_id``.
    ``status``
        Check current status of a previously submitted job.
    ``cancel``
        Cancel a queued or running job.
    ``wait``
        Block until a specific job completes (with timeout).
    ``list``
        List all tracked jobs with their statuses.
    """

    name = "hpc"
    description = "Submit and manage compute jobs on HPC cluster (mock)"

    def __init__(self, max_concurrent: int = 16) -> None:
        self.max_concurrent = max_concurrent
        self._jobs: dict[str, _MockJob] = {}
        self._running_count = 0

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        op = params.get("operation")
        if op not in ("submit", "status", "cancel", "wait", "list"):
            return False, f"Unknown operation {op!r}"

        if op in ("status", "cancel", "wait") and "job_id" not in params:
            return False, f"Operation {op!r} requires a 'job_id'."

        return True, "Parameters OK."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        op = params["operation"]

        if op == "submit":
            return await self._submit(params)
        elif op == "status":
            return self._status(params)
        elif op == "cancel":
            return self._cancel(params)
        elif op == "wait":
            return await self._wait(params)
        elif op == "list":
            return self._list()

        return ToolResult(success=False, error=f"Unhandled: {op}")

    # ------------------------------------------------------------------

    async def _submit(self, params: dict[str, Any]) -> ToolResult:
        job_id = f"hpc-{uuid.uuid4().hex[:8]}"
        job = _MockJob(job_id=job_id, params=params.get("job_params", {}))

        if self._running_count >= self.max_concurrent:
            # Job stays queued — caller should poll or wait
            job.status = JobStatus.QUEUED
        else:
            job.status = JobStatus.RUNNING
            self._running_count += 1
            # Simulate async job execution
            asyncio.create_task(self._run_job(job))

        self._jobs[job_id] = job

        return ToolResult(
            success=True,
            output={
                "job_id": job_id,
                "status": job.status.value,
                "running_jobs": self._running_count,
                "max_concurrent": self.max_concurrent,
            },
        )

    async def _run_job(self, job: _MockJob) -> None:
        """Simulate a running job completing after a short delay."""
        wall = job.params.get("estimated_wall_seconds", 1.0)
        await asyncio.sleep(min(wall, 3.0))  # cap simulation time
        job.status = JobStatus.COMPLETED
        job.wall_time = wall
        job.result = {"completed": True}
        self._running_count = max(0, self._running_count - 1)

        # Start any queued jobs
        for j in self._jobs.values():
            if j.status == JobStatus.QUEUED and self._running_count < self.max_concurrent:
                j.status = JobStatus.RUNNING
                self._running_count += 1
                asyncio.create_task(self._run_job(j))

    def _status(self, params: dict[str, Any]) -> ToolResult:
        job_id = params["job_id"]
        job = self._jobs.get(job_id)
        if job is None:
            return ToolResult(success=False, error=f"Unknown job_id: {job_id}")
        return ToolResult(
            success=True,
            output={
                "job_id": job_id,
                "status": job.status.value,
                "wall_time": job.wall_time,
            },
        )

    def _cancel(self, params: dict[str, Any]) -> ToolResult:
        job_id = params["job_id"]
        job = self._jobs.get(job_id)
        if job is None:
            return ToolResult(success=False, error=f"Unknown job_id: {job_id}")
        if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            if job.status == JobStatus.RUNNING:
                self._running_count = max(0, self._running_count - 1)
            job.status = JobStatus.CANCELLED
        return ToolResult(
            success=True,
            output={"job_id": job_id, "status": job.status.value},
        )

    async def _wait(self, params: dict[str, Any]) -> ToolResult:
        job_id = params["job_id"]
        timeout = params.get("timeout_seconds", 60.0)

        job = self._jobs.get(job_id)
        if job is None:
            return ToolResult(success=False, error=f"Unknown job_id: {job_id}")

        elapsed = 0.0
        poll = 0.2
        while job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            await asyncio.sleep(poll)
            elapsed += poll
            if elapsed >= timeout:
                return ToolResult(
                    success=False,
                    error=f"Timeout waiting for job {job_id} after {timeout}s.",
                    metadata={"status": job.status.value},
                )

        return ToolResult(
            success=job.status == JobStatus.COMPLETED,
            output={
                "job_id": job_id,
                "status": job.status.value,
                "wall_time": job.wall_time,
                "result": job.result,
            },
        )

    def _list(self) -> ToolResult:
        summary = [
            {"job_id": j.job_id, "status": j.status.value, "wall_time": j.wall_time}
            for j in self._jobs.values()
        ]
        return ToolResult(
            success=True,
            output={
                "jobs": summary,
                "total": len(summary),
                "running": self._running_count,
            },
        )

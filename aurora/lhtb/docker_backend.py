"""LHTB-specific Docker backend — thin wrapper over core DockerBackend.

Creates a ``core.docker_backend.DockerBackend`` from an ``LHTBTask``,
mapping task-specific fields (docker_image, cpus, memory, tests_dir)
to the generic backend's parameters.
"""

from __future__ import annotations

from pathlib import Path

from aurora.core.docker_backend import DockerBackend as _CoreDockerBackend
from aurora.lhtb.task_loader import LHTBTask


class DockerBackend(_CoreDockerBackend):
    """DockerBackend initialized from an LHTBTask."""

    def __init__(self, task: LHTBTask) -> None:
        tests_dir = ""
        if task.task_dir and (task.task_dir / "tests").is_dir():
            tests_dir = str(task.task_dir / "tests")

        super().__init__(
            docker_image=task.docker_image or "python:3.11-slim",
            cpus=task.cpus,
            memory_mb=task.memory_mb,
            allow_network=task.allow_internet,
            tests_dir=tests_dir,
            build_timeout=task.build_timeout_sec,
        )
        self._task = task

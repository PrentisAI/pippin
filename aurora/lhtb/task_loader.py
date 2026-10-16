"""Load and parse LHTB task definitions.

Reads ``task.toml`` and ``instruction.md`` from a task directory and
produces a structured :class:`LHTBTask` that the runner can consume.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LHTBTask:
    """Parsed LHTB task definition."""

    task_dir: Path
    name: str = ""
    description: str = ""
    instruction: str = ""

    # From task.toml
    difficulty: str = "unknown"
    category: str = ""
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)

    # Timeouts
    agent_timeout_sec: float = 3600.0
    verifier_timeout_sec: float = 600.0
    build_timeout_sec: float = 1800.0

    # Resources
    cpus: int = 2
    memory_mb: int = 4096
    storage_mb: int = 8192
    gpus: int = 0
    allow_internet: bool = True

    # Docker
    docker_image: str = ""
    continue_until_timeout: bool = False

    # Expert time estimates
    expert_time_estimate_min: float = 0.0
    junior_time_estimate_min: float = 0.0


def load_task(task_dir: str | Path) -> LHTBTask:
    """Load an LHTB task from its directory.

    Parameters
    ----------
    task_dir:
        Path to the task directory containing ``task.toml`` and ``instruction.md``.

    Returns
    -------
    LHTBTask
        Parsed task definition.
    """
    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        raise FileNotFoundError(f"Task directory not found: {task_dir}")

    task = LHTBTask(task_dir=task_dir, name=task_dir.name)

    # Parse task.toml
    toml_path = task_dir / "task.toml"
    if toml_path.exists():
        _parse_toml(task, toml_path)

    # Read instruction.md
    instruction_path = task_dir / "instruction.md"
    if instruction_path.exists():
        task.instruction = instruction_path.read_text(encoding="utf-8")
    else:
        logger.warning("No instruction.md found in %s", task_dir)

    return task


def _parse_toml(task: LHTBTask, path: Path) -> None:
    """Parse task.toml into the LHTBTask fields."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    # Top-level
    task.artifacts = raw.get("artifacts", [])

    # [task] section
    task_sec = raw.get("task", {})
    task.name = task_sec.get("name", task.name)
    task.description = task_sec.get("description", "")
    task.keywords = task_sec.get("keywords", [])

    # [metadata]
    meta = raw.get("metadata", {})
    task.difficulty = meta.get("difficulty", "unknown")
    task.category = meta.get("category", "")
    task.tags = meta.get("tags", [])
    task.expert_time_estimate_min = meta.get("expert_time_estimate_min", 0.0)
    task.junior_time_estimate_min = meta.get("junior_time_estimate_min", 0.0)

    # [agent]
    agent_sec = raw.get("agent", {})
    task.agent_timeout_sec = agent_sec.get("timeout_sec", 3600.0)
    task.continue_until_timeout = agent_sec.get("continue_until_timeout", False)

    # [verifier]
    verifier_sec = raw.get("verifier", {})
    task.verifier_timeout_sec = verifier_sec.get("timeout_sec", 600.0)

    # [environment]
    env_sec = raw.get("environment", {})
    task.docker_image = env_sec.get("docker_image", "")
    task.build_timeout_sec = env_sec.get("build_timeout_sec", 1800.0)
    task.cpus = env_sec.get("cpus", 2)
    task.memory_mb = env_sec.get("memory_mb", 4096)
    task.storage_mb = env_sec.get("storage_mb", 8192)
    task.gpus = env_sec.get("gpus", 0)
    task.allow_internet = env_sec.get("allow_internet", True)

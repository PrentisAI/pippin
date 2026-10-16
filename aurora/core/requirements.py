"""Startup checks for what a campaign needs before it starts.

Both entry points (``aurora run`` and the web console) go through
``ResearchEngine.run_campaign``, which calls :func:`check_runtime_requirements`
first, so a missing Claude Code CLI, Docker daemon or API key stops the
campaign at once with instructions instead of failing (or silently degrading)
somewhere in the middle of a run.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from aurora.config import AuroraConfig

logger = logging.getLogger(__name__)

_MOCK_HINT = (
    "To try the pipeline offline without any of these, set `mock_mode: true` "
    "in the campaign config (mock models and tools, no Claude Code, no Docker, "
    "no API key)."
)


class RuntimeRequirementError(RuntimeError):
    """A dependency the configured campaign needs is missing."""


def _docker_ok() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "the `docker` command is not on PATH"
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"`docker info` failed ({exc})"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, f"the Docker daemon is not reachable ({detail[-1] if detail else 'no output'})"
    return True, ""


def check_runtime_requirements(config: AuroraConfig) -> None:
    """Raise :class:`RuntimeRequirementError` listing every missing dependency."""
    if config.mock_mode:
        return

    problems: list[str] = []
    if shutil.which("claude") is None:
        problems.append(
            "Claude Code CLI not found (every phase runs as a Claude Code session). Install it with "
            "`npm install -g @anthropic-ai/claude-code` (Node.js 18+) and check that "
            "`claude --version` works in the shell that starts AURORA."
        )

    if config.sandbox.docker:
        ok, why = _docker_ok()
        if not ok:
            problems.append(
                f"Docker is required because `sandbox.docker` is true, but {why}. Install Docker "
                "(https://docs.docker.com/engine/install/) and make sure this user can run "
                "`docker info`; or set `sandbox.docker: false` (the agents can then only read and "
                "write files in the workspace)."
            )
    elif not config.sandbox.allow_host_bash:
        logger.warning(
            "sandbox.docker is false: the agents can read and write files in the workspace but "
            "cannot run commands (set sandbox.allow_host_bash: true to allow commands on this machine)."
        )

    if not config.api_key_value:
        problems.append(
            "No API key. Put `OPENROUTER_API_KEY=...` in a `.env` file (in the current directory or "
            "in ~/.aurora/) or export it in the shell, or set `api_key` in the config."
        )

    for item in config.inputs:
        if not Path(item).expanduser().exists():
            problems.append(f"Input not found: {item}")
    for d in config.skills_dirs:
        if not Path(d).expanduser().is_dir():
            problems.append(f"skills_dirs entry is not a directory: {d}")

    if problems:
        lines = "\n".join(f"  - {p}" for p in problems)
        raise RuntimeRequirementError(
            f"Campaign '{config.campaign_id}' cannot start:\n{lines}\n{_MOCK_HINT}"
        )

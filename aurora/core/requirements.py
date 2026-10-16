"""Startup checks for what a campaign needs before it starts.

Both entry points (``aurora run`` and the web console) go through
``ResearchEngine.run_campaign``, which calls :func:`check_runtime_requirements`
first, so a missing Claude Code CLI, Docker daemon or API key stops the
campaign at once with instructions instead of failing (or silently degrading)
somewhere in the middle of a run.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from aurora.config import AuroraConfig

logger = logging.getLogger(__name__)

_MOCK_HINT = (
    "To try the pipeline offline without any of these, set `mock_mode: true` "
    "in the campaign config (mock models and tools, no Claude Code, no Docker, "
    "no API key)."
)


class RuntimeRequirementError(RuntimeError):
    """A dependency the configured campaign needs is missing."""


def resolve_terminal_driver(config: AuroraConfig) -> str:
    """The agent driver the pipeline phases will use (mirrors TerminalIteration)."""
    driver = config.terminal_driver
    if driver != "auto":
        return driver
    base = config.terminal_base_url
    if not base or "anthropic.com" in base:
        return "anthropic"
    return "claude-code"


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


def _has_api_key(config: AuroraConfig, driver: str) -> bool:
    if config.terminal_api_key:
        return True
    if config.use_terminal_iteration and driver in ("claude-code", "openai"):
        # the agent sessions authenticate with the OpenRouter key specifically
        return bool(os.environ.get("OPENROUTER_API_KEY"))
    if any(getattr(p, "api_key", "") for p in config.llm_providers):
        return True
    return any(os.environ.get(n) for n in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"))


def check_runtime_requirements(config: AuroraConfig) -> None:
    """Raise :class:`RuntimeRequirementError` listing every missing dependency."""
    if config.mock_mode:
        return

    problems: list[str] = []
    driver = resolve_terminal_driver(config)

    if config.use_terminal_iteration and driver == "claude-code":
        if shutil.which("claude") is None:
            problems.append(
                "Claude Code CLI not found (the pipeline phases run as Claude Code "
                "sessions). Install it with `npm install -g @anthropic-ai/claude-code` "
                "(Node.js 18+), then check that `claude --version` works."
            )

    if config.use_docker:
        ok, why = _docker_ok()
        if not ok:
            problems.append(
                f"Docker is required because `use_docker: true`, but {why}. Install "
                "Docker (https://docs.docker.com/engine/install/) and make sure this "
                "user can run `docker info`; or set `use_docker: false` to run without "
                "a container (Claude Code sessions are then limited to reading and "
                "editing files in the workspace)."
            )
    elif config.use_terminal_iteration and not config.allow_host_bash:
        logger.warning(
            "use_docker is false: Claude Code sessions can read and edit files in the "
            "workspace but cannot run shell commands (set allow_host_bash: true to "
            "allow commands on the host)."
        )

    if not _has_api_key(config, driver):
        key = "OPENROUTER_API_KEY" if driver in ("claude-code", "openai") else "ANTHROPIC_API_KEY"
        problems.append(
            f"No API key found. Put `{key}=...` in the `.env` file at the project "
            "root (it is loaded automatically) or export it in the shell, or set "
            "`terminal_api_key` in the config."
        )

    if problems:
        lines = "\n".join(f"  - {p}" for p in problems)
        raise RuntimeRequirementError(
            f"Campaign '{config.campaign_id}' cannot start:\n{lines}\n{_MOCK_HINT}"
        )

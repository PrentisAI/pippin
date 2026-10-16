"""Docker sandboxes for the Claude Code sessions of the auditor evaluations.

Each session runs in its own container that mounts only its run directory (read-write) and the
host's Claude Code binary (read-only).  The container cannot see the dataset, the labels or the
future of a replayed trajectory.  The API key reaches the container through a 0600 env file
that is deleted when the session ends.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

CLAUDE_IN_CONTAINER = "/opt/claude/claude"


def claude_binary(explicit: str | None = None) -> Path:
    """The host's Claude Code executable (a self-contained binary that also runs in the container)."""
    found = explicit or os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not found:
        raise SystemExit("Claude Code CLI not found; install it or pass --claude-bin / set CLAUDE_BIN.")
    return Path(os.path.realpath(found))


def container_user() -> str:
    """Run the session as the invoking user so its files stay readable on the host."""
    return f"{os.getuid()}:{os.getgid()}"


def claude_env(api_key: str, model: str, base_url: str = "https://openrouter.ai/api") -> dict[str, str]:
    """Environment of a sandboxed Claude Code session that talks to OpenRouter with *model*."""
    return {
        "HOME": "/tmp/home",
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "CLAUDE_CODE_SUBAGENT_MODEL": model,
    }


def write_env_file(path: Path, env: dict[str, str]) -> Path:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("".join(f"{k}={v}\n" for k, v in env.items()))
    return path


def start_sandbox(run_dir: Path, image: str, claude_bin: Path, *, cpus: str, memory: str) -> str:
    """Start a detached container that mounts *run_dir* at the same path; return its ID."""
    run_dir = Path(run_dir).resolve()
    cid = subprocess.check_output([
        "docker", "run", "-d", "--rm", "--cpus", cpus, "--memory", memory,
        "-v", f"{run_dir}:{run_dir}", "-v", f"{claude_bin}:{CLAUDE_IN_CONTAINER}:ro",
        image, "sleep", "infinity",
    ], text=True).strip()
    subprocess.run(["docker", "exec", cid, "sh", "-c", "mkdir -p /tmp/home && chmod 777 /tmp/home"], check=True)
    return cid


def stop_sandbox(cid: str) -> None:
    subprocess.run(["docker", "rm", "-f", cid], capture_output=True)

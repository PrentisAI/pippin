"""Command-line interface for AURORA.

Subcommands
-----------
run     — Start a new research campaign from a YAML configuration file.
status  — Print the current state of an existing campaign workspace.
resume  — Resume execution from the last checkpoint.
audit   — Manually trigger an audit of the current research trajectory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aurora import __version__


def _cmd_run(args: argparse.Namespace) -> None:
    """Start a research campaign."""
    import asyncio
    import logging

    from aurora.config import load_config
    from aurora.core.engine import ResearchEngine
    from aurora.core.requirements import RuntimeRequirementError

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config = load_config(args.config)
    print(f"[AURORA] Starting campaign '{config.campaign_id}'"
          + ("  (mock mode)" if config.mock_mode else ""))
    print(f"  Objective : {config.objective.strip()[:120]}")
    print(f"  Domain    : {config.domain}")
    print(f"  Workspace : {config.workspace.workspace_root}")
    print(f"  Seats     : {config.num_roundtable_seats}")
    print()

    engine = ResearchEngine(config)
    try:
        result = asyncio.run(engine.run_campaign())
    except RuntimeRequirementError as exc:
        print(f"[AURORA] {exc}", file=sys.stderr)
        sys.exit(2)

    print()
    print("=" * 60)
    print(f"[AURORA] Campaign {result.status.upper()}")
    print(f"  Iterations     : {len(result.iterations)}")
    print(f"  Follow-up cycles: {result.research_cycles}")
    print(f"  Wall time      : {result.total_wall_time_seconds:.2f}s")
    if result.manuscript_path:
        print(f"  Manuscript     : {result.manuscript_path}")
    if result.proposal:
        print(f"  Proposal       : {result.proposal.title}")
    print(f"  Summary        : {result.summary}")
    print("=" * 60)

    if result.status != "completed":
        sys.exit(1)


def _cmd_status(args: argparse.Namespace) -> None:
    """Print current campaign status."""
    workspace = Path(args.workspace)
    if not workspace.exists():
        print(f"[AURORA] Workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    artifacts_path = workspace / "artifacts.json"
    if artifacts_path.exists():
        from aurora.models.artifacts import ResearchArtifacts

        artifacts = ResearchArtifacts.model_validate_json(artifacts_path.read_text())
        print(f"[AURORA] Campaign status")
        print(f"  Objective    : {artifacts.initial_objective}")
        print(f"  Current goal : {artifacts.current_goal}")
        print(f"  Experiments  : {len(artifacts.experiments)} total")
        print(f"  Observations : {len(artifacts.observations)} total")
        print(f"  Claims       : {len(artifacts.claims)} total")
        print(f"  Open questions: {len(artifacts.open_questions)}")
    else:
        print(f"[AURORA] No artifacts found in {workspace}")


def _cmd_resume(args: argparse.Namespace) -> None:
    """Resume a campaign from its last checkpoint."""
    import asyncio
    import json
    import logging

    from aurora.config import load_config
    from aurora.core.engine import ResearchEngine
    from aurora.core.requirements import RuntimeRequirementError

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    workspace = Path(args.workspace)
    if not workspace.exists():
        print(f"[AURORA] Workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    # Load config from the workspace's checkpoint
    checkpoint_path = workspace / "checkpoint.json"
    if not checkpoint_path.exists():
        print(f"[AURORA] No checkpoint found in {workspace}", file=sys.stderr)
        sys.exit(1)

    config_path = args.config
    if not config_path:
        print("[AURORA] --config is required for resume", file=sys.stderr)
        sys.exit(1)

    config = load_config(config_path)
    print(f"[AURORA] Resuming campaign '{config.campaign_id}' from {workspace}")

    engine = ResearchEngine(config)
    try:
        result = asyncio.run(engine.run_campaign(resume=True))
    except RuntimeRequirementError as exc:
        print(f"[AURORA] {exc}", file=sys.stderr)
        sys.exit(2)

    print()
    print(f"[AURORA] Campaign {result.status.upper()}")
    print(f"  Summary: {result.summary}")


def _cmd_audit(args: argparse.Namespace) -> None:
    """Manually trigger an audit."""
    workspace = Path(args.workspace)
    if not workspace.exists():
        print(f"[AURORA] Workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)
    print(f"[AURORA] Triggering manual audit for {workspace}")
    print("[AURORA] Manual audit not yet implemented.")


def _cmd_lhtb(args: argparse.Namespace) -> None:
    """Run an LHTB benchmark task."""
    import asyncio
    import logging
    import os

    from aurora.lhtb.task_loader import load_task
    from aurora.lhtb.runner import LHTBRunner
    from aurora.llm.tool_runner import StreamEvent

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    api_key = (
        args.api_key
        or os.environ.get("OPENROUTER_API_KEY", "")
        or os.environ.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not api_key:
        print("Error: No API key. Set --api-key, OPENROUTER_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY.", file=sys.stderr)
        sys.exit(1)

    task = load_task(args.task_dir)
    print(f"[AURORA LHTB] Task: {task.name}")
    print(f"  Category   : {task.category}")
    print(f"  Difficulty : {task.difficulty}")
    print(f"  Artifacts  : {len(task.artifacts)} required")
    print(f"  Docker     : {task.docker_image or '(none — local mode)'}")
    print(f"  Timeout    : {args.timeout or task.agent_timeout_sec}s")
    print(f"  Loop mode  : {'continue-until-timeout' if task.continue_until_timeout else 'single-shot'}")
    print(f"  Driver     : {args.driver}")
    print(f"  Model      : {args.model}")
    print()

    async def _stream(event: StreamEvent) -> None:
        d = event.data
        t = event.event_type
        if t == "reasoning":
            print(f"  [think] {d.get('text', '')[:200]}")
        elif t == "tool_call_start":
            print(f"  [tool]  {d.get('tool_name', '?')} ...", end="", flush=True)
        elif t == "tool_call_result":
            print(f" ({d.get('duration', 0):.1f}s)")
        elif t == "agent_done":
            print(f"  [done]  Agent finished")
        elif t == "phase_started":
            print(f"\n--- Phase {d.get('phase', 0)} "
                  f"(elapsed {d.get('elapsed', 0):.0f}s, "
                  f"remaining {d.get('remaining', 0):.0f}s) ---")
        elif t == "verifier_started":
            print(f"  [verify] Running verifier...")
        elif t == "verifier_result":
            status = "PASSED" if d.get("passed") else "FAILED"
            print(f"  [verify] {status} (reward: {d.get('reward', 0):.2%})")
        elif t == "docker_started":
            print(f"  [docker] Container {d.get('container_id', '?')} started")
        elif t == "error":
            print(f"  [ERR]   {d.get('error', '?')}")

    runner = LHTBRunner(
        task,
        model=args.model,
        api_key=api_key,
        base_url=args.base_url,
        driver=args.driver,
        use_docker=not args.no_docker,
        working_dir=args.working_dir,
        max_turns=args.max_turns,
        timeout_sec=args.timeout,
        stream_callback=_stream,
        allow_host_bash=args.allow_host_bash,
    )

    result = asyncio.run(runner.run())

    print()
    print("=" * 60)
    print(f"[AURORA LHTB] {result.status.upper()}")
    print(f"  Task       : {result.task_name}")
    print(f"  Phases     : {len(result.phases)}")
    print(f"  Turns      : {result.total_turns}")
    print(f"  Tokens     : {result.total_tokens:,}")
    print(f"  Wall time  : {result.wall_time_seconds:.1f}s")
    print(f"  Reward     : {result.final_reward:.2%}")
    print(f"  Artifacts  : {len(result.artifacts_found)}/{len(result.artifacts_found) + len(result.artifacts_missing)}")
    if result.artifacts_missing:
        print(f"  Missing    : {', '.join(result.artifacts_missing)}")
    if result.error:
        print(f"  Error      : {result.error}")
    print("=" * 60)

    if result.status not in ("completed", "partial"):
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="aurora",
        description=(
            "AURORA — an agent harness for long-horizon open-ended "
            "AutoResearch in computational materials science."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- run ---
    run_parser = subparsers.add_parser("run", help="Start a new research campaign.")
    run_parser.add_argument(
        "--config",
        required=True,
        help="Path to the YAML campaign configuration file.",
    )
    run_parser.set_defaults(func=_cmd_run)

    # --- status ---
    status_parser = subparsers.add_parser("status", help="Show campaign status.")
    status_parser.add_argument(
        "--workspace",
        required=True,
        help="Path to the campaign workspace directory.",
    )
    status_parser.set_defaults(func=_cmd_status)

    # --- resume ---
    resume_parser = subparsers.add_parser("resume", help="Resume from checkpoint.")
    resume_parser.add_argument(
        "--workspace",
        required=True,
        help="Path to the campaign workspace directory.",
    )
    resume_parser.add_argument(
        "--config",
        required=True,
        help="Path to the YAML campaign configuration file.",
    )
    resume_parser.set_defaults(func=_cmd_resume)

    # --- audit ---
    audit_parser = subparsers.add_parser("audit", help="Trigger a manual audit.")
    audit_parser.add_argument(
        "--workspace",
        required=True,
        help="Path to the campaign workspace directory.",
    )
    audit_parser.set_defaults(func=_cmd_audit)

    # --- lhtb ---
    lhtb_parser = subparsers.add_parser(
        "lhtb", help="Run an LHTB benchmark task.",
    )
    lhtb_parser.add_argument(
        "--task-dir", required=True,
        help="Path to the LHTB task directory.",
    )
    lhtb_parser.add_argument(
        "--model", default="anthropic/claude-sonnet-4",
        help="Model identifier (default: anthropic/claude-sonnet-4).",
    )
    lhtb_parser.add_argument(
        "--api-key", default="",
        help="API key (falls back to OPENROUTER_API_KEY / OPENAI_API_KEY).",
    )
    lhtb_parser.add_argument(
        "--base-url", default="https://openrouter.ai/api/v1",
        help="API base URL (default: OpenRouter).",
    )
    lhtb_parser.add_argument(
        "--driver", default="auto",
        choices=["auto", "claude-code", "openai"],
        help="Agent driver: claude-code (Claude Code CLI), openai (OpenAI-compatible loop), auto.",
    )
    lhtb_parser.add_argument(
        "--no-docker", action="store_true",
        help="Run locally without Docker (default: use Docker if task has an image).",
    )
    lhtb_parser.add_argument(
        "--allow-host-bash", action="store_true",
        help="With --no-docker and the claude-code driver, let the agent run shell "
             "commands on the host (by default it may only read/edit the working dir).",
    )
    lhtb_parser.add_argument(
        "--working-dir", default=None,
        help="Override working directory for local execution.",
    )
    lhtb_parser.add_argument(
        "--max-turns", type=int, default=200,
        help="Maximum agentic loop turns per phase.",
    )
    lhtb_parser.add_argument(
        "--timeout", type=float, default=None,
        help="Override task timeout in seconds.",
    )
    lhtb_parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging.",
    )
    lhtb_parser.set_defaults(func=_cmd_lhtb)

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    from aurora.env import load_dotenv

    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)

    args.func(args)

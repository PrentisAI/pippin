"""LHTB task adapter — converts task.yaml/task.toml into engine inputs.

This is the only LHTB-specific glue in core: it translates an LHTB task
definition into a ``RoundtableProposal`` (so screening/planning have
input without running Round Table) and a ``AuroraConfig``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from aurora.config import LLMModelAssignment, LLMProviderConfig, AuroraConfig
from aurora.lhtb.task_loader import LHTBTask
from aurora.models.roundtable import RoundtableProposal


LHTB_DEFAULT_SKIP_PHASES = ["roundtable", "screening", "writing"]


class LHTBTaskAdapter:
    """Convert an ``LHTBTask`` into core engine inputs."""

    @staticmethod
    def to_proposal(task: LHTBTask) -> RoundtableProposal:
        """Generate a ``RoundtableProposal`` from task metadata.

        This replaces the multi-agent Round Table deliberation for
        LHTB tasks, where the task is already fully specified.
        """
        return RoundtableProposal(
            proposal_id=f"lhtb_{task.name}_{uuid.uuid4().hex[:6]}",
            title=f"LHTB: {task.name}",
            description=task.description or task.instruction[:500],
            research_questions=[
                f"Complete the LHTB benchmark task: {task.name}",
            ],
            proposed_methods=[
                f"Category: {task.category}",
                f"Difficulty: {task.difficulty}",
                f"Docker image: {task.docker_image or 'local'}",
            ],
            expected_outcomes=[
                f"Artifact: {a}" for a in task.artifacts
            ] if task.artifacts else ["Task completion"],
            selected=True,
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def to_config(
        task: LHTBTask,
        *,
        model: str = "",
        driver: str = "claude-code",
        api_key: str = "",
        base_url: str = "https://openrouter.ai/api/v1",
        skip_phases: list[str] | None = None,
        max_turns: int = 200,
    ) -> AuroraConfig:
        """Build a ``AuroraConfig`` for running an LHTB task.

        Parameters
        ----------
        skip_phases:
            Phases to skip. If None, uses LHTB default
            ``["roundtable", "writing"]``.
            Pass an explicit list to override completely.
        """
        effective_skip = (
            skip_phases if skip_phases is not None
            else list(LHTB_DEFAULT_SKIP_PHASES)
        )

        effective_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        effective_model = model or "deepseek/deepseek-v4-flash"

        # Docker config from task
        docker_image = task.docker_image or "python:3.11-slim"
        tests_dir = ""
        if task.task_dir and (task.task_dir / "tests").is_dir():
            tests_dir = str(task.task_dir / "tests")

        return AuroraConfig(
            campaign_id=f"lhtb_{task.name}_{uuid.uuid4().hex[:6]}",
            objective=task.instruction[:2000] if task.instruction else task.name,
            domain="lhtb_benchmark",
            skip_phases=effective_skip,
            use_terminal_iteration=True,
            terminal_driver=driver,
            terminal_model=effective_model,
            terminal_api_key=effective_key,
            terminal_base_url=base_url,
            terminal_timeout_sec=task.agent_timeout_sec,
            terminal_max_turns=max_turns,
            continue_until_timeout=task.continue_until_timeout,
            max_research_iterations=1,
            # Docker execution environment from task
            use_docker=bool(task.docker_image),
            docker_image=docker_image,
            docker_cpus=task.cpus,
            docker_memory_mb=task.memory_mb,
            docker_network=task.allow_internet,
            docker_tests_dir=tests_dir,
            llm_providers=[
                LLMProviderConfig(
                    provider="openrouter",
                    api_key=effective_key,
                    base_url=base_url,
                ),
            ],
            llm_assignment=LLMModelAssignment(
                idea_screening=effective_model,
                researcher=effective_model,
                auditor_triage=effective_model,
                auditor_deep=effective_model,
                analyzer=effective_model,
                roundtable_chair=effective_model,
                roundtable_seat=[effective_model],
                writer=effective_model,
            ),
        )

    @staticmethod
    def build_system_prompt(task: LHTBTask) -> str:
        """Build the system prompt for the LHTB task agent."""
        artifacts_list = "\n".join(
            f"  - {a}" for a in task.artifacts
        ) if task.artifacts else "  (none specified)"

        return f"""You are an expert software engineer and scientist solving a benchmark task.

## Task Information
- **Name**: {task.name}
- **Category**: {task.category}
- **Difficulty**: {task.difficulty}
- **Keywords**: {', '.join(task.keywords or task.tags)}

## Required Output Artifacts
{artifacts_list}

## Constraints
- **DO NOT read, cat, or access any files under `/tests/`.**
  The `/tests/` directory contains the grader/verifier — reading it is not allowed.
  You must solve the task based on the instructions and source code, not by
  reverse-engineering the test suite.
- **DO NOT modify `artifacts.json`** in the workspace root. It is managed by the
  pipeline engine. Corrupting it will crash the pipeline.

## Guidelines
- Read files before editing them.
- Run scripts after changes to verify correctness.
- Diagnose errors before retrying.
- Work methodically — fix one issue at a time and verify.
- When all outputs look correct, call task_complete.
"""

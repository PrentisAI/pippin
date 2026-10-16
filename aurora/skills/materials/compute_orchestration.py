"""Skill 7: Compute Orchestration (Table 1 — row 7).

Manages the lifecycle of a batch of DFT / MLIP calculations through
the HPC tool:

  1. Submit a set of jobs from an experiment plan.
  2. Monitor completion / failure.
  3. Collect results and detect stragglers or dead loops.
  4. Return an aggregated result set ready for analysis.

This skill is the primary consumer of the ``hpc`` and ``mock_dft`` /
``mock_mlip`` tools and mirrors Section H.3 (resource management).
"""

from __future__ import annotations

from typing import Any

from aurora.skills.base import (
    FailureMode,
    Skill,
    SkillContext,
    SkillInput,
    SkillOutput,
    SkillResult,
    ValidationCheck,
    ValidationResult,
)

# Default wall-time cap per job (seconds), mirroring resource limits
_DEFAULT_WALLTIME_CAP = 120.0
_MAX_RETRIES = 2


class ComputeOrchestrationSkill(Skill):
    """Submit, monitor and collect a batch of compute jobs."""

    name = "compute_orchestration"
    description = (
        "Orchestrate a batch of DFT/MLIP calculations via HPC: "
        "submit, monitor, retry on failure, and aggregate results."
    )
    required_tools = ["hpc"]

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    async def validate_input(
        self, inp: SkillInput, ctx: SkillContext
    ) -> ValidationResult:
        checks: list[ValidationCheck] = []
        p = inp.parameters

        jobs = p.get("jobs", [])
        checks.append(
            ValidationCheck(
                name="jobs_nonempty",
                passed=len(jobs) > 0,
                message="Must provide a non-empty 'jobs' list.",
            )
        )

        # Each job should have at minimum a 'tool' and 'params' key
        for i, job in enumerate(jobs):
            checks.append(
                ValidationCheck(
                    name=f"job_{i}_tool",
                    passed="tool" in job,
                    message=f"Job {i} missing 'tool' key.",
                )
            )
            checks.append(
                ValidationCheck(
                    name=f"job_{i}_params",
                    passed="params" in job,
                    message=f"Job {i} missing 'params' key.",
                )
            )

        return ValidationResult.from_checks(checks)

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    async def execute(
        self, inp: SkillInput, ctx: SkillContext
    ) -> SkillResult:
        p = inp.parameters
        hpc = ctx.get_tool("hpc")
        tool_results = []

        jobs_spec: list[dict[str, Any]] = p["jobs"]
        walltime_cap: float = p.get("walltime_cap", _DEFAULT_WALLTIME_CAP)
        max_retries: int = p.get("max_retries", _MAX_RETRIES)
        compute_method: str = p.get("method", "dft")  # for labelling

        # Phase 1: submit all jobs
        submitted: list[dict[str, Any]] = []
        for i, spec in enumerate(jobs_spec):
            tool_name = spec["tool"]  # e.g. "mock_dft" or "mock_mlip"
            job_params = spec["params"]

            r_submit = await hpc.execute({
                "operation": "submit",
                "job_params": {
                    "tool": tool_name,
                    "params": job_params,
                    "estimated_wall_seconds": walltime_cap,
                },
            })
            tool_results.append(r_submit)

            if r_submit.success:
                submitted.append({
                    "index": i,
                    "job_id": r_submit.output["job_id"],
                    "tool": tool_name,
                    "params": job_params,
                    "retries": 0,
                })

        # Phase 2: wait for all jobs with timeout
        completed_results: list[dict[str, Any]] = []
        failed_jobs: list[dict[str, Any]] = []

        for entry in submitted:
            r_wait = await hpc.execute({
                "operation": "wait",
                "job_id": entry["job_id"],
                "timeout_seconds": walltime_cap + 10,
            })
            tool_results.append(r_wait)

            if r_wait.success:
                # Now run the actual tool to get the real result
                actual_tool = ctx.get_tool(entry["tool"])
                r_calc = await actual_tool.execute(entry["params"])
                tool_results.append(r_calc)

                if r_calc.success:
                    completed_results.append({
                        "index": entry["index"],
                        "job_id": entry["job_id"],
                        "output": r_calc.output,
                        "wall_time": r_calc.wall_time_seconds,
                    })
                else:
                    # Calculation itself failed — retry
                    entry["last_error"] = r_calc.error
                    failed_jobs.append(entry)
            else:
                # HPC wait timed out or job failed
                entry["last_error"] = r_wait.error
                failed_jobs.append(entry)

        # Phase 3: retry failed jobs
        still_failed: list[dict[str, Any]] = []
        for entry in failed_jobs:
            if entry["retries"] >= max_retries:
                still_failed.append(entry)
                continue

            entry["retries"] += 1
            actual_tool = ctx.get_tool(entry["tool"])
            r_retry = await actual_tool.execute(entry["params"])
            tool_results.append(r_retry)

            if r_retry.success:
                completed_results.append({
                    "index": entry["index"],
                    "job_id": entry["job_id"],
                    "output": r_retry.output,
                    "wall_time": r_retry.wall_time_seconds,
                    "retried": True,
                })
            else:
                entry["last_error"] = r_retry.error
                still_failed.append(entry)

        # Assemble summary
        n_total = len(jobs_spec)
        n_ok = len(completed_results)
        n_fail = len(still_failed)
        success_rate = n_ok / max(n_total, 1)

        failure_mode = None
        remediation = ""
        if n_fail > 0:
            failure_mode = FailureMode.SCF_NOT_CONVERGED
            errors = [f.get("last_error", "unknown") for f in still_failed]
            remediation = (
                f"{n_fail}/{n_total} jobs failed after {max_retries} retries. "
                f"Errors: {errors[:3]}"
            )

        return SkillResult(
            success=n_ok > 0,  # partial success is still success
            output=SkillOutput(
                data={
                    "method": compute_method,
                    "n_submitted": n_total,
                    "n_completed": n_ok,
                    "n_failed": n_fail,
                    "success_rate": round(success_rate, 3),
                    "results": completed_results,
                    "failed": [
                        {
                            "index": f["index"],
                            "error": f.get("last_error"),
                            "retries": f["retries"],
                        }
                        for f in still_failed
                    ],
                },
                summary=(
                    f"Batch {compute_method}: {n_ok}/{n_total} completed, "
                    f"{n_fail} failed (success rate {success_rate:.0%})."
                ),
            ),
            tool_results=tool_results,
            failure_mode=failure_mode,
            remediation_hint=remediation,
        )

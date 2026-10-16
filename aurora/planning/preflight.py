"""Pre-flight verification for experiment plans.

The :class:`PreflightVerifier` runs a battery of checks on each job in an
:class:`ExperimentPlan` *before* any computation begins.  This catches
configuration errors — missing skills, empty parameter dicts, unreasonable
resource requests — cheaply, rather than burning GPU hours on a doomed run.

Checks performed per job:

1. **skill_exists** — the job's ``skill_name`` is present in the
   :class:`SkillRegistry`.
2. **params_present** — the ``parameters`` dict is non-empty.
3. **resources_satisfiable** — GPU counts, memory, and wall-time are within
   sane bounds.
4. **dependencies_valid** — every declared dependency references a real job
   in the same plan.
5. **smoke_run** — the skill's ``validate_input`` method accepts the
   parameters (requires a :class:`SkillContext`).
"""

from __future__ import annotations

import logging
from typing import Any

from aurora.models.experiment import ExperimentPlan, PreflightResult
from aurora.skills.base import SkillContext, SkillInput, ValidationResult
from aurora.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

# Hard upper bounds used by the "resources_satisfiable" check.
_MAX_GPUS = 64
_MAX_MEMORY_GB = 2048
_MAX_WALL_TIME_HOURS = 168  # one week


class PreflightVerifier:
    """Validates an experiment plan before execution begins.

    Parameters
    ----------
    skill_registry:
        Registry of available skills — used to verify that each job
        references a real skill and to run input-validation smoke tests.
    skill_context:
        Optional context forwarded to skill ``validate_input`` calls.
        If ``None``, the smoke-run check is skipped gracefully.
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        skill_context: SkillContext | None = None,
    ) -> None:
        self._skills = skill_registry
        self._ctx = skill_context

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def verify(self, plan: ExperimentPlan) -> list[PreflightResult]:
        """Run all preflight checks on every job in *plan*.

        Parameters
        ----------
        plan:
            The experiment plan to verify.

        Returns
        -------
        list[PreflightResult]
            One result per job, in the same order as ``plan.jobs``.
            A job passes only when *all* of its individual checks pass.
        """
        logger.info(
            "Starting preflight verification for plan %s (%d jobs)",
            plan.plan_id,
            len(plan.jobs),
        )

        job_ids_in_plan: set[str] = {j.job_id for j in plan.jobs}
        results: list[PreflightResult] = []

        for job in plan.jobs:
            checks: dict[str, bool] = {}
            diagnostics: list[str] = []

            # 1. Skill exists
            checks["skill_exists"] = self._check_skill_exists(
                job.skill_name, diagnostics,
            )

            # 2. Parameters present
            checks["params_present"] = self._check_params_present(
                job.parameters, diagnostics,
            )

            # 3. Resources satisfiable
            checks["resources_satisfiable"] = self._check_resources(
                job.resource_requirements, diagnostics,
            )

            # 4. Dependencies valid
            checks["dependencies_valid"] = self._check_dependencies(
                job.dependencies, job_ids_in_plan, diagnostics,
            )

            # 5. Smoke run (skill input validation)
            checks["smoke_run"] = await self._check_smoke_run(
                job.skill_name, job.parameters, diagnostics,
            )

            passed = all(checks.values())
            results.append(PreflightResult(
                job_id=job.job_id,
                passed=passed,
                checks=checks,
                diagnostics=diagnostics,
            ))

            level = logging.DEBUG if passed else logging.WARNING
            logger.log(
                level,
                "Preflight %s for job %s: %s",
                "PASSED" if passed else "FAILED",
                job.job_id,
                checks,
            )

        n_passed = sum(1 for r in results if r.passed)
        logger.info(
            "Preflight complete: %d/%d jobs passed", n_passed, len(results),
        )
        return results

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_skill_exists(
        self,
        skill_name: str,
        diagnostics: list[str],
    ) -> bool:
        """Verify that *skill_name* is registered."""
        if skill_name in self._skills:
            return True
        available = self._skills.list_skills()
        diagnostics.append(
            f"Skill {skill_name!r} not found in registry. "
            f"Available skills: {available}"
        )
        return False

    @staticmethod
    def _check_params_present(
        parameters: dict[str, Any],
        diagnostics: list[str],
    ) -> bool:
        """Verify that the parameter dict is non-empty."""
        if parameters:
            return True
        diagnostics.append(
            "Job has an empty parameters dict — the skill likely needs "
            "configuration values to execute."
        )
        return False

    @staticmethod
    def _check_resources(
        requirements: dict[str, Any],
        diagnostics: list[str],
    ) -> bool:
        """Verify that resource requirements are within sane bounds."""
        ok = True

        num_gpus = requirements.get("num_gpus", 0)
        if isinstance(num_gpus, (int, float)) and num_gpus > _MAX_GPUS:
            diagnostics.append(
                f"Requested {num_gpus} GPUs exceeds hard cap of {_MAX_GPUS}."
            )
            ok = False

        memory_gb = requirements.get("memory_gb", 0)
        if isinstance(memory_gb, (int, float)) and memory_gb > _MAX_MEMORY_GB:
            diagnostics.append(
                f"Requested {memory_gb} GB memory exceeds cap of {_MAX_MEMORY_GB} GB."
            )
            ok = False

        wall_time_h = requirements.get("wall_time_hours", 0)
        if isinstance(wall_time_h, (int, float)) and wall_time_h > _MAX_WALL_TIME_HOURS:
            diagnostics.append(
                f"Requested wall time {wall_time_h}h exceeds cap of "
                f"{_MAX_WALL_TIME_HOURS}h."
            )
            ok = False

        return ok

    @staticmethod
    def _check_dependencies(
        dependencies: list[str],
        valid_ids: set[str],
        diagnostics: list[str],
    ) -> bool:
        """Verify that all declared dependency IDs exist in the plan."""
        unknown = [d for d in dependencies if d not in valid_ids]
        if not unknown:
            return True
        diagnostics.append(
            f"Unknown dependency job IDs: {unknown}"
        )
        return False

    async def _check_smoke_run(
        self,
        skill_name: str,
        parameters: dict[str, Any],
        diagnostics: list[str],
    ) -> bool:
        """Run the skill's ``validate_input`` as a lightweight smoke test.

        If no :class:`SkillContext` is available, or the skill is not
        registered, the check passes vacuously (the corresponding checks
        above will have already flagged the issue).
        """
        if self._ctx is None:
            diagnostics.append(
                "Smoke run skipped — no SkillContext provided."
            )
            return True

        if skill_name not in self._skills:
            # Already flagged by skill_exists check
            return True

        skill = self._skills.get(skill_name)
        inp = SkillInput(skill_name=skill_name, parameters=parameters)

        try:
            result: ValidationResult = await skill.validate_input(inp, self._ctx)
        except Exception as exc:
            diagnostics.append(
                f"Smoke run raised {type(exc).__name__}: {exc}"
            )
            return False

        if not result.passed:
            failed_checks = [c for c in result.checks if not c.passed]
            for c in failed_checks:
                diagnostics.append(f"Smoke-run check {c.name!r} failed: {c.message}")
            return False

        return True

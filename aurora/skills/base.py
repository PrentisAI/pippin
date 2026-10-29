"""Skill abstraction layer (Section D / Table 1).

A *skill* is a domain-specific procedure that an agent can invoke to
accomplish a well-scoped research sub-task — e.g. building a slab model
or running a convergence test.  Each skill:

  1. Receives structured *SkillInput* from the calling agent.
  2. Validates preconditions (parameter sanity, prerequisite data).
  3. Orchestrates one or more *Tool* calls.
  4. Validates postconditions (physical plausibility, consistency).
  5. Returns a typed *SkillResult* with diagnostics and artifacts.

Skills are **stateless** — any persistent state lives in the workspace.
They may, however, carry a reference to the current *SkillContext* which
bundles the workspace path and the tool registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from aurora.tools.base import ToolResult

# ------------------------------------------------------------------
# Skill I/O models
# ------------------------------------------------------------------

class SkillInput(BaseModel):
    """Base input for every skill invocation."""

    skill_name: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    context: str | dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque context from the calling agent — may be a string summary or a structured dict.",
    )


class SkillOutput(BaseModel):
    """Structured output from a skill."""

    data: dict[str, Any] = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)
    summary: str = ""


class ValidationCheck(BaseModel):
    """A single validation assertion applied during pre/post-checks."""

    name: str
    passed: bool
    message: str = ""
    severity: str = "error"  # error | warning | info


class ValidationResult(BaseModel):
    """Aggregate of all validation checks."""

    passed: bool
    checks: list[ValidationCheck] = Field(default_factory=list)

    @classmethod
    def from_checks(cls, checks: list[ValidationCheck]) -> ValidationResult:
        all_passed = all(
            c.passed for c in checks if c.severity == "error"
        )
        return cls(passed=all_passed, checks=checks)


class FailureMode(str, Enum):
    """Known failure modes a skill can diagnose (Section C.2)."""

    SCF_NOT_CONVERGED = "scf_not_converged"
    GEOMETRY_UNPHYSICAL = "geometry_unphysical"
    KPOINT_INSUFFICIENT = "kpoint_insufficient"
    WALLTIME_EXCEEDED = "walltime_exceeded"
    MLIP_SIGN_FLIP = "mlip_sign_flip"
    ENERGY_OUTLIER = "energy_outlier"
    MISSING_PREREQUISITE = "missing_prerequisite"
    PARAMETER_OUT_OF_RANGE = "parameter_out_of_range"
    UNKNOWN = "unknown"


class SkillResult(BaseModel):
    """Full result of a skill execution."""

    success: bool
    output: SkillOutput = Field(default_factory=SkillOutput)
    validation: ValidationResult = Field(
        default_factory=lambda: ValidationResult(passed=True)
    )
    tool_results: list[ToolResult] = Field(default_factory=list)
    failure_mode: FailureMode | None = None
    remediation_hint: str = ""
    wall_time_seconds: float = 0.0


# ------------------------------------------------------------------
# Skill context
# ------------------------------------------------------------------

class SkillContext(BaseModel):
    """Runtime context available to every skill during execution."""

    workspace_dir: str = "."
    tool_registry: Any = None  # ToolRegistry — kept as Any to avoid circular dep issues

    model_config = {"arbitrary_types_allowed": True}

    def get_tool(self, name: str):
        """Convenience: look up a tool by name from the registry."""
        if self.tool_registry is None:
            raise RuntimeError("No tool registry attached to SkillContext.")
        return self.tool_registry.get(name)


# ------------------------------------------------------------------
# Skill ABC
# ------------------------------------------------------------------

class Skill(ABC):
    """Abstract base class for all Pippin skills.

    Sub-classes must implement:

    * ``name`` / ``description`` — identity and docstring.
    * ``required_tools`` — tool names the skill depends on.
    * ``execute`` — the core logic.

    Optionally override ``validate_input`` and ``validate_output`` for
    domain-specific pre- and post-condition checks.
    """

    name: str = ""
    description: str = ""
    required_tools: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        inp: SkillInput,
        ctx: SkillContext,
    ) -> SkillResult:
        """Full lifecycle: validate → execute → validate output."""
        import time

        t0 = time.monotonic()

        # Pre-flight validation
        pre = await self.validate_input(inp, ctx)
        if not pre.passed:
            return SkillResult(
                success=False,
                validation=pre,
                failure_mode=FailureMode.PARAMETER_OUT_OF_RANGE,
                remediation_hint="Fix input parameters and retry.",
                wall_time_seconds=time.monotonic() - t0,
            )

        # Check required tools are registered
        if ctx.tool_registry is not None:
            for t in self.required_tools:
                try:
                    ctx.tool_registry.get(t)
                except KeyError:
                    return SkillResult(
                        success=False,
                        validation=ValidationResult(
                            passed=False,
                            checks=[
                                ValidationCheck(
                                    name="required_tool",
                                    passed=False,
                                    message=f"Required tool {t!r} not registered.",
                                )
                            ],
                        ),
                        failure_mode=FailureMode.MISSING_PREREQUISITE,
                        wall_time_seconds=time.monotonic() - t0,
                    )

        # Execute
        result = await self.execute(inp, ctx)
        result.wall_time_seconds = time.monotonic() - t0

        # Post-flight validation (only if execution succeeded)
        if result.success:
            post = await self.validate_output(result, ctx)
            if not post.passed:
                result.success = False
                result.validation = post
                result.failure_mode = result.failure_mode or FailureMode.ENERGY_OUTLIER

        return result

    # ------------------------------------------------------------------
    # Extension points
    # ------------------------------------------------------------------

    async def validate_input(
        self, inp: SkillInput, ctx: SkillContext
    ) -> ValidationResult:
        """Override for domain-specific pre-condition checks."""
        return ValidationResult(passed=True)

    @abstractmethod
    async def execute(
        self, inp: SkillInput, ctx: SkillContext
    ) -> SkillResult:
        """Core skill logic — must be implemented by sub-classes."""
        ...

    async def validate_output(
        self, result: SkillResult, ctx: SkillContext
    ) -> ValidationResult:
        """Override for domain-specific post-condition checks."""
        return ValidationResult(passed=True)

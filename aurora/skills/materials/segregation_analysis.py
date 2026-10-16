"""Skill 3: Segregation Analysis (Table 1 — row 3).

Computes ΔΔE_seg (adsorption-induced segregation shift, Eq. 1) for a
given facet and intermediate.  Uses the ``analysis`` tool for the core
arithmetic and applies the |ΔΔE_seg| > 0.5 eV gate to flag strong
segregation effects (Section D.2).
"""

from __future__ import annotations

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

# Physical threshold from the paper: |ΔΔE_seg| > 0.5 eV is "strong"
_STRONG_SEGREGATION_THRESHOLD_EV = 0.5

# Physically reasonable clean-surface segregation energies (eV)
# Zn in Cu is endothermic (~+0.1 to +0.4 eV depending on facet)
_CLEAN_SEG_RANGE = (-0.2, 1.0)

# ΔΔE_seg sanity: larger than ~2 eV is almost certainly wrong
_MAX_PLAUSIBLE_DDE = 2.0


class SegregationAnalysisSkill(Skill):
    """Compute and validate ΔΔE_seg for a given facet + intermediate."""

    name = "segregation_analysis"
    description = (
        "Compute adsorption-induced segregation energy shift (ΔΔE_seg) "
        "and classify it as weak or strong based on a 0.5 eV gate."
    )
    required_tools = ["analysis"]

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    async def validate_input(
        self, inp: SkillInput, ctx: SkillContext
    ) -> ValidationResult:
        checks: list[ValidationCheck] = []
        p = inp.parameters

        for key in ("e_seg_ads", "e_sub_ads"):
            val = p.get(key)
            checks.append(
                ValidationCheck(
                    name=f"{key}_present",
                    passed=val is not None,
                    message=f"Missing required energy: {key}.",
                )
            )
            if val is not None:
                checks.append(
                    ValidationCheck(
                        name=f"{key}_numeric",
                        passed=isinstance(val, (int, float)),
                        message=f"{key} must be numeric, got {type(val).__name__}.",
                    )
                )

        # Clean-surface energies are optional but recommended
        if p.get("e_seg_clean") is None or p.get("e_sub_clean") is None:
            checks.append(
                ValidationCheck(
                    name="clean_energies_provided",
                    passed=False,
                    message=(
                        "Clean-surface energies (e_seg_clean, e_sub_clean) "
                        "not provided — ΔΔE_seg will be ΔE_seg(ads) only."
                    ),
                    severity="warning",
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
        analysis = ctx.get_tool("analysis")
        tool_results = []

        # Build params for the analysis tool
        ana_params: dict = {
            "operation": "compute_segregation_energy",
            "e_segregated": p["e_seg_ads"],
            "e_substitutional": p["e_sub_ads"],
        }
        if p.get("e_seg_clean") is not None and p.get("e_sub_clean") is not None:
            ana_params["e_seg_clean"] = p["e_seg_clean"]
            ana_params["e_sub_clean"] = p["e_sub_clean"]

        r = await analysis.execute(ana_params)
        tool_results.append(r)

        if not r.success:
            return SkillResult(
                success=False,
                tool_results=tool_results,
                failure_mode=FailureMode.MISSING_PREREQUISITE,
                remediation_hint=f"Analysis tool failed: {r.error}",
            )

        dde = r.output["delta_delta_e_seg"]
        de_seg = r.output["delta_e_seg"]

        # Classify strength
        magnitude = abs(dde)
        is_strong = magnitude > _STRONG_SEGREGATION_THRESHOLD_EV
        direction = "promotes" if dde < 0 else "inhibits"

        facet = p.get("facet", "unknown")
        intermediate = p.get("intermediate", "unknown")

        return SkillResult(
            success=True,
            output=SkillOutput(
                data={
                    "facet": facet,
                    "intermediate": intermediate,
                    "delta_e_seg_ads_eV": de_seg,
                    "delta_delta_e_seg_eV": dde,
                    "magnitude_eV": round(magnitude, 6),
                    "is_strong_effect": is_strong,
                    "direction": direction,
                    "threshold_eV": _STRONG_SEGREGATION_THRESHOLD_EV,
                    "interpretation": (
                        f"{intermediate} {direction} Zn segregation on Cu({facet}) "
                        f"(ΔΔE_seg = {dde:+.3f} eV, "
                        f"{'strong' if is_strong else 'weak'} effect)."
                    ),
                },
                summary=(
                    f"Cu({facet}) + {intermediate}: ΔΔE_seg = {dde:+.3f} eV "
                    f"({'strong' if is_strong else 'weak'}, {direction})"
                ),
            ),
            tool_results=tool_results,
        )

    # ------------------------------------------------------------------
    # Output validation
    # ------------------------------------------------------------------

    async def validate_output(
        self, result: SkillResult, ctx: SkillContext
    ) -> ValidationResult:
        checks: list[ValidationCheck] = []
        data = result.output.data

        dde = data.get("delta_delta_e_seg_eV", 0)
        checks.append(
            ValidationCheck(
                name="dde_plausible",
                passed=abs(dde) <= _MAX_PLAUSIBLE_DDE,
                message=(
                    f"|ΔΔE_seg| = {abs(dde):.3f} eV exceeds plausible maximum "
                    f"of {_MAX_PLAUSIBLE_DDE} eV — check input energies."
                ),
            )
        )

        de_seg = data.get("delta_e_seg_ads_eV", 0)
        low, high = _CLEAN_SEG_RANGE
        checks.append(
            ValidationCheck(
                name="de_seg_range",
                passed=low <= de_seg <= high,
                message=(
                    f"ΔE_seg = {de_seg:.3f} eV outside typical range "
                    f"[{low}, {high}] for Zn in Cu."
                ),
                severity="warning",
            )
        )

        return ValidationResult.from_checks(checks)

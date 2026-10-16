"""Skill 5: Statistical Validation (Table 1 — row 5).

Runs leave-one-adsorbate-out cross-validation on segregation energy
data, reports MAE / RMSE / R², and flags configurations that are poorly
predicted (outliers).  The paper notes that this CV scheme is used both
for evaluating descriptor models and for flagging MLIP/DFT discrepancies.
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

# Thresholds from the paper's own discussion
_MAX_ACCEPTABLE_MAE_EV = 0.15  # eV — paper considers < 0.15 eV "good"
_MAX_ACCEPTABLE_RMSE_EV = 0.25
_MIN_R2 = 0.6


class StatisticalValidationSkill(Skill):
    """Leave-one-adsorbate-out cross-validation for descriptor models."""

    name = "statistical_validation"
    description = (
        "Validate segregation-energy descriptor models using "
        "leave-one-adsorbate-out cross-validation."
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

        for key in ("X", "y", "groups"):
            val = p.get(key)
            checks.append(
                ValidationCheck(
                    name=f"{key}_present",
                    passed=val is not None and len(val) > 0,
                    message=f"Missing or empty required array: {key}.",
                )
            )

        # Need at least 3 data points for meaningful CV
        y = p.get("y", [])
        checks.append(
            ValidationCheck(
                name="enough_data",
                passed=len(y) >= 3,
                message=f"Need ≥ 3 data points for CV, got {len(y)}.",
            )
        )

        groups = p.get("groups", [])
        unique_groups = set(groups) if groups else set()
        checks.append(
            ValidationCheck(
                name="enough_groups",
                passed=len(unique_groups) >= 2,
                message=(
                    f"Need ≥ 2 unique groups (adsorbates) for leave-one-out CV, "
                    f"got {len(unique_groups)}."
                ),
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

        # Run cross-validation
        r_cv = await analysis.execute({
            "operation": "cross_validation",
            "X": p["X"],
            "y": p["y"],
            "groups": p["groups"],
        })
        tool_results.append(r_cv)

        if not r_cv.success:
            return SkillResult(
                success=False,
                tool_results=tool_results,
                failure_mode=FailureMode.MISSING_PREREQUISITE,
                remediation_hint=f"CV failed: {r_cv.error}",
            )

        mae = r_cv.output["mae"]
        rmse = r_cv.output["rmse"]
        r2 = r_cv.output["r2"]
        n_folds = r_cv.output["n_folds"]
        predictions = r_cv.output.get("predictions_by_group", {})

        # Also run a full-data regression for the coefficients
        r_reg = await analysis.execute({
            "operation": "descriptor_regression",
            "X": p["X"],
            "y": p["y"],
        })
        tool_results.append(r_reg)

        coefficients = {}
        if r_reg.success:
            coefficients = {
                "weights": r_reg.output.get("coefficients", []),
                "intercept": r_reg.output.get("intercept", 0),
                "full_r2": r_reg.output.get("r2", 0),
            }

        # Quality assessment
        quality_flags: list[str] = []
        if mae > _MAX_ACCEPTABLE_MAE_EV:
            quality_flags.append(
                f"MAE ({mae:.3f} eV) exceeds {_MAX_ACCEPTABLE_MAE_EV} eV threshold."
            )
        if rmse > _MAX_ACCEPTABLE_RMSE_EV:
            quality_flags.append(
                f"RMSE ({rmse:.3f} eV) exceeds {_MAX_ACCEPTABLE_RMSE_EV} eV threshold."
            )
        if r2 < _MIN_R2:
            quality_flags.append(
                f"R² ({r2:.3f}) below {_MIN_R2} — model explains < 60% of variance."
            )

        model_acceptable = len(quality_flags) == 0

        # Identify outlier groups (those with worst prediction errors)
        import numpy as np

        outlier_groups: list[str] = []
        y_arr = np.array(p["y"], dtype=float)
        groups_arr = p["groups"]
        for group, preds in predictions.items():
            mask = [g == group for g in groups_arr]
            y_true = y_arr[mask]
            y_pred = np.array(preds, dtype=float)
            group_mae = float(np.mean(np.abs(y_true - y_pred)))
            if group_mae > 2 * mae and group_mae > _MAX_ACCEPTABLE_MAE_EV:
                outlier_groups.append(group)

        return SkillResult(
            success=True,
            output=SkillOutput(
                data={
                    "mae_eV": mae,
                    "rmse_eV": rmse,
                    "r2": r2,
                    "n_folds": n_folds,
                    "model_acceptable": model_acceptable,
                    "quality_flags": quality_flags,
                    "outlier_groups": outlier_groups,
                    "predictions_by_group": predictions,
                    "regression": coefficients,
                },
                summary=(
                    f"CV: MAE={mae:.3f} eV, RMSE={rmse:.3f} eV, R²={r2:.3f} "
                    f"({n_folds} folds). "
                    f"Model {'acceptable' if model_acceptable else 'needs improvement'}."
                    + (
                        f" Outlier groups: {outlier_groups}."
                        if outlier_groups
                        else ""
                    )
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

        # R² should be between -1 and 1 (can be negative for bad fits)
        r2 = data.get("r2", 0)
        import math
        checks.append(
            ValidationCheck(
                name="r2_range",
                passed=not math.isnan(r2) and r2 <= 1.0,
                message=f"R²={r2} is invalid (NaN or > 1).",
            )
        )

        # MAE should be non-negative
        mae = data.get("mae_eV", 0)
        checks.append(
            ValidationCheck(
                name="mae_nonneg",
                passed=not math.isnan(mae) and mae >= 0,
                message=f"MAE={mae} is negative or NaN.",
            )
        )

        return ValidationResult.from_checks(checks)

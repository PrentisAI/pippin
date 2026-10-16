"""Scientific analysis tools using real NumPy / pandas / scikit-learn.

These are *not* mocked — they run actual numerical computations on
in-memory data.  Operations mirror the analyses in the paper:
  * ΔΔE_seg computation (Eq. 1)
  * Statistical summaries with confidence intervals
  * Leave-one-adsorbate-out cross-validation
  * Descriptor regression
  * Cluster-expansion coverage fitting
"""

from __future__ import annotations

from typing import Any

import numpy as np

from aurora.tools.base import Tool, ToolResult


class AnalysisTool(Tool):
    """Scientific data analysis tool.

    Dispatches on ``params["operation"]`` to the appropriate method.
    """

    name = "analysis"
    description = "Scientific data analysis (NumPy/pandas/scikit-learn)"

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        op = params.get("operation")
        valid_ops = {
            "compute_segregation_energy",
            "statistical_summary",
            "cross_validation",
            "descriptor_regression",
            "cluster_expansion",
        }
        if op not in valid_ops:
            return False, f"Unknown operation {op!r}; choose from {sorted(valid_ops)}"
        return True, "Parameters OK."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        op = params["operation"]
        dispatch = {
            "compute_segregation_energy": self._compute_segregation_energy,
            "statistical_summary": self._statistical_summary,
            "cross_validation": self._cross_validation,
            "descriptor_regression": self._descriptor_regression,
            "cluster_expansion": self._cluster_expansion,
        }
        return dispatch[op](params)

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _compute_segregation_energy(self, params: dict[str, Any]) -> ToolResult:
        """Compute ΔΔE_seg = (E_seg - E_sub)_ads - (E_seg - E_sub)_clean.

        The identity (Eq. 1 in the generated manuscript):
            ΔΔE_seg = E_ads^Cu - E_ads^Zn
        where Cu/Zn refer to the exposed local environment.
        """
        try:
            e_seg = float(params["e_segregated"])
            e_sub = float(params["e_substitutional"])
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(
                success=False,
                error=f"Need numeric e_segregated and e_substitutional: {exc}",
            )

        delta_e_seg_ads = e_seg - e_sub

        e_seg_clean = params.get("e_seg_clean")
        e_sub_clean = params.get("e_sub_clean")

        if e_seg_clean is not None and e_sub_clean is not None:
            delta_e_seg_clean = float(e_seg_clean) - float(e_sub_clean)
            delta_delta = delta_e_seg_ads - delta_e_seg_clean
        else:
            delta_delta = delta_e_seg_ads

        return ToolResult(
            success=True,
            output={
                "delta_e_seg": round(delta_e_seg_ads, 6),
                "delta_delta_e_seg": round(delta_delta, 6),
                "unit": "eV",
            },
        )

    def _statistical_summary(self, params: dict[str, Any]) -> ToolResult:
        """Compute mean, std, median, confidence interval for a list of values."""
        values = params.get("values")
        if not values or not isinstance(values, (list, tuple)):
            return ToolResult(success=False, error="Provide a non-empty 'values' list.")

        arr = np.array(values, dtype=float)
        n = len(arr)
        mean = float(np.mean(arr))
        std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
        median = float(np.median(arr))

        # 95% CI using t-distribution approximation
        se = std / np.sqrt(n) if n > 1 else 0.0
        try:
            from scipy.stats import t as t_dist  # type: ignore[import-untyped]

            t_val = float(t_dist.ppf(0.975, df=max(n - 1, 1)))
            ci_low = mean - t_val * se
            ci_high = mean + t_val * se
        except Exception:
            # Fallback if scipy not available — use normal approximation
            ci_low = mean - 1.96 * se
            ci_high = mean + 1.96 * se

        return ToolResult(
            success=True,
            output={
                "n": n,
                "mean": round(mean, 6),
                "std": round(std, 6),
                "median": round(median, 6),
                "ci_95_low": round(ci_low, 6),
                "ci_95_high": round(ci_high, 6),
            },
        )

    def _cross_validation(self, params: dict[str, Any]) -> ToolResult:
        """Leave-one-adsorbate-out cross-validation for descriptor models.

        Parameters
        ----------
        params["X"] : list[list[float]]
            Feature matrix (one row per data point).
        params["y"] : list[float]
            Target values (e.g. ΔΔE_seg).
        params["groups"] : list[str]
            Group labels for leave-one-group-out (adsorbate names).
        """
        X = params.get("X")
        y = params.get("y")
        groups = params.get("groups")

        if not X or not y:
            return ToolResult(success=False, error="Need non-empty X and y.")

        X_arr = np.array(X, dtype=float)
        y_arr = np.array(y, dtype=float)

        if groups is None:
            groups = [str(i) for i in range(len(y_arr))]

        unique_groups = sorted(set(groups))
        errors: list[float] = []
        predictions: dict[str, list[float]] = {}

        for g in unique_groups:
            mask = np.array([gi != g for gi in groups])
            X_train, y_train = X_arr[mask], y_arr[mask]
            X_test, y_test = X_arr[~mask], y_arr[~mask]

            if len(X_train) < 2 or X_train.shape[1] == 0:
                continue

            # Simple linear regression (no sklearn dependency required)
            # y = X @ beta + intercept via least squares
            ones = np.ones((X_train.shape[0], 1))
            A = np.hstack([X_train, ones])
            beta, *_ = np.linalg.lstsq(A, y_train, rcond=None)

            A_test = np.hstack([X_test, np.ones((X_test.shape[0], 1))])
            y_pred = A_test @ beta

            fold_errors = (y_test - y_pred).tolist()
            errors.extend(fold_errors)
            predictions[g] = y_pred.tolist()

        errors_arr = np.array(errors)
        mae = float(np.mean(np.abs(errors_arr))) if len(errors_arr) else float("nan")
        rmse = float(np.sqrt(np.mean(errors_arr ** 2))) if len(errors_arr) else float("nan")

        # R² on full leave-one-out predictions
        if len(errors_arr) == len(y_arr):
            ss_res = float(np.sum(errors_arr ** 2))
            ss_tot = float(np.sum((y_arr - np.mean(y_arr)) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        else:
            r2 = float("nan")

        return ToolResult(
            success=True,
            output={
                "mae": round(mae, 6),
                "rmse": round(rmse, 6),
                "r2": round(r2, 6),
                "n_folds": len(unique_groups),
                "predictions_by_group": predictions,
            },
        )

    def _descriptor_regression(self, params: dict[str, Any]) -> ToolResult:
        """Fit a linear descriptor model y = X @ w + b."""
        X = np.array(params.get("X", []), dtype=float)
        y = np.array(params.get("y", []), dtype=float)

        if X.size == 0 or y.size == 0:
            return ToolResult(success=False, error="Need non-empty X and y.")

        ones = np.ones((X.shape[0], 1))
        A = np.hstack([X, ones])
        beta, residuals, rank, sv = np.linalg.lstsq(A, y, rcond=None)

        y_pred = A @ beta
        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        return ToolResult(
            success=True,
            output={
                "coefficients": beta[:-1].tolist(),
                "intercept": float(beta[-1]),
                "r2": round(r2, 6),
                "rmse": round(float(np.sqrt(ss_res / len(y))), 6),
            },
        )

    def _cluster_expansion(self, params: dict[str, Any]) -> ToolResult:
        """Fit a simple cluster-expansion model: E(θ) ≈ E⁰ + z·J·θ.

        Parameters
        ----------
        params["coverages"] : list[float]
            Adsorbate coverage values θ.
        params["energies"] : list[float]
            Corresponding ΔΔE_seg values.
        """
        coverages = np.array(params.get("coverages", []), dtype=float)
        energies = np.array(params.get("energies", []), dtype=float)

        if len(coverages) < 2:
            return ToolResult(success=False, error="Need at least 2 data points.")

        # Fit E = a + b * θ  (linear model)
        A = np.vstack([np.ones_like(coverages), coverages]).T
        beta, *_ = np.linalg.lstsq(A, energies, rcond=None)
        e0, j_eff = float(beta[0]), float(beta[1])

        y_pred = A @ beta
        ss_res = float(np.sum((energies - y_pred) ** 2))
        ss_tot = float(np.sum((energies - np.mean(energies)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        return ToolResult(
            success=True,
            output={
                "e0": round(e0, 6),
                "j_effective": round(j_eff, 6),
                "r2": round(r2, 6),
                "description": "E(θ) ≈ E⁰ + J_eff × θ",
            },
        )

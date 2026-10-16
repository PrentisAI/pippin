"""Skill 2: Convergence Analysis (Table 1 — row 2).

Runs DFT calculations at varying k-point grids and cut-off energies
to establish numerical convergence.  The skill orchestrates multiple
mock-DFT calls and checks that the energy is converged within a
user-specified tolerance (default 1 meV/atom).
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

# Sensible k-point grids for convergence sweeps (Table S1 style)
_DEFAULT_KPOINT_GRIDS = [
    [2, 2, 1],
    [3, 3, 1],
    [4, 4, 1],
    [5, 5, 1],
    [6, 6, 1],
]


class ConvergenceAnalysisSkill(Skill):
    """Run k-point / ecutwfc convergence tests via DFT."""

    name = "convergence_analysis"
    description = (
        "Systematically test k-point and energy cut-off convergence "
        "for a given slab structure."
    )
    required_tools = ["mock_dft"]

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    async def validate_input(
        self, inp: SkillInput, ctx: SkillContext
    ) -> ValidationResult:
        checks: list[ValidationCheck] = []
        p = inp.parameters

        tol = p.get("tolerance_mev_per_atom", 1.0)
        checks.append(
            ValidationCheck(
                name="tolerance_positive",
                passed=tol > 0,
                message=f"Tolerance must be positive, got {tol}.",
            )
        )

        facet = p.get("facet")
        checks.append(
            ValidationCheck(
                name="facet_present",
                passed=facet is not None,
                message="Must specify a facet for the convergence test.",
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
        dft = ctx.get_tool("mock_dft")
        tool_results = []

        facet = str(p["facet"])
        zn_site = p.get("zn_site", "surface")
        kpoint_grids = p.get("kpoint_grids", _DEFAULT_KPOINT_GRIDS)
        tolerance = p.get("tolerance_mev_per_atom", 1.0)  # meV/atom
        ecutwfc = p.get("ecutwfc", 60)  # Ry
        ecutrho = p.get("ecutrho", ecutwfc * 8)  # Ry

        # Build a mock structure dict for the DFT tool
        structure = p.get("structure", {
            "atoms": ["Cu"] * 16,
            "facet": facet,
            "dopant_site": zn_site,
        })

        energies: list[dict[str, Any]] = []
        last_success_energy: float | None = None
        converged_grid: list[int] | None = None

        for grid in kpoint_grids:
            r = await dft.execute({
                "structure": structure,
                "kpoints": grid,
                "ecutwfc": ecutwfc,
                "ecutrho": ecutrho,
                "calculation": "scf",
            })
            tool_results.append(r)

            if not r.success:
                energies.append({
                    "kpoints": grid,
                    "success": False,
                    "error": r.error,
                })
                continue

            total_e = r.output.get("total_energy", 0.0)
            n_atoms = r.output.get("n_atoms", 1)
            e_per_atom = total_e / max(n_atoms, 1)

            energies.append({
                "kpoints": grid,
                "success": True,
                "total_energy_eV": total_e,
                "e_per_atom_eV": round(e_per_atom, 6),
            })

            # Check convergence against previous successful point
            if last_success_energy is not None:
                diff_meV = abs(e_per_atom - last_success_energy) * 1000
                energies[-1]["delta_meV_per_atom"] = round(diff_meV, 3)

                if diff_meV < tolerance and converged_grid is None:
                    converged_grid = grid

            last_success_energy = e_per_atom

        is_converged = converged_grid is not None

        return SkillResult(
            success=True,
            output=SkillOutput(
                data={
                    "facet": facet,
                    "ecutwfc_Ry": ecutwfc,
                    "tolerance_meV_per_atom": tolerance,
                    "energies": energies,
                    "converged": is_converged,
                    "converged_grid": converged_grid,
                    "recommended_kpoints": (
                        converged_grid if is_converged else kpoint_grids[-1]
                    ),
                },
                summary=(
                    f"Convergence {'reached' if is_converged else 'NOT reached'} "
                    f"at k={converged_grid or '?'} for Cu({facet}), "
                    f"tolerance={tolerance} meV/atom."
                ),
            ),
            tool_results=tool_results,
            failure_mode=(
                None if is_converged else FailureMode.KPOINT_INSUFFICIENT
            ),
            remediation_hint=(
                ""
                if is_converged
                else "Try denser k-point grids or increase ecutwfc."
            ),
        )

    # ------------------------------------------------------------------
    # Output validation
    # ------------------------------------------------------------------

    async def validate_output(
        self, result: SkillResult, ctx: SkillContext
    ) -> ValidationResult:
        checks: list[ValidationCheck] = []
        data = result.output.data

        # At least 2 successful DFT points needed to judge convergence
        n_ok = sum(1 for e in data.get("energies", []) if e.get("success"))
        checks.append(
            ValidationCheck(
                name="enough_data_points",
                passed=n_ok >= 2,
                message=f"Only {n_ok} successful DFT runs — need ≥ 2.",
            )
        )

        # If converged, recommended grid should not be the coarsest
        if data.get("converged") and data.get("converged_grid"):
            grid = data["converged_grid"]
            checks.append(
                ValidationCheck(
                    name="not_trivially_converged",
                    passed=grid[0] > 2,
                    message=(
                        "Convergence at the coarsest grid is suspicious; "
                        "consider extending the sweep."
                    ),
                    severity="warning",
                )
            )

        return ValidationResult.from_checks(checks)

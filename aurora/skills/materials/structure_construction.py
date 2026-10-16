"""Skill 1: Structure Construction (Table 1 — row 1).

Builds Cu–Zn alloy slab models with substitutional Zn dopants at
surface or sub-surface sites, optionally with CO₂ hydrogenation
intermediates adsorbed.  Wraps the ``mock_ase`` tool.
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

# Physical sanity bounds
_VALID_CU_FACETS = {"111", "100", "110", "211"}
_VALID_ZN_COVERAGES = {0.0, 0.25, 0.5, 0.75, 1.0}
_KNOWN_INTERMEDIATES = {
    "CO", "HCO", "HCOO", "H2CO", "CH3OH", "H2O", "OH", "H", "CO2", "HCOOH",
}


class StructureConstructionSkill(Skill):
    """Build slab structures for Cu–Zn alloy surface studies."""

    name = "structure_construction"
    description = (
        "Construct Cu–Zn alloy slab models with dopants and adsorbates "
        "for segregation energy calculations."
    )
    required_tools = ["mock_ase"]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def validate_input(
        self, inp: SkillInput, ctx: SkillContext
    ) -> ValidationResult:
        checks: list[ValidationCheck] = []
        p = inp.parameters

        facet = str(p.get("facet", "111"))
        checks.append(
            ValidationCheck(
                name="facet_valid",
                passed=facet in _VALID_CU_FACETS,
                message=f"Facet {facet!r} must be one of {sorted(_VALID_CU_FACETS)}.",
            )
        )

        n_layers = p.get("n_layers", 4)
        checks.append(
            ValidationCheck(
                name="n_layers_range",
                passed=3 <= n_layers <= 8,
                message=f"n_layers={n_layers} should be in [3, 8].",
            )
        )

        vacuum = p.get("vacuum", 15.0)
        checks.append(
            ValidationCheck(
                name="vacuum_sufficient",
                passed=vacuum >= 10.0,
                message=f"vacuum={vacuum} Å should be ≥ 10 Å.",
                severity="warning",
            )
        )

        intermediate = p.get("intermediate")
        if intermediate:
            checks.append(
                ValidationCheck(
                    name="intermediate_known",
                    passed=intermediate.upper() in _KNOWN_INTERMEDIATES,
                    message=(
                        f"Intermediate {intermediate!r} not in known set: "
                        f"{sorted(_KNOWN_INTERMEDIATES)}."
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
        ase_tool = ctx.get_tool("mock_ase")
        tool_results = []

        facet = str(p.get("facet", "111"))
        n_layers = p.get("n_layers", 4)
        vacuum = p.get("vacuum", 15.0)
        zn_site = p.get("zn_site", "surface")  # surface | subsurface
        intermediate = p.get("intermediate")

        # Step 1: Build bulk Cu
        r_bulk = await ase_tool.execute({
            "operation": "build_bulk",
            "element": "Cu",
            "crystal_structure": "fcc",
        })
        tool_results.append(r_bulk)
        if not r_bulk.success:
            return SkillResult(
                success=False,
                tool_results=tool_results,
                failure_mode=FailureMode.MISSING_PREREQUISITE,
                remediation_hint="Bulk Cu construction failed.",
            )

        # Step 2: Build slab  (mock_ase expects: host, facet, layers, vacuum)
        r_slab = await ase_tool.execute({
            "operation": "build_slab",
            "host": "cu",
            "facet": facet,
            "layers": n_layers,
            "vacuum": vacuum,
        })
        tool_results.append(r_slab)
        if not r_slab.success:
            return SkillResult(
                success=False,
                tool_results=tool_results,
                failure_mode=FailureMode.GEOMETRY_UNPHYSICAL,
                remediation_hint="Slab construction failed — check facet and size.",
            )

        # Step 3: Add Zn dopant (mock_ase expects: structure, dopant_element, site)
        slab_structure = r_slab.output.get("structure", {})
        r_doped = await ase_tool.execute({
            "operation": "add_dopant",
            "structure": slab_structure,
            "dopant_element": "Zn",
            "site": zn_site,  # "surface" or "bulk"
        })
        tool_results.append(r_doped)
        if not r_doped.success:
            return SkillResult(
                success=False,
                tool_results=tool_results,
                failure_mode=FailureMode.GEOMETRY_UNPHYSICAL,
                remediation_hint="Zn dopant insertion failed.",
            )

        # Step 4 (optional): Add adsorbate (mock_ase expects: structure, molecule, height)
        if intermediate:
            doped_structure = r_doped.output.get("structure", {})
            r_ads = await ase_tool.execute({
                "operation": "add_adsorbate",
                "structure": doped_structure,
                "molecule": intermediate,
                "site": "ontop",
                "height": p.get("adsorbate_height", 2.0),
            })
            tool_results.append(r_ads)
            if not r_ads.success:
                return SkillResult(
                    success=False,
                    tool_results=tool_results,
                    failure_mode=FailureMode.GEOMETRY_UNPHYSICAL,
                    remediation_hint=f"Could not place adsorbate {intermediate}.",
                )

        # Assemble output
        doped_struct = r_doped.output.get("structure", {})
        final_struct = doped_struct
        structures: dict[str, Any] = {
            "bulk": r_bulk.output,
            "slab": r_slab.output,
            "doped_slab": r_doped.output,
        }
        if intermediate:
            structures["adsorbate_slab"] = r_ads.output  # type: ignore[possibly-undefined]
            final_struct = r_ads.output.get("structure", doped_struct)  # type: ignore[possibly-undefined]

        n_atoms = final_struct.get("n_atoms", doped_struct.get("n_atoms", 0))

        return SkillResult(
            success=True,
            output=SkillOutput(
                data={
                    "facet": facet,
                    "zn_site": zn_site,
                    "intermediate": intermediate,
                    "n_atoms": n_atoms,
                    "structures": structures,
                },
                summary=(
                    f"Built Cu({facet}) slab ({n_layers}L) "
                    f"with Zn at {zn_site}"
                    + (f" + {intermediate}" if intermediate else "")
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
        n_atoms = result.output.data.get("n_atoms", 0)

        checks.append(
            ValidationCheck(
                name="n_atoms_reasonable",
                passed=4 <= n_atoms <= 500,
                message=f"n_atoms={n_atoms} outside reasonable range [4, 500].",
            )
        )

        return ValidationResult.from_checks(checks)

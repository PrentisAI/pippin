"""Skill 6: Scientific Visualization (Table 1 — row 6).

Wraps the ``visualization`` tool with domain-specific defaults:
  * Segregation bar charts comparing clean vs. adsorbate-covered facets
    (cf. Figure 2 of the paper).
  * ΔΔE_seg vs. coverage θ plots (cf. Figure 1).
  * Descriptor-model parity plots.
"""

from __future__ import annotations

import os
from typing import Any

from aurora.skills.base import (
    Skill,
    SkillContext,
    SkillInput,
    SkillOutput,
    SkillResult,
    ValidationCheck,
    ValidationResult,
)


class ScientificVisualizationSkill(Skill):
    """Generate publication-quality figures for segregation studies."""

    name = "scientific_visualization"
    description = (
        "Produce segregation bar charts, coverage plots, and "
        "descriptor parity plots."
    )
    required_tools = ["visualization"]

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    async def validate_input(
        self, inp: SkillInput, ctx: SkillContext
    ) -> ValidationResult:
        checks: list[ValidationCheck] = []
        p = inp.parameters

        plot_type = p.get("plot_type")
        valid_types = {
            "segregation_bar",
            "coverage",
            "parity",
            "generic_bar",
            "generic_scatter",
        }
        checks.append(
            ValidationCheck(
                name="plot_type_valid",
                passed=plot_type in valid_types,
                message=f"plot_type must be one of {sorted(valid_types)}.",
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
        vis = ctx.get_tool("visualization")
        tool_results = []

        plot_type = p["plot_type"]
        output_dir = p.get("output_dir", os.path.join(ctx.workspace_dir, "figures"))
        os.makedirs(output_dir, exist_ok=True)

        if plot_type == "segregation_bar":
            r = await self._segregation_bar(vis, p, output_dir)
        elif plot_type == "coverage":
            r = await self._coverage(vis, p, output_dir)
        elif plot_type == "parity":
            r = await self._parity(vis, p, output_dir)
        elif plot_type == "generic_bar":
            r = await vis.execute({
                "operation": "generic_bar_chart",
                "labels": p.get("labels", []),
                "values": p.get("values", []),
                "title": p.get("title", ""),
                "ylabel": p.get("ylabel", ""),
                "output_path": os.path.join(output_dir, p.get("filename", "bar.png")),
            })
        elif plot_type == "generic_scatter":
            r = await vis.execute({
                "operation": "generic_scatter",
                "x": p.get("x", []),
                "y": p.get("y", []),
                "title": p.get("title", ""),
                "xlabel": p.get("xlabel", ""),
                "ylabel": p.get("ylabel", ""),
                "output_path": os.path.join(output_dir, p.get("filename", "scatter.png")),
            })
        else:
            return SkillResult(success=False)

        tool_results.append(r)
        files = r.output_files if r.success else []

        return SkillResult(
            success=r.success,
            output=SkillOutput(
                data={"plot_type": plot_type},
                files=files,
                summary=f"Generated {plot_type} plot → {files}",
            ),
            tool_results=tool_results,
        )

    # ------------------------------------------------------------------
    # Plot helpers
    # ------------------------------------------------------------------

    async def _segregation_bar(self, vis: Any, p: dict, out_dir: str) -> Any:
        """Grouped bar chart of E_seg (clean vs adsorbate) per facet."""
        filename = p.get("filename", "segregation_bar.png")
        return await vis.execute({
            "operation": "segregation_bar_chart",
            "facets": p.get("facets", []),
            "clean_energies": p.get("clean_energies", []),
            "ads_energies": p.get("ads_energies", []),
            "title": p.get("title", "Segregation energies by facet"),
            "output_path": os.path.join(out_dir, filename),
        })

    async def _coverage(self, vis: Any, p: dict, out_dir: str) -> Any:
        """ΔΔE_seg vs. coverage θ."""
        filename = p.get("filename", "coverage_plot.png")
        return await vis.execute({
            "operation": "coverage_plot",
            "coverages": p.get("coverages", []),
            "delta_delta_e_seg": p.get("delta_delta_e_seg", []),
            "facet": p.get("facet", ""),
            "intermediate": p.get("intermediate", ""),
            "output_path": os.path.join(out_dir, filename),
        })

    async def _parity(self, vis: Any, p: dict, out_dir: str) -> Any:
        """Parity plot: predicted vs actual ΔΔE_seg."""
        filename = p.get("filename", "parity_plot.png")
        return await vis.execute({
            "operation": "generic_scatter",
            "x": p.get("actual", []),
            "y": p.get("predicted", []),
            "xlabel": "DFT ΔΔE_seg (eV)",
            "ylabel": "Predicted ΔΔE_seg (eV)",
            "title": p.get("title", "Parity plot"),
            "output_path": os.path.join(out_dir, filename),
        })

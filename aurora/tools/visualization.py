"""Scientific visualisation tool using Matplotlib.

Generates publication-quality figures for the Cu–Zn segregation study:
  * Segregation-energy bar charts (like Figure 2 of the paper)
  * ΔΔE_seg coverage plots (like Figure 1)
  * Phase-diagram heatmaps
"""

from __future__ import annotations

import os
from typing import Any

from aurora.tools.base import Tool, ToolResult


class VisualizationTool(Tool):
    """Generate scientific figures with Matplotlib."""

    name = "visualization"
    description = "Generate scientific plots and figures"

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        op = params.get("operation")
        valid_ops = {
            "segregation_bar_chart",
            "coverage_plot",
            "generic_bar_chart",
            "generic_scatter",
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
            "segregation_bar_chart": self._segregation_bar_chart,
            "coverage_plot": self._coverage_plot,
            "generic_bar_chart": self._generic_bar_chart,
            "generic_scatter": self._generic_scatter,
        }
        return dispatch[op](params)

    # ------------------------------------------------------------------

    def _segregation_bar_chart(self, params: dict[str, Any]) -> ToolResult:
        """Bar chart of clean vs adsorbate segregation energies per facet.

        Parameters
        ----------
        params["facets"] : list[str]
        params["clean_energies"] : list[float]   (E_seg clean per facet)
        params["ads_energies"] : list[float]      (E_seg with adsorbate)
        params["output_path"] : str
        params["title"] : str, optional
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        facets = params.get("facets", [])
        clean = params.get("clean_energies", [])
        ads = params.get("ads_energies", [])
        output_path = params.get("output_path", "segregation_bar.png")
        title = params.get("title", "Segregation energies")

        x = np.arange(len(facets))
        width = 0.35

        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar(x - width / 2, clean, width, label="clean", color="#4a7fb5")
        ax.bar(x + width / 2, ads, width, label="with adsorbate", color="#d95f02")

        for i, (c, a) in enumerate(zip(clean, ads)):
            ax.text(i - width / 2, c, f"{c:+.2f}", ha="center", va="bottom", fontsize=7)
            ax.text(i + width / 2, a, f"{a:+.2f}", ha="center", va="bottom", fontsize=7)

        ax.set_xticks(x)
        ax.set_xticklabels(facets)
        ax.set_ylabel("$E_{\\mathrm{seg}}$ (eV)")
        ax.set_title(title)
        ax.legend()
        ax.axhline(0, color="grey", linewidth=0.5, linestyle="--")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fig.tight_layout()
        fig.savefig(output_path, dpi=150)
        plt.close(fig)

        return ToolResult(success=True, output_files=[output_path])

    def _coverage_plot(self, params: dict[str, Any]) -> ToolResult:
        """ΔΔE_seg as a function of adsorbate coverage θ.

        Parameters
        ----------
        params["coverages"] : list[float]
        params["delta_delta_e_seg"] : list[float]
        params["facet"] : str
        params["intermediate"] : str
        params["output_path"] : str
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        coverages = params.get("coverages", [])
        dde = params.get("delta_delta_e_seg", [])
        facet = params.get("facet", "")
        intermediate = params.get("intermediate", "")
        output_path = params.get("output_path", "coverage_plot.png")

        fig, ax = plt.subplots(figsize=(5, 4))
        ax.plot(coverages, dde, "o-", color="#4a7fb5", markersize=6)
        ax.axhline(0, color="grey", linewidth=0.5, linestyle="--")
        ax.set_xlabel("Adsorbate coverage θ")
        ax.set_ylabel("ΔΔ$E_{\\mathrm{seg}}$ (meV)")
        ax.set_title(f"{intermediate} on {facet}")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fig.tight_layout()
        fig.savefig(output_path, dpi=150)
        plt.close(fig)

        return ToolResult(success=True, output_files=[output_path])

    def _generic_bar_chart(self, params: dict[str, Any]) -> ToolResult:
        """Generic bar chart."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        labels = params.get("labels", [])
        values = params.get("values", [])
        output_path = params.get("output_path", "bar_chart.png")
        title = params.get("title", "")
        ylabel = params.get("ylabel", "")

        fig, ax = plt.subplots(figsize=(6, 4))
        colors = ["#4a7fb5" if v >= 0 else "#d95f02" for v in values]
        ax.bar(labels, values, color=colors)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.axhline(0, color="grey", linewidth=0.5, linestyle="--")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fig.tight_layout()
        fig.savefig(output_path, dpi=150)
        plt.close(fig)

        return ToolResult(success=True, output_files=[output_path])

    def _generic_scatter(self, params: dict[str, Any]) -> ToolResult:
        """Generic scatter plot."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        x = params.get("x", [])
        y = params.get("y", [])
        output_path = params.get("output_path", "scatter.png")
        title = params.get("title", "")
        xlabel = params.get("xlabel", "x")
        ylabel = params.get("ylabel", "y")

        fig, ax = plt.subplots(figsize=(5, 4))
        ax.scatter(x, y, color="#4a7fb5", s=40)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fig.tight_layout()
        fig.savefig(output_path, dpi=150)
        plt.close(fig)

        return ToolResult(success=True, output_files=[output_path])

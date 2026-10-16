"""Mock literature search tool for novelty pre-screening.

Returns a curated set of papers related to Cu–Zn surface segregation
and methanol-synthesis catalysis drawn from the actual references in the
AURORA paper.
"""

from __future__ import annotations

from typing import Any

from aurora.tools.base import Tool, ToolResult

# Pre-loaded mock papers (from the paper's reference list)
_MOCK_PAPERS: list[dict[str, Any]] = [
    {
        "title": "The active site of methanol synthesis over Cu/ZnO/Al2O3 industrial catalysts",
        "authors": "Behrens et al.",
        "year": 2012,
        "journal": "Science",
        "doi": "10.1126/science.1219831",
        "keywords": ["methanol synthesis", "Cu/ZnO", "active site", "catalysis"],
        "abstract": (
            "Identifies the active site for methanol synthesis on Cu/ZnO catalysts, "
            "showing that Zn promotes Cu steps."
        ),
    },
    {
        "title": "Quantifying the promotion of Cu catalysts by ZnO for methanol synthesis",
        "authors": "Kuld et al.",
        "year": 2016,
        "journal": "Science",
        "doi": "10.1126/science.aaf0718",
        "keywords": ["methanol synthesis", "Cu", "ZnO", "promotion", "segregation"],
        "abstract": "Quantifies the synergistic effect of ZnO on Cu catalysts.",
    },
    {
        "title": "Active sites for CO2 hydrogenation to methanol on Cu/ZnO catalysts",
        "authors": "Kattel et al.",
        "year": 2017,
        "journal": "Science",
        "doi": "10.1126/science.aal3573",
        "keywords": ["CO2 hydrogenation", "Cu/ZnO", "active sites", "methanol"],
        "abstract": "Identifies active sites for CO2-to-methanol conversion.",
    },
    {
        "title": "The state of zinc in methanol synthesis over a Zn/ZnO/Cu(211) model catalyst",
        "authors": "Amann et al.",
        "year": 2022,
        "journal": "Science",
        "doi": "10.1126/science.abj7747",
        "keywords": ["zinc", "methanol synthesis", "Cu(211)", "operando", "segregation"],
        "abstract": (
            "Operando study showing Zn chemical state and spatial distribution "
            "on a Cu(211) model catalyst under reaction conditions."
        ),
    },
    {
        "title": "Surface segregation energies in transition-metal alloys",
        "authors": "Ruban et al.",
        "year": 1999,
        "journal": "Phys. Rev. B",
        "doi": "10.1103/PhysRevB.59.15990",
        "keywords": ["segregation energy", "transition metal", "alloy", "surface"],
        "abstract": "Systematic DFT study of surface segregation energies across transition metals.",
    },
    {
        "title": "Water-vapor-induced segregation in transition metal-doped copper alloys",
        "authors": "Ye et al.",
        "year": 2024,
        "journal": "J. Phys. Chem. C",
        "doi": "10.1021/acs.jpcc.4c04418",
        "keywords": ["water vapor", "segregation", "copper alloy", "adsorbate-induced"],
        "abstract": "Demonstrates that water vapor can induce segregation in Cu alloys.",
    },
    {
        "title": "Reaction-driven restructuring of Rh-Pd and Pt-Pd core-shell nanoparticles",
        "authors": "Tao et al.",
        "year": 2008,
        "journal": "Science",
        "doi": "10.1126/science.1164170",
        "keywords": ["restructuring", "adsorbate-driven", "nanoparticle", "core-shell"],
        "abstract": "Shows adsorbate-driven structural changes in bimetallic nanoparticles.",
    },
    {
        "title": "Mechanism of methanol synthesis on Cu through CO2 and CO hydrogenation",
        "authors": "Grabow and Mavrikakis",
        "year": 2011,
        "journal": "ACS Catal.",
        "doi": "10.1021/cs200055d",
        "keywords": ["methanol synthesis", "Cu", "CO2 hydrogenation", "mechanism", "DFT"],
        "abstract": (
            "DFT study of the CO2-to-methanol pathway on Cu, identifying key "
            "intermediates: HCO, HCOO, H2CO, CH3OH."
        ),
    },
    {
        "title": "Composition, structure and stability of RuO2(110) as a function of oxygen pressure",
        "authors": "Reuter and Scheffler",
        "year": 2001,
        "journal": "Phys. Rev. B",
        "doi": "10.1103/PhysRevB.65.035406",
        "keywords": ["ab initio thermodynamics", "surface", "stability", "pressure"],
        "abstract": "Introduces the ab initio atomistic thermodynamics framework for surfaces.",
    },
    {
        "title": "CHGNet as a pretrained universal neural network potential",
        "authors": "Deng et al.",
        "year": 2023,
        "journal": "Nat. Mach. Intell.",
        "doi": "10.1038/s42256-023-00716-3",
        "keywords": ["CHGNet", "neural network potential", "MLIP", "charge-informed"],
        "abstract": "Charge-informed GNN potential for general atomistic modelling.",
    },
    {
        "title": "MACE: Higher order equivariant message passing neural networks",
        "authors": "Batatia et al.",
        "year": 2022,
        "journal": "NeurIPS",
        "doi": "N/A",
        "keywords": ["MACE", "equivariant", "neural network", "interatomic potential"],
        "abstract": "E(3)-equivariant message passing for accurate interatomic potentials.",
    },
    {
        "title": "NequIP: E(3)-equivariant graph neural networks for data-efficient interatomic potentials",
        "authors": "Batzner et al.",
        "year": 2022,
        "journal": "Nat. Commun.",
        "doi": "10.1038/s41467-022-29939-5",
        "keywords": ["NequIP", "equivariant", "graph neural network", "interatomic potential"],
        "abstract": "Data-efficient E(3)-equivariant GNN for interatomic potentials.",
    },
]


class MockLiteratureTool(Tool):
    """Mock literature search returning papers from the paper's reference list."""

    name = "literature"
    description = "Search scientific literature (mock)"

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        if "query" not in params:
            return False, "Missing required parameter: query"
        return True, "Parameters OK."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        query: str = params["query"].lower()
        max_results: int = params.get("max_results", 10)

        # Score each paper by keyword overlap with query
        scored: list[tuple[float, dict[str, Any]]] = []
        query_tokens = set(query.split())

        for paper in _MOCK_PAPERS:
            kw_tokens: set[str] = set()
            for kw in paper["keywords"]:
                kw_tokens.update(kw.lower().split())
            title_tokens = set(paper["title"].lower().split())
            all_tokens = kw_tokens | title_tokens

            overlap = len(query_tokens & all_tokens)
            if overlap > 0 or any(qt in " ".join(all_tokens) for qt in query_tokens):
                score = overlap + 0.1  # ensure non-zero
            else:
                score = 0.0

            scored.append((score, paper))

        # Sort by relevance
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [p for s, p in scored if s > 0][:max_results]

        return ToolResult(
            success=True,
            output={
                "query": params["query"],
                "n_results": len(results),
                "papers": results,
            },
        )

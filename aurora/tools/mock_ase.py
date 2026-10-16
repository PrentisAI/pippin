"""Mock atomic-structure manipulation tool (ASE-like).

Provides structure construction operations for Cu–Zn alloy slab models
with adsorbates, matching the systems studied in the paper:
* fcc Cu facets: (111), (100), (110), (211)
* hcp Zn facets: (0001), (10-10), (11-20), (10-11)
* CO₂-hydrogenation intermediates as adsorbates
"""

from __future__ import annotations

import uuid
from typing import Any

from aurora.tools.base import Tool, ToolResult

# Lattice parameters (Å)
_CU_A0 = 3.6366  # DFT-optimised fcc Cu (paper)
_ZN_A = 2.6649
_ZN_C = 4.9468

# Supercell sizes per facet (matching paper Table 1)
_CU_SUPERCELL: dict[str, tuple[int, int]] = {
    "111": (5, 5),
    "100": (5, 5),
    "110": (4, 5),
    "211": (4, 5),
}
_ZN_SUPERCELL: dict[str, tuple[int, int]] = {
    "0001": (4, 4),
    "10-10": (4, 4),
    "11-20": (3, 3),
    "10-11": (3, 3),
}

INTERMEDIATES = [
    "CO", "CO2", "H", "O", "OH", "H2O", "HCO", "HCOO", "H2COO", "CH3OH",
]


def _make_slab(
    host: str,
    facet: str,
    layers: int,
    vacuum: float,
) -> dict[str, Any]:
    """Return a synthetic slab structure dict."""
    if host == "cu":
        a0 = _CU_A0
        sc = _CU_SUPERCELL.get(facet, (4, 4))
        n_per_layer = sc[0] * sc[1]
    else:
        a0 = _ZN_A
        sc = _ZN_SUPERCELL.get(facet, (3, 3))
        n_per_layer = sc[0] * sc[1]

    n_atoms = n_per_layer * layers
    element = "Cu" if host == "cu" else "Zn"
    atoms = [element] * n_atoms

    # Simplified positions (placeholder; real ASE would compute these)
    positions = [
        [
            (i % sc[0]) * a0,
            ((i // sc[0]) % sc[1]) * a0,
            (i // n_per_layer) * (a0 / 2),
        ]
        for i in range(n_atoms)
    ]

    cell = [
        [sc[0] * a0, 0.0, 0.0],
        [0.0, sc[1] * a0, 0.0],
        [0.0, 0.0, layers * (a0 / 2) + vacuum],
    ]

    return {
        "atoms": atoms,
        "positions": positions,
        "cell": cell,
        "host": host,
        "facet": facet,
        "layers": layers,
        "vacuum": vacuum,
        "supercell": list(sc),
        "n_atoms": n_atoms,
        "pbc": [True, True, False],
    }


class MockASETool(Tool):
    """Mock atomic-structure builder.

    Supported operations
    --------------------
    ``build_slab``
        Create a periodic slab for a given host metal and facet.
    ``build_bulk``
        Create a bulk unit cell.
    ``add_dopant``
        Replace one atom with a dopant (Zn in Cu slab or Cu in Zn slab).
    ``add_adsorbate``
        Place an adsorbate molecule on the slab surface.
    """

    name = "mock_ase"
    description = "Mock atomic structure construction and manipulation"

    FACETS = {
        "cu_fcc": ["111", "100", "110", "211"],
        "zn_hcp": ["0001", "10-10", "11-20", "10-11"],
    }

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        op = params.get("operation")
        if op not in ("build_slab", "build_bulk", "add_dopant", "add_adsorbate"):
            return False, f"Unknown operation: {op!r}"

        if op == "build_slab":
            host = params.get("host")
            if host not in ("cu", "zn"):
                return False, f"host must be 'cu' or 'zn', got {host!r}"
            facet = params.get("facet", "")
            valid_facets = (
                self.FACETS["cu_fcc"] if host == "cu" else self.FACETS["zn_hcp"]
            )
            if facet not in valid_facets:
                return False, (
                    f"Facet {facet!r} not valid for {host}; "
                    f"choose from {valid_facets}"
                )

        if op in ("add_dopant", "add_adsorbate"):
            if "structure" not in params:
                return False, "Missing required parameter: structure"

        if op == "add_adsorbate":
            mol = params.get("molecule", "")
            if mol not in INTERMEDIATES:
                return False, (
                    f"Unknown intermediate {mol!r}; "
                    f"choose from {INTERMEDIATES}"
                )

        return True, "Parameters OK."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        op = params["operation"]

        if op == "build_bulk":
            return self._build_bulk(params)
        elif op == "build_slab":
            return self._build_slab(params)
        elif op == "add_dopant":
            return self._add_dopant(params)
        elif op == "add_adsorbate":
            return self._add_adsorbate(params)

        return ToolResult(success=False, error=f"Unhandled operation: {op}")

    # ------------------------------------------------------------------

    def _build_bulk(self, params: dict[str, Any]) -> ToolResult:
        element = params.get("element", "Cu")
        a0 = _CU_A0 if element == "Cu" else _ZN_A
        structure = {
            "atoms": [element],
            "positions": [[0.0, 0.0, 0.0]],
            "cell": [[a0, 0, 0], [0, a0, 0], [0, 0, a0]],
            "host": element.lower(),
            "n_atoms": 1,
            "pbc": [True, True, True],
        }
        return ToolResult(success=True, output={"structure": structure})

    def _build_slab(self, params: dict[str, Any]) -> ToolResult:
        host: str = params["host"]
        facet: str = params["facet"]
        layers: int = params.get("layers", 4)
        vacuum: float = params.get("vacuum", 15.0)

        structure = _make_slab(host, facet, layers, vacuum)
        structure["structure_id"] = str(uuid.uuid4())[:8]

        return ToolResult(
            success=True,
            output={"structure": structure},
            metadata={"host": host, "facet": facet, "layers": layers},
        )

    def _add_dopant(self, params: dict[str, Any]) -> ToolResult:
        structure: dict[str, Any] = dict(params["structure"])  # shallow copy
        atoms = list(structure.get("atoms", []))
        dopant = params.get("dopant_element", "Zn")
        site = params.get("site", "surface")  # "surface" or "bulk"

        if not atoms:
            return ToolResult(success=False, error="Structure has no atoms.")

        # Pick index: surface = last layer, bulk = middle layer
        n_atoms = len(atoms)
        if site == "surface":
            idx = n_atoms - 1  # top atom
        else:
            idx = n_atoms // 2  # middle atom

        atoms[idx] = dopant
        structure["atoms"] = atoms
        structure["dopant_site"] = site
        structure["dopant_index"] = idx
        structure["dopant_element"] = dopant

        return ToolResult(
            success=True,
            output={"structure": structure},
            metadata={"dopant": dopant, "site": site, "index": idx},
        )

    def _add_adsorbate(self, params: dict[str, Any]) -> ToolResult:
        structure: dict[str, Any] = dict(params["structure"])
        molecule: str = params["molecule"]
        site: str = params.get("site", "ontop")
        height: float = params.get("height", 2.0)

        # Append adsorbate atoms (simplified — just tag the structure)
        ads_atoms = list(molecule)  # crude: split string into characters
        atoms = list(structure.get("atoms", []))
        positions = list(structure.get("positions", []))

        # Place adsorbate above the surface atom
        if positions:
            top_pos = positions[-1]
            for i, a in enumerate(ads_atoms):
                pos = [top_pos[0], top_pos[1], top_pos[2] + height + i * 1.2]
                atoms.append(a)
                positions.append(pos)

        structure["atoms"] = atoms
        structure["positions"] = positions
        structure["adsorbate"] = molecule
        structure["adsorbate_site"] = site
        structure["n_atoms"] = len(atoms)

        return ToolResult(
            success=True,
            output={"structure": structure},
            metadata={
                "molecule": molecule,
                "site": site,
                "height": height,
                "n_adsorbate_atoms": len(ads_atoms),
            },
        )

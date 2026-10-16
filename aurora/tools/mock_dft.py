"""Mock DFT (Quantum ESPRESSO) simulator.

Returns physically plausible energetics for Cu–Zn alloy systems with
configurable failure injection for testing error-recovery paths.

Failure modes mirror the paper:
* SCF non-convergence (configurable rate)
* BFGS death-loop from truncated history file (Transition II)
* Force-convergence plateau (best-effort result)
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
from typing import Any

from aurora.tools.base import Tool, ToolResult


# ---------------------------------------------------------------------------
# Physical constants and reference energetics
# ---------------------------------------------------------------------------

# Approximate DFT-PBE energy per atom (eV) for common elements in bulk
_BULK_ENERGY: dict[str, float] = {
    "Cu": -3.7298,
    "Zn": -1.1005,
    "O": -4.9485,
    "C": -8.6125,
    "H": -1.1156,
}

# Clean-surface segregation energies (eV) for Zn-in-Cu on each fcc facet
# (from paper: −0.235, −0.295, −0.258, −0.304 on 100, 110, 111, 211)
_CLEAN_SEG_ENERGY: dict[str, float] = {
    "100": -0.235,
    "110": -0.295,
    "111": -0.258,
    "211": -0.304,
}

# Typical ΔΔE_seg shifts by intermediate category (eV, approximate)
_INTERMEDIATE_SHIFT: dict[str, float] = {
    "CO": 0.26,
    "HCO": 0.35,
    "H": 0.25,
    "O": -0.10,
    "OH": -0.08,
    "CO2": 0.02,
    "H2O": 0.01,
    "HCOO": -0.15,
    "H2COO": -0.05,
    "CH3OH": 0.03,
}


def _seed_from_params(params: dict[str, Any]) -> int:
    """Derive a reproducible seed from the parameter dictionary."""
    raw = str(sorted(params.items()))
    return int(hashlib.md5(raw.encode()).hexdigest()[:8], 16)


class MockDFTTool(Tool):
    """Mock plane-wave DFT calculator (Quantum ESPRESSO).

    Parameters
    ----------
    failure_rate :
        Probability that an SCF calculation fails to converge.
    death_loop_rate :
        Probability of a BFGS death-loop (truncated history file).
    base_energy_per_atom :
        Reference energy per atom in eV (≈ Cu bulk).
    noise_scale :
        Gaussian noise standard deviation in eV.
    min_wall_time / max_wall_time :
        Simulated wall-clock range in seconds.
    """

    name = "mock_dft"
    description = "Mock plane-wave DFT calculation (Quantum ESPRESSO)"

    def __init__(
        self,
        failure_rate: float = 0.10,
        death_loop_rate: float = 0.02,
        base_energy_per_atom: float = -3.73,
        noise_scale: float = 0.05,
        min_wall_time: float = 0.5,
        max_wall_time: float = 2.0,
    ) -> None:
        self.failure_rate = failure_rate
        self.death_loop_rate = death_loop_rate
        self.base_energy_per_atom = base_energy_per_atom
        self.noise_scale = noise_scale
        self.min_wall_time = min_wall_time
        self.max_wall_time = max_wall_time

    # ----- validation -----

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        required = {"structure", "kpoints", "ecutwfc", "ecutrho", "calculation"}
        missing = required - set(params)
        if missing:
            return False, f"Missing required parameters: {sorted(missing)}"

        if params.get("calculation") not in ("scf", "relax", "vc-relax"):
            return False, f"Unsupported calculation type: {params.get('calculation')}"

        kpts = params.get("kpoints", [])
        if not (isinstance(kpts, (list, tuple)) and len(kpts) == 3):
            return False, "kpoints must be a list of three integers."

        return True, "Parameters OK."

    # ----- execution -----

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        rng = random.Random(_seed_from_params(params))
        wall = rng.uniform(self.min_wall_time, self.max_wall_time)

        # Simulate computation time
        await asyncio.sleep(wall)

        # --- Failure injection ---
        if rng.random() < self.death_loop_rate:
            return ToolResult(
                success=False,
                error=(
                    "BFGS history file truncated — non-converging death loop "
                    "detected. Optimizer reset to low ionic step and never "
                    "reaches convergence (see paper Transition II)."
                ),
                wall_time_seconds=wall,
                metadata={"failure_type": "death_loop"},
            )

        if rng.random() < self.failure_rate:
            return ToolResult(
                success=False,
                error="SCF convergence not achieved after maximum iterations.",
                wall_time_seconds=wall,
                metadata={"failure_type": "scf_non_convergence"},
            )

        # --- Successful calculation ---
        structure = params.get("structure", {})
        atoms: list[str] = structure.get("atoms", ["Cu"] * 16)
        n_atoms = len(atoms)
        kpoints: list[int] = params["kpoints"]
        facet = structure.get("facet", "111")
        dopant_site = structure.get("dopant_site", "surface")  # surface | bulk
        adsorbate = structure.get("adsorbate")

        # Base energy
        total_energy = sum(_BULK_ENERGY.get(a, self.base_energy_per_atom) for a in atoms)

        # k-point quality correction (fewer k-points → larger noise)
        k_product = max(1, kpoints[0] * kpoints[1] * kpoints[2])
        k_noise_factor = 1.0 / math.sqrt(k_product)
        total_energy += rng.gauss(0, self.noise_scale * k_noise_factor)

        # Segregation contribution
        seg = _CLEAN_SEG_ENERGY.get(facet, -0.26)
        if dopant_site == "surface":
            total_energy += seg
        # else: bulk site, no segregation shift

        # Adsorbate contribution
        if adsorbate:
            shift = _INTERMEDIATE_SHIFT.get(adsorbate, 0.0)
            total_energy += shift + rng.gauss(0, 0.02)

        energy_per_atom = total_energy / max(n_atoms, 1)

        # Synthetic forces (small random near zero for relaxed structures)
        forces = [[rng.gauss(0, 0.005) for _ in range(3)] for _ in range(n_atoms)]

        # Convergence check — relaxation may end at plateau
        calc_type = params.get("calculation", "scf")
        max_steps = params.get("max_steps", 100)
        converged = True
        n_steps = rng.randint(5, min(max_steps, 80))

        # ~20% chance of force-plateau (best-effort, not strictly converged)
        if calc_type == "relax" and rng.random() < 0.20:
            converged = False
            n_steps = max_steps
            # Still return result — it's a best-effort calculation

        return ToolResult(
            success=True,
            output={
                "total_energy": round(total_energy, 6),
                "energy_per_atom": round(energy_per_atom, 6),
                "forces": forces,
                "converged": converged,
                "n_steps": n_steps,
                "stress_tensor": [[rng.gauss(0, 0.01) for _ in range(3)] for _ in range(3)],
                "n_atoms": n_atoms,
                "kpoints_used": kpoints,
                "ecutwfc": params["ecutwfc"],
                "ecutrho": params["ecutrho"],
            },
            wall_time_seconds=wall,
            metadata={
                "calculation": calc_type,
                "facet": facet,
                "dopant_site": dopant_site,
                "adsorbate": adsorbate,
                "best_effort": not converged,
            },
        )

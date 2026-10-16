"""Mock machine-learning interatomic potential (MLIP) evaluator.

Simulates NequIP / MACE / CHGNet surrogate models that are faster but
less accurate than DFT.  The mock reproduces the paper's observation
that the surrogate *systematically mis-signed* oxygen-bearing adsorbates
and manufactured spurious high-coverage blow-ups (Transition I).
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from typing import Any

from aurora.tools.base import Tool, ToolResult

# Intermediates with oxygen that the surrogate tends to mis-sign (paper obs.)
_OXYGEN_BEARING = {"O", "OH", "CO2", "HCOO", "H2COO", "CH3OH", "H2O"}

# Approximate ΔΔE_seg from DFT (ground truth) by intermediate
_TRUE_SHIFT: dict[str, float] = {
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
    raw = str(sorted(params.items()))
    return int(hashlib.md5(raw.encode()).hexdigest()[:8], 16)


class MockMLIPTool(Tool):
    """Mock MLIP surrogate evaluator.

    Parameters
    ----------
    model_name :
        Surrogate identity: ``"nequip"``, ``"mace"``, or ``"chgnet"``.
    systematic_bias :
        Constant energy bias in eV added to all predictions.
    noise_scale :
        Gaussian noise σ (higher than DFT).
    sign_flip_rate :
        Probability that an oxygen-bearing adsorbate's ΔΔE_seg sign is
        flipped, reproducing the MLIP mischaracterisation from the paper.
    """

    name = "mock_mlip"
    description = "Mock MLIP surrogate evaluation (NequIP/MACE/CHGNet)"

    def __init__(
        self,
        model_name: str = "nequip",
        systematic_bias: float = 0.05,
        noise_scale: float = 0.10,
        sign_flip_rate: float = 0.15,
    ) -> None:
        self.model_name = model_name
        self.systematic_bias = systematic_bias
        self.noise_scale = noise_scale
        self.sign_flip_rate = sign_flip_rate

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        if "structure" not in params:
            return False, "Missing required parameter: structure"
        return True, "Parameters OK."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        rng = random.Random(_seed_from_params(params))

        # MLIP is very fast compared to DFT
        await asyncio.sleep(rng.uniform(0.05, 0.15))

        structure = params.get("structure", {})
        atoms: list[str] = structure.get("atoms", ["Cu"] * 16)
        n_atoms = len(atoms)
        adsorbate: str | None = structure.get("adsorbate")
        facet: str = structure.get("facet", "111")
        dopant_site: str = structure.get("dopant_site", "surface")

        # Base energy (less accurate than DFT)
        total_energy = -3.73 * n_atoms + self.systematic_bias
        total_energy += rng.gauss(0, self.noise_scale)

        # Segregation
        if dopant_site == "surface":
            total_energy += -0.26  # rough average clean-surface seg. energy

        # Adsorbate shift — with possible sign flip
        delta_delta_e_seg = 0.0
        sign_flipped = False
        if adsorbate:
            true_shift = _TRUE_SHIFT.get(adsorbate, 0.0)
            delta_delta_e_seg = true_shift + rng.gauss(0, self.noise_scale)

            if adsorbate in _OXYGEN_BEARING and rng.random() < self.sign_flip_rate:
                delta_delta_e_seg = -delta_delta_e_seg
                sign_flipped = True

            total_energy += delta_delta_e_seg

        energy_per_atom = total_energy / max(n_atoms, 1)

        # Synthetic forces (noisier than DFT)
        forces = [[rng.gauss(0, 0.02) for _ in range(3)] for _ in range(n_atoms)]

        return ToolResult(
            success=True,
            output={
                "total_energy": round(total_energy, 6),
                "energy_per_atom": round(energy_per_atom, 6),
                "delta_delta_e_seg": round(delta_delta_e_seg, 4),
                "forces": forces,
                "n_atoms": n_atoms,
            },
            wall_time_seconds=0.1,
            metadata={
                "model_name": self.model_name,
                "facet": facet,
                "dopant_site": dopant_site,
                "adsorbate": adsorbate,
                "sign_flipped": sign_flipped,
                "systematic_bias": self.systematic_bias,
            },
        )

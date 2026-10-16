"""Real machine-learning interatomic potential (MLIP) tool wrapper.

Wraps MACE, CHGNet, and NequIP for single-point evaluations and
geometry relaxation.  Falls back gracefully when libraries are not
installed.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from aurora.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


def _check_ase_available() -> tuple[bool, str]:
    """ASE is needed as the common atoms interface for all MLIPs."""
    try:
        import ase  # noqa: F401
        return True, "ASE is available."
    except ImportError:
        return False, "ASE is required for MLIP tools. Install with: pip install ase"


def _check_model_available(model_name: str) -> tuple[bool, str]:
    """Check whether the requested MLIP backend is importable."""
    model_name = model_name.lower()
    if model_name == "mace":
        try:
            import mace  # noqa: F401
            return True, "MACE is available."
        except ImportError:
            return False, "MACE is not installed. Install with: pip install mace-torch"
    elif model_name == "chgnet":
        try:
            import chgnet  # noqa: F401
            return True, "CHGNet is available."
        except ImportError:
            return False, "CHGNet is not installed. Install with: pip install chgnet"
    elif model_name == "nequip":
        try:
            import nequip  # noqa: F401
            return True, "NequIP is available."
        except ImportError:
            return False, "NequIP is not installed. Install with: pip install nequip"
    else:
        return False, f"Unknown model_name: {model_name!r}; choose from mace, chgnet, nequip"


class RealMLIPTool(Tool):
    """Machine-learning interatomic potential evaluator.

    Supported operations
    --------------------
    ``evaluate``
        Single-point energy and force evaluation using the chosen MLIP.
    ``relax``
        Geometry relaxation using the chosen MLIP as a calculator.

    Supported model_name values: ``"mace"``, ``"chgnet"``, ``"nequip"``.
    """

    name = "mlip"
    description = "MLIP surrogate evaluation and relaxation (MACE/CHGNet/NequIP)"

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        ase_ok, ase_msg = _check_ase_available()
        if not ase_ok:
            return False, ase_msg

        op = params.get("operation")
        if op not in ("evaluate", "relax"):
            return False, f"Unknown operation: {op!r}; choose from ('evaluate', 'relax')"

        if "atoms_path" not in params:
            return False, "Missing required parameter: atoms_path"

        model_name = params.get("model_name", "mace")
        model_ok, model_msg = _check_model_available(model_name)
        if not model_ok:
            return False, model_msg

        return True, "Parameters OK."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        op = params["operation"]
        t0 = time.monotonic()
        try:
            if op == "evaluate":
                result = self._evaluate(params)
            elif op == "relax":
                result = self._relax(params)
            else:
                result = ToolResult(success=False, error=f"Unhandled: {op}")
            result.wall_time_seconds = time.monotonic() - t0
            return result
        except Exception as exc:
            logger.exception("MLIP operation %r failed", op)
            return ToolResult(
                success=False,
                error=f"MLIP operation {op!r} failed: {exc}",
                wall_time_seconds=time.monotonic() - t0,
            )

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _evaluate(self, params: dict[str, Any]) -> ToolResult:
        """Single-point energy/force evaluation."""
        from ase.io import read as ase_read

        atoms_path: str = params["atoms_path"]
        model_name: str = params.get("model_name", "mace")
        model_path: str | None = params.get("model_path")

        atoms = ase_read(atoms_path)
        calc = self._load_calculator(model_name, model_path)
        atoms.calc = calc

        energy = float(atoms.get_potential_energy())
        forces = atoms.get_forces().tolist()
        n_atoms = len(atoms)

        try:
            stress = atoms.get_stress().tolist()
        except Exception:
            stress = None

        return ToolResult(
            success=True,
            output={
                "total_energy": round(energy, 6),
                "energy_per_atom": round(energy / max(n_atoms, 1), 6),
                "forces": forces,
                "stress": stress,
                "n_atoms": n_atoms,
            },
            metadata={
                "model_name": model_name,
                "model_path": model_path,
            },
        )

    def _relax(self, params: dict[str, Any]) -> ToolResult:
        """Geometry relaxation using an MLIP calculator."""
        from ase.io import read as ase_read, write as ase_write
        from ase.optimize import BFGS

        atoms_path: str = params["atoms_path"]
        model_name: str = params.get("model_name", "mace")
        model_path: str | None = params.get("model_path")
        fmax: float = params.get("fmax", 0.05)
        steps: int = params.get("steps", 200)

        atoms = ase_read(atoms_path)
        calc = self._load_calculator(model_name, model_path)
        atoms.calc = calc

        optimizer = BFGS(atoms, logfile=None)
        converged = optimizer.run(fmax=fmax, steps=steps)

        energy = float(atoms.get_potential_energy())
        forces = atoms.get_forces().tolist()
        n_atoms = len(atoms)
        n_steps = optimizer.nsteps

        # Max force after relaxation
        import numpy as np
        max_force = float(np.max(np.linalg.norm(atoms.get_forces(), axis=1)))

        output_files: list[str] = []
        output_path = params.get("output_path")
        if output_path:
            import os
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            ase_write(output_path, atoms)
            output_files.append(output_path)

        return ToolResult(
            success=True,
            output={
                "total_energy": round(energy, 6),
                "energy_per_atom": round(energy / max(n_atoms, 1), 6),
                "forces": forces,
                "converged": bool(converged),
                "n_steps": n_steps,
                "max_force": round(max_force, 6),
                "n_atoms": n_atoms,
            },
            output_files=output_files,
            metadata={
                "model_name": model_name,
                "model_path": model_path,
                "fmax": fmax,
                "steps": steps,
            },
        )

    # ------------------------------------------------------------------
    # Calculator loading
    # ------------------------------------------------------------------

    def _load_calculator(
        self, model_name: str, model_path: str | None
    ) -> Any:
        """Instantiate the appropriate MLIP calculator.

        Parameters
        ----------
        model_name :
            One of ``"mace"``, ``"chgnet"``, ``"nequip"``.
        model_path :
            Optional path to a trained model checkpoint.  If ``None``,
            use the library's default pretrained model (when available).
        """
        model_name = model_name.lower()

        if model_name == "mace":
            return self._load_mace(model_path)
        elif model_name == "chgnet":
            return self._load_chgnet(model_path)
        elif model_name == "nequip":
            return self._load_nequip(model_path)
        else:
            raise ValueError(f"Unknown model: {model_name}")

    @staticmethod
    def _load_mace(model_path: str | None) -> Any:
        """Load a MACE calculator."""
        from mace.calculators import MACECalculator

        if model_path:
            return MACECalculator(model_paths=model_path, device="cpu")

        # Use the default pretrained MACE-MP model
        try:
            from mace.calculators import mace_mp
            return mace_mp(default_dtype="float64")
        except (ImportError, Exception):
            # Older MACE versions
            return MACECalculator(
                model_paths="https://github.com/ACEsuit/mace-mp/releases/download/mace_mp_0/2023-12-10-mace-128-L0_energy_composition_statwt_f_b-epoch-249.model",
                device="cpu",
            )

    @staticmethod
    def _load_chgnet(model_path: str | None) -> Any:
        """Load a CHGNet calculator."""
        from chgnet.model.dynamics import CHGNetCalculator

        if model_path:
            from chgnet.model.model import CHGNet
            model = CHGNet.from_file(model_path)
            return CHGNetCalculator(model=model)

        # Default pretrained CHGNet
        return CHGNetCalculator()

    @staticmethod
    def _load_nequip(model_path: str | None) -> Any:
        """Load a NequIP calculator.

        NequIP requires a trained model path — there is no default
        pretrained model.
        """
        if model_path is None:
            raise ValueError(
                "NequIP requires a model_path to a trained .pth model file."
            )

        from nequip.ase import NequIPCalculator

        return NequIPCalculator.from_deployed_model(
            model_path=model_path,
        )

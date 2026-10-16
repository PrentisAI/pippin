"""Real Quantum ESPRESSO DFT tool wrapper.

Generates pw.x input files, launches calculations via ``mpirun``,
and parses output files for energies, forces, and convergence status.
Falls back gracefully when Quantum ESPRESSO is not installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from aurora.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


def _check_qe_available() -> tuple[bool, str]:
    """Check whether Quantum ESPRESSO's ``pw.x`` is on the PATH."""
    import shutil

    if shutil.which("pw.x") is not None:
        return True, "Quantum ESPRESSO (pw.x) is available."
    return False, (
        "Quantum ESPRESSO (pw.x) not found on PATH. "
        "Install QE or ensure pw.x is in your $PATH."
    )


class RealDFTTool(Tool):
    """Plane-wave DFT calculator wrapping Quantum ESPRESSO pw.x.

    Supported operations
    --------------------
    ``setup_calculation``
        Generate a pw.x input file from atomic structure and parameters.
    ``run_calculation``
        Launch ``mpirun pw.x`` via subprocess.
    ``parse_output``
        Parse a pw.x output file for total energy, forces, etc.
    ``check_convergence``
        Check SCF and ionic convergence from output.
    """

    name = "dft"
    description = "Plane-wave DFT calculation (Quantum ESPRESSO pw.x)"

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        op = params.get("operation")
        valid_ops = (
            "setup_calculation", "run_calculation",
            "parse_output", "check_convergence",
        )
        if op not in valid_ops:
            return False, f"Unknown operation: {op!r}; choose from {valid_ops}"

        if op == "setup_calculation":
            if "atoms_path" not in params:
                return False, "Missing required parameter: atoms_path"
            if "calculation_type" not in params:
                return False, "Missing required parameter: calculation_type"

        if op == "run_calculation":
            available, msg = _check_qe_available()
            if not available:
                return False, msg
            if "input_file" not in params:
                return False, "Missing required parameter: input_file"

        if op in ("parse_output", "check_convergence"):
            if "output_file" not in params:
                return False, "Missing required parameter: output_file"

        return True, "Parameters OK."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        op = params["operation"]
        t0 = time.monotonic()
        try:
            if op == "setup_calculation":
                result = self._setup_calculation(params)
            elif op == "run_calculation":
                result = await self._run_calculation(params)
            elif op == "parse_output":
                result = self._parse_output(params)
            elif op == "check_convergence":
                result = self._check_convergence(params)
            else:
                result = ToolResult(success=False, error=f"Unhandled: {op}")
            result.wall_time_seconds = time.monotonic() - t0
            return result
        except Exception as exc:
            logger.exception("DFT operation %r failed", op)
            return ToolResult(
                success=False,
                error=f"DFT operation {op!r} failed: {exc}",
                wall_time_seconds=time.monotonic() - t0,
            )

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _setup_calculation(self, params: dict[str, Any]) -> ToolResult:
        """Generate a pw.x input file.

        Reads atomic structure from ``atoms_path`` (using ASE if available,
        otherwise expecting a JSON description) and writes a QE-format
        input file.

        Parameters in ``params``:
        - atoms_path: path to structure file
        - calculation_type: "scf" | "relax" | "vc-relax"
        - params: dict with ecutwfc, ecutrho, kpoints, pseudo_dir, etc.
        - output_path: where to write the input file
        """
        atoms_path: str = params["atoms_path"]
        calc_type: str = params["calculation_type"]
        calc_params: dict[str, Any] = params.get("params", {})
        output_path: str = params.get("output_path", "pw.in")

        # Read structure
        positions, symbols, cell = self._read_atoms(atoms_path)
        if positions is None:
            return ToolResult(
                success=False,
                error=f"Could not read structure from {atoms_path}",
            )

        n_atoms = len(symbols)
        ecutwfc = calc_params.get("ecutwfc", 60.0)
        ecutrho = calc_params.get("ecutrho", ecutwfc * 8)
        kpoints = calc_params.get("kpoints", [4, 4, 1])
        pseudo_dir = calc_params.get("pseudo_dir", "./pseudo")
        outdir = calc_params.get("outdir", "./tmp")
        prefix = calc_params.get("prefix", "pwscf")
        conv_thr = calc_params.get("conv_thr", 1.0e-8)
        mixing_beta = calc_params.get("mixing_beta", 0.3)
        smearing = calc_params.get("smearing", "cold")
        degauss = calc_params.get("degauss", 0.02)
        forc_conv_thr = calc_params.get("forc_conv_thr", 1.0e-3)

        # Determine unique species
        unique_species = list(dict.fromkeys(symbols))
        n_types = len(unique_species)

        # Build pseudopotential mapping
        pseudos = calc_params.get("pseudopotentials", {})

        lines = []
        lines.append("&CONTROL")
        lines.append(f"  calculation = '{calc_type}',")
        lines.append(f"  prefix = '{prefix}',")
        lines.append(f"  pseudo_dir = '{pseudo_dir}',")
        lines.append(f"  outdir = '{outdir}',")
        lines.append(f"  tprnfor = .true.,")
        lines.append(f"  tstress = .true.,")
        if calc_type in ("relax", "vc-relax"):
            lines.append(f"  forc_conv_thr = {forc_conv_thr},")
        lines.append("/")
        lines.append("")
        lines.append("&SYSTEM")
        lines.append(f"  ibrav = 0,")
        lines.append(f"  nat = {n_atoms},")
        lines.append(f"  ntyp = {n_types},")
        lines.append(f"  ecutwfc = {ecutwfc},")
        lines.append(f"  ecutrho = {ecutrho},")
        lines.append(f"  occupations = 'smearing',")
        lines.append(f"  smearing = '{smearing}',")
        lines.append(f"  degauss = {degauss},")
        lines.append("/")
        lines.append("")
        lines.append("&ELECTRONS")
        lines.append(f"  conv_thr = {conv_thr},")
        lines.append(f"  mixing_beta = {mixing_beta},")
        lines.append("/")
        lines.append("")

        if calc_type in ("relax", "vc-relax"):
            lines.append("&IONS")
            lines.append("  ion_dynamics = 'bfgs',")
            lines.append("/")
            lines.append("")

        if calc_type == "vc-relax":
            lines.append("&CELL")
            lines.append("  cell_dynamics = 'bfgs',")
            lines.append("/")
            lines.append("")

        # ATOMIC_SPECIES
        lines.append("ATOMIC_SPECIES")
        for sp in unique_species:
            # Use provided pseudo or default naming convention
            pseudo = pseudos.get(sp, f"{sp}.UPF")
            # Rough atomic masses
            mass = self._atomic_mass(sp)
            lines.append(f"  {sp}  {mass:.4f}  {pseudo}")
        lines.append("")

        # CELL_PARAMETERS
        lines.append("CELL_PARAMETERS angstrom")
        for row in cell:
            lines.append(f"  {row[0]:.10f}  {row[1]:.10f}  {row[2]:.10f}")
        lines.append("")

        # ATOMIC_POSITIONS
        lines.append("ATOMIC_POSITIONS angstrom")
        for sym, pos in zip(symbols, positions):
            lines.append(f"  {sym}  {pos[0]:.10f}  {pos[1]:.10f}  {pos[2]:.10f}")
        lines.append("")

        # K_POINTS
        lines.append("K_POINTS automatic")
        kp = kpoints if len(kpoints) >= 3 else kpoints + [1] * (3 - len(kpoints))
        lines.append(f"  {kp[0]} {kp[1]} {kp[2]}  0 0 0")
        lines.append("")

        input_text = "\n".join(lines)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        Path(output_path).write_text(input_text, encoding="utf-8")

        return ToolResult(
            success=True,
            output={
                "input_file": output_path,
                "calculation_type": calc_type,
                "n_atoms": n_atoms,
                "n_types": n_types,
                "ecutwfc": ecutwfc,
                "ecutrho": ecutrho,
                "kpoints": kp,
            },
            output_files=[output_path],
            metadata={"calculation_type": calc_type},
        )

    async def _run_calculation(self, params: dict[str, Any]) -> ToolResult:
        """Launch pw.x via mpirun as a subprocess."""
        input_file: str = params["input_file"]
        output_file: str = params.get("output_file", input_file.replace(".in", ".out"))
        num_procs: int = params.get("num_procs", 1)
        gpu: bool = params.get("gpu", False)

        if not Path(input_file).exists():
            return ToolResult(
                success=False,
                error=f"Input file not found: {input_file}",
            )

        # Build command
        cmd: list[str] = []
        if num_procs > 1:
            cmd.extend(["mpirun", "-np", str(num_procs)])
        cmd.append("pw.x")
        if gpu:
            cmd.extend(["-gpu", "1"])
        cmd.extend(["-in", input_file])

        logger.info("Running QE: %s > %s", " ".join(cmd), output_file)

        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as out_fh:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=out_fh,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

        if proc.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
            return ToolResult(
                success=False,
                error=(
                    f"pw.x exited with code {proc.returncode}. "
                    f"stderr: {stderr_text[:500]}"
                ),
                output_files=[output_file],
                metadata={"returncode": proc.returncode},
            )

        return ToolResult(
            success=True,
            output={
                "output_file": output_file,
                "returncode": proc.returncode,
            },
            output_files=[output_file],
            metadata={"command": " ".join(cmd)},
        )

    def _parse_output(self, params: dict[str, Any]) -> ToolResult:
        """Parse a pw.x output file for key results.

        Extracts: total_energy, forces, stress, n_scf_iterations, wall_time.
        """
        output_file: str = params["output_file"]

        if not Path(output_file).exists():
            return ToolResult(
                success=False,
                error=f"Output file not found: {output_file}",
            )

        text = Path(output_file).read_text(encoding="utf-8", errors="replace")

        # Total energy
        energy_matches = re.findall(
            r"!\s+total energy\s+=\s+([-\d.]+)\s+Ry", text
        )
        total_energy_ry = float(energy_matches[-1]) if energy_matches else None
        total_energy_ev = total_energy_ry * 13.605693 if total_energy_ry else None

        # Forces
        forces: list[list[float]] = []
        force_block = re.search(
            r"Forces acting on atoms.*?\n(.*?)(?=\n\s*Total force|\n\s*$)",
            text, re.DOTALL,
        )
        if force_block:
            for line in force_block.group(1).strip().splitlines():
                m = re.search(
                    r"force\s*=\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", line
                )
                if m:
                    forces.append([float(m.group(i)) for i in (1, 2, 3)])

        # Total force
        total_force_match = re.search(
            r"Total force\s*=\s*([\d.]+)", text
        )
        total_force = float(total_force_match.group(1)) if total_force_match else None

        # Wall time
        wall_match = re.search(
            r"PWSCF\s*:\s*(\S+)\s+CPU\s+(\S+)\s+WALL", text
        )
        wall_time_str = wall_match.group(2) if wall_match else None

        # Number of SCF iterations
        scf_matches = re.findall(r"convergence has been achieved in\s+(\d+)", text)
        n_scf_iterations = int(scf_matches[-1]) if scf_matches else None

        # Number of atoms
        nat_match = re.search(r"number of atoms/cell\s*=\s*(\d+)", text)
        n_atoms = int(nat_match.group(1)) if nat_match else None

        energy_per_atom = (
            total_energy_ev / n_atoms
            if total_energy_ev is not None and n_atoms
            else None
        )

        output: dict[str, Any] = {
            "total_energy_ry": total_energy_ry,
            "total_energy_ev": round(total_energy_ev, 6) if total_energy_ev else None,
            "energy_per_atom_ev": round(energy_per_atom, 6) if energy_per_atom else None,
            "forces": forces,
            "total_force": total_force,
            "n_scf_iterations": n_scf_iterations,
            "n_atoms": n_atoms,
            "wall_time": wall_time_str,
        }

        return ToolResult(
            success=True,
            output=output,
            metadata={"output_file": output_file},
        )

    def _check_convergence(self, params: dict[str, Any]) -> ToolResult:
        """Check SCF and ionic convergence from a pw.x output file."""
        output_file: str = params["output_file"]

        if not Path(output_file).exists():
            return ToolResult(
                success=False,
                error=f"Output file not found: {output_file}",
            )

        text = Path(output_file).read_text(encoding="utf-8", errors="replace")

        scf_converged = "convergence has been achieved" in text
        scf_not_converged = "convergence NOT achieved" in text

        # Ionic convergence (for relax / vc-relax)
        ionic_converged = "End final coordinates" in text or "bfgs converged" in text.lower()

        # Check for JOB DONE
        job_done = "JOB DONE" in text

        # Check for common errors
        errors: list[str] = []
        if "error" in text.lower() and "convergence" not in text.lower():
            # Try to find error messages
            for line in text.splitlines():
                ll = line.strip().lower()
                if ll.startswith("error") or "%%%%" in line:
                    errors.append(line.strip())

        return ToolResult(
            success=True,
            output={
                "scf_converged": scf_converged and not scf_not_converged,
                "ionic_converged": ionic_converged,
                "job_done": job_done,
                "errors": errors[:10],
                "converged": (scf_converged and not scf_not_converged)
                             and (ionic_converged or "relax" not in text.lower()[:200]),
            },
            metadata={"output_file": output_file},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_atoms(
        self, path: str
    ) -> tuple[list[list[float]] | None, list[str] | None, list[list[float]] | None]:
        """Read atomic positions, symbols, and cell from a file.

        Tries ASE first; falls back to a simple JSON reader.
        """
        try:
            from ase.io import read as ase_read
            atoms = ase_read(path)
            return (
                atoms.positions.tolist(),
                list(atoms.get_chemical_symbols()),
                atoms.cell.tolist(),
            )
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("ASE read failed for %s: %s", path, exc)

        # Fallback: try JSON
        try:
            import json
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return (
                data.get("positions"),
                data.get("symbols") or data.get("atoms"),
                data.get("cell"),
            )
        except Exception:
            pass

        return None, None, None

    @staticmethod
    def _atomic_mass(symbol: str) -> float:
        """Return approximate atomic mass for common elements."""
        masses: dict[str, float] = {
            "H": 1.008, "He": 4.003, "Li": 6.941, "Be": 9.012,
            "B": 10.81, "C": 12.011, "N": 14.007, "O": 15.999,
            "F": 18.998, "Na": 22.990, "Mg": 24.305, "Al": 26.982,
            "Si": 28.086, "P": 30.974, "S": 32.065, "Cl": 35.453,
            "K": 39.098, "Ca": 40.078, "Ti": 47.867, "V": 50.942,
            "Cr": 51.996, "Mn": 54.938, "Fe": 55.845, "Co": 58.933,
            "Ni": 58.693, "Cu": 63.546, "Zn": 65.38, "Ga": 69.723,
            "Ge": 72.630, "As": 74.922, "Se": 78.971, "Br": 79.904,
            "Zr": 91.224, "Nb": 92.906, "Mo": 95.95, "Ru": 101.07,
            "Rh": 102.906, "Pd": 106.42, "Ag": 107.868, "Sn": 118.710,
            "Pt": 195.084, "Au": 196.967, "Pb": 207.2,
        }
        return masses.get(symbol, 1.0)

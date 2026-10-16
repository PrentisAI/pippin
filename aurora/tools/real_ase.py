"""Real ASE (Atomic Simulation Environment) tool wrapper.

Wraps the ``ase`` library for atomic-structure construction and I/O.
Falls back gracefully when ASE is not installed.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from aurora.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


def _check_ase_available() -> tuple[bool, str]:
    """Return (available, message) for the ASE library."""
    try:
        import ase  # noqa: F401
        return True, "ASE is available."
    except ImportError:
        return False, (
            "ASE is not installed. Install with: pip install ase"
        )


class RealASETool(Tool):
    """Atomic structure construction and manipulation using ASE.

    Supported operations
    --------------------
    ``build_slab``
        Create a periodic slab for a given element and facet.
    ``build_bulk``
        Create a bulk unit cell.
    ``add_adsorbate``
        Place an adsorbate molecule on a slab surface.
    ``add_dopant``
        Replace one atom with a dopant element.
    ``write_structure``
        Write an Atoms object (from file) to a new path/format.
    ``read_structure``
        Read a structure file and return metadata.
    """

    name = "ase"
    description = "Atomic structure construction and manipulation (ASE)"

    # Mapping of crystal-structure type and facet to ASE builder functions.
    _SLAB_BUILDERS: dict[str, str] = {
        "fcc111": "fcc111",
        "fcc100": "fcc100",
        "fcc110": "fcc110",
        "fcc211": "fcc211",
        "bcc100": "bcc100",
        "bcc110": "bcc110",
        "bcc111": "bcc111",
        "hcp0001": "hcp0001",
        "hcp10m10": "hcp10m10",
    }

    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        available, msg = _check_ase_available()
        if not available:
            return False, msg

        op = params.get("operation")
        valid_ops = (
            "build_slab", "build_bulk", "add_adsorbate",
            "add_dopant", "write_structure", "read_structure",
        )
        if op not in valid_ops:
            return False, f"Unknown operation: {op!r}; choose from {valid_ops}"

        if op == "build_slab":
            if "element" not in params:
                return False, "Missing required parameter: element"
            if "facet" not in params:
                return False, "Missing required parameter: facet"

        if op == "build_bulk":
            if "element" not in params:
                return False, "Missing required parameter: element"

        if op in ("add_adsorbate", "add_dopant"):
            if "slab_path" not in params:
                return False, "Missing required parameter: slab_path"

        if op == "add_adsorbate":
            if "adsorbate" not in params:
                return False, "Missing required parameter: adsorbate"

        if op == "add_dopant":
            if "dopant_element" not in params:
                return False, "Missing required parameter: dopant_element"

        if op == "write_structure":
            if "path" not in params:
                return False, "Missing required parameter: path"
            if "atoms_path" not in params:
                return False, "Missing required parameter: atoms_path"

        if op == "read_structure":
            if "path" not in params:
                return False, "Missing required parameter: path"

        return True, "Parameters OK."

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        valid, msg = await self.validate_params(params)
        if not valid:
            return ToolResult(success=False, error=msg)

        op = params["operation"]
        t0 = time.monotonic()
        try:
            dispatch = {
                "build_slab": self._build_slab,
                "build_bulk": self._build_bulk,
                "add_adsorbate": self._add_adsorbate,
                "add_dopant": self._add_dopant,
                "write_structure": self._write_structure,
                "read_structure": self._read_structure,
            }
            result = dispatch[op](params)
            result.wall_time_seconds = time.monotonic() - t0
            return result
        except Exception as exc:
            logger.exception("ASE operation %r failed", op)
            return ToolResult(
                success=False,
                error=f"ASE operation {op!r} failed: {exc}",
                wall_time_seconds=time.monotonic() - t0,
            )

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _build_slab(self, params: dict[str, Any]) -> ToolResult:
        """Build a periodic slab using ASE builders."""
        from ase import build as ase_build
        from ase.io import write as ase_write

        element: str = params["element"]
        facet: str = params["facet"]
        size: tuple[int, ...] = tuple(params.get("size", (2, 2, 1)))
        layers: int = params.get("layers", 4)
        vacuum: float = params.get("vacuum", 15.0)

        # Try to find the appropriate builder.  Common pattern:
        # fcc111, fcc100, bcc110, hcp0001, etc.
        builder_name = None
        crystal = params.get("crystalstructure", "fcc")
        candidate = f"{crystal}{facet}"
        if hasattr(ase_build, candidate):
            builder_name = candidate

        if builder_name is None:
            # Try without crystal structure prefix — facet as-is
            if hasattr(ase_build, f"fcc{facet}"):
                builder_name = f"fcc{facet}"
            elif hasattr(ase_build, f"bcc{facet}"):
                builder_name = f"bcc{facet}"
            elif hasattr(ase_build, f"hcp{facet}"):
                builder_name = f"hcp{facet}"

        if builder_name is not None:
            builder_fn = getattr(ase_build, builder_name)
            kwargs: dict[str, Any] = {
                "symbol": element,
                "size": size,
                "vacuum": vacuum,
            }
            # Some builders accept 'layers' instead of the third size element
            import inspect
            sig = inspect.signature(builder_fn)
            if "layers" in sig.parameters:
                kwargs["layers"] = layers
                # Ensure size is 2-tuple for these builders
                if len(size) >= 2:
                    kwargs["size"] = (size[0], size[1])
            atoms = builder_fn(**kwargs)
        else:
            # Fallback: build bulk and use ase.build.surface
            from ase.build import bulk, surface
            bulk_atoms = bulk(element, crystalstructure=crystal)
            facet_ints = tuple(int(c) for c in facet if c.lstrip("-").isdigit())
            if len(facet_ints) < 3:
                return ToolResult(
                    success=False,
                    error=f"Cannot parse Miller indices from facet {facet!r}",
                )
            atoms = surface(bulk_atoms, facet_ints, layers=layers, vacuum=vacuum)
            # Tile in x, y
            if len(size) >= 2:
                atoms = atoms.repeat((size[0], size[1], 1))

        # Write output if path given
        output_files: list[str] = []
        output_path = params.get("output_path")
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            ase_write(output_path, atoms)
            output_files.append(output_path)

        return ToolResult(
            success=True,
            output={
                "n_atoms": len(atoms),
                "cell": atoms.cell.tolist(),
                "pbc": atoms.pbc.tolist(),
                "chemical_formula": atoms.get_chemical_formula(),
                "positions_shape": list(atoms.positions.shape),
            },
            output_files=output_files,
            metadata={
                "element": element,
                "facet": facet,
                "layers": layers,
                "vacuum": vacuum,
                "size": list(size),
            },
        )

    def _build_bulk(self, params: dict[str, Any]) -> ToolResult:
        """Build a bulk unit cell using ase.build.bulk."""
        from ase.build import bulk
        from ase.io import write as ase_write

        element: str = params["element"]
        crystalstructure: str | None = params.get("crystalstructure")
        a: float | None = params.get("a")

        kwargs: dict[str, Any] = {}
        if crystalstructure:
            kwargs["crystalstructure"] = crystalstructure
        if a is not None:
            kwargs["a"] = a

        atoms = bulk(element, **kwargs)

        output_files: list[str] = []
        output_path = params.get("output_path")
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            ase_write(output_path, atoms)
            output_files.append(output_path)

        return ToolResult(
            success=True,
            output={
                "n_atoms": len(atoms),
                "cell": atoms.cell.tolist(),
                "pbc": atoms.pbc.tolist(),
                "chemical_formula": atoms.get_chemical_formula(),
            },
            output_files=output_files,
            metadata={"element": element, "crystalstructure": crystalstructure},
        )

    def _add_adsorbate(self, params: dict[str, Any]) -> ToolResult:
        """Place an adsorbate on a slab surface."""
        from ase.build import add_adsorbate
        from ase.io import read as ase_read, write as ase_write

        slab_path: str = params["slab_path"]
        adsorbate_spec: str = params["adsorbate"]
        position: tuple[float, float] | str = params.get("position", "ontop")
        height: float = params.get("height", 2.0)

        slab = ase_read(slab_path)

        # The adsorbate can be an element symbol or a molecule string.
        # For simple species, use a string; ASE will create an Atom.
        from ase import Atom, Atoms as AseAtoms
        if len(adsorbate_spec) <= 2 and adsorbate_spec[0].isupper():
            ads = Atom(adsorbate_spec)
        else:
            # Try to build a small molecule; fall back to simple atom
            try:
                from ase.build import molecule
                ads = molecule(adsorbate_spec)
            except Exception:
                ads = Atom(adsorbate_spec)

        # Determine position on slab
        if isinstance(position, str):
            # Use a site on the topmost atom
            top_idx = slab.positions[:, 2].argmax()
            pos = tuple(slab.positions[top_idx, :2])
        else:
            pos = tuple(position)

        add_adsorbate(slab, ads, height=height, position=pos)

        # Write result
        output_files: list[str] = []
        output_path = params.get("output_path", slab_path)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        ase_write(output_path, slab)
        output_files.append(output_path)

        return ToolResult(
            success=True,
            output={
                "n_atoms": len(slab),
                "chemical_formula": slab.get_chemical_formula(),
            },
            output_files=output_files,
            metadata={
                "adsorbate": adsorbate_spec,
                "height": height,
            },
        )

    def _add_dopant(self, params: dict[str, Any]) -> ToolResult:
        """Replace one atom with a dopant element."""
        from ase.io import read as ase_read, write as ase_write

        slab_path: str = params["slab_path"]
        dopant_element: str = params["dopant_element"]
        site_index: int | None = params.get("site_index")

        atoms = ase_read(slab_path)

        if site_index is None:
            # Default: replace the topmost surface atom
            site_index = int(atoms.positions[:, 2].argmax())

        if site_index < 0 or site_index >= len(atoms):
            return ToolResult(
                success=False,
                error=(
                    f"site_index {site_index} out of range for "
                    f"structure with {len(atoms)} atoms."
                ),
            )

        original_element = atoms[site_index].symbol
        atoms[site_index].symbol = dopant_element

        output_files: list[str] = []
        output_path = params.get("output_path", slab_path)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        ase_write(output_path, atoms)
        output_files.append(output_path)

        return ToolResult(
            success=True,
            output={
                "n_atoms": len(atoms),
                "chemical_formula": atoms.get_chemical_formula(),
                "replaced": original_element,
                "dopant": dopant_element,
                "site_index": site_index,
            },
            output_files=output_files,
            metadata={
                "dopant_element": dopant_element,
                "site_index": site_index,
                "original_element": original_element,
            },
        )

    def _write_structure(self, params: dict[str, Any]) -> ToolResult:
        """Write a structure from one file/format to another."""
        from ase.io import read as ase_read, write as ase_write

        atoms_path: str = params["atoms_path"]
        output_path: str = params["path"]
        fmt: str | None = params.get("format")

        atoms = ase_read(atoms_path)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        kwargs: dict[str, Any] = {}
        if fmt:
            kwargs["format"] = fmt
        ase_write(output_path, atoms, **kwargs)

        return ToolResult(
            success=True,
            output={
                "n_atoms": len(atoms),
                "chemical_formula": atoms.get_chemical_formula(),
                "output_path": output_path,
            },
            output_files=[output_path],
        )

    def _read_structure(self, params: dict[str, Any]) -> ToolResult:
        """Read a structure file and return metadata."""
        from ase.io import read as ase_read

        path: str = params["path"]
        if not Path(path).exists():
            return ToolResult(success=False, error=f"File not found: {path}")

        atoms = ase_read(path)

        return ToolResult(
            success=True,
            output={
                "n_atoms": len(atoms),
                "chemical_formula": atoms.get_chemical_formula(),
                "cell": atoms.cell.tolist(),
                "pbc": atoms.pbc.tolist(),
                "positions": atoms.positions.tolist(),
                "symbols": list(atoms.get_chemical_symbols()),
            },
            metadata={"path": path},
        )

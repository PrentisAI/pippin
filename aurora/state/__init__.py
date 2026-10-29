"""State management for Pippin research campaigns.

This package provides the durable state layer that persists across
context truncations, model restarts, and agent reconfigurations:

- :class:`WorkspaceManager` — structured directory layout and atomic I/O
- :class:`ArtifactStore` — append-only research artifact CRUD
- :class:`MentalModelManager` — evidence-based mental model reconstruction
- :class:`ContextReconstructor` — on-demand context selection within a token budget
"""

from __future__ import annotations

from aurora.state.artifacts import ArtifactStore
from aurora.state.context import ContextReconstructor
from aurora.state.mental_model import MentalModelManager
from aurora.state.workspace import WorkspaceManager

__all__ = [
    "ArtifactStore",
    "ContextReconstructor",
    "MentalModelManager",
    "WorkspaceManager",
]

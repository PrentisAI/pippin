"""Computational tools for AURORA.

Tools are low-level executors that perform specific computational operations
(DFT calculations, structure manipulation, data analysis, etc.).  Each tool
exposes a uniform ``execute`` / ``validate_params`` async interface and returns
a typed :class:`ToolResult`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aurora.tools.base import Tool, ToolRegistry, ToolResult

if TYPE_CHECKING:
    from aurora.config import AuroraConfig

logger = logging.getLogger(__name__)

__all__ = ["Tool", "ToolRegistry", "ToolResult", "create_tool_registry"]


def create_tool_registry(config: AuroraConfig) -> ToolRegistry:
    """Build the tool registry used by the mock-mode research iteration.

    Real runs do not use these tools: each phase is a Claude Code session that
    works with its own tools inside the experiment container.  In mock mode the
    scripted iteration calls the simulated materials tools registered here.
    """
    registry = ToolRegistry()
    from aurora.tools.analysis import AnalysisTool
    from aurora.tools.visualization import VisualizationTool
    registry.register(AnalysisTool())
    registry.register(VisualizationTool())
    for register in (_register_mock_ase, _register_mock_dft, _register_mock_mlip, _register_mock_hpc):
        register(registry)
    return registry


# ---------------------------------------------------------------------------
# Mock-tool registration helpers
# ---------------------------------------------------------------------------

def _register_mock_ase(registry: ToolRegistry) -> None:
    try:
        from aurora.tools.mock_ase import MockASETool
        registry.register(MockASETool())
    except Exception as exc:
        logger.debug("Could not register MockASETool: %s", exc)


def _register_mock_dft(registry: ToolRegistry) -> None:
    try:
        from aurora.tools.mock_dft import MockDFTTool
        registry.register(MockDFTTool())
    except Exception as exc:
        logger.debug("Could not register MockDFTTool: %s", exc)


def _register_mock_mlip(registry: ToolRegistry) -> None:
    try:
        from aurora.tools.mock_mlip import MockMLIPTool
        registry.register(MockMLIPTool())
    except Exception as exc:
        logger.debug("Could not register MockMLIPTool: %s", exc)


def _register_mock_hpc(registry: ToolRegistry) -> None:
    try:
        from aurora.tools.hpc import MockHPCTool
        registry.register(MockHPCTool())
    except Exception as exc:
        logger.debug("Could not register MockHPCTool: %s", exc)


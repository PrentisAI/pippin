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
    """Build a :class:`ToolRegistry` populated according to *config*.

    When ``config.use_real_tools`` is ``True``, attempts to import and
    instantiate the real tool wrappers (ASE, DFT, MLIP, HPC, literature).
    If a real tool's underlying library is not installed, the
    corresponding mock is used as a fallback.

    When ``config.use_real_tools`` is ``False`` (the default), all mock
    tools are used.

    The ``analysis`` and ``visualization`` tools are always used as-is
    since they already contain real implementations.

    Parameters
    ----------
    config :
        Campaign configuration.

    Returns
    -------
    ToolRegistry
        A registry populated with all available tools.
    """
    registry = ToolRegistry()

    # -- Always-real tools: analysis and visualization ---------------------
    try:
        from aurora.tools.analysis import AnalysisTool
        registry.register(AnalysisTool())
    except Exception as exc:
        logger.debug("Could not register AnalysisTool: %s", exc)

    try:
        from aurora.tools.visualization import VisualizationTool
        registry.register(VisualizationTool())
    except Exception as exc:
        logger.debug("Could not register VisualizationTool: %s", exc)

    # -- ASE tool ---------------------------------------------------------
    if config.use_real_tools:
        try:
            from aurora.tools.real_ase import RealASETool
            registry.register(RealASETool())
            logger.info("Registered real ASE tool.")
        except Exception as exc:
            logger.info("Real ASE tool unavailable (%s); falling back to mock.", exc)
            _register_mock_ase(registry)
    else:
        _register_mock_ase(registry)

    # -- DFT tool ---------------------------------------------------------
    if config.use_real_tools:
        try:
            from aurora.tools.real_dft import RealDFTTool
            registry.register(RealDFTTool())
            logger.info("Registered real DFT tool.")
        except Exception as exc:
            logger.info("Real DFT tool unavailable (%s); falling back to mock.", exc)
            _register_mock_dft(registry)
    else:
        _register_mock_dft(registry)

    # -- MLIP tool --------------------------------------------------------
    if config.use_real_tools:
        try:
            from aurora.tools.real_mlip import RealMLIPTool
            registry.register(RealMLIPTool())
            logger.info("Registered real MLIP tool.")
        except Exception as exc:
            logger.info("Real MLIP tool unavailable (%s); falling back to mock.", exc)
            _register_mock_mlip(registry)
    else:
        _register_mock_mlip(registry)

    # -- HPC tool ---------------------------------------------------------
    if config.use_real_tools:
        try:
            from aurora.tools.real_hpc import RealHPCTool
            registry.register(RealHPCTool())
            logger.info("Registered real HPC tool.")
        except Exception as exc:
            logger.info("Real HPC tool unavailable (%s); falling back to mock.", exc)
            _register_mock_hpc(registry)
    else:
        _register_mock_hpc(registry)

    # -- Literature tool --------------------------------------------------
    if config.use_real_tools:
        try:
            from aurora.tools.real_literature import RealLiteratureTool
            registry.register(RealLiteratureTool())
            logger.info("Registered real literature tool.")
        except Exception as exc:
            logger.info("Real literature tool unavailable (%s); falling back to mock.", exc)
            _register_mock_literature(registry)
    else:
        _register_mock_literature(registry)

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


def _register_mock_literature(registry: ToolRegistry) -> None:
    try:
        from aurora.tools.literature import MockLiteratureTool
        registry.register(MockLiteratureTool())
    except Exception as exc:
        logger.debug("Could not register MockLiteratureTool: %s", exc)

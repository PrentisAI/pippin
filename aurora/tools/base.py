"""Abstract base classes for computational tools and their registry.

Every tool implements :meth:`execute` (the async computation) and
:meth:`validate_params` (pre-execution parameter check).  Results are
returned as :class:`ToolResult` Pydantic models so they integrate cleanly
with the rest of the AURORA data layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ToolResult(BaseModel):
    """Typed result from a tool execution."""

    success: bool = Field(description="Whether the execution completed successfully.")
    output: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured output data.",
    )
    output_files: list[str] = Field(
        default_factory=list,
        description="Paths to files produced by the tool.",
    )
    error: str | None = Field(
        default=None,
        description="Error message when success is False.",
    )
    wall_time_seconds: float = Field(
        default=0.0,
        description="Simulated or real wall-clock time consumed.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Tool-specific metadata (model name, convergence info, …).",
    )


# ---------------------------------------------------------------------------
# Tool abstract base
# ---------------------------------------------------------------------------

class Tool(ABC):
    """Abstract base for a computational tool.

    Subclasses must set the class-level ``name`` and ``description``
    attributes and implement the two async methods.
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Run the tool with the given parameters."""
        ...

    @abstractmethod
    async def validate_params(self, params: dict[str, Any]) -> tuple[bool, str]:
        """Validate *params* before execution.

        Returns
        -------
        (valid, message)
            ``valid`` is True when parameters are acceptable; *message*
            provides diagnostics when they are not.
        """
        ...

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Simple name→tool registry used by skills and agents."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register *tool* under its ``name``."""
        if not tool.name:
            raise ValueError(f"Tool {type(tool).__name__} has no name set.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Look up a tool by *name*.

        Raises ``KeyError`` if the tool has not been registered.
        """
        return self._tools[name]

    def list_tools(self) -> list[str]:
        """Return the names of all registered tools."""
        return sorted(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

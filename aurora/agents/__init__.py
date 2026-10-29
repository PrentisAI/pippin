"""Agent implementations for Pippin pipeline stages.

Re-exports all agent classes and the base abstractions.
"""

from aurora.agents.analyzer import AnalyzerAgent
from aurora.agents.base import (
    Agent,
    AgentAction,
    AgentActionType,
    AgentContext,
    AgentRole,
)
from aurora.agents.roundtable import Roundtable, RoundtableChair, RoundtableSeat
from aurora.agents.writer import WriterAgent

__all__ = [
    # Base
    "Agent",
    "AgentAction",
    "AgentActionType",
    "AgentContext",
    "AgentRole",
    # Agents
    "AnalyzerAgent",
    "WriterAgent",
    # Roundtable
    "Roundtable",
    "RoundtableChair",
    "RoundtableSeat",
]

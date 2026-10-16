"""Agent implementations for AURORA pipeline stages.

Re-exports all agent classes and the base abstractions.
"""

from aurora.agents.base import (
    Agent,
    AgentAction,
    AgentActionType,
    AgentContext,
    AgentRole,
)
from aurora.agents.researcher import ResearcherAgent
from aurora.agents.auditor import AuditorAgent
from aurora.agents.analyzer import AnalyzerAgent
from aurora.agents.writer import WriterAgent
from aurora.agents.roundtable import Roundtable, RoundtableChair, RoundtableSeat

__all__ = [
    # Base
    "Agent",
    "AgentAction",
    "AgentActionType",
    "AgentContext",
    "AgentRole",
    # Agents
    "ResearcherAgent",
    "AuditorAgent",
    "AnalyzerAgent",
    "WriterAgent",
    # Roundtable
    "Roundtable",
    "RoundtableChair",
    "RoundtableSeat",
]

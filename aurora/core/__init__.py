"""Core engine — orchestrates the full ideation-to-paper pipeline.

The :class:`ResearchEngine` drives the complete AURORA lifecycle:
roundtable → screening → planning → research loop → analysis → writing.
Analysis may loop back to research if more data is needed.
"""

from __future__ import annotations

from aurora.core.events import EventBus, Event, EventHandler
from aurora.core.registry import ComponentRegistry
from aurora.core.engine import ResearchEngine, CampaignResult
from aurora.core.lifecycle import ResearchIteration, IterationResult

__all__ = [
    "EventBus",
    "Event",
    "EventHandler",
    "ComponentRegistry",
    "ResearchEngine",
    "CampaignResult",
    "ResearchIteration",
    "IterationResult",
]

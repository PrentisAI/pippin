"""Human-in-the-loop interaction layer (Section G).

Provides structured checkpoint approval and intervention logging
with H_cap (capability-building) vs H_run (per-run) classification.
"""

from aurora.human.checkpoint import HumanCheckpoint, CheckpointDecision
from aurora.human.interaction import InteractionLogger
from aurora.human.web_checkpoint import WebCheckpointHandler

__all__ = [
    "HumanCheckpoint",
    "CheckpointDecision",
    "InteractionLogger",
    "WebCheckpointHandler",
]

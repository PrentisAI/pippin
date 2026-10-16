"""JIT audit hooks and human checkpoint infrastructure (Section F.3, G).

Re-exports
----------
From :mod:`~aurora.hooks.base`:
    :class:`HookPoint`, :class:`HookContext`, :class:`HookResult`,
    :class:`Hook`, :class:`HookRegistry`

From :mod:`~aurora.hooks.audit_hooks`:
    :class:`EnergyAnomalyHook`, :class:`ConvergenceHook`,
    :class:`ConsistencyHook`, :class:`PeriodicAuditHook`,
    :class:`GoalDriftHook`, :class:`ResourceExhaustionHook`,
    :class:`RepeatedFailureHook`, :class:`NovelResultHook`,
    :class:`ClaimPromotionHook`, :class:`TerminationGuardHook`,
    :class:`MilestoneAuditHook`

From :mod:`~aurora.hooks.checkpoint`:
    :class:`HumanCheckpointHook`
"""

from aurora.hooks.base import (
    Hook,
    HookContext,
    HookPoint,
    HookRegistry,
    HookResult,
)
from aurora.hooks.audit_hooks import (
    ClaimPromotionHook,
    ConsistencyHook,
    ConvergenceHook,
    EnergyAnomalyHook,
    GoalDriftHook,
    MilestoneAuditHook,
    NovelResultHook,
    PeriodicAuditHook,
    RepeatedFailureHook,
    ResourceExhaustionHook,
    TerminationGuardHook,
)
from aurora.hooks.checkpoint import HumanCheckpointHook

__all__ = [
    # Base
    "HookPoint",
    "HookContext",
    "HookResult",
    "Hook",
    "HookRegistry",
    # Audit hooks
    "EnergyAnomalyHook",
    "ConvergenceHook",
    "ConsistencyHook",
    "PeriodicAuditHook",
    "GoalDriftHook",
    "ResourceExhaustionHook",
    "RepeatedFailureHook",
    "NovelResultHook",
    "ClaimPromotionHook",
    "TerminationGuardHook",
    "MilestoneAuditHook",
    # Checkpoint
    "HumanCheckpointHook",
]

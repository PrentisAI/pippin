"""Lightweight audit hooks (used by mock mode and for periodic checks).

Re-exports
----------
From :mod:`~aurora.hooks.base`:
    :class:`HookPoint`, :class:`HookContext`, :class:`HookResult`,
    :class:`Hook`, :class:`HookRegistry`

From :mod:`~aurora.hooks.audit_hooks`:
    :class:`EnergyAnomalyHook`, :class:`ConvergenceHook`,
    :class:`ConsistencyHook`, :class:`PeriodicAuditHook`,
    :class:`GoalDriftHook`,
    :class:`RepeatedFailureHook`, :class:`NovelResultHook`,
    :class:`ClaimPromotionHook`, :class:`TerminationGuardHook`,
    :class:`MilestoneAuditHook`
"""

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
    TerminationGuardHook,
)
from aurora.hooks.base import (
    Hook,
    HookContext,
    HookPoint,
    HookRegistry,
    HookResult,
)

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
    "RepeatedFailureHook",
    "NovelResultHook",
    "ClaimPromotionHook",
    "TerminationGuardHook",
    "MilestoneAuditHook",
]

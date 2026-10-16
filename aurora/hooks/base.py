"""Hook infrastructure for JIT audit and checkpoint triggers (Section F.3).

The hook system provides event-driven extensibility across the AURORA
pipeline.  Each :class:`Hook` declares the :class:`HookPoint` values it
subscribes to and an async ``execute`` method that receives a
:class:`HookContext` snapshot and returns a :class:`HookResult`.

The :class:`HookRegistry` collects hooks and dispatches them —
``fire(hook_point, context)`` runs every matching, enabled hook in
priority order and collects results.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hook points — moments in the pipeline where hooks may fire
# ---------------------------------------------------------------------------

class HookPoint(str, Enum):
    """Named extension points distributed across the pipeline."""

    PRE_EXPERIMENT = "pre_experiment"
    POST_EXPERIMENT = "post_experiment"
    PRE_ANALYSIS = "pre_analysis"
    POST_ANALYSIS = "post_analysis"
    ENERGY_COMPUTED = "energy_computed"
    ANOMALY_DETECTED = "anomaly_detected"
    ITERATION_COMPLETE = "iteration_complete"
    MENTAL_MODEL_UPDATED = "mental_model_updated"
    WRITER_DRAFT_COMPLETE = "writer_draft_complete"
    PERIODIC = "periodic"
    CLAIM_PROMOTED = "claim_promoted"
    TERMINATION_ATTEMPT = "termination_attempt"
    MILESTONE_COMPLETE = "milestone_complete"


# ---------------------------------------------------------------------------
# Context and result data classes
# ---------------------------------------------------------------------------

@dataclass
class HookContext:
    """Immutable snapshot passed to every hook at firing time.

    Attributes
    ----------
    hook_point:
        Which pipeline point triggered this invocation.
    iteration:
        Current research iteration counter (0-based).
    timestamp:
        UTC timestamp when the context was created.
    data:
        Payload specific to the hook point (e.g. energy values,
        convergence metrics, job results).
    metadata:
        Auxiliary information (campaign id, proposal id, …).
    """

    hook_point: HookPoint
    iteration: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """Outcome returned by a single hook execution.

    Attributes
    ----------
    hook_name:
        Identifier of the hook that produced this result.
    passed:
        ``True`` when no issue was detected; ``False`` to signal a
        problem or checkpoint.
    message:
        Human-readable description of the finding.
    severity:
        One of ``"info"``, ``"warning"``, ``"critical"``.
    human_intervention_required:
        When ``True``, the orchestrator must pause and request human
        approval before continuing.
    data:
        Structured payload for downstream consumers (audit records,
        corrective actions, …).
    """

    hook_name: str = ""
    passed: bool = True
    message: str = ""
    severity: str = "info"  # "info" | "warning" | "critical"
    human_intervention_required: bool = False
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Hook abstract base class
# ---------------------------------------------------------------------------

class Hook(ABC):
    """Abstract base for all pipeline hooks.

    Subclasses must set ``name`` and ``hook_points`` and implement the
    async :meth:`execute` method.

    Attributes
    ----------
    name:
        Unique identifier for the hook.
    hook_points:
        Set of :class:`HookPoint` values this hook subscribes to.
    priority:
        Execution order within a hook point — lower values run first.
    enabled:
        Toggle to temporarily disable the hook without unregistering it.
    """

    name: str = ""
    hook_points: set[HookPoint] = set()
    priority: int = 0
    enabled: bool = True

    @abstractmethod
    async def execute(self, context: HookContext) -> HookResult:
        """Run the hook logic and return a result.

        Parameters
        ----------
        context:
            Snapshot of the pipeline state at the firing hook point.
        """
        ...

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name!r} priority={self.priority}>"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class HookRegistry:
    """Collects hooks and dispatches them to matching hook points.

    Usage::

        registry = HookRegistry()
        registry.register(my_hook)
        results = await registry.fire(HookPoint.POST_EXPERIMENT, ctx)
    """

    def __init__(self) -> None:
        self._hooks: dict[str, Hook] = {}

    def register(self, hook: Hook) -> None:
        """Register *hook* under its ``name``.

        Raises
        ------
        ValueError
            If the hook has no name.
        KeyError
            If a hook with the same name is already registered.
        """
        if not hook.name:
            raise ValueError(f"Hook {type(hook).__name__} has no name set.")
        if hook.name in self._hooks:
            raise KeyError(f"Hook {hook.name!r} already registered.")
        self._hooks[hook.name] = hook
        logger.debug("Registered hook %r for points %s", hook.name, hook.hook_points)

    def unregister(self, name: str) -> None:
        """Remove a hook by *name*.

        Raises
        ------
        KeyError
            If the name is not registered.
        """
        if name not in self._hooks:
            raise KeyError(f"Hook {name!r} not registered.")
        del self._hooks[name]
        logger.debug("Unregistered hook %r", name)

    def get_hooks_for(self, hook_point: HookPoint) -> list[Hook]:
        """Return all enabled hooks subscribed to *hook_point*, sorted by priority."""
        matching = [
            h for h in self._hooks.values()
            if hook_point in h.hook_points and h.enabled
        ]
        matching.sort(key=lambda h: h.priority)
        return matching

    async def fire(self, hook_point: HookPoint, context: HookContext) -> list[HookResult]:
        """Fire all enabled hooks for *hook_point* and collect results.

        Hooks are executed sequentially in priority order.  A hook that
        raises is logged and skipped — it does not prevent subsequent
        hooks from running.

        Parameters
        ----------
        hook_point:
            The pipeline point being fired.
        context:
            Snapshot of current pipeline state.

        Returns
        -------
        list[HookResult]
            One result per successfully executed hook.
        """
        hooks = self.get_hooks_for(hook_point)
        results: list[HookResult] = []

        for hook in hooks:
            try:
                result = await hook.execute(context)
                results.append(result)
                if not result.passed:
                    logger.warning(
                        "Hook %r flagged issue at %s: %s (severity=%s)",
                        hook.name, hook_point.value, result.message, result.severity,
                    )
            except Exception:
                logger.exception("Hook %r raised an exception at %s", hook.name, hook_point.value)
                results.append(HookResult(
                    hook_name=hook.name,
                    passed=False,
                    message=f"Hook {hook.name!r} raised an unhandled exception.",
                    severity="critical",
                    data={"error": "unhandled_exception"},
                ))

        return results

    def list_hooks(self) -> list[str]:
        """Return names of all registered hooks."""
        return sorted(self._hooks)

    def __contains__(self, name: str) -> bool:
        return name in self._hooks

    def __len__(self) -> int:
        return len(self._hooks)

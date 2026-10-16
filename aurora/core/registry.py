"""Component registry — lightweight dependency injection.

:class:`ComponentRegistry` wires up the campaign's subsystems so that
every agent, hook, and engine module can look up its collaborators by
type without importing them directly.

Typical usage::

    registry = ComponentRegistry()
    registry.register(LLMRouter, router)
    registry.register(ArtifactStore, store)

    # Later, inside an agent:
    router = registry.get(LLMRouter)
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ComponentRegistry:
    """A simple type → instance mapping for dependency injection.

    Components are keyed by their *type* (class object), so each type
    can have at most one registered instance.
    """

    def __init__(self) -> None:
        self._components: dict[type, Any] = {}

    def register(self, component_type: type[T], instance: T) -> None:
        """Register *instance* under *component_type*.

        Overwrites any previous registration for the same type.
        """
        self._components[component_type] = instance
        logger.debug("Registered component %s", component_type.__name__)

    def get(self, component_type: type[T]) -> T:
        """Look up the instance registered for *component_type*.

        Raises
        ------
        KeyError
            If the type was never registered.
        """
        try:
            return self._components[component_type]
        except KeyError:
            raise KeyError(
                f"No component registered for {component_type.__name__}. "
                f"Available: {[t.__name__ for t in self._components]}"
            ) from None

    def get_optional(self, component_type: type[T]) -> T | None:
        """Look up *component_type*, returning ``None`` if not registered."""
        return self._components.get(component_type)

    def has(self, component_type: type) -> bool:
        """Check whether *component_type* is registered."""
        return component_type in self._components

    def all_types(self) -> list[type]:
        """Return the list of registered component types."""
        return list(self._components.keys())

    def clear(self) -> None:
        """Remove all registered components."""
        self._components.clear()

    def __repr__(self) -> str:
        names = [t.__name__ for t in self._components]
        return f"ComponentRegistry({names})"

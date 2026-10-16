"""Skill registry — central look-up for all registered skills."""

from __future__ import annotations

from aurora.skills.base import Skill


class SkillRegistry:
    """Thread-safe registry of :class:`Skill` instances.

    Usage::

        registry = SkillRegistry()
        registry.register(StructureConstructionSkill())
        skill = registry.get("structure_construction")
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if not skill.name:
            raise ValueError("Skill must have a non-empty 'name'.")
        if skill.name in self._skills:
            raise KeyError(f"Skill {skill.name!r} already registered.")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(
                f"Skill {name!r} not registered. "
                f"Available: {sorted(self._skills)}"
            )
        return self._skills[name]

    def list_skills(self) -> list[str]:
        return sorted(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)

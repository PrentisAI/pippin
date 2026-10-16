"""Domain-specific skills for AURORA (Section D).

Skills encapsulate established conventions, recommended procedures, and
domain-specific validation checks so that agents can invoke them rather
than reconstructing procedural knowledge from scratch each time.
"""

from __future__ import annotations

from aurora.skills.base import (
    FailureMode,
    Skill,
    SkillContext,
    SkillInput,
    SkillOutput,
    SkillResult,
    ValidationCheck,
    ValidationResult,
)
from aurora.skills.registry import SkillRegistry

__all__ = [
    "FailureMode",
    "Skill",
    "SkillContext",
    "SkillInput",
    "SkillOutput",
    "SkillRegistry",
    "SkillResult",
    "ValidationCheck",
    "ValidationResult",
]

"""Skill materializer — writes distilled skills to .claude/skills/ for Claude Code.

At experiment startup, relevant skills are materialized as
`.claude/skills/<name>/SKILL.md` so that Claude Code's native skill
loader picks them up.  At teardown, materialized files are cleaned up.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aurora.skills.store import DistilledSkill, SkillStore

logger = logging.getLogger(__name__)


class SkillMaterializer:
    """Materialize distilled skills as `.claude/skills/` files."""

    def __init__(self, store: SkillStore, workspace_root: Path) -> None:
        self._store = store
        self._skills_dir = workspace_root / ".claude" / "skills"
        self._materialized: list[Path] = []

    def materialize_for_experiment(
        self, experiment_id: str, campaign_id: str = ""
    ) -> list[Path]:
        """Write all relevant skills to disk for this experiment."""
        skills = self._store.list_for_experiment(experiment_id, campaign_id=campaign_id)
        paths: list[Path] = []
        for skill in skills:
            p = self._write_skill(skill)
            paths.append(p)
        self._materialized = paths
        logger.info(
            "Materialized %d skills for experiment %s", len(paths), experiment_id
        )
        return paths

    def materialize_all(self) -> list[Path]:
        """Write all skills (used for global materialization)."""
        skills = self._store.list_all()
        paths: list[Path] = []
        for skill in skills:
            p = self._write_skill(skill)
            paths.append(p)
        self._materialized = paths
        return paths

    def cleanup(self) -> None:
        """Remove previously materialized skill files."""
        for p in self._materialized:
            try:
                if p.exists():
                    p.unlink()
                parent = p.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
        self._materialized.clear()

    def _write_skill(self, skill: DistilledSkill) -> Path:
        skill_dir = self._skills_dir / skill.name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"

        tags_line = ""
        if skill.tags:
            tags_line = f"tags: {', '.join(skill.tags)}\n"
        source_line = ""
        if skill.source_phase:
            source_line = f"source: {skill.source_phase}"
            if skill.source_experiment:
                source_line += f" / {skill.source_experiment}"
            source_line += "\n"

        content = f"""---
name: {skill.name}
description: {skill.description or skill.name}
{tags_line}{source_line}---

{skill.rule}
"""
        skill_file.write_text(content)
        return skill_file

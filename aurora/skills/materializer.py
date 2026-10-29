"""Skill materializer — writes distilled skills to .claude/skills/ for Claude Code.

At experiment startup, relevant skills are materialized as
`.claude/skills/<name>/SKILL.md` so that Claude Code's native skill
loader picks them up.  At teardown, materialized files are cleaned up.
"""

from __future__ import annotations

import logging
import re
import shutil
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
                if (p.parent / "SOURCE.md").is_file():      # a whole folder from the catalog
                    shutil.rmtree(p.parent, ignore_errors=True)
                    continue
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
        from aurora.skills.catalog import bundle_of
        if bundle := bundle_of(skill):
            # from the catalog: its whole folder (references, scripts, licence), as its authors wrote it
            mark, there = bundle / "SOURCE.md", skill_dir / "SOURCE.md"
            if not (there.is_file() and mark.is_file() and there.read_text() == mark.read_text()):
                shutil.rmtree(skill_dir, ignore_errors=True)       # not there yet, or another version
                shutil.copytree(bundle, skill_dir)
            return skill_dir / "SKILL.md"
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

        origin = {"human": "the researcher's corrections", "self": "earlier runs",
                  "manual": "written by hand"}.get(skill.origin, skill.origin)
        content = f"""---
name: {skill.name}
description: {_one_line(skill.description or skill.name)}
{tags_line}{source_line}---

<!-- skill v{skill.version}, learned from {origin} -->

{skill.rule}
"""
        skill_file.write_text(content)
        return skill_file


def _one_line(text: str) -> str:
    """Frontmatter values must stay on one line."""
    return " ".join(text.split())


def description_of(skill_md: str) -> str:
    """A SKILL.md's description, on one line (also when written as a folded or quoted block)."""
    m = re.search(r"^description:[ \t]*(.*)$", skill_md, re.M)
    if not m:
        return ""
    first = m.group(1).strip()
    if first in ("", ">", ">-", "|", "|-"):
        block = []
        for line in skill_md[m.end():].splitlines()[1:]:
            if line and not line[0].isspace():         # the next key
                break
            block.append(line.strip())
        first = " ".join(block)
    return _one_line(first.strip().strip('"\''))

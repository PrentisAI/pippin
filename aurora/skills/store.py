"""Distilled skill store — SQLite-backed CRUD for learned rules.

Distilled skills are reusable rules/patterns extracted from human
interactions and experiment runs.  They live in SQLite for persistent
storage and are materialized as `.claude/skills/<name>/SKILL.md`
at experiment runtime so Claude Code loads them natively.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_SKILL_SCHEMA = """
CREATE TABLE IF NOT EXISTS distilled_skills (
    skill_id     TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    rule         TEXT NOT NULL,
    description  TEXT DEFAULT '',
    source_phase TEXT DEFAULT '',
    source_experiment TEXT DEFAULT '',
    source_campaign TEXT DEFAULT '',
    scope        TEXT DEFAULT 'experiment',
    tags         TEXT DEFAULT '[]',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
"""


class DistilledSkill(BaseModel):
    skill_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    rule: str
    description: str = ""
    source_phase: str = ""
    source_experiment: str = ""
    source_campaign: str = ""
    scope: str = "experiment"  # experiment | campaign | global
    tags: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SkillStore:
    """SQLite-backed store for distilled skills."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SKILL_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        try:
            self._conn.execute(
                "ALTER TABLE distilled_skills ADD COLUMN source_campaign TEXT DEFAULT ''"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

    def close(self) -> None:
        self._conn.close()

    def _row_to_skill(self, row: sqlite3.Row) -> DistilledSkill:
        import json
        d = dict(row)
        d["tags"] = json.loads(d.get("tags", "[]"))
        return DistilledSkill(**d)

    def create(self, skill: DistilledSkill) -> DistilledSkill:
        import json
        now = time.time()
        skill.created_at = now
        skill.updated_at = now
        self._conn.execute(
            """INSERT INTO distilled_skills
               (skill_id, name, rule, description, source_phase,
                source_experiment, source_campaign, scope, tags,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                skill.skill_id, skill.name, skill.rule, skill.description,
                skill.source_phase, skill.source_experiment,
                skill.source_campaign, skill.scope,
                json.dumps(skill.tags), skill.created_at, skill.updated_at,
            ),
        )
        self._conn.commit()
        return skill

    def get(self, skill_id: str) -> DistilledSkill | None:
        row = self._conn.execute(
            "SELECT * FROM distilled_skills WHERE skill_id=?", (skill_id,)
        ).fetchone()
        return self._row_to_skill(row) if row else None

    def get_by_name(self, name: str) -> DistilledSkill | None:
        row = self._conn.execute(
            "SELECT * FROM distilled_skills WHERE name=?", (name,)
        ).fetchone()
        return self._row_to_skill(row) if row else None

    def list_all(
        self,
        scope: str | None = None,
        source_experiment: str | None = None,
    ) -> list[DistilledSkill]:
        query = "SELECT * FROM distilled_skills"
        conditions: list[str] = []
        params: list[Any] = []
        if scope:
            conditions.append("scope=?")
            params.append(scope)
        if source_experiment:
            conditions.append("source_experiment=?")
            params.append(source_experiment)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_skill(r) for r in rows]

    def update(self, skill_id: str, **fields: Any) -> DistilledSkill | None:
        import json
        allowed = {"name", "rule", "description", "scope", "tags", "source_phase", "source_experiment"}
        parts: list[str] = []
        values: list[Any] = []
        for k, v in fields.items():
            if k in allowed:
                if k == "tags":
                    v = json.dumps(v)
                parts.append(f"{k}=?")
                values.append(v)
        if not parts:
            return self.get(skill_id)
        parts.append("updated_at=?")
        values.append(time.time())
        values.append(skill_id)
        self._conn.execute(
            f"UPDATE distilled_skills SET {', '.join(parts)} WHERE skill_id=?",
            values,
        )
        self._conn.commit()
        return self.get(skill_id)

    def delete(self, skill_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM distilled_skills WHERE skill_id=?", (skill_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def search(self, query: str) -> list[DistilledSkill]:
        rows = self._conn.execute(
            """SELECT * FROM distilled_skills
               WHERE name LIKE ? OR rule LIKE ? OR description LIKE ?
               ORDER BY updated_at DESC""",
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        ).fetchall()
        return [self._row_to_skill(r) for r in rows]

    def list_for_experiment(
        self, experiment_id: str, campaign_id: str = ""
    ) -> list[DistilledSkill]:
        """Return skills relevant to a specific experiment: its own + its campaign's + global."""
        rows = self._conn.execute(
            """SELECT * FROM distilled_skills
               WHERE scope='global'
                  OR (scope='campaign' AND source_campaign=?)
                  OR (scope='experiment' AND source_experiment=?)
               ORDER BY updated_at DESC""",
            (campaign_id, experiment_id),
        ).fetchall()
        return [self._row_to_skill(r) for r in rows]

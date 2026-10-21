"""The skill library: SQLite-backed store of skills learned from people and from runs.

A skill is a structured specification (Appendix B of the paper; see :data:`SKILL_SECTIONS`):
its purpose, inputs and preconditions, tools, procedure, expected outputs, validation checks,
failure modes, interpretation, and when to ask a human.  Skills come from the researcher's
corrections (``origin="human"``), from the agents' own runs (``origin="self"``) or are written
by hand (``origin="manual"``), and are revised over time (``version``, ``history``).  At
runtime they are written as ``.claude/skills/<name>/SKILL.md`` so Claude Code loads them.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_SKILL_SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestion_outcomes (
    suggestion_id TEXT PRIMARY KEY,
    state         TEXT NOT NULL,          -- added | dismissed
    detail        TEXT DEFAULT '{}',      -- where it went: skill_id, version, scope
    decided_at    REAL NOT NULL
);
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

# The sections of a skill (Appendix B): (heading, what goes there)
SKILL_SECTIONS: list[tuple[str, str]] = [
    ("Purpose", "the scientific or practical task the skill is for, and when to use it"),
    ("Inputs and preconditions", "what must be given or true before starting"),
    ("Tools", "the programs, libraries or commands it uses"),
    ("Procedure", "the steps, with the settings that matter"),
    ("Expected outputs", "files and results it produces"),
    ("Validation checks", "how to tell the result is right"),
    ("Failure modes", "what goes wrong, how it shows, and what to do"),
    ("Interpretation", "how to read the results, and their limits"),
    ("When to ask a human", "decisions the agent should not take alone"),
]

ORIGINS = ("human", "self", "manual")
SCOPES = ("experiment", "campaign", "projects", "global")
KINDS = ("procedure", "failure", "convention", "correction")


def skill_template() -> str:
    """An empty skill in the standard sections."""
    return "\n\n".join(f"## {h}\n\n<{what}>" for h, what in SKILL_SECTIONS)


def missing_sections(text: str) -> list[str]:
    """Standard sections a skill's text lacks."""
    low = text.lower()
    return [h for h, _ in SKILL_SECTIONS if f"## {h.lower()}" not in low]


class DistilledSkill(BaseModel):
    skill_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    rule: str
    description: str = ""
    source_phase: str = ""
    source_experiment: str = ""
    source_campaign: str = ""
    # where agents may use it: experiment (its run only) | campaign (its project) |
    # projects (the projects listed in ``projects``) | global (everywhere)
    scope: str = "experiment"
    projects: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    # ``rule`` holds the skill's text: Markdown in the SKILL_SECTIONS (older skills: one rule)
    origin: str = "manual"     # human | self | manual
    kind: str = ""             # procedure | failure | convention | correction
    version: int = 1
    history: list[dict[str, Any]] = Field(default_factory=list)   # earlier versions, oldest first
    # reaching other projects (scope projects or global) may wait for a team admin's approval:
    # '' (none needed, or approved) | pending | rejected.  Until approved it is used only where it
    # was learned.
    approval: str = ""
    approved_by: str = ""


class SkillStore:
    """SQLite-backed store for distilled skills."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SKILL_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        for column in ("source_campaign TEXT DEFAULT ''", "origin TEXT DEFAULT 'manual'", "kind TEXT DEFAULT ''",
                       "version INTEGER DEFAULT 1", "history TEXT DEFAULT '[]'", "projects TEXT DEFAULT '[]'",
                       "approval TEXT DEFAULT ''", "approved_by TEXT DEFAULT ''"):
            try:
                self._conn.execute(f"ALTER TABLE distilled_skills ADD COLUMN {column}")
                self._conn.commit()
            except sqlite3.OperationalError:
                pass        # the column exists

    def close(self) -> None:
        self._conn.close()

    def _row_to_skill(self, row: sqlite3.Row) -> DistilledSkill:
        import json
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["history"] = json.loads(d.get("history") or "[]")
        d["projects"] = json.loads(d.get("projects") or "[]")
        d["origin"] = d.get("origin") or "manual"
        d["kind"] = d.get("kind") or ""
        d["version"] = d.get("version") or 1
        d["approval"] = d.get("approval") or ""
        d["approved_by"] = d.get("approved_by") or ""
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
                created_at, updated_at, origin, kind, version, history, projects, approval, approved_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                skill.skill_id, skill.name, skill.rule, skill.description,
                skill.source_phase, skill.source_experiment,
                skill.source_campaign, skill.scope,
                json.dumps(skill.tags), skill.created_at, skill.updated_at,
                skill.origin, skill.kind, skill.version, json.dumps(skill.history),
                json.dumps(skill.projects), skill.approval, skill.approved_by,
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
        allowed = {"name", "rule", "description", "scope", "tags", "source_phase", "source_experiment",
                   "source_campaign", "origin", "kind", "projects", "approval", "approved_by"}
        parts: list[str] = []
        values: list[Any] = []
        for k, v in fields.items():
            if k in allowed:
                if k in ("tags", "projects"):
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

    def revise(self, skill_id: str, text: str, *, reason: str = "", description: str | None = None,
               source_experiment: str = "") -> DistilledSkill | None:
        """A new version of a skill; the current one goes into its history."""
        import json
        skill = self.get(skill_id)
        if skill is None:
            return None
        previous = {"version": skill.version, "text": skill.rule, "description": skill.description,
                    "updated_at": skill.updated_at, "replaced_because": reason}
        history = (skill.history + [previous])[-20:]
        self._conn.execute(
            """UPDATE distilled_skills SET rule=?, description=?, version=?, history=?, updated_at=?,
               source_experiment=CASE WHEN ?='' THEN source_experiment ELSE ? END WHERE skill_id=?""",
            (text, skill.description if description is None else description, skill.version + 1,
             json.dumps(history), time.time(), source_experiment, source_experiment, skill_id))
        self._conn.commit()
        return self.get(skill_id)

    # -- what the researcher did with suggested skills (so a card stays answered) --

    def record_outcome(self, suggestion_id: str, state: str, **detail: Any) -> None:
        import json
        self._conn.execute(
            "INSERT OR REPLACE INTO suggestion_outcomes (suggestion_id, state, detail, decided_at) VALUES (?, ?, ?, ?)",
            (suggestion_id, state, json.dumps(detail), time.time()))
        self._conn.commit()

    def outcomes(self, suggestion_ids: list[str]) -> dict[str, dict[str, Any]]:
        import json
        if not suggestion_ids:
            return {}
        rows = self._conn.execute(
            f"SELECT * FROM suggestion_outcomes WHERE suggestion_id IN ({','.join('?' * len(suggestion_ids))})",
            suggestion_ids).fetchall()
        return {r["suggestion_id"]: {"state": r["state"], **json.loads(r["detail"] or "{}"),
                                     "decided_at": r["decided_at"]} for r in rows}

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
        """Skills an experiment may use: global ones, its project's, those listing its project, its own."""
        rows = self._conn.execute(
            """SELECT * FROM distilled_skills
               WHERE scope='global' OR scope='projects'
                  OR (scope='campaign' AND source_campaign=?)
                  OR (scope='experiment' AND source_experiment=?)
               ORDER BY updated_at DESC""",
            (campaign_id, experiment_id),
        ).fetchall()
        skills = [self._row_to_skill(r) for r in rows]

        def usable(s: DistilledSkill) -> bool:
            if s.scope in ("projects", "global") and s.approval in ("pending", "rejected"):
                # not approved for other projects yet: only where it was learned
                return bool(s.source_campaign and s.source_campaign == campaign_id) or \
                    bool(s.source_experiment and s.source_experiment == experiment_id)
            return s.scope != "projects" or bool(campaign_id and campaign_id in s.projects)
        return [s for s in skills if usable(s)]

    def pending(self) -> list[DistilledSkill]:
        rows = self._conn.execute("SELECT * FROM distilled_skills WHERE approval='pending' ORDER BY updated_at DESC").fetchall()
        return [self._row_to_skill(r) for r in rows]

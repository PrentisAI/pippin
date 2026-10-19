"""SQLite + JSONL persistence for campaigns, experiments and event streams.

- Campaign/experiment metadata → SQLite (queryable, small)
- Event streams → per-experiment JSONL files (append-only, replay-friendly)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    objective   TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    run_id         TEXT PRIMARY KEY,
    campaign_id    TEXT NOT NULL,
    task_name      TEXT NOT NULL,
    model          TEXT NOT NULL,
    autonomy_mode  TEXT DEFAULT 'supervised',
    status         TEXT DEFAULT 'starting',
    phase          TEXT DEFAULT '',
    turn           INTEGER DEFAULT 0,
    cost_usd       REAL DEFAULT 0.0,
    started_at     REAL NOT NULL,
    finished_at    REAL,
    error          TEXT,
    config_json    TEXT DEFAULT '{}',
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
);
"""


class Store:
    """Persistent store backed by SQLite + JSONL event files."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            from aurora.paths import aurora_home
            data_dir = aurora_home()
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._events_dir = self._data_dir / "events"
        self._events_dir.mkdir(parents=True, exist_ok=True)

        db_path = self._data_dir / "aurora.db"
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        logger.info("Persistence store opened at %s", db_path)

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def _migrate(self) -> None:
        """Add columns that databases created by earlier versions lack."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(experiments)")}
        for column, decl in (("phase", "TEXT DEFAULT ''"), ("turn", "INTEGER DEFAULT 0"),
                             ("cost_usd", "REAL DEFAULT 0.0"), ("error", "TEXT"),
                             ("config_json", "TEXT DEFAULT '{}'")):
            if column not in have:
                self._conn.execute(f"ALTER TABLE experiments ADD COLUMN {column} {decl}")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Campaigns
    # ------------------------------------------------------------------

    def save_campaign(
        self,
        campaign_id: str,
        name: str,
        objective: str,
        created_at: float,
        updated_at: float,
    ) -> None:
        self._conn.execute(
            """INSERT INTO campaigns (campaign_id, name, objective, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(campaign_id) DO UPDATE SET
                   name=excluded.name,
                   objective=excluded.objective,
                   updated_at=excluded.updated_at""",
            (campaign_id, name, objective, created_at, updated_at),
        )
        self._conn.commit()

    def update_campaign_ts(self, campaign_id: str, updated_at: float) -> None:
        self._conn.execute(
            "UPDATE campaigns SET updated_at=? WHERE campaign_id=?",
            (updated_at, campaign_id),
        )
        self._conn.commit()

    def delete_campaign(self, campaign_id: str) -> None:
        self._conn.execute("DELETE FROM experiments WHERE campaign_id=?", (campaign_id,))
        self._conn.execute("DELETE FROM campaigns WHERE campaign_id=?", (campaign_id,))
        self._conn.commit()

    def list_campaigns(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM campaigns ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM campaigns WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Experiments
    # ------------------------------------------------------------------

    def save_experiment(
        self,
        run_id: str,
        campaign_id: str,
        task_name: str,
        model: str,
        autonomy_mode: str = "supervised",
        status: str = "starting",
        started_at: float = 0.0,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO experiments
               (run_id, campaign_id, task_name, model, autonomy_mode, status, started_at, config_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                   status=excluded.status,
                   config_json=excluded.config_json""",
            (run_id, campaign_id, task_name, model, autonomy_mode, status, started_at,
             json.dumps(config or {})),
        )
        self._conn.commit()

    def update_experiment(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status", "phase", "turn", "cost_usd", "finished_at",
            "error", "autonomy_mode", "config_json",
        }
        parts = []
        values = []
        for k, v in fields.items():
            if k in allowed:
                parts.append(f"{k}=?")
                values.append(v)
        if not parts:
            return
        values.append(run_id)
        self._conn.execute(
            f"UPDATE experiments SET {', '.join(parts)} WHERE run_id=?",
            values,
        )
        self._conn.commit()

    def delete_experiment(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM experiments WHERE run_id=?", (run_id,))
        self._conn.commit()

    def list_experiments(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM experiments WHERE campaign_id=? ORDER BY started_at DESC",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_experiment(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM experiments WHERE run_id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Events (JSONL files)
    # ------------------------------------------------------------------

    def _event_path(self, campaign_id: str, run_id: str) -> Path:
        d = self._events_dir / campaign_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{run_id}.jsonl"

    def append_event(self, campaign_id: str, run_id: str, event: dict[str, Any]) -> None:
        p = self._event_path(campaign_id, run_id)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def load_events(self, campaign_id: str, run_id: str) -> list[dict[str, Any]]:
        p = self._event_path(campaign_id, run_id)
        if not p.exists():
            return []
        events = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return events

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    def recover_interrupted(self) -> int:
        """Mark any 'running'/'starting' experiments as 'interrupted' on startup."""
        cursor = self._conn.execute(
            "UPDATE experiments SET status='stopped', error='the server was restarted', finished_at=? "
            "WHERE status IN ('running', 'starting', 'awaiting_human')",
            (time.time(),),
        )
        self._conn.commit()
        count = cursor.rowcount
        if count:
            logger.warning("Marked %d interrupted experiments on startup", count)
        return count


class LazyStore:
    """The web console's store, opened on first use (so ``AURORA_HOME`` is read then, not at import)."""

    def __init__(self) -> None:
        self._store: Store | None = None

    def reset(self) -> None:
        """Forget the open store; the next use opens the one under the current ``AURORA_HOME``."""
        if self._store is not None:
            self._store.close()
        self._store = None

    def __getattr__(self, name: str) -> Any:
        if self._store is None:
            self._store = Store()
        return getattr(self._store, name)

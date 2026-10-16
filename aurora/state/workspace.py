"""Workspace directory management with atomic I/O.

The :class:`WorkspaceManager` owns the on-disk layout for a single research
campaign.  Every write goes through an atomic *tmp → fsync → rename* path
so that a crash mid-write never corrupts the canonical file.  An append-only
JSONL trajectory log records every significant event for post-hoc analysis.

Directory layout::

    {workspace.root}/{campaign_id}/
    ├── config.yaml                  # effective configuration (no secrets), for `aurora resume`
    ├── checkpoint.json              # where to resume
    ├── artifacts.json               # experiments, observations and claims recorded so far
    ├── human_gates.jsonl            # every human gate and its decision
    ├── trajectory.jsonl             # append-only event log
    ├── audit_records/               # milestone audit reports, one per hand-off
    ├── phases/
    │   ├── ideation/round_N/        # proposal JSON (with meeting notes and the chair's decision)
    │   ├── screening/round_N/       # score.json + session transcript
    │   ├── planning/round_N/        # plan.json + session transcript
    │   ├── research/                # research_loop.jsonl: live transcript of the research agent
    │   ├── supervisor/round_N/      # live-auditor log and verdicts
    │   ├── analysis/round_N/        # verdict.json + session transcript
    │   └── writing/                 # draft_N.md: the report
    ├── workspace/                   # the container's /app, copied out when the campaign ends
    └── .claude/skills/              # skills available to the agent sessions
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from aurora.config import WorkspaceConfig

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """Manages the structured workspace directory for a research campaign."""

    def __init__(self, config: WorkspaceConfig, campaign_id: str) -> None:
        self._config = config
        self._campaign_id = campaign_id
        self._root = Path(config.root).expanduser().resolve() / campaign_id

    # ------------------------------------------------------------------
    # Root paths
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def campaign_dir(self) -> Path:
        return self._root

    @property
    def artifacts_path(self) -> Path:
        return self._root / "artifacts.json"

    @property
    def mental_model_path(self) -> Path:
        return self._root / "mental_model.json"

    @property
    def trajectory_path(self) -> Path:
        return self._root / "trajectory.jsonl"

    # ------------------------------------------------------------------
    # Phase directories — generic round-based layout
    # ------------------------------------------------------------------
    #
    # Every phase except writing supports multiple rounds (analysis
    # can loop back to any earlier phase). The on-disk layout is:
    #
    #   phases/<phase>/round_<N>/
    #       transcript.jsonl   — agent trajectory
    #       <phase-specific outputs>
    #
    # Writing has no rounds — it runs once at the end:
    #
    #   phases/writing/
    #       transcript.jsonl
    #       draft_<version>.md

    @property
    def phases_dir(self) -> Path:
        return self._root / "phases"

    def phase_dir(self, phase_name: str) -> Path:
        d = self.phases_dir / phase_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def phase_round_dir(self, phase_name: str, round_n: int) -> Path:
        d = self.phase_dir(phase_name) / f"round_{round_n}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def phase_transcript_path(self, phase_name: str, round_n: int = 0) -> Path:
        if phase_name == "writing":
            return self.phase_dir("writing") / "transcript.jsonl"
        return self.phase_round_dir(phase_name, round_n) / "transcript.jsonl"

    # -- Roundtable --

    @property
    def roundtable_dir(self) -> Path:
        return self.phases_dir / "ideation"

    def proposal_path(self, proposal_id: str, round_n: int = 0) -> Path:
        return self.phase_round_dir("ideation", round_n) / f"{proposal_id}.json"

    # -- Screening --

    @property
    def screening_dir(self) -> Path:
        return self.phases_dir / "screening"

    def screening_verdict_path(self, round_n: int = 0) -> Path:
        return self.phase_round_dir("screening", round_n) / "verdict.json"

    # -- Planning --

    @property
    def planning_dir(self) -> Path:
        return self.phases_dir / "planning"

    def experiment_plan_path_for_round(self, round_n: int) -> Path:
        return self.phase_round_dir("planning", round_n) / "plan.json"

    @property
    def experiment_plan_path(self) -> Path:
        """Latest plan — finds the highest round, falls back to round_0."""
        rounds = self._list_rounds("planning")
        rn = rounds[-1] if rounds else 0
        return self.experiment_plan_path_for_round(rn)

    # -- Research --

    @property
    def research_dir(self) -> Path:
        return self.phases_dir / "research"

    def research_round_dir(self, round_n: int) -> Path:
        return self.phase_round_dir("research", round_n)

    def research_job_dir(self, round_n: int, job_id: str) -> Path:
        d = self.research_round_dir(round_n) / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def research_iter_dir(
        self, round_n: int, job_id: str, iter_n: int
    ) -> Path:
        d = self.research_job_dir(round_n, job_id) / f"iter_{iter_n}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def research_transcript_path(
        self, round_n: int, job_id: str, iter_n: int
    ) -> Path:
        return self.research_iter_dir(round_n, job_id, iter_n) / "transcript.jsonl"

    def research_workspace_dir(
        self, round_n: int, job_id: str, iter_n: int
    ) -> Path:
        d = self.research_iter_dir(round_n, job_id, iter_n) / "workspace"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def research_verifier_path(
        self, round_n: int, job_id: str, iter_n: int
    ) -> Path:
        return self.research_iter_dir(round_n, job_id, iter_n) / "verifier.json"

    def research_best_result_path(self, round_n: int, job_id: str) -> Path:
        return self.research_job_dir(round_n, job_id) / "best_result.json"

    # -- Analysis --

    @property
    def analysis_dir(self) -> Path:
        return self.phases_dir / "analysis"

    def analysis_round_dir(self, round_n: int) -> Path:
        return self.phase_round_dir("analysis", round_n)

    @property
    def analysis_master_path(self) -> Path:
        return self.analysis_dir / "analysis_master.json"

    def analysis_verdict_path(self, round_n: int) -> Path:
        return self.analysis_round_dir(round_n) / "verdict.json"

    # -- Writing (no rounds) --

    @property
    def writing_dir(self) -> Path:
        return self.phases_dir / "writing"

    def manuscript_path(self, version: int) -> Path:
        return self.writing_dir / f"draft_{version}.md"

    # -- Audit --

    def audit_record_path(self, audit_id: str) -> Path:
        return self._root / "audit_records" / f"{audit_id}.json"

    # -- Legacy compat --

    def experiment_dir(self, job_id: str) -> Path:
        return self.research_job_dir(0, job_id)

    # -- Round helpers --

    def _list_rounds(self, phase_name: str) -> list[int]:
        """Return sorted round numbers for a phase."""
        pd = self.phases_dir / phase_name
        if not pd.exists():
            return []
        rounds = []
        for d in pd.iterdir():
            if d.is_dir() and d.name.startswith("round_"):
                try:
                    rounds.append(int(d.name.split("_", 1)[1]))
                except (ValueError, IndexError):
                    pass
        return sorted(rounds)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create the workspace directory structure."""
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "phases").mkdir(exist_ok=True)
        (self._root / "backups").mkdir(exist_ok=True)
        (self._root / "audit_records").mkdir(exist_ok=True)

        for phase in ("ideation", "screening", "planning", "research",
                       "analysis", "writing"):
            (self.phases_dir / phase).mkdir(exist_ok=True)

        logger.info("Workspace initialised at %s", self._root)

    # ------------------------------------------------------------------
    # Atomic I/O
    # ------------------------------------------------------------------

    def atomic_write_json(self, path: Path, data: dict[str, Any] | BaseModel) -> None:
        """Atomically write JSON data via *tmp → fsync → os.replace*."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, BaseModel):
            content = data.model_dump_json(indent=2)
        else:
            content = json.dumps(data, indent=2, default=str, ensure_ascii=False)

        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def atomic_write_text(self, path: Path, text: str) -> None:
        """Atomically write a text file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def read_json(self, path: Path) -> dict[str, Any]:
        """Read a JSON file, returning an empty dict if not found."""
        path = Path(path)
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    # ------------------------------------------------------------------
    # Append-only event log
    # ------------------------------------------------------------------

    def append_event(self, event: dict[str, Any]) -> None:
        """Append a timestamped event to the trajectory JSONL log."""
        event.setdefault("timestamp", datetime.now(UTC).isoformat())
        line = json.dumps(event, default=str, ensure_ascii=False) + "\n"

        self.trajectory_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.trajectory_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def read_events(self, last_n: int | None = None) -> list[dict[str, Any]]:
        """Read events from the trajectory log."""
        if not self.trajectory_path.exists():
            return []
        events: list[dict[str, Any]] = []
        with open(self.trajectory_path, encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if raw_line:
                    try:
                        events.append(json.loads(raw_line))
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed trajectory line")
        if last_n is not None:
            events = events[-last_n:]
        return events

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    def backup_file(self, path: Path) -> Path:
        """Create a timestamped backup of *path* before modification."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Cannot back up non-existent file: {path}")

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        backup_dir = self._root / "backups" / ts
        backup_dir.mkdir(parents=True, exist_ok=True)

        try:
            rel = path.relative_to(self._root)
        except ValueError:
            rel = Path(path.name)

        dest = backup_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

        logger.debug("Backed up %s → %s", path, dest)
        return dest

    # ------------------------------------------------------------------
    # Listing helpers
    # ------------------------------------------------------------------

    def list_rounds(self, phase_name: str) -> list[int]:
        """Return sorted round numbers for a phase."""
        return self._list_rounds(phase_name)

    def list_research_rounds(self) -> list[int]:
        return self._list_rounds("research")

    def list_research_jobs(self, round_n: int) -> list[str]:
        """Return job IDs in a research round."""
        rd = self.research_dir / f"round_{round_n}"
        if not rd.exists():
            return []
        return sorted(d.name for d in rd.iterdir() if d.is_dir())

    def list_research_iterations(self, round_n: int, job_id: str) -> list[int]:
        """Return iteration numbers for a job in a round."""
        jd = self.research_dir / f"round_{round_n}" / job_id
        if not jd.exists():
            return []
        iters = []
        for d in jd.iterdir():
            if d.is_dir() and d.name.startswith("iter_"):
                try:
                    iters.append(int(d.name.split("_", 1)[1]))
                except (ValueError, IndexError):
                    pass
        return sorted(iters)

    def list_experiments(self) -> list[str]:
        """Return job IDs across all research rounds."""
        jobs: set[str] = set()
        for rn in self.list_research_rounds():
            jobs.update(self.list_research_jobs(rn))
        return sorted(jobs)

    def list_audit_records(self) -> list[str]:
        aud_dir = self._root / "audit_records"
        if not aud_dir.exists():
            return []
        return sorted(p.stem for p in aud_dir.glob("*.json"))

    def list_proposals(self) -> list[str]:
        proposals: list[str] = []
        for rn in self._list_rounds("ideation"):
            rd = self.phase_round_dir("ideation", rn)
            for p in rd.glob("*.json"):
                if p.name != "transcript.jsonl":
                    proposals.append(p.stem)
        return sorted(set(proposals))

    def list_manuscripts(self) -> list[int]:
        if not self.writing_dir.exists():
            return []
        versions: list[int] = []
        for p in self.writing_dir.glob("draft_*.md"):
            try:
                versions.append(int(p.stem.split("_", 1)[1]))
            except (ValueError, IndexError):
                pass
        return sorted(versions)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def exists(self) -> bool:
        return self._root.exists()

    def __repr__(self) -> str:
        return f"WorkspaceManager(root={self._root})"

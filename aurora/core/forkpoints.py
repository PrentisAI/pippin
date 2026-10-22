"""Points a run can be forked from: after each phase and after each research job.

A point keeps what the run knew then (checkpoint, artifacts, mental model, cost) and a snapshot of
its workspace, so a new run can start from exactly there with something done differently.  Large
files are not kept in the snapshot (they are listed instead): a fork that needs them recomputes them.
"""

from __future__ import annotations

import json
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any

DIR = "forkpoints"
KEEP = ("checkpoint.json", "artifacts.json", "mental_model.json", "cost.json", "human_gates.jsonl")
MAX_FILE = 50 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".cache", ".ipynb_checkpoints"}


def save(campaign_dir: Path, *, label: str, phase: str, completed_jobs: list[str] | None = None,
         cost_usd: float = 0.0, round_: int = 0) -> dict[str, Any]:
    """Keep the run's state now as a fork point; returns its record."""
    root = Path(campaign_dir) / DIR
    root.mkdir(parents=True, exist_ok=True)
    n = len([p for p in root.iterdir() if p.is_dir()]) + 1
    point = root / f"{n:03d}"
    point.mkdir()
    for name in KEEP:
        src = Path(campaign_dir) / name
        if src.is_file():
            shutil.copy2(src, point / name)
    ws = Path(campaign_dir) / "workspace"
    skipped: list[str] = []
    files = 0
    total = 0
    if ws.is_dir():
        with tarfile.open(point / "workspace.tar.gz", "w:gz") as tar:
            for f in sorted(ws.rglob("*")):
                rel = f.relative_to(ws)
                if any(part in _SKIP_DIRS for part in rel.parts) or not f.is_file() or f.is_symlink():
                    continue
                size = f.stat().st_size
                if size > MAX_FILE or total + size > MAX_TOTAL:
                    skipped.append(f"{rel} ({size // (1024 * 1024)} MB)")
                    continue
                tar.add(f, arcname=str(rel))
                files += 1
                total += size
    record = {"id": point.name, "label": label, "phase": phase, "completed_jobs": completed_jobs or [],
              "round": round_, "cost_usd": round(cost_usd, 4), "at": time.time(), "files": files, "bytes": total,
              "skipped": skipped[:200]}
    (point / "point.json").write_text(json.dumps(record))
    return record


def points(campaign_dir: Path) -> list[dict[str, Any]]:
    root = Path(campaign_dir) / DIR
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        try:
            out.append(json.loads((p / "point.json").read_text()))
        except (OSError, ValueError):
            continue
    return out


def restore_into(source_dir: Path, point_id: str, target_dir: Path) -> dict[str, Any]:
    """Make *target_dir* the run as it was at the point: its state, its workspace, and the phase files,
    results and scripts written until then."""
    source_dir, target_dir = Path(source_dir), Path(target_dir)
    point = source_dir / DIR / point_id
    record = json.loads((point / "point.json").read_text())
    at = record["at"]
    target_dir.mkdir(parents=True, exist_ok=False)
    for name in KEEP:
        if (point / name).is_file():
            shutil.copy2(point / name, target_dir / name)
    # what the phases wrote until then (proposals, plans, verdicts, transcripts) and the audit records
    for sub in ("phases", "audit_records"):
        src = source_dir / sub
        if not src.is_dir():
            continue
        for f in src.rglob("*"):
            if f.is_file() and f.stat().st_mtime <= at + 1:
                dest = target_dir / f.relative_to(source_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
    # results recorded until then, and the scripts they kept
    results = source_dir / "results.jsonl"
    if results.is_file():
        kept = []
        for line in results.read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("at", 0) <= at + 1:
                kept.append(line)
                snap = source_dir / "provenance" / r["id"]
                if snap.is_dir():
                    shutil.copytree(snap, target_dir / "provenance" / r["id"], dirs_exist_ok=True)
        if kept:
            (target_dir / "results.jsonl").write_text("\n".join(kept) + "\n")
    ws = target_dir / "workspace"
    ws.mkdir()
    archive = point / "workspace.tar.gz"
    if archive.is_file():
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(ws, filter="data")
    return record

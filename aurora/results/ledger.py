"""The results of one run: ``results.jsonl`` in its campaign directory, scripts under ``provenance/``."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

FILE = "results.jsonl"
SNAPSHOTS = "provenance"
_MAX_SNAPSHOT = 2 * 1024 * 1024          # scripts larger than this are referenced, not copied
_REF = re.compile(r"\{(R\d+)\}")


def result_refs(text: str) -> list[str]:
    """The result IDs a text cites as ``{R3}``, in order of first use."""
    return list(dict.fromkeys(_REF.findall(text or "")))


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ResultsLedger:
    """Append-only: a result recorded again under the same name gets a new ID and supersedes the old."""

    def __init__(self, campaign_dir: str | Path, workspace_dir: str | Path | None = None) -> None:
        self.campaign_dir = Path(campaign_dir)
        self.workspace_dir = Path(workspace_dir) if workspace_dir else self.campaign_dir / "workspace"
        self.path = self.campaign_dir / FILE
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ reading

    def all(self, *, current: bool = False) -> list[dict[str, Any]]:
        """Every result, oldest first; ``current``: only the latest of each name."""
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        if current:
            latest: dict[str, dict[str, Any]] = {}
            for r in rows:
                latest[r["name"].lower()] = r
            keep = {id(r) for r in latest.values()}
            rows = [r for r in rows if id(r) in keep]
        return rows

    def get(self, rid: str) -> dict[str, Any] | None:
        rid = rid.strip().strip("{}").upper()
        return next((r for r in self.all() if r["id"] == rid), None)

    # ------------------------------------------------------------------ recording

    def _resolve(self, path: str) -> Path | None:
        """A workspace path as the agent gave it (/app/..., relative, or under the workspace)."""
        if not path:
            return None
        p = path.strip()
        if p.startswith("/app/"):
            p = p[5:]
        target = (self.workspace_dir / p).resolve() if not p.startswith("/") else Path(p).resolve()
        root = self.workspace_dir.resolve()
        return target if target.is_relative_to(root) else None

    def _rel(self, target: Path) -> str:
        return str(target.relative_to(self.workspace_dir.resolve()))

    def _file(self, path: str) -> dict[str, Any]:
        target = self._resolve(path)
        if target is None:
            return {"path": path, "exists": False, "note": "outside the workspace"}
        if not target.is_file():
            return {"path": self._rel(target), "exists": False}
        return {"path": self._rel(target), "exists": True, "size": target.stat().st_size, "sha256": _sha(target)}

    def record(self, name: str, value: Any, *, unit: str = "", uncertainty: Any = None, description: str = "",
               script: str = "", command: str = "", inputs: list[str] | None = None,
               outputs: list[str] | None = None, job: str = "", by: str = "") -> dict[str, Any]:
        name = " ".join(str(name).split())
        if not name:
            raise ValueError("a result needs a name")
        if value is None or str(value).strip() == "":
            raise ValueError("a result needs a value")
        with self._lock:
            n = len(self.all()) + 1
            rid = f"R{n}"
            entry: dict[str, Any] = {
                "id": rid, "name": name[:200], "value": _num(value) if _num(value) is not None else str(value)[:200],
                "unit": unit[:40], "uncertainty": _num(uncertainty) if uncertainty not in (None, "") else None,
                "description": description[:2000], "command": command[:2000], "job": job, "by": by,
                "inputs": [self._file(p) for p in (inputs or [])][:30],
                "outputs": [self._file(p) for p in (outputs or [])][:30], "script": None, "at": time.time(),
            }
            if script:
                info = self._file(script)
                target = self._resolve(script)
                if target is not None and target.is_file() and target.stat().st_size <= _MAX_SNAPSHOT:
                    snap = self.campaign_dir / SNAPSHOTS / rid / target.name
                    snap.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, snap)
                    info["snapshot"] = str(snap.relative_to(self.campaign_dir))
                entry["script"] = info
            previous = [r["id"] for r in self.all() if r["name"].lower() == name.lower()]
            if previous:
                entry["supersedes"] = previous[-1]
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        return entry

    # ------------------------------------------------------------------ for people and the writer

    @staticmethod
    def show_value(r: dict[str, Any]) -> str:
        v = r["value"]
        text = f"{v:.6g}" if isinstance(v, float) else str(v)
        if r.get("uncertainty") is not None:
            text += f" ± {r['uncertainty']:.3g}"
        return text + (f" {r['unit']}" if r.get("unit") else "")

    def table(self, limit: int = 60) -> str:
        """The current results as a Markdown list (for analysis and the writer)."""
        rows = self.all(current=True)[-limit:]
        if not rows:
            return ""
        lines = []
        for r in rows:
            where = r["script"]["path"] if r.get("script") else (r["outputs"][0]["path"] if r.get("outputs") else "")
            lines.append(f"- {{{r['id']}}} {r['name']} = {self.show_value(r)}"
                         + (f" (job {r['job']}" + (f", {where}" if where else "") + ")" if r.get("job") or where else "")
                         + (f": {r['description'][:200]}" if r.get("description") else ""))
        return "\n".join(lines)

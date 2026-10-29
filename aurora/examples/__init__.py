"""Example projects that come with Pippin: finished runs a new console shows from the start.

Each example is a folder here (made with ``scripts/build_example.py``): ``example.json`` (what it
is, the project and its run), ``run/`` (the run's directory, slimmed) and ``events.jsonl`` (its
stream).  Installing one makes it a project of the console with the ID ``ex_<id>``: read-only for
everyone (:func:`is_example`), hidden per person on request, and copied into a person's own project
when they want to run it themselves.

The scripted demo is listed with them; it is run, not installed (``POST /api/demo``).
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
PREFIX = "ex_"
_TEXT = {".json", ".jsonl", ".md", ".py", ".yaml", ".yml", ".txt", ".log", ".out", ".sh", ".csv"}

DEMO = {"id": "demo", "kind": "scripted", "title": "Zn segregation in Cu(111)", "field": "materials",
        "blurb": "Scripted models replay a small study: three seats argue at the ideation meeting, every phase "
                 "runs, the auditors check each hand-off, and a report is written.",
        "shows": ["ideation meeting", "screening", "milestone audits", "report"],
        "facts": "seconds · no key · no cost"}


def is_example(campaign_id: str) -> bool:
    return campaign_id.startswith(PREFIX)


def project_id(example_id: str) -> str:
    return PREFIX + example_id.replace("-", "_")


def run_id(example_id: str) -> str:
    """The example's run as installed: its own ID, so it never meets the run it was made from."""
    return "ex-" + example_id


def catalogue() -> list[dict[str, Any]]:
    """Every example that comes with this version, as cards (the scripted demo last)."""
    out = []
    for meta_file in sorted(HERE.glob("*/example.json")):
        try:
            meta = json.loads(meta_file.read_text())
        except (OSError, ValueError):
            continue
        exp = meta["experiment"]
        hours, minutes = divmod(int(((exp.get("finished_at") or 0) - exp["started_at"]) // 60), 60)
        out.append({"id": meta["id"], "kind": meta.get("kind", "real run"), "title": meta["title"],
                    "field": meta.get("field", ""), "blurb": meta.get("blurb", ""), "shows": meta.get("shows", []),
                    "facts": f"{hours} h {minutes:02d} min · ${exp.get('cost_usd', 0):.2f} · {exp['model'].split('/')[-1]}",
                    "campaign_id": project_id(meta["id"]), "run_id": run_id(meta["id"])})
    return [*out, DEMO]


def _fill(text: str, home: Path, campaign_id: str, original_run: str, run: str) -> str:
    return text.replace("{AURORA_HOME}", str(home)).replace("{PROJECT_ID}", campaign_id).replace(original_run, run)


def install(example_id: str, *, home: Path, store: Any) -> str | None:
    """Make the example a project of this console (once; again if its files went missing).
    Returns its project ID, or None when there is no such example."""
    src = HERE / example_id
    meta_file = src / "example.json"
    if not meta_file.is_file():
        return None
    meta = json.loads(meta_file.read_text())
    cid = project_id(example_id)
    exp = meta["experiment"]
    rid = run_id(example_id)
    run_dir = home / "projects" / cid / rid
    row = store.get_experiment(rid)
    if store.get_campaign(cid) and run_dir.is_dir() and row and row.get("campaign_id") == cid:
        return cid
    fill = lambda text: _fill(text, home, cid, exp["run_id"], rid)  # noqa: E731
    if run_dir.exists():
        shutil.rmtree(run_dir)
    for f in sorted((src / "run").rglob("*")):
        if not f.is_file():
            continue
        dest = run_dir / f.relative_to(src / "run")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if f.suffix in _TEXT:
            dest.write_text(fill(f.read_text(errors="replace")))
        else:
            shutil.copy2(f, dest)
    events = home / "events" / cid / f"{rid}.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(fill((src / "events.jsonl").read_text()))
    now = time.time()
    project = meta["project"]
    store.save_campaign(cid, project["name"], project.get("objective", ""), exp["started_at"], now)
    store.save_experiment(rid, cid, exp["task_name"], exp["model"], exp.get("autonomy_mode", "supervised"),
                          status=exp.get("status", "completed"), started_at=exp["started_at"],
                          config=json.loads(exp.get("config_json") or "{}"))
    store.update_experiment(rid, status=exp.get("status", "completed"), phase=exp.get("phase", ""),
                            cost_usd=exp.get("cost_usd", 0.0), finished_at=exp.get("finished_at"))
    logger.info("Installed the example %s as project %s", example_id, cid)
    return cid


def install_all(*, home: Path, store: Any) -> list[str]:
    """Install every example this console does not have yet, unless the admin removed it."""
    removed = set(_removed(home))
    out = []
    for card in catalogue():
        if card["kind"] == "scripted" or card["id"] in removed:
            continue
        try:
            cid = install(card["id"], home=home, store=store)
        except Exception:
            logger.warning("Could not install the example %s", card["id"], exc_info=True)
            continue
        if cid:
            out.append(cid)
    return out


def _removed_file(home: Path) -> Path:
    return home / "examples_removed.json"


def _removed(home: Path) -> list[str]:
    try:
        return json.loads(_removed_file(home).read_text())
    except (OSError, ValueError):
        return []


def set_removed(home: Path, example_id: str, removed: bool) -> None:
    """The admin removes an example from the console (or brings it back)."""
    ids = set(_removed(home))
    ids.discard(example_id) if not removed else ids.add(example_id)
    _removed_file(home).write_text(json.dumps(sorted(ids)))


def removed_ids(home: Path) -> list[str]:
    return _removed(home)

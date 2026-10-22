"""Build a bundled example project from a finished run of a console's data directory.

    python scripts/build_example.py --home ~/.aurora --project p_20261019_16dc60 \
        --run 20261019-025018_citi-bike-usage-patterns --id citi-bike \
        --title "Citi Bike usage patterns" --out aurora/examples/citi-bike

What is kept: the run's state files, its phases (proposals, plans, transcripts, the report and its
figures), the audit records, and from its workspace the code and the outputs (logs, tables,
figures) — not the downloaded data, not files uploaded to it, not Claude Code's session records.
The stream keeps the engine's events and the researcher's questions that are about the run (the
ones to keep are named with --keep-chat); paths of the console it ran in become placeholders,
filled in when the example is installed.  The build stops if anything looks like a key.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from pathlib import Path

RUN_FILES = ("artifacts.json", "checkpoint.json", "cost.json", "human_gates.jsonl", "mental_model.json",
             "trajectory.jsonl", "results.jsonl", "fork.json")
RUN_DIRS = ("phases", "audit_records", "provenance")
WORKSPACE_KEEP = re.compile(r"^(?!.*__pycache__)(code/|output/|[^/]+\.py$|[^/]+\.(png|svg|md|csv|txt|json)$|\.aurora/(environment\.txt|jobs/[^/]+/(run\.sh|exit_code|[^/]+\.out)))")
MAX_FILE = 3 * 1024 * 1024
DROP_EVENTS = {"autonomy_changed"}
SECRET = re.compile(r"sk-or-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}")


def _copy(src: Path, dst: Path) -> int:
    if src.stat().st_size > MAX_FILE:
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return 1


def build(home: Path, project: str, run: str, out: Path, *, example_id: str, title: str, keep_chat: list[str],
          blurb: str, field: str, shows: list[str], scrub: list[tuple[str, str]] = ()) -> dict:
    src = home / "projects" / project / run
    if out.exists():
        shutil.rmtree(out)
    target = out / "run"
    n = 0
    for name in RUN_FILES:
        if (src / name).is_file():
            n += _copy(src / name, target / name)
    for d in RUN_DIRS:
        for f in sorted((src / d).rglob("*")) if (src / d).is_dir() else []:
            if f.is_file():
                n += _copy(f, target / f.relative_to(src))
    ws = src / "workspace"
    for f in sorted(ws.rglob("*")) if ws.is_dir() else []:
        rel = f.relative_to(ws).as_posix()
        if f.is_file() and not f.is_symlink() and WORKSPACE_KEEP.match(rel):
            n += _copy(f, target / "workspace" / rel)

    # the configuration, with the console's paths as placeholders
    config = (src / "config.yaml").read_text()
    (target / "config.yaml").write_text(config.replace(str(home), "{AURORA_HOME}").replace(project, "{PROJECT_ID}"))

    # the stream: engine events, and the researcher's questions worth keeping with the front desk's answers
    events_in = home / "events" / project / f"{run}.jsonl"
    kept, dropping = [], False
    for line in events_in.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        kind = e.get("event_type", "")
        if kind == "chat_message":
            dropping = (e.get("data") or {}).get("text", "") not in keep_chat
        elif not kind.startswith("frontdesk_"):
            dropping = False if kind not in DROP_EVENTS else dropping
        if dropping or kind in DROP_EVENTS:
            continue
        kept.append(json.dumps(e, ensure_ascii=False).replace(str(home), "{AURORA_HOME}"))
    (out / "events.jsonl").write_text("\n".join(kept) + "\n")

    db = sqlite3.connect(home / "aurora.db")
    db.row_factory = sqlite3.Row
    row = dict(db.execute("SELECT * FROM experiments WHERE run_id = ?", (run,)).fetchone())
    camp = dict(db.execute("SELECT * FROM campaigns WHERE campaign_id = ?", (project,)).fetchone())
    request = json.loads(row["config_json"] or "{}").get("request") or {}
    objective = request.get("instruction") or camp.get("objective") or ""
    meta = {
        "id": example_id, "title": title, "blurb": blurb, "field": field, "shows": shows, "kind": "real run",
        "project": {"name": title, "objective": objective},
        "experiment": {k: row[k] for k in ("run_id", "task_name", "model", "autonomy_mode", "status", "phase",
                                           "cost_usd", "started_at", "finished_at")} | {"config_json": row["config_json"]},
    }
    (out / "example.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))

    # nothing that looks like a key, and no path of the machine it ran on (also cut short in previews)
    home_s = str(home)
    prefixes = [home_s[:k] for k in range(len(home_s), 5, -1)]
    path_re = re.compile("(" + "|".join(map(re.escape, prefixes)) + r")(?![\w.-])")
    for f in out.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".jsonl", ".md", ".py", ".yaml", ".txt", ".log", ".out", ".sh", ".csv"):
            text = f.read_text(errors="replace")
            if SECRET.search(text):
                raise SystemExit(f"{f} contains something that looks like a key; not building")
            scrubbed = path_re.sub("{AURORA_HOME}", text).replace(project, "{PROJECT_ID}")
            for old, new in scrub:
                scrubbed = scrubbed.replace(old, new)
            if scrubbed != text:
                f.write_text(scrubbed)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    return {"files": n, "events": len(kept), "bytes": size}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--home", required=True, type=Path)
    ap.add_argument("--project", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--blurb", default="")
    ap.add_argument("--field", default="")
    ap.add_argument("--shows", nargs="*", default=[])
    ap.add_argument("--keep-chat", nargs="*", default=[], help="researcher messages to keep, verbatim")
    ap.add_argument("--scrub", nargs="*", default=[], metavar="OLD=NEW",
                    help="more text to replace everywhere (a user name in a path, say)")
    a = ap.parse_args()
    print(build(a.home.expanduser(), a.project, a.run, a.out, example_id=a.id, title=a.title, keep_chat=a.keep_chat,
                blurb=a.blurb, field=a.field, shows=a.shows,
                scrub=[tuple(x.split("=", 1)) for x in a.scrub]))


if __name__ == "__main__":
    main()

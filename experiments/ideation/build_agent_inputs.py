"""Collect each AutoResearchEval run's ideation record and a source-paper exposure flag (free, offline).

Writes ideation_inputs.json: one entry per open-ended task with its premise / tension, the paper's
reference conclusion, and the runs' ideation fields (from decision.json).
saw_source_before_code: the source paper's DOI or title appeared in the trajectory before the agent's
first code-writing call (a proxy for "saw the answer before ideating").

Usage (from experiments/):
  python -m ideation.build_agent_inputs --out runs/ideation/agent_ideas/ideation_inputs.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from common.dataset import AutoResearchEval
from common.normalize import load_steps
from common.paths import RUNS_DIR, add_data_args, are_root

LIMIT = {"problem": 600, "hypothesis": 900, "method": 900, "alternatives": 700, "falsification": 500}
CODE_CALL = re.compile(r"(\.py\b|cat\s*>|<<\s*['\"]?\w*EOF|write_file|\"file_path\")", re.I)


def clip(x, n: int) -> str:
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + " …"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower())


def build(are: AutoResearchEval) -> list[dict]:
    out: dict[str, dict] = {}
    for r in are.rollouts():
        tid, model = r["task_id"], r["model"]
        t = are.task(tid)
        entry = out.setdefault(tid, {
            "task_id": tid, "premise": t["premise"], "tension": t["tension"],
            "conclusion_gt": t["conclusion_gt"], "novelty_move": t["novelty_move"],
            "novelty_rationale": t.get("novelty_rationale"), "runs": {}})
        try:
            d = json.loads(r["decision_json"] or "{}")
        except json.JSONDecodeError:
            d = {}
        if not d:
            entry["runs"][model] = None
            continue
        pl = d.get("process_log") if isinstance(d.get("process_log"), dict) else {}
        ide = pl.get("ideation") if isinstance(pl.get("ideation"), dict) else {}
        method = d.get("method")
        if isinstance(method, dict):
            method = method.get("approach") or method
        idea = {"problem": clip(d.get("problem", ""), LIMIT["problem"]),
                "hypothesis": clip(d.get("hypothesis", ""), LIMIT["hypothesis"]),
                "method": clip(method or "", LIMIT["method"]),
                "alternatives_considered": clip(ide.get("alternative_framing_considered", ""), LIMIT["alternatives"]),
                "falsification_check": clip(ide.get("falsification_check", ""), LIMIT["falsification"])}
        sp = t["source_paper"]
        doi, tkey = (sp.get("doi") or "").lower(), norm(sp.get("title") or "")[:45]
        traj = are.root / r["trajectory_path"]
        steps = load_steps(traj) if traj.exists() else []
        first_code = next((s["i"] for s in steps if s["kind"] == "call" and CODE_CALL.search(s["body"][:400])), None)
        first_src = next((s["i"] for s in steps
                          if (doi and doi in s["body"].lower()) or (tkey and tkey in norm(s["body"][:30000]))), None)
        entry["runs"][model] = {"idea": idea,
                                "saw_source_before_code": first_src is not None and (first_code is None or first_src < first_code),
                                "saw_source_any": first_src is not None,
                                "regime": r.get("regime_move_verified")}
    return list(out.values())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_data_args(ap, are=True)
    ap.add_argument("--out", type=Path, default=RUNS_DIR / "ideation" / "agent_ideas" / "ideation_inputs.json")
    a = ap.parse_args()
    entries = build(AutoResearchEval(are_root(a)))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(entries, ensure_ascii=False, indent=1))
    runs = [v for e in entries for v in e["runs"].values() if v]
    print(f"{len(entries)} tasks, {len(runs)} runs with ideation records, "
          f"{sum(v['saw_source_before_code'] for v in runs)} saw the source paper before first code; wrote {a.out}")


if __name__ == "__main__":
    main()

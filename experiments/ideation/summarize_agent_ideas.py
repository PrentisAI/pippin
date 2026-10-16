"""Summarize the judge scores of the AutoResearchEval agents' own ideation records (Appendix C,
"Ideation scoring"; the 67% proxy rate of Section 5): per-model quality and flags, distinct
directions, best-of-group headroom, agreement with a second judge.  Offline and free.

Usage (from experiments/):
  python -m ideation.summarize_agent_ideas          # the released GPT-5.6 scores + Gemini second judge
  python -m ideation.summarize_agent_ideas SCORES.json [SECOND_JUDGE.json] --inputs ideation_inputs.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from common.paths import add_data_args, require

QUAL = ("falsifiable", "specific", "feasible", "important")
FLAGS = ("restates_known_answer", "proxy_for_question", "unfalsifiable")


def load(path: Path, inputs: dict) -> tuple[dict, list[dict]]:
    S = json.loads(path.read_text())
    rows = []
    for r in S["results"]:
        j = r.get("judgement") or {}
        if "ideas" not in j:
            continue
        task = inputs[r["task_id"]]
        for it in j["ideas"]:
            m = r["mapping"].get(it.get("label"))
            if not m:
                continue
            run = task["runs"][m]
            rows.append({"task": r["task_id"], "model": m, "alignment": it.get("alignment", 0),
                         "quality": statistics.mean(it.get(k, 0) for k in QUAL),
                         **{k: it.get(k, 0) for k in QUAL},
                         **{f: bool((it.get("flags") or {}).get(f)) for f in FLAGS},
                         "saw_source": run["saw_source_before_code"], "regime": run["regime"],
                         "n_clusters": len(j.get("clusters") or []), "n_props": len(j["ideas"])})
    return S, rows


def headroom(rows: list[dict], label: str) -> None:
    by_task = defaultdict(list)
    for x in rows:
        by_task[x["task"]].append(x)
    best_a = statistics.mean(max(x["alignment"] for x in xs) for xs in by_task.values())
    mean_a = statistics.mean(statistics.mean(x["alignment"] for x in xs) for xs in by_task.values())
    best_q = statistics.mean(max(x["quality"] for x in xs) for xs in by_task.values())
    mean_q = statistics.mean(statistics.mean(x["quality"] for x in xs) for xs in by_task.values())
    cov_any = sum(1 for xs in by_task.values() if max(x["alignment"] for x in xs) >= 2) / len(by_task)
    cov_single = statistics.mean(x["alignment"] >= 2 for x in rows)
    print(f"[{label}] tasks={len(by_task)} | alignment: best-of-group {best_a:.2f} vs single-model mean {mean_a:.2f} | "
          f"quality: best {best_q:.2f} vs mean {mean_q:.2f} | tasks where >=1 proposal has alignment>=2: {cov_any:.0%} "
          f"vs a single proposal: {cov_single:.0%}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_data_args(ap)
    ap.add_argument("scores", nargs="*", type=Path,
                    help="main judge scores [second judge scores] (default: the released GPT-5.6 and Gemini files)")
    ap.add_argument("--inputs", type=Path, default=None,
                    help="default: <data-dir>/ideation/agent_ideas/ideation_inputs.json")
    a = ap.parse_args()
    base = a.data_dir / "ideation" / "agent_ideas"
    files = a.scores or [base / "ideation_scores_gpt.json", base / "ideation_scores_gemini15.json"]
    inputs_f = require(a.inputs or base / "ideation_inputs.json", "ideation inputs")
    inputs = {t["task_id"]: t for t in json.loads(inputs_f.read_text())}
    S, rows = load(require(files[0], "scores"), inputs)
    print(f"judge {S['judge']}, spend ${S['spend']['cost']:.2f}, scored proposals {len(rows)}")
    print(f"{'model':18s} n   align  quality  restates  proxy  unfalsif  align(saw src) align(not)")
    by_m = defaultdict(list)
    for x in rows:
        by_m[x["model"]].append(x)
    for m, xs in sorted(by_m.items()):
        saw = [x["alignment"] for x in xs if x["saw_source"]]
        nos = [x["alignment"] for x in xs if not x["saw_source"]]
        print(f"{m:18s} {len(xs):3d} {statistics.mean(x['alignment'] for x in xs):5.2f} "
              f"{statistics.mean(x['quality'] for x in xs):7.2f}"
              f" {statistics.mean(x['restates_known_answer'] for x in xs):8.0%} "
              f"{statistics.mean(x['proxy_for_question'] for x in xs):6.0%}"
              f" {statistics.mean(x['unfalsifiable'] for x in xs):8.0%}   "
              f"{statistics.mean(saw) if saw else float('nan'):5.2f}        {statistics.mean(nos) if nos else float('nan'):5.2f}")
    print(f"all proposals: proxy_for_question {statistics.mean(x['proxy_for_question'] for x in rows):.0%}, "
          f"restates_known_answer {statistics.mean(x['restates_known_answer'] for x in rows):.0%}; "
          f"saw the source paper before code: {sum(x['saw_source'] for x in rows)}/{len(rows)}")
    nc = [x["n_clusters"] for x in {x["task"]: x for x in rows}.values()]
    print(f"distinct directions per task: median {statistics.median(nc)}, mean {statistics.mean(nc):.1f} "
          f"(out of ~{statistics.mean(x['n_props'] for x in rows):.1f} proposals)")
    headroom(rows, "all proposals")
    headroom([x for x in rows if not x["saw_source"]], "independent ideation only (did not see source paper before code)")
    disc = defaultdict(list)
    for x in rows:
        disc[x["alignment"]].append(x["regime"] == "discovery")
    print("share reaching 'discovery' by alignment:",
          {k: f"{statistics.mean(v):.0%} (n={len(v)})" for k, v in sorted(disc.items())})
    if len(files) > 1:
        S2, r2 = load(require(files[1], "second judge scores"), inputs)
        al = {(x["task"], x["model"]): x["alignment"] for x in rows}
        pairs = [(al[(x["task"], x["model"])], x["alignment"]) for x in r2 if (x["task"], x["model"]) in al]
        print(f"second judge {S2['judge']} (spend ${S2['spend']['cost']:.2f}, {len(r2)} proposals): "
              f"proxy_for_question {statistics.mean(x['proxy_for_question'] for x in r2):.0%}; agreement on "
              f"alignment: exact {statistics.mean(p == q for p, q in pairs):.0%}, "
              f"within 1 point {statistics.mean(abs(p - q) <= 1 for p, q in pairs):.0%}")


if __name__ == "__main__":
    main()

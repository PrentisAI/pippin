"""Headline metrics of the milestone-auditor evaluation (Table 1 "Milestone" column, Figure 3b, appendix "Evaluation Details").

Offline and free: reads only the judge output of auditor.score (and, for the timing line, the
audit summaries next to it when present).

Usage (from experiments/):
  python -m auditor.summarize                                     # the released paper run
  python -m auditor.summarize runs/auditor/test_sonnet/scores.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from common.paths import add_data_args, require

# Claude Sonnet 5.5 via OpenRouter, USD per million tokens
PRICE = {"input_tokens": 2.0, "cache_creation_input_tokens": 2.5, "cache_read_input_tokens": 0.10, "output_tokens": 10.0}


def summarize(S: dict, run_dir: Path | None = None) -> None:
    R = [r for r in S["results"] if "error" not in r]
    print(f"packages scored: {len(R)} (errors: {len(S['results']) - len(R)}); "
          f"agent models: {len({r['item'].split('__')[0] for r in R})}")
    det, tot = Counter(), Counter()
    for r in R:
        found = {x["issue_id"] for x in (r.get("stage_a") or {}).get("issues", []) if x.get("detected")}
        for i in r["issues"]:
            for key in ("all", f"sev:{i['severity']}", f"vis:{i['visible']}", f"src:{i['source']}",
                        f"model:{r['item'].split('__')[0]}"):
                tot[key] += 1
                det[key] += i["issue_id"] in found
    for k in sorted(tot, key=lambda k: (k != "all", k)):
        print(f"   {k:32s} {det[k]}/{tot[k]} ({det[k] / tot[k]:.0%})")
    cats, n_find = Counter(), 0
    for r in R:
        n_find += len(r["findings"])
        cats["matched_label"] += len({x["finding_id"] for x in (r.get("stage_a") or {}).get("findings", [])
                                      if x.get("issue_ids")})
        for x in (r.get("stage_b") or {}).get("findings", []):
            cats[x.get("verdict", "?")] += 1
    real = cats["matched_label"] + cats["valid_substantive"] + cats["valid_minor"]
    print(f"findings: {n_find}; categories: {dict(cats)}")
    print(f"   valid (matched label or judged real): {real}/{n_find} ({real / max(n_find, 1):.0%}); "
          f"incorrect: {cats['incorrect']} ({cats['incorrect'] / max(n_find, 1):.0%}); "
          f"packaging artifacts: {cats['packaging_artifact']}")
    verdicts = Counter((r["verdict_label"], r["overall_verdict"]) for r in R)
    print("overall verdict by label:", dict(verdicts))
    problem = sum(v for (lab, _), v in verdicts.items() if lab == "confirmed_problem")
    print(f"   fail on confirmed problems: {verdicts[('confirmed_problem', 'fail')]}/{problem}; "
          f"fail otherwise: {sum(v for (lab, o), v in verdicts.items() if lab != 'confirmed_problem' and o == 'fail')}"
          f"/{len(R) - problem}; pass: {sum(v for (_, o), v in verdicts.items() if o == 'pass')}")
    cost = sum(sum((r.get("usage") or {}).get(k, 0) * p / 1e6 for k, p in PRICE.items()) for r in R)
    turns = [((r.get("result") or {}).get("num_turns") or 0) for r in R]
    print(f"auditor cost (est.): ${cost:.2f} (${cost / max(len(R), 1):.2f} per package); "
          f"mean turns {statistics.mean(turns):.1f}, median {statistics.median(turns):.0f}; "
          f"judge cost ${S['judge_usage']['cost']:.2f}")
    if run_dir is not None:
        walls = [json.loads(f.read_text())["wall_s"] for f in run_dir.glob("*/audit_summary.json")]
        if walls:
            print(f"wall time per package: median {statistics.median(walls):.1f} s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_data_args(ap)
    ap.add_argument("scores", nargs="?", type=Path, default=None,
                    help="scores.json (default: <data-dir>/auditor/runs/test_sonnet/scores.json)")
    a = ap.parse_args()
    path = require(a.scores or a.data_dir / "auditor" / "runs" / "test_sonnet" / "scores.json", "scores")
    summarize(json.loads(path.read_text()), path.parent)


if __name__ == "__main__":
    main()

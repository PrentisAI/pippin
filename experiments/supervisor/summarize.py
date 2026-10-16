"""Headline metrics of the live-auditor evaluation (Table 1 "Live" column, Figure 3, Appendix C).

Offline and free: reads only the judge output of supervisor.score.

Usage (from experiments/):
  python -m supervisor.summarize                                  # the released paper run
  python -m supervisor.summarize runs/supervisor/test_sonnet_pi/scores.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from common.paths import add_data_args, require

# Claude Sonnet 5.5 via OpenRouter, USD per million tokens (for the cost estimate of the auditor)
PRICE = {"input_tokens": 2.0, "cache_creation_input_tokens": 2.5, "cache_read_input_tokens": 0.10, "output_tokens": 10.0}


def summarize(scores: dict, chunk: int = 5) -> None:
    R = [r for r in scores["results"] if "error" not in r]
    det, n_iss, first_chance, before_end = 0, 0, 0, 0
    sev_tot, sev_det, adv_cat = Counter(), Counter(), Counter()
    by_model = defaultdict(lambda: [0, 0])
    leads, clean = [], []
    cost = 0.0
    for r in R:
        cost += sum((r.get("usage") or {}).get(k, 0) * p / 1e6 for k, p in PRICE.items())
        advice = {v["advice_id"]: v for v in r["advice"]}
        sa = r.get("stage_a") or {}
        adv_cat["matched_label"] += len({x["advice_id"] for x in sa.get("advice", []) if x.get("issue_ids")})
        for x in (r.get("stage_b") or {}).get("advice", []):
            adv_cat[x.get("verdict", "unknown")] += 1
        if r["verdict_label"] != "confirmed_problem":
            clean.append(r)
            continue
        model = r["item"].split("/")[0]
        for i in r["issues"]:
            n_iss += 1
            sev_tot[i["severity"]] += 1
            by_model[model][1] += 1
            hit = next((x for x in sa.get("issues", []) if x.get("issue_id") == i["issue_id"]), None)
            if not hit or not hit.get("detected") or not hit.get("advice_ids"):
                continue
            det += 1
            sev_det[i["severity"]] += 1
            by_model[model][0] += 1
            step = min(advice[k]["seen_through_step"] for k in hit["advice_ids"] if k in advice)
            if isinstance(i.get("first_step"), int):
                leads.append(step - i["first_step"])
                if step - i["first_step"] < chunk:
                    first_chance += 1
            if step < r["n_steps"] - chunk:
                before_end += 1

    n_adv = sum(adv_cat.values())
    print(f"trajectories scored: {len(R)} ({len(R) - len(clean)} problem, {len(clean)} clean); "
          f"agent models: {len({r['item'].split('/')[0] for r in R})}; tasks: {len({r['item'].split('/')[1] for r in R})}; "
          f"steps: {sum(r['n_steps'] for r in R)}")
    print(f"labelled issues: {n_iss}; detected: {det} ({det / max(n_iss, 1):.0%})")
    for s in ("invalidates_conclusion", "weakens", "minor"):
        if sev_tot[s]:
            print(f"   {s:24s} {sev_det[s]}/{sev_tot[s]} ({sev_det[s] / sev_tot[s]:.0%})")
    print("   by model:", {m: f"{d}/{t}" for m, (d, t) in sorted(by_model.items())})
    if leads:
        print(f"   anticipatory (advice before the issue appeared): {sum(1 for x in leads if x < 0)}; "
              f"after it appeared: {sum(1 for x in leads if x >= 0)}")
        print(f"detection delay vs first visible step: median {statistics.median(leads):+.0f} steps; "
              f"caught at first push after the issue appeared: {first_chance}/{det}; "
              f"caught before the run ended: {before_end}/{det}")
    print(f"advice messages: {n_adv}; categories: {dict(adv_cat)}")
    valid = adv_cat["matched_label"] + adv_cat["valid_substantive"] + adv_cat["valid_minor"]
    print(f"   valid (matched label or judged real): {valid}/{n_adv} ({valid / max(n_adv, 1):.0%}); "
          f"incorrect: {adv_cat['incorrect']} ({adv_cat['incorrect'] / max(n_adv, 1):.0%})")
    if clean:
        steps = sum(r["n_steps"] for r in clean)
        adv = sum(r["n_advice"] for r in clean)
        cats = Counter(x.get("verdict") for r in clean for x in (r.get("stage_b") or {}).get("advice", []))
        print(f"clean-control advice categories: {dict(cats)}")
        print(f"clean controls: {adv} advice over {steps} steps ({100 * adv / steps:.1f} per 100 steps); "
              f"judged substantive {cats['valid_substantive']}, incorrect {cats['incorrect']}")
    print(f"live-auditor cost (est.): ${cost:.2f} (${cost / max(len(R), 1):.2f} per trajectory); "
          f"judge cost: ${scores['judge_usage'].get('cost', 0):.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_data_args(ap)
    ap.add_argument("scores", nargs="?", type=Path, default=None,
                    help="scores.json (default: <data-dir>/supervisor/runs/test_sonnet_pi/scores.json)")
    ap.add_argument("--chunk", type=int, default=5, help="steps per push used in the replay")
    a = ap.parse_args()
    path = require(a.scores or a.data_dir / "supervisor" / "runs" / "test_sonnet_pi" / "scores.json", "scores")
    summarize(json.loads(path.read_text()), a.chunk)


if __name__ == "__main__":
    main()

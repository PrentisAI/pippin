"""Aggregate the blind judgements of ideation.judge (meeting target).

Per condition: mean normalised rank (0 = best), first places, too_broad rate; pairwise wins with a
two-sided sign test; the oracle "best single model per task"; agreement between two judges (Pearson
correlation of per-task mean ranks over shared conditions).  Offline and free.

Usage (from experiments/):
  python -m ideation.analyze_judges                         # the two released judge files, all 21 tasks
  python -m ideation.analyze_judges --exclude W4409170628   # the 20 tasks of the paper's meeting statistics
  python -m ideation.analyze_judges runs/ideation/meeting_test/_judge_*.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

from common.paths import add_data_args, require

CONDS = ["single:sonnet", "single:gpt", "single:gemini", "bestof3:sonnet", "bestof3:gpt", "hetero_pick", "heterogeneous"]
SINGLE = ("single:sonnet", "single:gpt", "single:gemini")


def sign_test(w: int, losses: int) -> float:
    n = w + losses
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(w, losses) + 1)) / 2 ** n)


def per_task_scores(files: list[Path], exclude: set[str]):
    """task -> condition -> normalised ranks (one per judge x order); too_broad flags; the same per judge."""
    rank = defaultdict(lambda: defaultdict(list))
    broad = defaultdict(lambda: defaultdict(list))
    by_judge = defaultdict(lambda: defaultdict(dict))
    for f in files:
        d = json.loads(Path(f).read_text())
        for r in d["results"]:
            if r["task_id"] in exclude:
                continue
            for o in r.get("orders", []):
                j = o["judgement"]
                if not isinstance(j, dict) or "rank" not in j:
                    continue
                order = [x for x in j["rank"] if x in o["labels"]]
                n = len(o["labels"])
                if len(order) != n:
                    continue
                flags = {i["label"]: (i.get("flags") or {}).get("too_broad") for i in j.get("ideas", [])}
                for pos, lab in enumerate(order):
                    for c in o["labels"][lab]:
                        rank[r["task_id"]][c].append(pos / (n - 1))
                        by_judge[d["judge"]][r["task_id"]].setdefault(c, []).append(pos / (n - 1))
                        if flags.get(lab) is not None:
                            broad[r["task_id"]][c].append(bool(flags[lab]))
    return rank, broad, by_judge


def analyze(files: list[Path], exclude: set[str]) -> dict:
    rank, broad, by_judge = per_task_scores(files, exclude)
    tasks = sorted(rank)
    mean = {t: {c: st.mean(v) for c, v in rank[t].items()} for t in tasks}
    out = {"judges": list(by_judge), "n_tasks": len(tasks), "conditions": {}}
    for c in CONDS:
        vals = [mean[t][c] for t in tasks if c in mean[t]]
        if not vals:
            continue
        best = sum(1 for t in tasks if c in mean[t] and mean[t][c] == min(mean[t].values()))
        b = [x for t in tasks for x in broad[t].get(c, [])]
        out["conditions"][c] = {"mean_norm_rank": round(st.mean(vals), 3), "best_on_tasks": best,
                                "too_broad_rate": round(sum(b) / len(b), 2) if b else None}
    pairs = {}
    for a, b in itertools.combinations([c for c in CONDS if c in out["conditions"]], 2):
        w = sum(1 for t in tasks if a in mean[t] and b in mean[t] and mean[t][a] < mean[t][b])
        lo = sum(1 for t in tasks if a in mean[t] and b in mean[t] and mean[t][a] > mean[t][b])
        pairs[f"{a} vs {b}"] = {"wins": w, "losses": lo, "ties": len(tasks) - w - lo, "p_sign": round(sign_test(w, lo), 3)}
    out["pairwise"] = pairs
    orc = [min(mean[t][c] for c in SINGLE if c in mean[t]) for t in tasks]
    out["oracle_best_single_model_mean_norm_rank"] = round(st.mean(orc), 3)
    out["best_single_model_by_task"] = dict(Counter(min(SINGLE, key=lambda c: mean[t].get(c, 9)) for t in tasks))
    js = list(by_judge)
    if len(js) == 2:
        xs, ys = [], []
        for t in tasks:
            for c in CONDS:
                if c in by_judge[js[0]].get(t, {}) and c in by_judge[js[1]].get(t, {}):
                    xs.append(st.mean(by_judge[js[0]][t][c]))
                    ys.append(st.mean(by_judge[js[1]][t][c]))
        mx, my = st.mean(xs), st.mean(ys)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        out["judge_rank_correlation"] = round(
            cov / math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)), 3)
        agree = 0
        for t in tasks:
            b0 = min(by_judge[js[0]][t], key=lambda c: st.mean(by_judge[js[0]][t][c]))
            b1 = min(by_judge[js[1]][t], key=lambda c: st.mean(by_judge[js[1]][t][c]))
            agree += st.mean(by_judge[js[0]][t][b0]) == st.mean(by_judge[js[0]][t][b1]) or b0 == b1
        out["judges_agree_on_best"] = f"{agree}/{len(tasks)}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_data_args(ap)
    ap.add_argument("files", nargs="*", type=Path,
                    help="judge files (default: <data-dir>/ideation/meeting_test/_judge_{grok,qwen}.json)")
    ap.add_argument("--exclude", nargs="*", default=[], help="task IDs to leave out")
    ap.add_argument("--json", type=Path, default=None, help="also write the result here")
    a = ap.parse_args()
    files = a.files or [require(a.data_dir / "ideation" / "meeting_test" / f"_judge_{j}.json", "judge file")
                        for j in ("grok", "qwen")]
    out = analyze(files, set(a.exclude))
    print(f"judges: {out['judges']}; tasks: {out['n_tasks']}")
    print(f"{'condition':16s} mean norm. rank  first on  too_broad")
    for c, v in sorted(out["conditions"].items(), key=lambda kv: kv[1]["mean_norm_rank"]):
        print(f"{c:16s} {v['mean_norm_rank']:15.3f}  {v['best_on_tasks']:8d}  {v['too_broad_rate']}")
    print("pairwise vs hetero_pick (wins/losses/ties, sign test):")
    for k, v in out["pairwise"].items():
        if "hetero_pick" in k:
            print(f"   {k:34s} {v['wins']}/{v['losses']}/{v['ties']}  p={v['p_sign']}")
    print(f"oracle best single model per task: {out['oracle_best_single_model_mean_norm_rank']:.3f} "
          f"{out['best_single_model_by_task']}")
    if "judge_rank_correlation" in out:
        print(f"judge rank correlation: {out['judge_rank_correlation']:.3f}; "
              f"judges agree on the best proposal: {out['judges_agree_on_best']}")
    if a.json:
        a.json.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

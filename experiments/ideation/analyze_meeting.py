"""Judge-free analysis of the multi-model ideation run (ideation.run_meeting, pool + heterogeneous).

Reports the numbers of the paper's "Collective intelligence in ideation" paragraph:
  * diversity: in how many tasks the chair itself described the three candidates as essentially the
    same study (hand annotation of the chair's selection rationale, chair_same_study.json; a keyword
    heuristic is printed next to it), plus a TF-IDF similarity of the candidates;
  * meeting dynamics: ACCEPT / REBUT / DEFER shares of the presenters' answers, remark tags
    (share of EVIDENCE), merges of several seats' directions;
  * cost per task of the meeting and of independent selection;
and, beyond the paper, whose idea each selection picked and how much of the other ideas the final
proposal absorbed.  Offline and free.

The paper's statistics cover 20 tasks: W4409170628 (the last meeting to finish; the chair's notes for
one agenda item failed to parse) is not included.  Pass --exclude "" to analyse all 21 released tasks.

Usage (from experiments/):
  python -m ideation.analyze_meeting                       # released run, the paper's 20 tasks
  python -m ideation.analyze_meeting runs/ideation/meeting_test --annotations none
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

from common.paths import add_data_args, require

STOP = set("the a an of and or to in for on with by is are be as that this it its from at we our will can not if than "
           "then which their these those into using use under over between each per vs".split())
SHORT = {"anthropic": "Sonnet", "openai": "GPT", "google": "Gemini"}
SELECTIONS = {"bestof3:sonnet": "bestof3-sonnet", "bestof3:gpt": "bestof3-gpt", "hetero_pick": "hetero_pick"}
PAPER_EXCLUDE = ["W4409170628"]
TAGS = ("QUESTION", "CONCERN", "SUGGESTION", "EVIDENCE", "BUILD-ON", "AGREE")


def toks(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9\-]+", s.lower()) if w not in STOP and len(w) > 2]


class Tfidf:
    def __init__(self, docs: list[str]) -> None:
        self.n = len(docs)
        self.df = Counter(w for d in docs for w in set(toks(d)))

    def vec(self, d: str) -> dict:
        c = Counter(toks(d))
        return {w: (1 + math.log(k)) * math.log((self.n + 1) / (self.df[w] + 1) + 1) for w, k in c.items()}

    @staticmethod
    def cos(a: dict, b: dict) -> float:
        num = sum(v * b.get(w, 0) for w, v in a.items())
        den = math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))
        return num / den if den else 0.0


def idea_text(i: dict) -> str:
    return " ".join(str(i.get(k, "")) for k in ("title", "problem", "hypothesis", "method", "falsification_check"))


def model(seat_ids: dict, sid: str) -> str:
    return SHORT[seat_ids[sid].split("/")[0]]


def chosen_ids(decision: dict) -> list[str]:
    return list(dict.fromkeys(re.findall(r"p\d{2}", decision["chosen"])))


def says_same_study(rationale: str) -> bool:
    """Keyword heuristic: the rationale opens with 'All three ...' and calls the ideas the same or convergent."""
    first = re.split(r"(?<=[.!?])\s", rationale.strip(), maxsplit=1)[0]
    return first.startswith("All three") and bool(re.search(r"\bsame\b|\bconverg", first))


def analyze(d: Path, exclude: set[str], annotations: dict | None) -> dict:
    load = lambda t, c: json.loads((d / f"{t}__{c}.json").read_text())  # noqa: E731
    pool_tasks = sorted({f.name.split("__")[0] for f in d.glob("*__hetero_pick.json")} - exclude)
    meet_tasks = sorted({f.name.split("__")[0] for f in d.glob("*__heterogeneous.json")} - exclude)
    out = {"n_pool_tasks": len(pool_tasks), "n_meeting_tasks": len(meet_tasks), "excluded": sorted(exclude)}

    # 1. diversity: the chair's own words, and TF-IDF similarity of the three candidates
    same = {}
    for cond, stem in SELECTIONS.items():
        rats = {t: load(t, stem)["selection"]["rationale"] for t in pool_tasks}
        row = {"heuristic": sum(says_same_study(r) for r in rats.values())}
        if annotations is not None:
            row["annotated"] = sum(bool(annotations[t][cond]) for t in pool_tasks)
        same[cond] = row
    out["chair_says_same_study"] = same
    div = defaultdict(list)
    for t in pool_tasks:
        groups = {c: [x["content"] for x in load(t, c)["transcript"]] for c in SELECTIONS.values()}
        tf = Tfidf([x for g in groups.values() for x in g])
        for c, g in groups.items():
            vs = [tf.vec(x) for x in g]
            div[c].append(st.mean(tf.cos(a, b) for a, b in itertools.combinations(vs, 2)))
    out["candidate_similarity"] = {c: round(st.mean(v), 3) for c, v in div.items()}

    # 2. whose idea the selections and the meeting chose
    out["hetero_pick_chosen_model"] = dict(Counter(model(load(t, "hetero_pick")["seat_ids"],
                                                         load(t, "hetero_pick")["chosen_seat"]) for t in pool_tasks))
    agree = Counter()
    for t in meet_tasks:
        h, p = load(t, "heterogeneous"), load(t, "hetero_pick")
        ids = chosen_ids(h["decision"])
        mm = "merged" if len(ids) > 1 else model(h["seat_ids"], ids[0]) if ids else "?"
        agree["same" if mm == model(p["seat_ids"], p["chosen_seat"]) else "different"] += 1
    out["meeting_vs_selection_same_model"] = dict(agree)

    # 3. meeting dynamics
    tags, passes, remarks, meet_choice = Counter(), Counter(), Counter(), Counter()
    resp = defaultdict(Counter)
    merges, mcost, mtime = 0, [], []
    for t in meet_tasks:
        h = load(t, "heterogeneous")
        ids = chosen_ids(h["decision"])
        merges += len(ids) > 1
        meet_choice["merged" if len(ids) > 1 else model(h["seat_ids"], ids[0])] += 1
        mcost.append(h["calls_summary"]["total_cost"])
        mtime.append(h["wall_s"] / 60)
        for c in h["transcript"]:
            m = model(h["seat_ids"], c["seat_id"])
            if c["kind"] == "remarks":
                remarks[m] += 1
                if re.match(r"^\W*pass\W*$", c["content"].strip(), re.I) or (
                        len(c["content"]) < 40 and "pass" in c["content"].lower()):
                    passes[m] += 1
                for tg in re.findall(r"\[(" + "|".join(TAGS) + r")\]", c["content"]):
                    tags[tg] += 1
            elif c["kind"] == "response":
                for k in ("ACCEPT", "REBUT", "DEFER"):
                    resp[m][k] += len(re.findall(rf"\b{k}\b", c["content"]))
    tot = sum(resp.values(), Counter())
    n_resp, n_tags = max(1, sum(tot.values())), max(1, sum(tags.values()))
    out["meeting"] = {
        "merges": merges, "chosen_model": dict(meet_choice), "remark_tags": dict(tags),
        "evidence_share_of_tags": round(tags["EVIDENCE"] / n_tags, 3),
        "pass_rate_by_model": {m: round(passes[m] / remarks[m], 2) for m in remarks},
        "responses_by_presenter_model": {m: dict(v) for m, v in resp.items()},
        "accept_share": round(tot["ACCEPT"] / n_resp, 3), "rebut_share": round(tot["REBUT"] / n_resp, 3),
        "defer_share": round(tot["DEFER"] / n_resp, 3),
        "cost_mean": round(st.mean(mcost), 3), "minutes_mean": round(st.mean(mtime), 1)}

    # 4. absorption: similarity of the final proposal to the other ideas relative to the chosen idea
    absorb = {"meeting": [], "selection": []}
    for t in meet_tasks:
        h, p = load(t, "heterogeneous"), load(t, "hetero_pick")
        pres = {c["seat_id"]: c["content"] for c in h["transcript"] if c["kind"] == "presentation"}
        ids = chosen_ids(h["decision"])
        tf = Tfidf(list(pres.values()) + [idea_text(h["idea"])])
        fv = tf.vec(idea_text(h["idea"]))
        sims = {s: tf.cos(fv, tf.vec(x)) for s, x in pres.items()}
        if ids and ids[0] in sims:
            absorb["meeting"].append(st.mean(v for s, v in sims.items() if s != ids[0]) / sims[ids[0]])
        pdr = {c["seat_id"]: c["content"] for c in p["transcript"]}
        tf2 = Tfidf(list(pdr.values()) + [idea_text(p["idea"])])
        fv2 = tf2.vec(idea_text(p["idea"]))
        s2 = {s: tf2.cos(fv2, tf2.vec(x)) for s, x in pdr.items()}
        absorb["selection"].append(st.mean(v for s, v in s2.items() if s != p["chosen_seat"]) / s2[p["chosen_seat"]])
    out["absorption_ratio"] = {k: round(st.mean(v), 3) for k, v in absorb.items() if v}

    pc = [load(t, "hetero_pick")["calls_summary"]["total_cost"] for t in pool_tasks]
    out["pool_cost_mean"] = round(st.mean(pc), 3)
    out["total_cost"] = round(sum(pc) + sum(mcost), 2)
    return out


def report(o: dict) -> None:
    print(f"tasks: {o['n_pool_tasks']} (selection), {o['n_meeting_tasks']} (meeting); excluded: {o['excluded'] or 'none'}")
    print("chair says the three candidates are essentially the same study:")
    for cond, row in o["chair_says_same_study"].items():
        ann = f"{row['annotated']}/{o['n_pool_tasks']} (annotation)" if "annotated" in row else "no annotation"
        print(f"   {cond:15s} {ann}; keyword heuristic {row['heuristic']}/{o['n_pool_tasks']}")
    print(f"candidate TF-IDF similarity: {o['candidate_similarity']}")
    m = o["meeting"]
    print(f"presenters' answers: ACCEPT {m['accept_share']:.1%}, REBUT {m['rebut_share']:.1%}, "
          f"DEFER {m['defer_share']:.1%}  {m['responses_by_presenter_model']}")
    print(f"remark tags: {m['remark_tags']}; EVIDENCE share {m['evidence_share_of_tags']:.1%}")
    print(f"meetings that merged several seats' directions: {m['merges']}/{o['n_meeting_tasks']}; "
          f"chosen: {m['chosen_model']}")
    print(f"cost per task: meeting ${m['cost_mean']:.2f} vs independent selection (pool) ${o['pool_cost_mean']:.2f}; "
          f"total ${o['total_cost']:.2f}; meeting wall time {m['minutes_mean']} min")
    print(f"hetero_pick chose: {o['hetero_pick_chosen_model']}; meeting vs selection: {o['meeting_vs_selection_same_model']}")
    print(f"absorption ratio (other ideas / chosen idea): {o['absorption_ratio']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_data_args(ap)
    ap.add_argument("runs_dir", nargs="?", type=Path, default=None,
                    help="output directory of ideation.run_meeting (default: <data-dir>/ideation/meeting_test)")
    ap.add_argument("--exclude", nargs="*", default=PAPER_EXCLUDE,
                    help=f"task IDs to leave out (default: {PAPER_EXCLUDE}, as in the paper)")
    ap.add_argument("--annotations", default=None,
                    help="chair_same_study.json (default: <data-dir>/ideation/chair_same_study.json; 'none' to skip)")
    ap.add_argument("--json", type=Path, default=None, help="also write the result here")
    a = ap.parse_args()
    d = require(a.runs_dir or a.data_dir / "ideation" / "meeting_test", "meeting runs")
    ann = None
    if a.annotations != "none":
        f = Path(a.annotations) if a.annotations else a.data_dir / "ideation" / "chair_same_study.json"
        if f.exists():
            ann = json.loads(f.read_text())["labels"]
    out = analyze(d, {x for x in a.exclude if x}, ann)
    report(out)
    if a.json:
        a.json.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

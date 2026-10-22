"""Blind LLM judging of research proposals.

Two targets, one rubric family:

* ``meeting`` - the ideation conditions of ideation.run_meeting: all distinct proposals of a task at
  once, shuffled, scored with the agents' rubric plus a rigour score and a ``too_broad`` flag, and
  ranked best to worst.  Conditions that share a proposal (e.g. hetero_pick picks the GPT idea, which
  is also single:gpt) are shown once and the scores are mapped back to every condition that produced it.
* ``agents`` - the ideation records of the eight AutoResearchEval agents per task (built by
  ideation.build_agent_inputs), scored with the base rubric (paper, appendix "Evaluation Details").

Needs OPENROUTER_API_KEY and AutoResearchEval (task conclusions are shown to the judge only).
Usage (from experiments/):
  python -m ideation.judge meeting --runs-dir runs/ideation/meeting_test --judge x-ai/grok-4.7
  python -m ideation.judge agents --judge openai/gpt-5.6-sol
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path

from common.dataset import AutoResearchEval
from common.openrouter import chat_json, client, new_spend
from common.paths import RUNS_DIR, add_data_args, are_root, require

# -- prompts ------------------------------------------------------------------

AGENT_PROMPT = """You are an expert scientific reviewer assessing the IDEATION of several independent research agents
that were each given the same open research problem. Each agent proposed a research question, hypothesis and method.
You also see the conclusion of the published paper the problem was built from; the agents were NOT given it.

## Problem given to the agents
Premise: {premise}

Tension (the open gap): {tension}

## Reference (published paper; for your judgment only)
Contribution type: {move}
Paper's conclusion: {gt}

## Agent proposals
{ideas}

For EACH proposal, score:
- "alignment" 0-3: 0 unrelated to the paper's contribution; 1 adjacent direction; 2 same core question but a different
  approach or answer; 3 anticipates the paper's core finding.
- "falsifiable" 1-5: concrete conditions that would refute it.
- "specific" 1-5: measurable observables, quantities, parameters.
- "feasible" 1-5: realistically doable by a single agent with code and public resources in a few hours.
- "important" 1-5: answering it would genuinely address the stated tension.
- flags (booleans): "restates_known_answer" (asserts the field's/paper's answer as the hypothesis rather than proposing
  a test of an open question), "proxy_for_question" (tests an easier proxy, e.g. a toy simulation, instead of the
  stated question), "unfalsifiable".
Then group the proposals into clusters that pursue essentially the same research direction, and name the single best proposal.

Return JSON only:
{{"ideas": [{{"label": "A", "alignment": 0, "falsifiable": 1, "specific": 1, "feasible": 1, "important": 1,
  "flags": {{"restates_known_answer": false, "proxy_for_question": false, "unfalsifiable": false}}, "why": "<one sentence>"}}],
 "clusters": [["A", "C"], ["B"]], "best": "A", "best_why": "<one sentence>"}}
"""

# meeting proposals: the same rubric plus a methodological-rigour score, a scope flag and a full ranking
MEETING_PROMPT = AGENT_PROMPT.replace(
    '- "important" 1-5: answering it would genuinely address the stated tension.\n',
    '- "important" 1-5: answering it would genuinely address the stated tension.\n'
    '- "rigorous" 1-5: the method anticipates how it could mislead (confounds, circularity, leakage, missing\n'
    '  controls or baselines, identifiability) and guards against it.\n').replace(
    '"important": 1,\n', '"important": 1, "rigorous": 1,\n').replace(
    'Then group the proposals into clusters that pursue essentially the same research direction, and name the single best proposal.',
    'Then group the proposals into clusters that pursue essentially the same research direction, name the single best\n'
    'proposal, and rank ALL proposals from best to worst as research directions for this problem (judge substance, not\n'
    'length or style).').replace(
    '"best": "A", "best_why": "<one sentence>"}}', '"best": "A", "best_why": "<one sentence>", "rank": ["A", "B"]}}').replace(
    '"unfalsifiable".\n', '"unfalsifiable", "too_broad" (bundles several sub-studies, tiers or stages that one agent\n'
    '  could not finish in a few hours).\n').replace(
    '"unfalsifiable": false}}', '"unfalsifiable": false, "too_broad": false}}')
assert all(s in MEETING_PROMPT for s in ('"rigorous" 1-5', '"rank": ["A", "B"]', '"too_broad": false', '"too_broad" (bundles'))

# proposal fields shown to the judge (meeting conditions)
FIELDS = ("problem", "hypothesis", "method", "alternatives_considered", "falsification_check")


# -- meeting conditions -------------------------------------------------------

def load_meeting_runs(runs_dir: Path) -> dict:
    by_task = defaultdict(dict)
    for f in sorted(runs_dir.glob("*.json")):
        if f.name.startswith("_") or f.name.endswith("__pool.json"):
            continue
        r = json.loads(f.read_text())
        if "error" not in r and r.get("idea"):
            by_task[r["task_id"]][r["condition"]] = r
    return by_task


def meeting_proposals(task_id: str, runs: dict, seed: str = "") -> tuple[dict, dict, str]:
    """Distinct proposals, shuffled; returns label -> key, key -> conditions, and the prompt block."""
    key_of = {c: hashlib.md5(json.dumps(r["idea"], sort_keys=True).encode()).hexdigest() for c, r in runs.items()}
    conds_of = defaultdict(list)
    for c, k in key_of.items():
        conds_of[k].append(c)
    keys = sorted(conds_of)
    random.Random(int(hashlib.md5((task_id + seed).encode()).hexdigest(), 16)).shuffle(keys)
    label_key = {chr(ord("A") + i): k for i, k in enumerate(keys)}
    text = []
    for lab, k in label_key.items():
        idea = runs[conds_of[k][0]]["idea"]
        text.append(f"### Proposal {lab}\n" + "\n".join(f"- {f}: {idea.get(f, '')}" for f in FIELDS if idea.get(f)))
    return label_key, dict(conds_of), "\n\n".join(text)


async def judge_meeting(a: argparse.Namespace, are: AutoResearchEval) -> None:
    by_task = load_meeting_runs(a.runs_dir)
    spend = new_spend()
    sem = asyncio.Semaphore(a.concurrency)
    async with client() as http:
        async def one(tid, runs):
            async with sem:
                if spend["cost"] > a.budget:
                    return {"task_id": tid, "error": "budget exceeded"}
                t = are.task(tid)
                out = []
                for k in range(a.orders):
                    label_key, conds_of, ideas = meeting_proposals(tid, runs, f"{a.judge}#{k}")
                    res = await chat_json(http, a.judge, MEETING_PROMPT.format(
                        premise=t["premise"], tension=t["tension"], move=t["novelty_move"], gt=t["conclusion_gt"],
                        ideas=ideas), spend)
                    out.append({"labels": {lab: conds_of[key] for lab, key in label_key.items()}, "judgement": res})
                print(f"{tid}: {len(label_key)} distinct proposals from {len(runs)} conditions, "
                      f"spend ${spend['cost']:.2f}", flush=True)
                return {"task_id": tid, "orders": out}
        results = await asyncio.gather(*(one(tid, runs) for tid, runs in sorted(by_task.items())))
    out = a.out or a.runs_dir / f"_judge_{a.judge.split('/')[-1]}.json"
    out.write_text(json.dumps({"judge": a.judge, "spend": spend, "results": results}, ensure_ascii=False, indent=1))
    print(f"done: {len(results)} tasks, spend ${spend['cost']:.2f}; wrote {out}")


# -- agents' own ideation records ----------------------------------------------

def anonymize(task: dict) -> tuple[dict, str]:
    models = sorted(m for m, v in task["runs"].items() if v)
    rnd = random.Random(int(hashlib.md5(task["task_id"].encode()).hexdigest(), 16))
    rnd.shuffle(models)
    mapping = {chr(ord("A") + i): m for i, m in enumerate(models)}
    blocks = []
    for lab, m in mapping.items():
        idea = task["runs"][m]["idea"]
        blocks.append(f"### Proposal {lab}\n" + "\n".join(f"- {k}: {v}" for k, v in idea.items() if v))
    return mapping, "\n\n".join(blocks)


async def judge_agents(a: argparse.Namespace) -> None:
    inputs = require(a.inputs or a.data_dir / "ideation" / "agent_ideas" / "ideation_inputs.json", "ideation inputs")
    tasks = sorted(json.loads(inputs.read_text()), key=lambda t: t["task_id"])
    if a.tasks:
        tasks = tasks[: a.tasks]
    spend = new_spend()
    sem = asyncio.Semaphore(a.concurrency)
    async with client() as http:
        async def one(t):
            async with sem:
                if spend["cost"] > a.budget:
                    return {"task_id": t["task_id"], "error": "budget exceeded, skipped"}
                mapping, ideas = anonymize(t)
                res = await chat_json(http, a.judge, AGENT_PROMPT.format(
                    premise=t["premise"], tension=t["tension"], move=t["novelty_move"],
                    gt=t["conclusion_gt"], ideas=ideas), spend)
                print(f"{t['task_id']}: {len(mapping)} proposals, spend ${spend['cost']:.2f}", flush=True)
                return {"task_id": t["task_id"], "mapping": mapping, "judgement": res}
        results = await asyncio.gather(*(one(t) for t in tasks))
    out = a.out or RUNS_DIR / "ideation" / "agent_ideas" / f"ideation_scores_{a.judge.split('/')[-1]}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"judge": a.judge, "spend": spend, "results": results}, ensure_ascii=False, indent=1))
    print(f"done: {len(results)} tasks, spend ${spend['cost']:.2f}; wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", choices=["meeting", "agents"])
    add_data_args(ap, are=True)
    ap.add_argument("--judge", required=True, help="judge model (OpenRouter ID)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--budget", type=float, default=10.0, help="stop launching calls past this spend ($)")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--runs-dir", type=Path, default=None, help="meeting: output directory of ideation.run_meeting")
    ap.add_argument("--orders", type=int, default=2, help="meeting: presentation orders per task (position bias)")
    ap.add_argument("--inputs", type=Path, default=None,
                    help="agents: default <data-dir>/ideation/agent_ideas/ideation_inputs.json")
    ap.add_argument("--tasks", type=int, default=0, help="agents: score only the first N tasks (0 = all)")
    a = ap.parse_args()
    if a.target == "meeting":
        a.runs_dir = require(a.runs_dir or RUNS_DIR / "ideation" / "meeting_test", "meeting runs")
        asyncio.run(judge_meeting(a, AutoResearchEval(are_root(a))))
    else:
        asyncio.run(judge_agents(a))


if __name__ == "__main__":
    main()

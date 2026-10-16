"""Multi-model ideation on AutoResearchEval open-ended tasks: independent selection vs. a group meeting.

Seats are one strong model per family (MODELS); the chair comes from a family that holds no seat.
Two kinds of run per task (``--conditions``):

  pool          run once per task; yields six conditions that share the same independently prepared ideas
                (Sonnet x3, GPT x3, Gemini x1):
                  single:<m>       the first idea of model m, written up as a proposal
                  bestof3:<m>      three ideas of model m (sonnet, gpt); the chair picks one
                  hetero_pick      one idea per model; the chair picks one, no discussion
  heterogeneous a group meeting of one seat of each model (aurora.agents.roundtable.Roundtable)

Final proposals share one schema with word limits.  Web search is off (with it, the seats find the
source paper and the meeting replays its idea).  Each run writes <task>__<condition>.json (record),
.md (readable transcript) and .calls.jsonl.gz (every model call).

Needs OPENROUTER_API_KEY and AutoResearchEval.  Usage (from experiments/):
  python -m ideation.run_meeting --split test --out-dir runs/ideation/meeting_test
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import random
import re
import time
from collections import defaultdict
from pathlib import Path

from common.dataset import AutoResearchEval
from common.openrouter import BASE_URL, api_key, client, key_usage
from common.paths import add_data_args, are_root

from aurora.agents.base import AgentContext
from aurora.agents.roundtable import Roundtable
from aurora.agents.roundtable.chair import RoundtableChair
from aurora.agents.roundtable.seat import RoundtableSeat
from aurora.config import AuroraConfig
from aurora.llm.base import UsageTracker
from aurora.llm.openai_provider import OpenAIProvider
from aurora.llm.router import LLMRouter

MODELS = {"sonnet": "anthropic/claude-sonnet-5.5", "gpt": "openai/gpt-5.6-sol", "gemini": "google/gemini-3.1-pro-preview"}
CHAIR = "moonshotai/kimi-k3"
POOL_CONDITIONS = ["single:sonnet", "single:gpt", "single:gemini", "bestof3:sonnet", "bestof3:gpt", "hetero_pick"]
SETTING = ("The research will be carried out by a single autonomous AI agent in a sandbox with Python, network "
           "access (pip, public datasets, literature) and one GPU, within one session of a few hours. It cannot run "
           "wet-lab experiments or obtain new proprietary data.")
# tasks used in the first (pre-group-meeting) round-table attempt; never part of the held-out test split.
# Three of them were the development set of the meeting protocol.
USED = {"W4410251380", "W4415384230", "W4394998531", "W4412611796", "W7124686564", "W7116982623", "W7119963730",
        "W4412027352", "W7125908009", "W4403382065", "W4396532040", "W4394931621", "W4407848322", "W4400415064",
        "W4394931502", "W7128902052", "W4407227665", "W4400324640", "W4409116437", "W4399544266", "W4410458079"}
DEV = ["W4410251380", "W7116982623", "W4403382065"]


def load_tasks(are: AutoResearchEval, split: str, per_domain: int) -> list[dict]:
    tasks = list(are.tasks.values())
    if split == "dev":
        return [t for t in tasks if t["task_id"] in DEV]
    by_dom = defaultdict(list)
    for t in tasks:
        if t["task_id"] not in USED:
            by_dom[t["domain"]].append(t)
    rnd = random.Random(1)
    return [t for d in sorted(by_dom) for t in rnd.sample(sorted(by_dom[d], key=lambda t: t["task_id"]), per_domain)]


def context_for(task: dict) -> tuple[AgentContext, str]:
    topic = (f"Background (established consensus): {task['premise']}\n\n"
             f"The open question (tension): {task['tension']}\n\n"
             f"Decide what research to do on this open question. {SETTING}")
    return AgentContext(campaign_id=task["task_id"], current_goal="Propose research on the open question below."), topic


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower())


def leak_check(task: dict, contribs: list[dict]) -> dict:
    """Did any seat mention the source paper (DOI or title)?"""
    sp = task["source_paper"]
    doi, tkey = (sp.get("doi") or "").lower(), norm(sp.get("title"))[:45]
    hits = []
    for c in contribs:
        blob = (c["content"] + " " + " ".join(c.get("citations", []))).lower()
        if (doi and doi in blob) or (tkey and tkey in norm(blob)):
            hits.append(f"{c['seat_id']}:{c.get('kind')}")
    return {"source_seen": bool(hits), "where": hits}


def make(seats: list[str], web: bool, calls: list) -> tuple[AuroraConfig, LLMRouter, UsageTracker]:
    """Config, router and usage tracker for one run; every chat-completion call is appended to *calls*."""
    cfg = AuroraConfig.model_validate({
        "objective": "ideation", "campaign_id": "meeting_eval",
        "models": {"ideation_seats": seats, "ideation_chair": CHAIR},
        "ideation": {"mode": "roundtable", "rounds_per_item": 2, "web_search": web}})
    tracker = UsageTracker()
    provider = OpenAIProvider(api_key=api_key(), base_url=BASE_URL, usage_tracker=tracker)
    provider.call_hook = calls.append
    return cfg, LLMRouter(cfg, providers={"openrouter": provider}, usage_tracker=tracker), tracker


async def run_meeting(task: dict, web: bool, calls: list) -> dict:
    """A group meeting of one seat per model; seat IDs are permuted per task."""
    ctx, topic = context_for(task)
    seats = list(MODELS.values())
    random.Random(task["task_id"]).shuffle(seats)
    cfg, router, tracker = make(seats, web, calls)
    t0 = time.time()
    rt = Roundtable(cfg, router)
    prop = await rt.deliberate(ctx, topic)
    contribs = [c.model_dump(mode="json") for c in rt.contributions]
    idea = {"title": prop.title, "problem": (prop.research_questions or [""])[0], "hypothesis": prop.hypothesis,
            "method": (prop.proposed_methods or [""])[0], "alternatives_considered": prop.alternatives_considered,
            "falsification_check": prop.falsification_check, "expected_outcomes": prop.expected_outcomes,
            "unresolved_disagreements": prop.unresolved_disagreements}
    return {"task_id": task["task_id"], "domain": task["domain"], "condition": "heterogeneous", "seats": seats,
            "web_search": web, "seat_ids": {f"p{i + 1:02d}": s for i, s in enumerate(seats)}, "chair": CHAIR,
            "wall_s": round(time.time() - t0), "idea": idea, "transcript": contribs,
            "usage": tracker.summary(), "leak": leak_check(task, contribs),
            "notes": prop.meeting_notes, "decision": prop.decision}


async def run_pool(task: dict, web: bool, calls: list) -> list[dict]:
    """Independent ideas (Sonnet x3, GPT x3, Gemini), three chair selections, and the write-ups they need."""
    ctx, topic = context_for(task)
    seats_m = [MODELS["sonnet"]] * 3 + [MODELS["gpt"]] * 3 + [MODELS["gemini"]]
    _, router, _ = make(seats_m, web, calls)
    t0 = time.time()
    seats = [RoundtableSeat(router, i, web_search=web) for i in range(len(seats_m))]
    drafts = await asyncio.gather(*(s.prepare(ctx, topic) for s in seats))
    chair = RoundtableChair(router)
    briefing = seats[0].briefing(ctx, topic)
    rnd = random.Random(task["task_id"])

    async def pick(idx: list[int]) -> tuple[int, dict]:
        order = idx[:]
        rnd.shuffle(order)
        labels = {chr(ord("A") + k): i for k, i in enumerate(order)}
        sel = await chair.select(briefing, {lab: drafts[i].content for lab, i in labels.items()})
        lab = next((c for c in re.findall(r"[A-Z]", sel.chosen) if c in labels), "A")
        return labels[lab], {"labels": {k: seats[i].agent_id for k, i in labels.items()}, **sel.model_dump()}

    (best_s, sel_s), (best_g, sel_g), (best_h, sel_h) = await asyncio.gather(
        pick([0, 1, 2]), pick([3, 4, 5]), pick([0, 3, 6]))
    need = sorted({0, 3, 6, best_s, best_g, best_h})
    written = dict(zip(need, await asyncio.gather(*(
        seats[i].write_proposal(ctx, topic, f"### {seats[i].agent_id} — presentation\n\n{drafts[i].content}", None)
        for i in need))))
    common = {"task_id": task["task_id"], "domain": task["domain"], "web_search": web, "chair": CHAIR,
              "seat_ids": {s.agent_id: m for s, m in zip(seats, seats_m)}, "pool": True,
              "wall_s": round(time.time() - t0)}

    def rec(cond, i, members, selection=None):
        contribs = [drafts[j].model_dump(mode="json") for j in members]
        return {**common, "condition": cond, "seats": [seats_m[j] for j in members], "idea": written[i].model_dump(),
                "chosen_seat": seats[i].agent_id, "transcript": contribs, "leak": leak_check(task, contribs),
                **({"selection": selection} if selection else {})}
    return [rec("single:sonnet", 0, [0]), rec("single:gpt", 3, [3]), rec("single:gemini", 6, [6]),
            rec("bestof3:sonnet", best_s, [0, 1, 2], sel_s), rec("bestof3:gpt", best_g, [3, 4, 5], sel_g),
            rec("hetero_pick", best_h, [0, 3, 6], sel_h)]


def call_summary(calls: list) -> dict:
    by = defaultdict(lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "web_calls": 0})
    for c in calls:
        d = by[c["model"]]
        d["calls"] += 1
        d["prompt_tokens"] += c["prompt_tokens"]
        d["completion_tokens"] += c["completion_tokens"]
        d["cost"] += c["cost"]
        d["web_calls"] += bool(c["extra"].get("plugins"))
    return {"by_model": dict(by), "total_cost": sum(c["cost"] for c in calls)}


def _notes_md(n: dict) -> list[str]:
    out = [f"#### Chair's notes ({n.get('item')})", "", n.get("summary", ""), ""]
    if n.get("threads"):
        out += ["| by | kind | about | gist | outcome |", "|---|---|---|---|---|"]
        out += [f"| {t.get('raised_by')} | {t.get('kind')} | {t.get('about')} | {t.get('gist', '').replace('|', '/')} | "
                f"{t.get('outcome')}: {t.get('outcome_note', '').replace('|', '/')} |" for t in n["threads"]]
        out.append("")
    return out


def render_markdown(r: dict, task: dict) -> str:
    """Human-readable record of one run, in speaking order."""
    out = [f"# {r['task_id']} — {r['condition']}", "",
           f"**Domain:** {task['domain']}  ", "**Seats:** " + ", ".join(f"{k} = {v}" for k, v in r["seat_ids"].items()) + "  ",
           f"**Chair:** {r.get('chair') or '—'}  ",
           f"**Cost:** ${r['calls_summary']['total_cost']:.3f} · **Wall time:** {r['wall_s']} s  ",
           f"**Source paper seen:** {r['leak']['source_seen']} {r['leak']['where'] or ''}", "",
           "## Topic", "", f"**Background:** {task['premise']}", "", f"**Open question:** {task['tension']}", ""]
    label = {"presentation": "Presentation", "remarks": "Remarks on {item}", "response": "Response (round {k})",
             "open": "Open discussion (round {k})"}
    notes = {n.get("item"): n for n in r.get("notes", [])}
    section = None
    for c in r["transcript"]:
        sec = ("Preparation" if c["kind"] == "presentation" else "Open discussion" if c["kind"] == "open"
               else f"Agenda item: {c['agenda_item']}'s presentation")
        if sec != section:
            if section and section.startswith("Agenda item") and section.split(": ")[1].split("'")[0] in notes:
                out += _notes_md(notes[section.split(": ")[1].split("'")[0]])
            out += [f"## {sec}", ""]
            section = sec
        head = label.get(c["kind"], c["kind"]).format(item=c["agenda_item"], k=c["round_number"])
        out += [f"### {c['seat_id']} ({r['seat_ids'].get(c['seat_id'], c.get('model_id', ''))}) — {head}", ""]
        out += ["*PASS*" if c["kind"] == "remarks" and len(c["content"]) < 40 and "pass" in c["content"].lower()
                else c["content"], ""]
        if c.get("citations"):
            out += ["<details><summary>Sources returned by web search</summary>", ""]
            out += [f"- {u}" for u in c["citations"]] + ["", "</details>", ""]
    if section == "Open discussion" and "open discussion" in notes:
        out += _notes_md(notes["open discussion"])
    if r.get("decision"):
        d = r["decision"]
        out += ["## Chair's decision", "", f"**Chosen:** {d.get('chosen')} — {d.get('direction')}", "",
                f"**Rationale:** {d.get('rationale')}", ""]
        for k, t in (("accepted_changes", "Accepted changes"), ("not_chosen", "Not chosen"),
                     ("open_concerns", "Open concerns"), ("backlog", "Backlog")):
            if d.get(k):
                out += [f"**{t}:**", ""] + [f"- {x}" for x in d[k]] + [""]
    out += ["## Final proposal", ""]
    for k, v in r["idea"].items():
        if v:
            out += [f"**{k}:**", "", "\n".join(f"- {x}" for x in v) if isinstance(v, list) else str(v), ""]
    return "\n".join(out)


def _ok(f: Path) -> bool:
    return f.exists() and "error" not in json.loads(f.read_text())


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_data_args(ap, out="ideation/meeting_test", are=True)
    ap.add_argument("--split", choices=["dev", "test"], default="test")
    ap.add_argument("--per-domain", type=int, default=3, help="test split: tasks sampled per domain")
    ap.add_argument("--tasks", nargs="*", default=[], help="restrict to these task IDs within the split")
    ap.add_argument("--conditions", nargs="+", default=["pool", "heterogeneous"], choices=["pool", "heterogeneous"])
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--budget", type=float, default=150.0, help="stop launching new runs past this spend ($)")
    ap.add_argument("--web", action="store_true", help="let seats search the web (off by default: leaks the source paper)")
    a = ap.parse_args()
    are = AutoResearchEval(are_root(a))
    out = a.out_dir
    out.mkdir(parents=True, exist_ok=True)
    tasks = load_tasks(are, a.split, a.per_domain)
    if a.tasks:
        tasks = [t for t in tasks if t["task_id"] in a.tasks]
    print(f"{len(tasks)} tasks: {[t['task_id'] for t in tasks]}", flush=True)
    sem = asyncio.Semaphore(a.concurrency)
    async with client() as http:
        u0 = await key_usage(http)

        async def guarded(t: dict, name: str, stems: list[Path], job) -> list[dict]:
            if all(_ok(st.with_suffix(".json")) for st in stems):
                print(f"{t['task_id']} {name}: already done, skipped", flush=True)
                return []
            async with sem:
                spent = await key_usage(http) - u0
                if spent > a.budget:
                    return [{"task_id": t["task_id"], "condition": name, "error": f"budget exceeded (${spent:.2f})"}]
                calls: list = []
                try:
                    recs = await job(t, a.web, calls)
                    recs = recs if isinstance(recs, list) else [recs]
                except Exception as exc:  # noqa: BLE001 - recorded, the other tasks continue
                    recs = [{"task_id": t["task_id"], "condition": name, "error": repr(exc)}]
                summ = call_summary(calls)
                with gzip.open(out / f"{t['task_id']}__{name}.calls.jsonl.gz", "wt") as fh:
                    for call in calls:
                        fh.write(json.dumps(call, ensure_ascii=False) + "\n")
                for r in recs:
                    r["calls_summary"] = summ  # shared by all pool conditions of a task
                    st = out / f"{t['task_id']}__{r['condition'].replace(':', '-')}"
                    st.with_suffix(".json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
                    if "error" not in r:
                        st.with_suffix(".md").write_text(render_markdown(r, t))
                status = recs[0]["error"] if "error" in recs[0] else "ok"
                print(f"{t['task_id']} {name}: {status} | ${summ['total_cost']:.3f} | "
                      f"spend so far ${await key_usage(http) - u0:.2f}", flush=True)
                return recs

        jobs = []
        for t in tasks:  # task by task (pool first), so each task finishes as early as possible
            if "pool" in a.conditions:
                stems = [out / f"{t['task_id']}__{c.replace(':', '-')}" for c in POOL_CONDITIONS]
                jobs.append(guarded(t, "pool", stems, run_pool))
            if "heterogeneous" in a.conditions:
                jobs.append(guarded(t, "heterogeneous", [out / f"{t['task_id']}__heterogeneous"], run_meeting))
        res = [x for r in await asyncio.gather(*jobs) for x in r]
        total = await key_usage(http) - u0
    (out / "_summary.json").write_text(json.dumps(
        {"total_cost_key_delta": total, "n_runs": len(res), "errors": [r for r in res if "error" in r]},
        ensure_ascii=False, indent=1))
    print(f"done: {len(res)} runs, total spend ${total:.2f}")


if __name__ == "__main__":
    asyncio.run(main())

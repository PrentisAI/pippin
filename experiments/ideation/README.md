# Multi-agent ideation

**What is measured.** (1) Whether ideas from three foundation models are more diverse than three samples
of one model, and what a group meeting among them does to the ideas (paper: "Collective
intelligence in ideation" and the appendix "Evaluation Details"); (2) how the AutoResearchEval
agents' own ideation records score, e.g. how often they test an easier proxy (paper: "Component-wise System Evaluation", appendix "Evaluation Details").

**Protocol (1).** 21 held-out open-ended tasks (3 per domain, sampled with a fixed seed from the tasks not
used while developing the meeting protocol).  Seats: Claude Sonnet 5.5, GPT-5.6, Gemini 3.1 Pro; chair:
Kimi K3; web search off.  Per task, `ideation.run_meeting` makes

* a **pool** of independently prepared ideas (Sonnet x3, GPT x3, Gemini x1), from which come
  `single:<m>` (the first idea of a model, written up), `bestof3:sonnet` / `bestof3:gpt` (the chair picks
  one of three samples of one model) and `hetero_pick` (the chair picks one of the three models' ideas,
  no discussion); and
* a **group meeting** (`heterogeneous`, `aurora.agents.roundtable.Roundtable`): presentations, tagged
  remarks, ACCEPT / REBUT / DEFER answers, open discussion, chair notes and decision, final proposal.

`ideation.analyze_meeting` reads the chair's selection rationales and the meeting transcripts.
Supplementary: two blind judges (Grok 4.7, Qwen 3.8 Max; two presentation orders each) rank all distinct
proposals of a task (`ideation.judge meeting`, `ideation.analyze_judges`).

**Protocol (2).** `ideation.build_agent_inputs` extracts the 525 ideation records (problem, hypothesis,
method, alternatives, falsification check) of the 8 agents on the 70 open-ended tasks from AutoResearchEval,
with a flag for agents that saw the source paper before their first code; `ideation.judge agents` scores
them blind (GPT-5.6 on all tasks, Gemini 3.1 Pro on 15 as a second judge).

**Inputs** (`<data-dir>/ideation/`): `meeting_test/` (one `.json` record and `.md` transcript per task and
condition, `_judge_grok.json`, `_judge_qwen.json`), `chair_same_study.json`, and `agent_ideas/`
(`ideation_inputs.json`, `ideation_scores_gpt.json`, `ideation_scores_gemini15.json`).

## (a) Recompute the paper numbers (free, offline)

```bash
bash ideation/reproduce.sh
# = python -m ideation.analyze_meeting          (the paper's 20 tasks)
#   python -m ideation.analyze_judges           (all 21 tasks)
#   python -m ideation.summarize_agent_ideas
```

| Paper | Output line | Value |
|---|---|---|
| chair: "essentially the same study" in 19/20 (Sonnet x3), 12 (GPT x3), 4 (three models) | `chair says ... same study` (annotation) | 19/20, 12/20, 4/20 |
| presenters accept 90% of addressed remarks, rebut 5% | `presenters' answers` | ACCEPT 90.2%, REBUT 4.7% |
| 2% of tagged remarks are evidence | `EVIDENCE share` | 1.9% (22 of 1,131) |
| 5 of 20 meetings merged directions | `meetings that merged` | 5/20 |
| US$2.71 vs US$0.72 per task; about US$69 for the 20 tasks | `cost per task` | $2.71 vs $0.72; total $68.56 |
| 67% of agents' proposals test a proxy (50-83% by model) | `all proposals: proxy_for_question` | 67% |
| best proposal 2.90 vs 2.23 mean alignment; 2.63 vs 2.14 without source-paper exposure | `[all proposals]`, `[independent ideation only ...]` | as stated |
| second judge within one point on 99%, 53% proxies; cost $2.35 + $0.98 | last line / first line | as stated |
| (supplementary) mean normalised rank: hetero_pick 0.20, bestof3:gpt 0.29, single:gpt 0.33, bestof3:sonnet 0.38, single:sonnet 0.425, meeting 0.48, single:gemini 0.91; judge correlation 0.80 | `analyze_judges` | as stated (21 tasks) |

**The 20 tasks.** The paper's meeting statistics cover 20 of the 21 released tasks: W4409170628, the last
meeting to finish (the chair's notes for one agenda item failed to parse), is not included.
`analyze_meeting --exclude ""` gives all 21 (ACCEPT 90.1%, 5/21 merges, $2.75 vs $0.72).
The judge ranking above is over all 21 tasks; `analyze_judges --exclude W4409170628` gives the 20-task
version (hetero_pick 0.21, bestof3:gpt 0.29, ...).

**The "same study" count.** In the paper this was counted by reading the chair's rationales.
`chair_same_study.json` is a per-task annotation reconstructed for this release that reproduces those
counts; it agrees with a keyword rule (rationale opens with "All three ..." and calls the ideas the same
or convergent) everywhere except one GPT task, which `analyze_meeting` prints alongside (11/20 for GPT).

## (b) Re-run the models

```bash
export OPENROUTER_API_KEY=...
bash ideation/reproduce.sh rerun
# or step by step:
python -m ideation.run_meeting --split test --out-dir runs/ideation/meeting_test          # ~$73, ~3 h at --concurrency 6
python -m ideation.judge meeting --runs-dir runs/ideation/meeting_test --judge x-ai/grok-4.7 \
    --out runs/ideation/meeting_test/_judge_grok.json                                       # ~$2.3
python -m ideation.judge meeting --runs-dir runs/ideation/meeting_test --judge qwen/qwen3.8-max-0902 \
    --out runs/ideation/meeting_test/_judge_qwen.json                                       # ~$2.9
python -m ideation.analyze_meeting runs/ideation/meeting_test --annotations none
python -m ideation.analyze_judges runs/ideation/meeting_test/_judge_grok.json runs/ideation/meeting_test/_judge_qwen.json

python -m ideation.build_agent_inputs --out runs/ideation/agent_ideas/ideation_inputs.json   # free; identical to the released file
python -m ideation.judge agents --inputs runs/ideation/agent_ideas/ideation_inputs.json --judge openai/gpt-5.6-sol --budget 6
python -m ideation.judge agents --inputs runs/ideation/agent_ideas/ideation_inputs.json --judge google/gemini-3.1-pro-preview --tasks 15 --budget 2
```

`run_meeting` skips runs that already have a record, so an interrupted run can be resumed; `--budget`
stops launching new runs past that spend, `--tasks` restricts to some task IDs, `--split dev` runs the
three development tasks.  For a new run, the "same study" count needs a fresh reading of the chair's
rationales (`--annotations none` prints the keyword heuristic only).  The meeting code is the released
`aurora.agents.roundtable`, identical to the paper runs except that a failed chair decision now raises
instead of returning a fallback proposal; model outputs differ from run to run.

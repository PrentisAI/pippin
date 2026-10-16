# Reproducing the paper's component-wise evaluation

This directory reproduces Section 5 ("Component-wise evaluation") and Appendix C of the AURORA paper:
the live auditor, the milestone auditor, auditor interventions, and multi-agent ideation, all evaluated
on recorded runs of other agents from [AutoResearchEval](https://huggingface.co/datasets/PrentisAI/AutoResearchEval)
(Fei et al., arXiv:2608.14905).

Every number can be recomputed **offline and for free** from the released outputs of the paper's runs.
Each experiment can also be **re-run** with models (needs an OpenRouter key; costs below).

## Setup

```bash
pip install -e ..                     # the aurora package (from the repository root)
pip install -r requirements.txt       # httpx, huggingface_hub
cd experiments
python scripts/download_data.py       # release tarball (8.5 MB, 32 MB unpacked) + AutoResearchEval open-ended subset (~0.5 GB)
python scripts/download_data.py --skip-are   # enough for the offline recomputation
```

Data goes to `experiments/data/` (or `--data-dir`, or `$AURORA_EXPERIMENTS_DATA`); new results go to
`experiments/runs/` (`--out-dir`).  All commands are run from `experiments/` as modules
(`python -m supervisor.summarize`, ...).  Re-runs need `OPENROUTER_API_KEY`; the two auditor
experiments also need Docker and the Claude Code CLI (`claude`), whose binary is mounted into the
sandbox containers.

## Overview

| Paper claim / table | Experiment | Recompute (free, offline) | Expected | Re-run | Cost / time of a re-run (from the paper runs) |
|---|---|---|---|---|---|
| Table 1 "Live", Fig. 3, App. C: live auditor flags 93/95 annotated failures (29/29 invalidating), 59 before onset, 91 at the first push, 95% of 641 messages valid, 1 incorrect, $0.89 per trajectory | [supervisor/](supervisor/) | `bash supervisor/reproduce.sh` | 93/95; 29/29, 63/65, 1/1; 607/641 valid | `bash supervisor/reproduce.sh rerun` | $40.73 auditor (Sonnet 5.5) + $3.62 judge; ~30 min |
| Table 1 "Milestone", Fig. 3b, App. C: milestone auditor finds 100/106 visible failures (31/31 invalidating), 88% of 440 findings valid, 2% incorrect, 30 packaging complaints, fail on 36/47 problem runs vs 1/5, $0.23 per package, median 71 s | [auditor/](auditor/) | `bash auditor/reproduce.sh` | 100/106; 31/31, 69/75; 388/440 valid | `bash auditor/reproduce.sh rerun` | $11.87 auditor + $3.81 judge; ~10 min |
| Sec. 5 "Behavioral correction", App. C: corrections 3/28 to 16/28 (round 1); 2/70 to 41/70 (round 2), 21/30 correction, 20/40 prevention, 17/20 invalidating; wrong advice followed 39/44 (GLM-5.2, GPT-5-mini), Opus 4.8 questioned 17/26 | [intervention/](intervention/) | `bash intervention/reproduce.sh` | see left | `bash intervention/reproduce.sh rerun` | ~$4 + ~$12 (paper: "about $17"); ~20 min |
| Sec. 5 "Collective intelligence": chair calls three ideas the same study in 19/20 (Sonnet), 12/20 (GPT), 4/20 (three models); presenters ACCEPT 90%, REBUT 5%; EVIDENCE 2% of tags; 5/20 merges; $2.71 vs $0.72 per task; ~$69 total | [ideation/](ideation/) | `bash ideation/reproduce.sh` | see left | `bash ideation/reproduce.sh rerun` | ~$73 runs (21 tasks) + ~$5 judges; ~3 h |
| Blind ranking of ideation conditions (supplementary): mean normalised rank hetero_pick 0.20, bestof3:gpt 0.29, single:gpt 0.33, bestof3:sonnet 0.38, single:sonnet 0.425 (printed 0.425; rounds to 0.43), meeting 0.48, single:gemini 0.91; judge correlation 0.80 | [ideation/](ideation/) | `python -m ideation.analyze_judges` | see left | (part of the ideation re-run) | $2.27 (Grok 4.7) + $2.86 (Qwen 3.8 Max) |
| Sec. 5 / App. C "Ideation scoring": 67% of agents' proposals test a proxy (50-83% by model); best proposal 2.90 vs 2.23 mean alignment (2.63 vs 2.14 without source-paper exposure); 99% within one point, second judge 53% proxies; $2.35 + $0.98 | [ideation/](ideation/) | `python -m ideation.summarize_agent_ideas` | see left | (part of the ideation re-run) | $2.35 + $0.98 |

## Comparable, not identical

The released outputs were produced with the pre-release implementation of AURORA.  The scripts here
run against the released `aurora` package, which changed in two places that matter:

* **Live auditor.** `aurora.core.supervisor.Supervisor` now returns verdicts as `VERDICT {...}` lines in
  its replies (four outcomes: continue / advise / interrupt / escalate) instead of appending them to a
  verdict file.  `supervisor/replay.py` keeps the evaluation protocol (Docker sandbox with only the run
  directory mounted, five normalized steps per push, the next push only after the auditor's turn, web
  tools disabled, the principal-investigator prompt), changing only the verdict instructions in that prompt.
* **Ideation.** The meeting code (`aurora.agents.roundtable`) is the same as in the paper runs except that
  a failed chair decision now raises instead of returning a fallback proposal.

Model outputs are stochastic and hosted models change, so a re-run gives comparable but not identical
numbers.  The judges are re-run in the same way; `*.summarize` / `analyze_*` on the released files give
the paper's numbers exactly.

## Layout

```
common/          paths.py (--data-dir / --out-dir / --are-dir), dataset.py (AutoResearchEval access),
                 normalize.py (Claude Code / Codex / Gemini CLI trajectories -> one step list),
                 openrouter.py (the one judge/generator client), sandbox.py (Docker sandboxes for Claude Code)
supervisor/      replay.py, score.py, summarize.py, reproduce.sh
auditor/         Dockerfile (+ sandbox-requirements.txt), run_auditor.py, score.py, summarize.py, reproduce.sh
intervention/    run_intervention.py (select / seed / prep / probe / run / judge / summary), reproduce.sh
ideation/        run_meeting.py, judge.py, analyze_meeting.py, analyze_judges.py,
                 build_agent_inputs.py, summarize_agent_ideas.py, reproduce.sh
scripts/         download_data.py
```

## Data and licence

`MANIFEST.md` in the release tarball lists every file with its provenance.  The tarball is released
under CC-BY-4.0, like AutoResearchEval, from which all items are derived.  The test sets were selected
and annotated by us; how is described in Appendix C of the paper.  Not released: development and pilot
runs, raw per-call logs, and the scripts that built the annotation candidates and the deliverable
packages from internal run archives.

# Live auditor (supervisor) replay

**What is measured.** Whether AURORA's live auditor, following an agent's work as it happens, flags the
research failures that annotators found in the agent's trajectory, how early, and how often its messages
point at real problems (paper: Table 1 "Live", Figure 3, Appendix C "Replay harness" and "Judging").

**Protocol.** 46 AutoResearchEval trajectories of 7 agent models (25 tasks, 5,361 normalized steps; 41
with confirmed failures, 5 clean controls).  Each trajectory is pushed to the auditor five normalized
steps at a time (assistant text, reasoning, tool calls, tool outputs cut to 5,000 characters) in the
JSONL format of live runs; the next push is sent only after the auditor's turn has finished, so it never
sees future steps.  The auditor is `aurora.core.supervisor.Supervisor` (Claude Sonnet 5.5, principal-
investigator prompt) running inside a Docker container (`python:3.11-slim`, 1 CPU, 2 GB) that mounts only
its run directory; web tools are disabled; the task description is the premise and open problem, without
the source paper's conclusion.  An LLM judge (GPT-5.6) then
(A) matches the labelled failures to the auditor's advise / escalate messages, and
(B) classifies the unmatched messages as substantive, minor, speculative, incorrect or unverifiable.

**Inputs** (`<data-dir>/supervisor/`): `test_items.txt` (46 `model/task`), `labels_test/` (annotations),
`runs/test_sonnet_pi/` (the paper run: `replay_summary.json` per trajectory and the judge's `scores.json`).
Re-runs also need AutoResearchEval (`<data-dir>/AutoResearchEval`).

## (a) Recompute the paper numbers (free, offline)

```bash
bash supervisor/reproduce.sh                      # = python -m supervisor.summarize
```

Expected output (abridged):

```
trajectories scored: 46 (41 problem, 5 clean); agent models: 7; tasks: 25; steps: 5361
labelled issues: 95; detected: 93 (98%)
   invalidates_conclusion   29/29 (100%)
   weakens                  63/65 (97%)
   minor                    1/1 (100%)
   anticipatory (advice before the issue appeared): 59; after it appeared: 34
detection delay vs first visible step: median -7 steps; caught at first push after the issue appeared: 91/93; ...
advice messages: 641; categories: {'matched_label': 403, 'speculative': 32, 'valid_substantive': 144, 'valid_minor': 60, 'incorrect': 1, 'unverifiable': 1}
   valid (matched label or judged real): 607/641 (95%); incorrect: 1 (0%)
live-auditor cost (est.): $40.73 ($0.89 per trajectory); judge cost: $3.62
```

"204 problems missed by the annotations" in the paper is 144 + 60 (substantive + minor).

## (b) Re-run the models

```bash
export OPENROUTER_API_KEY=...
bash supervisor/reproduce.sh rerun
# or step by step:
python -m supervisor.replay --model anthropic/claude-sonnet-5.5 --prompt-variant pi --chunk 5 \
    --out-dir runs/supervisor/test_sonnet_pi            # ~26 min at --concurrency 8, ~$41
python -m supervisor.score --runs runs/supervisor/test_sonnet_pi --judge openai/gpt-5.6-sol   # ~$3.6
python -m supervisor.summarize runs/supervisor/test_sonnet_pi/scores.json
```

Requirements: Docker, the Claude Code CLI on the host (`claude`; its binary is mounted read-only into the
sandbox, or pass `--claude-bin`), and AutoResearchEval.  `--items model/task ...` replays a subset;
`--prompt-variant production` uses the released Supervisor's own prompt instead of the paper's PI prompt.

**Differences from the paper run.** The paper run used the pre-release Supervisor, where the auditor
appended verdict lines to a file; the released Supervisor parses `VERDICT {...}` lines from its replies.
The PI prompt is otherwise unchanged (its unused checkpoint paragraph is gone).  The judge counts
`interrupt` verdicts as messages too (the paper's PI prompt offers only continue / advise / escalate).
Expect comparable but not identical numbers.

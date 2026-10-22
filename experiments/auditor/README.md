# Milestone auditor on final deliverables

**What is measured.** Whether AURORA's milestone audit, given only the final deliverables of a research
run, finds the failures annotated in that run, and how often its findings are real (paper: Table 1
"Milestone", Figure 3b, appendix "Evaluation Details").

**Protocol.** 52 deliverable packages from 8 agent models (`task.md`, `agent_report.md`, `decision.json`,
`code/`, `PACKAGE_INFO.md`; no trajectory, no labels).  The auditor (Claude Sonnet 5.5, the paper's
milestone-audit prompt with a findings list) runs once per package as a Claude Code session in the
`aurora-auditor-sandbox:py311-sci` container (2 CPUs, 4 GB, Python 3.11 with numpy / scipy / pandas /
matplotlib / scikit-learn) where it can rerun code; web tools are disabled; at most 60 turns.
The judge (GPT-5.6) (A) matches the failures visible in the deliverables (`visible` yes / partial, plus
`deliverable_issues`) to findings, and (B) classifies unmatched findings as substantive, minor,
packaging artifact (only complains that code is missing from the reconstructed package), incorrect or
unverifiable.

**Inputs** (`<data-dir>/auditor/`): `test_items.txt`, `labels/` (52), `packages/` (52), and
`runs/test_sonnet/` (the paper run: `audit_summary.json` per package and the judge's `scores.json`).

## (a) Recompute the paper numbers (free, offline)

```bash
bash auditor/reproduce.sh                         # = python -m auditor.summarize
```

Expected output (abridged):

```
packages scored: 52 (errors: 0); agent models: 8
   all                              100/106 (94%)
   sev:invalidates_conclusion       31/31 (100%)
   sev:weakens                      69/75 (92%)
findings: 440; categories: {'matched_label': 128, 'valid_minor': 139, 'valid_substantive': 121, 'packaging_artifact': 30, 'incorrect': 9, 'unverifiable': 13}
   valid (matched label or judged real): 388/440 (88%); incorrect: 9 (2%); packaging artifacts: 30
   fail on confirmed problems: 36/47; fail otherwise: 1/5; pass: 0
auditor cost (est.): $11.87 ($0.23 per package); mean turns 7.2, median 7; judge cost $3.81
wall time per package: median 70.5 s
```

## (b) Re-run the models

```bash
docker build -t aurora-auditor-sandbox:py311-sci auditor/     # once
export OPENROUTER_API_KEY=...
bash auditor/reproduce.sh rerun
# or step by step:
python -m auditor.run_auditor --model anthropic/claude-sonnet-5.5 --out-dir runs/auditor/test_sonnet   # ~10 min, ~$12
python -m auditor.score --runs runs/auditor/test_sonnet --judge openai/gpt-5.6-sol                     # ~$3.8
python -m auditor.summarize runs/auditor/test_sonnet/scores.json
```

Requirements: Docker and the Claude Code CLI on the host (mounted into the container; `--claude-bin`).
AutoResearchEval is not needed.

**Sandbox image.** `Dockerfile` rebuilds the image used for the paper (`python:3.11.17-slim` plus one pip
layer); `sandbox-requirements.txt` pins the versions that layer resolved to, and a build from this
Dockerfile reproduces the original image's `pip list` exactly.

**Prompt.** The released engine's milestone audit (`aurora.core.engine`) asks for pass / reject /
escalate without a findings list; this evaluation keeps the paper's prompt (in `run_auditor.py`) so that
findings can be scored.  A re-run gives comparable but not identical numbers.

**Packages.** 46 packages were reconstructed from AutoResearchEval rollouts (files the agent wrote,
recovered from its trajectory; incomplete by construction, as `PACKAGE_INFO.md` says), and the 6
`opus-4.8__*` packages contain the agent's real workspace files.  The script that built them read
internal run archives and is not released; the packages themselves are.

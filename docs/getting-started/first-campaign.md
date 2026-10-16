# Your first campaign

This page runs a small real campaign, explains what you see while it runs, and
shows where the results are. It takes a few minutes and a few dollars.

## Run the quickstart

The repository ships a [quickstart example](https://github.com/titanresearchlabs/aurora/tree/main/examples/quickstart):
a CSV of 400 simulated reactor runs with a known ground truth, and a config
asking which variables drive the yield.

```bash
aurora run examples/quickstart/aurora.yaml
```

The config is short:

```yaml
objective: >
  The file inputs/measurements.csv holds 400 runs of a (simulated) reactor ...
  Find out which variables actually drive the yield ...
inputs:
  - data/measurements.csv        # copied to /app/inputs in the container
skip_phases: [ideation, screening]
budget:
  max_cost_usd: 5
```

`skip_phases` drops ideation and screening because the question is already
concrete. For an open question, keep them (see `examples/open_ended`).

## What happens

The log shows each phase as it starts:

1. **Planning** — a Claude Code session looks at the inputs and writes a plan of
   jobs (`phases/planning/round_0/plan.json`).
2. **Milestone audit** — the auditor reviews the plan. *pass* moves on;
   *reject* sends fixes back to the planner; *escalate* asks you.
3. **Research** — for each job, a fresh research session works in the container:
   it installs what it needs, writes and runs code, saves figures. The live
   auditor reads its transcript as it goes and may advise or interrupt it.
4. **Analysis** — checks the results against the objective and either accepts
   them or asks for another research round.
5. **Writing** — writes the report.

When a decision is yours (an escalation), the run pauses and asks in the
terminal:

```text
=== Human gate: The live auditor escalated: flow_rate is collinear with temperature ===
...
Answer: [c]ontinue, [i]nstruct (then type the instruction), [s]top >
```

With `human.autonomy: autonomous` the campaign does not wait; the gate is
recorded and it continues.

## Results

At the end AURORA prints a summary:

```text
================================================================
Campaign COMPLETED: Campaign completed with 2 research jobs and 0 follow-up rounds.
  jobs      : 2/2 succeeded
  report    : workspace/the-file-inputs-measurements-csv-.../phases/writing/draft_1.md
  workspace : workspace/the-file-inputs-measurements-csv-...
  time      : 7.9 min   API cost: $1.84
================================================================
```

| Look at | For |
|---|---|
| `phases/writing/draft_1.md` | the report |
| `workspace/` | code, data and figures the research agent produced |
| `audit_records/` | every milestone audit verdict and its reasons |
| `phases/supervisor/` | the live auditor's verdicts |
| `human_gates.jsonl` | the decisions you were asked for |

`aurora status workspace/<campaign>` summarises a campaign;
`aurora resume workspace/<campaign>` continues one that was stopped or failed.

## Writing a good objective

The objective is the brief every agent reads. Good objectives

- say what you want to **find out** (or build), not how;
- say what **data or code** exists and where (list it under `inputs`);
- say what a **good result** looks like: which numbers, figures, comparisons;
- name **constraints**: compute, time, methods to avoid.

Start your own with `aurora init`, which writes a commented `aurora.yaml`.

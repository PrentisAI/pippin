# How a campaign runs

```text
ideation → screening → planning → pre-flight → research → analysis → writing
                                                   ↑___________|   analysis may ask for more research
```

Every arrow is a **hand-off** that the milestone auditor reviews. During
research the live auditor watches the research agent. Any of them, and a few
other points, can open a **human gate**.

## Phases

| Phase | Who | Produces |
|---|---|---|
| **Ideation** | several models (*seats*) and a chair, via direct API calls | a proposal: direction, hypothesis, method, how it could be falsified, alternatives considered |
| **Screening** | Claude Code session | novelty / feasibility / significance scores; below 6/10 the proposal is rejected and ideation runs again (twice, then you decide) |
| **Planning** | Claude Code session | a plan of jobs with dependencies |
| **Pre-flight** | Claude Code session, in the container | for every job: prerequisite checks, a smoke run, an estimate of the full run, problems; problems send the plan back to planning |
| **Research** | one Claude Code session per job, in the container | code, data, figures, a summary per job |
| **Analysis** | Claude Code session | a verdict: sufficient, or which phase should run again (research, planning or ideation) |
| **Writing** | Claude Code session | the report (Markdown with figures) |

## Pre-flight

Before any job runs at full scale, a pre-flight agent checks each one:

- **prerequisites** — the inputs and data exist or can be obtained, the software
  is installed or installable, the parameters agree with each other and with the
  proposal (in a DFT study, say, slab geometry and pseudopotentials);
- a **smoke run** — the job's critical steps on the smallest representative
  input (a small sample, one iteration, a tiny system), under
  `/app/preflight/<job>/`, with time and memory measured to estimate the full
  run against the container's limits.

It writes `phases/preflight/round_N/report_K.json`, one entry per job (*ok*,
*problem* or *untestable*). If any job has a problem, planning revises the plan
from the report and the checks run again (`agent.preflight_max_revisions`,
default 2); problems that remain open a human gate (continue anyway, plan again
with your instruction, or stop). The findings are attached to each job
(`parameters.preflight`), so its research agent starts with them. The checks
have their own time limit (`agent.preflight_timeout_minutes`, default 20); jobs
not reached are marked untestable. Skip the phase with `skip_phases:
[preflight]`.

## Running the jobs

Research runs the plan's jobs in the order of their dependencies. With
`agent.max_parallel_jobs` above 1 (default 1; *Advanced settings* on the
proposal card), jobs whose inputs are ready run at the same time, each with its
own research agent, and a new one starts as soon as another finishes. They share
the container (its CPUs, memory and `/app`) and are told which jobs run beside
them; give the container more resources accordingly. The live auditor watches
all of them, and the front desk can address one job or all.

Each phase agent starts a fresh session and is told where the earlier phases'
outputs and transcripts are, so it reads what it needs instead of inheriting a
long context.

Any phase can be skipped with `skip_phases`. Skipping ideation makes the
objective itself the direction; skipping planning gives a single job.

## The sandbox

A campaign gets one Docker container for its whole life. The agents run every
command in it (`docker exec`); on the host they can only read and write files in
the campaign directory. The container's `/app` is the campaign's `workspace/`
directory on the host; when the campaign ends the container is removed. See
[Sandbox and security](../guide/sandbox.md).

## What the campaign knows: the mental model

Each analysis records the campaign's current understanding:

- **findings**, each with its evidence and status (supported, tentative or
  refuted), stored as claims in `artifacts.json`;
- **open questions**;
- the **scientific mental model** (`mental_model.json`): the hypotheses with their
  status (active, competing, confirmed, refuted), confidence and evidence,
  inferred mechanisms, unresolved ambiguities and the current rationale.

Refuted hypotheses stay in the record, so the report and later rounds see what
was overturned. Later phases receive this understanding: ideation seats in a
new round, planning, the research agent and the live auditor (in their brief),
and the writing agent. Each research job's final summary is recorded too, so
analysis and writing work from what the agent actually reported.

## Follow-up rounds

When analysis finds the results insufficient it names a phase to return to.
The campaign starts a new *round* from there (`round_1/`, `round_2/` folders),
up to `agent.max_followup_cycles` times; after that it writes up what there is.

## Stopping and resuming

A campaign can be stopped at any time (Ctrl-C, *Stop* in the web console, a
budget limit, a human *stop*, or a crash) and continued with
`aurora resume <campaign dir>` (or *Resume* in the web console). Work in
progress is kept as far as possible:

- **Finished phases and jobs** are not repeated (`checkpoint.json`).
- **Files** are on disk: the container's `/app` is the host's
  `<campaign>/workspace/`, which the new container mounts again on resume.
- **An interrupted agent session** (screening, planning, a research job,
  analysis, writing, a milestone audit) is continued in the same Claude Code
  session (`sessions.json`), with what it had read, thought and written so far,
  and told to pick up where it stopped. If that session cannot be restored, a
  new one starts and is pointed at the partial outputs.
- **An interrupted ideation meeting** resumes after the last finished step:
  prepared ideas, discussed agenda items and the chair's notes are kept
  (`phases/ideation/round_N/meeting_state.json`).

## Budgets

`budget.max_hours` and `budget.max_cost_usd` are checked before every phase and
research job; when one is exceeded the campaign stops cleanly (status
*stopped*) and can be resumed with a larger budget. Cost is what the providers
report (OpenRouter) for all agent sessions and API calls.

## Mock mode

`mock_mode: true` (or `aurora demo`) replaces every model with a scripted mock
and every tool with a simulation. Nothing external runs. The pipeline, the
files and the events are the same as in a real run, which makes it useful for
trying AURORA, for developing integrations and for tests.

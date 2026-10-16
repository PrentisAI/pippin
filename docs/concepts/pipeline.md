# How a campaign runs

```text
ideation → screening → planning → research → analysis → writing
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
| **Research** | one Claude Code session per job, in the container | code, data, figures, a summary per job |
| **Analysis** | Claude Code session | a verdict: sufficient, or which phase should run again (research, planning or ideation) |
| **Writing** | Claude Code session | the report (Markdown with figures) |

Each phase agent starts a fresh session and is told where the earlier phases'
outputs and transcripts are, so it reads what it needs instead of inheriting a
long context.

Any phase can be skipped with `skip_phases`. Skipping ideation makes the
objective itself the direction; skipping planning gives a single job.

## The sandbox

A campaign gets one Docker container for its whole life. The agents run every
command in it (`docker exec`); on the host they can only read and write files in
the campaign directory. When the campaign ends, the container's `/app` is
copied to `<campaign>/workspace/` and the container is removed. See
[Sandbox and security](../guide/sandbox.md).

## Follow-up rounds

When analysis finds the results insufficient it names a phase to return to.
The campaign starts a new *round* from there (`round_1/`, `round_2/` folders),
up to `agent.max_followup_cycles` times; after that it writes up what there is.

## Stopping and resuming

After every phase and every research job the campaign saves `checkpoint.json`.
A campaign that was interrupted (Ctrl-C, a crash, a budget limit, a human
*stop*) continues with `aurora resume <campaign dir>` (or *Resume* in the web
console) from the last checkpoint.

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

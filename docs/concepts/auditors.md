# Auditors

Long campaigns go wrong in two ways: a phase hands bad output to the next one,
and an agent goes astray in the middle of long work. AURORA has one auditor
for each.

## Milestone auditor

Runs at every hand-off between phases (`audit.milestone: true`). It is a Claude
Code session (`models.auditor`) that reads the phase's actual output files — and,
read-only, the container — and checks goal alignment, completeness,
correctness and traceability of claims. Its verdict:

| Verdict | Effect |
|---|---|
| **pass** | the campaign moves on |
| **reject** | the recommendations go back into the phase's own session as required fixes; the phase revises its output and is audited again (up to `audit.milestone_max_retries` times) |
| **escalate** | a human gate: the auditor's question, summary and recommendations are shown to you |

A phase still rejected after its retries also escalates. Reports are saved as
`audit_records/round<N>_<phase>_to_<next>.json`.

## Live auditor (supervisor)

Runs next to the research agent (`audit.live: true`). The engine tails the
research agent's transcript (`phases/research/research_loop.jsonl`: its
reasoning, tool calls and tool outputs) and pushes new entries to a second
Claude Code session (`models.supervisor`) every few seconds. After each update
it answers with a verdict:

| Verdict | Effect |
|---|---|
| **continue** | nothing (most updates) |
| **advise** | its suggestion is sent to the research agent, which keeps working |
| **interrupt** | the agent's current action is stopped (SIGINT) and the correction sent |
| **escalate** | the agent is paused and a human gate opens |

The live auditor can read the workspace and inspect the container read-only;
it cannot edit files or change the plan — it advises, the agent decides.
Verdicts are saved in `phases/supervisor/round_N/verdicts.jsonl` and shown
live in the web console.

## Cost

The milestone auditor runs one short session per hand-off (typically
US$0.05–0.30 each with Sonnet-class models). The live auditor reads every
transcript update, so its cost grows with the length of the research; use a
cheaper `models.supervisor` for very long runs, or turn it off with
`audit.live: false`.

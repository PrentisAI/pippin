# Campaign files

Everything a campaign produces is in its directory, `<workspace.root>/<campaign_id>/`
(web console: `~/.aurora/projects/<project>/<experiment>/`):

```text
config.yaml              effective configuration, without secrets (used by `aurora resume`)
checkpoint.json          where the campaign is; resume continues from here
artifacts.json           experiments, observations and claims recorded so far
human_gates.jsonl        every human gate and its decision
trajectory.jsonl         append-only event log
audit_records/           milestone audit reports: round<N>_<phase>_to_<next>.json
phases/
  ideation/round_N/      proposal (direction, hypothesis, method, meeting notes, chair's decision)
  screening/round_N/     score.json and the session transcript
  planning/round_N/      plan.json and the session transcript
  research/              research_loop.jsonl: live transcript of the research agent
  supervisor/round_N/    live-auditor log and verdicts.jsonl
  analysis/round_N/      verdict.json and the session transcript
  writing/               draft_N.md: the report
workspace/               the container's /app, copied out when the campaign ends
  .aurora/environment.txt  the Python version and `pip freeze` of the container at the end
  .aurora/jobs/<id>-<name>/  a GPU job's command (run.sh), output (slurm.out or job.out), exit_code
results.jsonl            every recorded result (R1, R2, ...) with its script, command, files, job
provenance/R<n>/         the script of result R<n> as it was when it was recorded
cost.json                the cost so far (a resumed or forked run counts it against its budget)
forkpoints/NNN/          a fork point: state files, workspace.tar.gz and point.json
fork.json                (a forked run) the run and point it was forked from, and the guidance
.claude/skills/          the skills the agent sessions could use
```

See [Results, reports and runs](../guide/results.md) for the results, the
fork points and what is made from them.

## Formats

**Transcripts** (`*.jsonl`) have one entry per event of a Claude Code session:
`{"timestamp", "turn", "event_type", "data"}` with `event_type` one of
`thinking`, `tool_call`, `tool_result`, `text`, `permission_denied`, `error`.

**`plan.json`**

```json
{"plan_id": "plan-1", "proposal_id": "prop_ab12cd34", "jobs": [
  {"job_id": "fit-models", "skill_name": "Fit and compare the regression models",
   "parameters": {"description": "..."}, "expected_outputs": ["results/fits.csv"],
   "dependencies": ["load-data"]}]}
```

**Audit report** (`audit_records/*.json`)

```json
{"completed_phase": "planning", "next_phase": "research", "verdict": "pass|reject|escalate",
 "dimensions": {"goal_alignment": {"ok": true, "note": "..."}, "...": {}},
 "summary": "...", "recommendations": ["..."], "question_for_human": "..."}
```

**`human_gates.jsonl`** — one line per gate:
`{"request": {"gate_id", "source", "title", "detail", "options", "created_at"},
"decision": {"action", "message", "decided_by", "gate_id"}, "resolved_at"}`.
`decided_by` is `human`, `frontdesk`, `autonomous`, `timeout` or `system`.

**Live-auditor verdicts** (`phases/supervisor/round_N/verdicts.jsonl`):
`{"outcome": "continue|advise|interrupt|escalate", "reason", "suggestion", "timestamp"}`.

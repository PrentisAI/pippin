# Events

The engine publishes events on an in-process bus; the web console streams them
to the browser over
`ws://<host>/api/campaigns/<project>/experiments/<experiment>/ws` as

```json
{"type": "experiment_event", "run_id": "...", "event_type": "phase_started",
 "source": "engine", "data": {"phase": "planning"}, "timestamp": 1760000000.0}
```

To consume them in Python, subscribe to the engine's bus:

```python
from aurora.config import load_config
from aurora.core.engine import ResearchEngine

engine = ResearchEngine(load_config("aurora.yaml"))

async def on_event(event):
    print(event.name, event.data)

engine.event_bus.subscribe_all(on_event)
result = await engine.run_campaign()
```

## Campaign

| Event | Data |
|---|---|
| `campaign_started` | `campaign_id`, `workspace`, `resume` |
| `phase_started` | `phase` |
| `ideation_progress` | `step` (preparing, prepared, agenda_item, item_notes, open_discussion, chair_selecting, chair_deciding, decided, resumed) and the step's details |
| `ideation_stream` | `seat`, `model`, `kind`, `item`, `round`, `text` (so far), `thinking` (end of the reasoning); about once a second while a seat writes a turn. Web console only, not stored |
| `ideation_turn` | `seat`, `model`, `kind` (presentation, remarks, response, open), `item`, `round`, `content`, `citations`, `headline`; `error` if the turn failed |
| `ideation_complete` | `proposal_id`, `title`, `hypothesis` |
| `literature_search` | `search_id`, `by` (a seat, `Screening`), `query`, `context`, `found`, `reached` and `failed` (indexes that answered and did not) |
| `references_checked` | `by`, `place`, `verified` (keys), `flagged` (count), `unknown` (keys that name no paper) |
| `reference_flagged` | `by`, `place`, `ref_id`, `label`, `status` (not_found, mismatch, unchecked), `reason`, `closest_key` |
| `resources_updated` | `cpus`, `memory_mb`, `old_cpus`, `old_memory_mb`, `live` |
| `proposal_from_researcher` | `proposal_id`, `title`, `text` (the researcher's message at the screening gate), `basis` (the proposal they meant, or `new`), `basis_title`, `interpretation`, `changes`, `rerun` (phases it goes through first) |
| `screening_skipped` | `proposal_id`, `reason` (the researcher's direction, developed by ideation, is not screened) |
| `screening_complete` | `proposal_id`, `selected`, `score` |
| `planning_complete` | `plan_id`, `num_jobs` |
| `preflight_complete` | `attempt`, `revisions`, `ok`, `problems`, `untestable`, `summary`, `jobs` (each: `job_id`, `status`, `smoke_run`, `estimate`, `problems`) |
| `preflight_revision` | `attempt`, `problems` (sent back to planning) |
| `planning_fallback` | `reason` (no valid plan; one job carries out the whole proposal) |
| `job_started` | `job_id`, `title`, `index`, `total`, `parallel_with` (jobs already running) |
| `iteration_complete` | `job_id`, `decision`, `cost_usd`, `errors` |
| `research_loop_complete` | `iterations`, `completed` |
| `analysis_complete` | `loop_back_to` (phase or null) |
| `writing_complete` | `version`, `path` |
| `campaign_completed` / `campaign_failed` / `campaign_stopped` | `status`, `summary`, `cost_usd`, `wall_time_seconds`, `by_user` (stopped by the researcher) |
| `budget_warning` | `share` (of the cost or time budget used), `detail` |
| `estimate_exceeded` | `what` (`cost` or `time`), `now`, `estimate` (low, high), `level` (1: past the upper end, 2: twice it), `detail` |
| `result_recorded` | `id` (R3), `name`, `value`, `unit`, `job`, `supersedes` |
| `report_results_checked` | `cited`, `unknown` (IDs naming no result), `superseded`, `recorded` |
| `forkpoint_saved` | `id`, `label` (*after planning*, *after job X (2 of 5)*), `files`, `skipped` |
| `fork_direction` | `title`: a forked run took another direction |
| `compute_job_submitted` / `compute_job_update` / `compute_job_ended` | a [GPU job](../guide/cluster-jobs.md): `id`, `name`, `backend` (`slurm` or `ssh`), `slurm_id` (SSH: the process ID there), `state`, `reason`, `node`, `gpus`, `gpu_ids` (SSH), `cpus`, `mem_gb`, `time_limit`, `elapsed_seconds`, `exit_code`, `command`, `log` |
| `waiting_for_cluster_jobs` | `job_id` (the research job), `cluster_jobs` (each: `id`, `name`, `slurm_id`), `check_in_minutes`; its agent ended its turn to wait |
| `cluster_jobs_awaited` | `job_id`, `ended` (false: a check-in), `cluster_jobs` (each: `id`, `name`, `state`, `exit_code`); its agent is resumed |

## Agent sessions

Events from research agents also carry `job_id`, since several jobs may run at once.

| Event | Data |
|---|---|
| `agent_session_started` / `agent_session_complete` | `phase`, `turns` |
| `reasoning` | `text`, `turn` |
| `tool_call_start` | `tool_name`, `turn`, `input_preview` |
| `tool_call_result` | `tool_name`, `success`, `duration_seconds`, `result_preview` |
| `permission_denied` | the refused tool call (sandbox) |
| `agent_done` | `turn`, `cost_usd`, `error` |
| `verifier_started` / `verifier_result` | acceptance tests: `attempt`, `passed`, `score` |

## Auditors and the human

| Event | Data |
|---|---|
| `milestone_audit_started` | `completed_phase`, `next_phase`, `attempt` |
| `milestone_audit` | `completed_phase`, `next_phase`, `verdict`, `attempt`, `summary`, `recommendations`, `report_path` |
| `supervisor_continue` / `_advise` / `_interrupt` / `_escalate` / `_withdraw` | `outcome`, `reason`, `suggestion` |
| `human_gate_opened` | `gate_id`, `source`, `title`, `detail`, `options` |
| `human_gate_resolved` | `gate_id`, `action`, `message`, `decided_by`, `source`, `title` |
| `instruction_relayed` | `message` |
| `agent_interrupted` | `reason`, `by` |
| `agent_reply_to_human` | `text` |
| `frontdesk_response` / `frontdesk_tool_call` / `frontdesk_tool_result` / `frontdesk_error` | front-desk activity |
| `skill_suggestion` | `action` (new, revise), `name`, `description`, `kind`, `origin` (human, self), `text`, `reason`, `source_phase`, `source_experiment`; for a revision `skill_id`, `version`, `previous_text`; from a correction `feedback` |
| `skill_learned` | as `skill_suggestion`; the skill was added or revised at once (`skills.auto_accept`) |

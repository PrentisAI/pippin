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
| `proposal_from_researcher` | `proposal_id`, `title`, `text` (the researcher's own proposal, from the screening gate) |
| `screening_complete` | `proposal_id`, `selected`, `score` |
| `planning_complete` | `plan_id`, `num_jobs` |
| `planning_fallback` | `reason` (no valid plan; one job carries out the whole proposal) |
| `job_started` | `job_id`, `title`, `index`, `total` |
| `iteration_complete` | `job_id`, `decision`, `cost_usd`, `errors` |
| `research_loop_complete` | `iterations`, `completed` |
| `analysis_complete` | `loop_back_to` (phase or null) |
| `writing_complete` | `version`, `path` |
| `campaign_completed` / `campaign_failed` / `campaign_stopped` | `status`, `summary`, `cost_usd`, `wall_time_seconds` |

## Agent sessions

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
| `supervisor_continue` / `_advise` / `_interrupt` / `_escalate` | `outcome`, `reason`, `suggestion` |
| `human_gate_opened` | `gate_id`, `source`, `title`, `detail`, `options` |
| `human_gate_resolved` | `gate_id`, `action`, `message`, `decided_by`, `source`, `title` |
| `instruction_relayed` | `message` |
| `agent_interrupted` | `reason`, `by` |
| `agent_reply_to_human` | `text` |
| `frontdesk_response` / `frontdesk_tool_call` / `frontdesk_tool_result` / `frontdesk_error` | front-desk activity |
| `skill_suggestion` | `name`, `rule`, `description`, `source_phase`, `source_experiment` |

import { createContext, Fragment, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { api, describeError } from '../api/client'
import { clip, formatCost, formatDuration } from '../format'
import { GATE_SOURCE_LABELS, phaseLabel, PHASES, type SuggestionOutcome } from '../types'
import { turnKey, type StreamEvent } from '../types/chat'
import { CiteProvider, CiteText, useFlagged } from './Cite'
import { ScopePicker, type ScopeValue } from './SkillScope'

interface AgentStreamViewProps {
  events: StreamEvent[]
  /** Ideation turns being written right now (`ideation_stream` events, latest per turn). */
  live?: StreamEvent[]
  campaignId: string
  experimentId: string
}

// ---------------------------------------------------------------------------
// What each event looks like
// ---------------------------------------------------------------------------

type Tone = 'info' | 'success' | 'warning' | 'error' | 'muted'

/**
 * Where an event is shown:
 * - `agent`: the research agent's column (reasoning, tool calls, tests),
 * - `oversight`: the auditors' and human-gate column,
 * - `system`: a full-width line (phase results, conversation, the end of the run),
 * - `hidden`: not shown.
 */
type Lane = 'agent' | 'oversight' | 'system' | 'hidden'

interface Described {
  lane: Lane
  tone: Tone
  /** One line. */
  text: string
  /** Headline of an oversight card. */
  label?: string
  /** Verdict or decision badge of an oversight card. */
  badge?: string
  /** Further lines (summaries, recommendations, error messages). */
  detail?: string
  /** A button that opens one of the project's tabs (the Literature page). */
  link?: [string, string]
}

const DECIDED_BY_LABELS: Record<string, string> = {
  human: 'you',
  frontdesk: 'the front desk (from your message)',
  autonomous: 'autonomous mode',
  timeout: 'timeout (no answer)',
  system: 'the system',
  auditor: 'the live auditor (it withdrew the question)',
}

const GATE_ACTION_LABELS: Record<string, string> = {
  continue: 'Continue',
  instruct: 'Continue with instruction',
  propose: 'Pursue your own proposal',
  stop: 'Stop',
}

const AUDIT_VERDICT_TONES: Record<string, Tone> = {
  pass: 'success',
  reject: 'error',
  escalate: 'warning',
  missing: 'muted',
}

function str(value: unknown): string {
  if (value == null) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function lines(...parts: unknown[]): string | undefined {
  const text = parts.map(str).filter(Boolean).join('\n')
  return text || undefined
}

function bullets(items: unknown): string {
  return Array.isArray(items) ? items.map((r) => `• ${str(r)}`).join('\n') : str(items)
}

function statusTone(status: string): Tone {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'error'
  return 'warning'
}

function describeEvent(event: StreamEvent): Described {
  const d = event.data ?? {}
  const system = (tone: Tone, text: string, detail?: string): Described => ({ lane: 'system', tone, text, detail })
  const agent = (tone: Tone, text: string, detail?: string): Described => ({ lane: 'agent', tone, text, detail })
  const oversight = (tone: Tone, label: string, text: string, badge?: string, detail?: string): Described =>
    ({ lane: 'oversight', tone, label, text, badge, detail })

  switch (event.event_type) {
    // -- campaign lifecycle --------------------------------------------------
    case 'campaign_started':
      return system('info', 'Campaign started', d.workspace ? `Workspace: ${str(d.workspace)}` : undefined)
    case 'phase_started':
      return system('info', `${phaseLabel(d.phase)} phase`)
    case 'agent_session_started':
      return agent('muted', `${phaseLabel(d.phase) || 'Agent'} session started`)
    case 'agent_session_complete':
      return agent('muted', `${phaseLabel(d.phase) || 'Agent'} session finished — ${d.turns ?? '?'} turns`)
    case 'campaign_completed':
    case 'campaign_failed':
    case 'campaign_stopped':
      return system(statusTone(d.status ?? event.event_type.replace('campaign_', '')),
        `Campaign ${d.status ?? event.event_type.replace('campaign_', '')}`)
    case 'run_finished': {
      const status = str(d.status) || 'finished'
      return system(statusTone(status),
        `Run ${status} — ${formatCost(d.cost_usd)} · ${formatDuration(d.wall_time)}`,
        lines(d.summary, d.report ? `Report: ${str(d.report)}` : ''))
    }
    case 'run_error':
      return system('error', 'The run crashed', str(d.error))
    case 'resources_updated':
      return system('info', `Container limits: ${String(d.cpus)} CPUs, ${Number(d.memory_mb) / 1024} GB`
        + ` (before: ${String(d.old_cpus)} CPUs, ${Number(d.old_memory_mb) / 1024} GB)`)
    case 'budget_warning':
      return system('warning', `Budget ${Math.round(Number(d.share) * 100)}% used`, str(d.detail) || undefined)
    case 'compute_job_submitted':
      return system('info', `Cluster job ${String(d.id)} (${str(d.name)}) `
        + (d.backend === 'ssh'
          ? (d.slurm_id ? `started on ${str(d.node)}${d.gpu_ids ? ` (GPU ${str(d.gpu_ids)})` : ''}: ` : `waiting for GPUs on ${str(d.node)}: `)
          : `submitted as SLURM ${str(d.slurm_id)}: `)
        + `${String(d.gpus)} GPU, up to ${str(d.time_limit)}`, str(d.command) || undefined)
    case 'compute_job_update':
      return system('info', `Cluster job ${String(d.id)} (${str(d.name)}): ${str(d.state).toLowerCase()}`
        + (d.node ? ` on ${str(d.node)}` : '') + (d.reason ? ` [${str(d.reason)}]` : ''))
    case 'waiting_for_cluster_jobs': {
      const jobs = (Array.isArray(d.cluster_jobs) ? d.cluster_jobs : []) as { id: number; name: string }[]
      return agent('muted', `Waiting for cluster job${jobs.length === 1 ? '' : 's'} `
        + jobs.map((j) => `${j.id} (${j.name})`).join(', ')
        + ` — paused, no model calls, its time not counted; next check-in in ${String(d.check_in_minutes)} min`)
    }
    case 'cluster_jobs_awaited':
      return agent('info', d.ended ? 'Resumed: the cluster jobs it waited for have ended' : 'Resumed to look at its cluster jobs')
    case 'compute_job_ended':
      return system(d.state === 'COMPLETED' ? 'success' : 'warning',
        `Cluster job ${String(d.id)} (${str(d.name)}) ${str(d.state).toLowerCase()}`
        + (d.exit_code != null ? `, exit code ${String(d.exit_code)}` : '') + ` after ${formatDuration(Number(d.elapsed_seconds))}`,
        str(d.log) || undefined)
    case 'budget_changed':
      return system('info', `Budget: ${d.new == null ? 'no limit' : formatCost(d.new)} (was ${d.old == null ? 'no limit' : formatCost(d.old)}; spent ${formatCost(d.spent)})`)
    case 'frontdesk_switched':
      return system('info', `Front desk ${d.enabled ? 'on' : 'off'}`)
    case 'autonomy_changed':
      return system('info', `Mode switched: ${d.old_mode ?? '?'} → ${d.new_mode ?? '?'}`)

    // -- phase results -----------------------------------------------------------
    case 'ideation_progress':
      return ideationProgress(d)
    case 'ideation_stream':
      return { lane: 'hidden', tone: 'muted', text: '' }
    case 'mental_model_updated': {
      const hyps = Array.isArray(d.hypotheses) ? (d.hypotheses as Array<Record<string, unknown>>) : []
      const refuted = hyps.filter((h) => h.status === 'refuted').length
      return system('info', `Mental model updated — ${hyps.length} hypotheses${refuted ? `, ${refuted} refuted` : ''}`,
        lines(d.rationale, ...hyps.map((h) => `[${str(h.status)}] ${str(h.statement)}`)))
    }
    case 'mental_model_invalid':
      return system('warning', 'The analysis returned an invalid mental model; the previous one is kept', str(d.reason))
    case 'proposal_from_researcher':
      return system('success',
        d.basis && d.basis !== 'new'
          ? `Your direction: “${str(d.basis_title)}”${d.changes ? ', with your changes' : ''}`
          : `Your direction: ${str(d.title)}`,
        lines(d.interpretation ? `Understood as: ${str(d.interpretation)}` : '',
          d.changes ? `Changes: ${str(d.changes)}` : '', `You wrote: “${str(d.text)}”`,
          Array.isArray(d.rerun) && d.rerun.length ? `Next: ${(d.rerun as string[]).join(' and ')}` : ''))
    case 'screening_skipped':
      return system('info', 'Screening skipped — you chose not to screen your direction')
    case 'ideation_complete':
      return system('success', `Idea: ${str(d.title) || 'untitled'}`, str(d.hypothesis) || undefined)
    case 'screening_complete': {
      const score = typeof d.score === 'number' ? ` (score ${d.score.toFixed(2)})` : ''
      if (!d.selected) return system('warning', `Screening selected no idea${score}`)
      const what = typeof d.selected === 'string' ? `"${d.selected}"` : 'an idea'
      return system('success', `Screening selected ${what}${score}`)
    }
    case 'preflight_complete': {
      const jobs = Array.isArray(d.jobs) ? (d.jobs as Array<Record<string, unknown>>) : []
      const problems = Number(d.problems) || 0
      return system(problems ? 'warning' : 'success',
        `Pre-flight: ${String(d.ok ?? 0)} ok, ${problems} with problems, ${String(d.untestable ?? 0)} untestable`
          + (Number(d.revisions) ? ` (after ${String(d.revisions)} revision${Number(d.revisions) === 1 ? '' : 's'})` : ''),
        lines(d.summary, ...jobs.map((j) => `[${str(j.status)}] ${str(j.job_id)}: ${str(j.smoke_run) || '—'}`
          + (j.estimate ? ` · full run: ${str(j.estimate)}` : '')
          + (Array.isArray(j.problems) && j.problems.length ? `\n    problems: ${(j.problems as unknown[]).map(str).join('; ')}` : ''))))
    }
    case 'preflight_revision':
      return system('warning', 'Pre-flight found problems; planning revises the plan', bullets(d.problems))
    case 'planning_complete':
      return system('success', `Plan ready — ${d.num_jobs ?? '?'} job${d.num_jobs === 1 ? '' : 's'}`)
    case 'planning_fallback':
      return system('warning', 'Planning failed; falling back to a default plan', str(d.reason) || undefined)
    case 'iteration_complete': {
      const ok = d.decision === 'done'
      const errors = Array.isArray(d.errors) && d.errors.length ? bullets(d.errors) : undefined
      return system(ok ? 'success' : 'warning',
        `Job ${str(d.job_id)} ${ok ? 'done' : `ended: ${str(d.decision) || '?'}`} · ${formatCost(d.cost_usd)}`, errors)
    }
    case 'experiment_started':
      return system('muted', `Running ${str(d.skill) || str(d.job_id)}`)
    case 'experiment_complete':
    case 'experiment_failed': {
      const n = Number(d.observations) || 0
      return system(d.success ? 'success' : 'warning',
        `Job ${d.success ? 'succeeded' : 'failed'} — ${n} observation${n === 1 ? '' : 's'}, next: ${str(d.decision) || '?'}`)
    }
    case 'research_loop_complete':
      return system('success', 'Research loop finished')
    case 'analysis_complete':
      return d.loop_back_to
        ? system('info', `Analysis: back to ${phaseLabel(d.loop_back_to)} for another round`)
        : system('success', 'Analysis complete — results are sufficient')
    case 'analysis_verdict_missing':
      return system('warning', 'The analysis gave no verdict; continuing')
    case 'writing_complete':
      return system('success', `Report written (v${d.version ?? '?'})`, d.path ? str(d.path) : undefined)
    case 'report_missing':
      return system('warning', 'No report was written', d.path ? str(d.path) : undefined)
    case 'skill_suggestion':
      return system('info', `Skill suggested: ${str(d.name)}`, str(d.description) || undefined)
    case 'skill_learned':
      return system('success', `Skill ${d.action === 'revise' ? 'revised' : 'added'}: ${str(d.name)}`
        + ` (${d.origin === 'human' ? 'from your correction' : 'learned from this run'})`, str(d.description) || undefined)

    // -- the research agent ----------------------------------------------------------
    case 'loop_started':
      return agent('muted', 'Agent started')
    case 'reasoning':
      return agent('info', str(d.text))
    case 'tool_call_start':
      return agent('info', `${d.tool_name ?? 'tool'}${d.input_preview ? `: ${str(d.input_preview)}` : ''}`)
    case 'tool_call_result':
      return agent(d.success === false ? 'warning' : 'info',
        `${d.tool_name ?? 'tool'}: ${d.success === false ? 'failed' : 'ok'}`)
    case 'permission_denied':
      return agent('warning', 'Permission denied — the agent tried something outside its sandbox',
        d.detail ? clip(str(d.detail), 400) : undefined)
    case 'agent_stderr':
      return agent('muted', `stderr: ${str(d.text)}`)
    case 'agent_done':
      return d.error
        ? agent('error', `Agent stopped with an error · ${formatCost(d.cost_usd)}`, str(d.error))
        : agent('success', `Agent finished · ${formatCost(d.cost_usd)}`)
    case 'verifier_started':
      return agent('muted', 'Running the acceptance tests…')
    case 'verifier_result': {
      const score = typeof d.score === 'number' ? ` (score ${d.score.toFixed(2)})` : ''
      return agent(d.passed ? 'success' : 'warning', `Acceptance tests ${d.passed ? 'passed' : 'failed'}${score}`)
    }

    // -- oversight ----------------------------------------------------------------
    case 'milestone_audit_started':
      return oversight('muted', 'Milestone auditor',
        `Reviewing ${phaseLabel(d.completed_phase)} before ${phaseLabel(d.next_phase)}` +
        (Number(d.attempt) > 1 ? ` (attempt ${d.attempt})` : ''))
    case 'milestone_audit': {
      const verdict = str(d.verdict) || 'missing'
      const summary = str(d.summary) || (verdict === 'missing' ? 'The auditor wrote no report; proceeding.' : '')
      const recs = Array.isArray(d.recommendations) && d.recommendations.length ? bullets(d.recommendations) : ''
      return oversight(AUDIT_VERDICT_TONES[verdict] ?? 'info',
        `Milestone auditor · ${phaseLabel(d.completed_phase)} → ${phaseLabel(d.next_phase)}`,
        summary, verdict, lines(recs))
    }
    case 'supervisor_continue':
      return oversight('muted', 'Live auditor', str(d.reason) || 'On track', 'continue')
    case 'supervisor_advise':
      return oversight('info', 'Live auditor · advice', str(d.suggestion) || str(d.reason), 'advise',
        d.suggestion ? str(d.reason) || undefined : undefined)
    case 'supervisor_interrupt':
      return oversight('error', 'Live auditor · interrupted the agent', str(d.suggestion) || str(d.reason), 'interrupt',
        d.suggestion ? str(d.reason) || undefined : undefined)
    case 'supervisor_withdraw':
      return oversight('muted', 'Live auditor · question withdrawn', str(d.reason), 'withdraw')
    case 'supervisor_escalate':
      return oversight('warning', 'Live auditor · asks for your decision', str(d.reason), 'escalate',
        str(d.suggestion) || undefined)
    case 'human_gate_opened':
      return oversight('warning', `Decision needed · ${GATE_SOURCE_LABELS[d.source] ?? str(d.source)}`,
        str(d.title), 'waiting', str(d.detail) || undefined)
    case 'human_gate_resolved': {
      const action = str(d.action)
      const by = d.by && (d.decided_by === 'human' || d.decided_by === 'frontdesk')
        ? (d.decided_by === 'human' ? str(d.by) : `the front desk (from ${str(d.by)}’s message)`)
        : DECIDED_BY_LABELS[d.decided_by] ?? str(d.decided_by)
      return oversight(action === 'stop' ? 'error' : 'success',
        `Decision · ${GATE_SOURCE_LABELS[d.source] ?? str(d.source)}`,
        `${GATE_ACTION_LABELS[action] ?? action}, decided by ${by}`, action,
        lines(d.title, d.message ? `“${str(d.message)}”` : ''))
    }

    // -- the researcher and the front desk -------------------------------------------
    case 'chat_message':
      return system('info', `${d.role === 'user' ? (d.by ? str(d.by) : 'You') : 'Assistant'}: ${str(d.text)}`)
    case 'frontdesk_response':
      return system('info', `Front desk: ${str(d.text)}`)
    case 'frontdesk_tool_call':
      return system('muted', `Front desk → ${str(d.tool_name)}`)
    case 'frontdesk_tool_result':
      return { lane: 'hidden', tone: 'muted', text: '' }
    case 'frontdesk_error':
      return system('error', 'Front desk error', str(d.error))
    case 'instruction_relayed':
      return system('info', `Relayed to the agent: “${clip(str(d.message), 300)}”`)
    case 'instruction_queued':
      return system('info', `Instruction saved for the planning and research agents: “${clip(str(d.message), 300)}”`)
    case 'agent_interrupted':
      return system('warning', 'Agent interrupted', str(d.reason) || undefined)
    case 'agent_reply_to_human':
      return system('info', `Agent replied: ${str(d.text)}`)

    // -- literature ------------------------------------------------------------------
    case 'literature_search': {
      const failed = Array.isArray(d.failed) && d.failed.length ? ` (${(d.failed as string[]).join(', ')} did not answer)` : ''
      return system('muted', `${str(d.by)} searched the literature: “${clip(str(d.query), 120)}” · ${String(d.found ?? 0)} papers${failed}`,
        str(d.context) || undefined)
    }
    case 'references_checked': {
      const verified = Array.isArray(d.verified) ? (d.verified as string[]) : []
      if (!d.flagged && !(Array.isArray(d.unknown) && d.unknown.length)) {
        return system('muted', `${str(d.by)}: ${verified.length} reference${verified.length === 1 ? '' : 's'} checked, all found`, verified.join(', ') || undefined)
      }
      return { lane: 'hidden', tone: 'muted', text: '' }
    }
    case 'reference_flagged': {
      const what = d.status === 'mismatch' ? 'its identifier names another paper' : d.status === 'unchecked' ? 'could not be checked yet' : 'not found in any index'
      return {...system('warning', `${str(d.by)} cited “${clip(str(d.label), 120)}”: ${what}`,
        [str(d.reason), d.closest_key ? `Closest real paper: [${str(d.closest_key)}]` : '',
          'What it supports is treated as unsupported.'].filter(Boolean).join('\n')), link: ['Fix it in Literature', 'literature']}
    }

    default:
      return system('muted', event.event_type.replace(/_/g, ' '))
  }
}

// ---------------------------------------------------------------------------
// Timeline rows
// ---------------------------------------------------------------------------

type TimelineRow =
  | { kind: 'stage'; phase: string }
  | { kind: 'job'; badge: string; title: string }
  | { kind: 'system'; event: StreamEvent; info: Described }
  | { kind: 'skill'; event: StreamEvent }
  | { kind: 'meeting'; event: StreamEvent }
  | { kind: 'screening'; event: StreamEvent; index: number }
  | { kind: 'event-row'; ts: number; agentEvents: StreamEvent[]; oversightEvents: StreamEvent[]; isLatest: boolean; hasAlert: boolean }

function buildTimeline(events: StreamEvent[]): TimelineRow[] {
  const rows: TimelineRow[] = []
  let agentBatch: StreamEvent[] = []
  let oversightBatch: StreamEvent[] = []
  let batchTs = 0

  function flush(isLatest: boolean) {
    if (agentBatch.length === 0 && oversightBatch.length === 0) return
    const hasAlert = [...agentBatch, ...oversightBatch].some((e) => {
      const tone = describeEvent(e).tone
      return tone === 'error' || tone === 'warning'
    })
    rows.push({ kind: 'event-row', ts: batchTs, agentEvents: agentBatch, oversightEvents: oversightBatch, isLatest, hasAlert })
    agentBatch = []
    oversightBatch = []
  }

  for (const [index, evt] of events.entries()) {
    const ts = evt.timestamp || 0

    if (evt.event_type === 'screening_complete' && evt.data?.title) {
      flush(false)
      rows.push({ kind: 'screening', event: evt, index })
      continue
    }

    if (evt.event_type === 'phase_started') {
      flush(false)
      rows.push({ kind: 'stage', phase: str(evt.data?.phase) })
      continue
    }
    if (evt.event_type === 'job_started') {
      flush(false)
      const d = evt.data ?? {}
      const badge = d.index != null ? `Job ${d.index}/${d.total ?? '?'}` : 'Job'
      const alongside = Array.isArray(d.parallel_with) && d.parallel_with.length
        ? ` · alongside ${(d.parallel_with as string[]).join(', ')}` : ''
      rows.push({ kind: 'job', badge, title: `${str(d.job_id)}: ${str(d.title) || str(d.job_id)}${alongside}` })
      continue
    }

    if (evt.event_type === 'ideation_turn') {
      flush(false)
      rows.push({ kind: 'meeting', event: evt })
      continue
    }

    if (evt.event_type === 'skill_suggestion') {
      flush(false)
      rows.push({ kind: 'skill', event: evt })
      continue
    }

    const info = describeEvent(evt)
    if (info.lane === 'hidden') continue
    if (info.lane === 'system') {
      flush(false)
      rows.push({ kind: 'system', event: evt, info })
      continue
    }

    // Start a new row when the agent's turn or job changes, or after a pause of more than 30 s
    const prevTurn = agentBatch[0]?.turn
    const newTurn = evt.turn !== undefined && prevTurn !== undefined && evt.turn !== prevTurn
    const gap = ts > 0 && batchTs > 0 && ts - batchTs > 30
    const otherJob = info.lane === 'agent' && agentBatch.length > 0 && (evt.data?.job_id ?? '') !== (agentBatch[0].data?.job_id ?? '')
    if (newTurn || gap || otherJob) flush(false)

    batchTs = ts || batchTs
    if (info.lane === 'oversight') oversightBatch.push(evt)
    else agentBatch.push(evt)
  }

  flush(true)
  return rows
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function formatClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
}

function toolIcon(toolName: string): { cls: string; char: string } {
  const t = toolName.toLowerCase()
  if (t.includes('read') || t.includes('glob') || t.includes('grep')) return { cls: 'read', char: '\u{1F4C4}' }
  if (t.includes('edit') || t.includes('write')) return { cls: 'edit', char: '✎' }
  if (t.includes('think') || t.includes('todo')) return { cls: 'think', char: '\u{1F9E0}' }
  return { cls: 'bash', char: '$' }
}

const TONE_ICONS: Record<Tone, { cls: string; char: string }> = {
  success: { cls: 'green', char: '✓' },
  info: { cls: 'blue', char: '▶' },
  warning: { cls: 'amber', char: '⚠' },
  error: { cls: 'red', char: '✕' },
  muted: { cls: 'blue', char: '·' },
}

/** Pair each tool_call_result with its tool_call_start; starts without a result are still running. */
function pairToolCalls(events: StreamEvent[]): StreamEvent[] {
  const out: StreamEvent[] = []
  const pending = new Map<string, StreamEvent[]>()
  for (const e of events) {
    const name = str(e.data?.tool_name)
    if (e.event_type === 'tool_call_start') {
      pending.set(name, [...(pending.get(name) ?? []), e])
    } else {
      pending.get(name)?.shift()
      out.push(e)
    }
  }
  for (const starts of pending.values()) out.push(...starts)
  return out
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ToolCallRow({ event }: { event: StreamEvent }) {
  const [open, setOpen] = useState(false)
  const d = event.data
  const toolName = str(d.tool_name) || 'tool'
  const icon = toolIcon(toolName)
  const running = event.event_type === 'tool_call_start'
  const failed = d.success === false

  return (
    <div className={`tool-call${open ? ' open' : ''}`} onClick={(e) => { e.stopPropagation(); setOpen(!open) }}>
      <div className="tool-call-header">
        <span className={`tool-icon ${icon.cls}`}>{icon.char}</span>
        <span className="tool-name">{toolName}</span>
        <span className="tool-summary">
          {running ? str(d.input_preview) : failed ? `failed${d.result_preview ? ` — ${str(d.result_preview)}` : ''}` : str(d.result_preview)}
        </span>
        <span className="tool-dur">
          {running ? <span className="spinner" /> : d.duration_seconds ? `${Number(d.duration_seconds).toFixed(1)}s` : ''}
        </span>
      </div>
      {!running && d.result_preview && <div className="tool-output">{str(d.result_preview)}</div>}
    </div>
  )
}

function AgentBlock({ events }: { events: StreamEvent[] }) {
  const [open, setOpen] = useState(false)

  const isTool = (e: StreamEvent) => e.event_type === 'tool_call_start' || e.event_type === 'tool_call_result'
  const reasoning = events.filter((e) => e.event_type === 'reasoning')
  const tools = pairToolCalls(events.filter(isTool))
  const others = events.filter((e) => e.event_type !== 'reasoning' && !isTool(e))

  const running = events[events.length - 1]?.event_type === 'tool_call_start'
  const first = reasoning[0] ?? events.find(isTool) ?? others[0]
  const label = first ? clip(describeEvent(first).text, 80) : 'Agent activity'
  const totalSeconds = events.reduce(
    (sum, e) => sum + (e.event_type === 'tool_call_result' ? Number(e.data?.duration_seconds) || 0 : 0), 0)

  const job = str(events[0]?.data?.job_id)
  return (
    <div className={`agent-block${open ? ' open' : ''}`}>
      <div className="agent-block-header" onClick={() => setOpen(!open)}>
        {job && <span className="agent-job">{job}</span>}
        <span className="agent-label">{label}</span>
        <span className={`agent-status ${running ? 'running' : 'done'}`}>
          {running ? <span className="spinner" /> : totalSeconds > 0 ? `${totalSeconds.toFixed(0)}s` : ''}
        </span>
        <span className="agent-chevron">{'▶'}</span>
      </div>
      <div className="agent-block-body">
        {reasoning.map((e, i) => (
          <div key={`r-${i}`} className="agent-text thinking">{str(e.data?.text)}</div>
        ))}
        {tools.map((e, i) => <ToolCallRow key={`t-${i}`} event={e} />)}
        {others.map((e, i) => {
          const info = describeEvent(e)
          return (
            <div key={`o-${i}`} className={`agent-text tone-${info.tone}`}>
              <CiteText text={info.text} />
              {info.detail && <div className="tl-detail"><CiteText text={info.detail} /></div>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function OversightCard({ event }: { event: StreamEvent }) {
  const info = describeEvent(event)
  return (
    <div className={`auditor-verdict tone-${info.tone}`}>
      <div className="auditor-content">
        <div className="auditor-label">
          {info.label}
          {info.badge && <span className={`verdict-badge tone-${info.tone}`}>{info.badge}</span>}
        </div>
        {info.text && <div className="auditor-text">{info.text}</div>}
        {info.detail && <div className="auditor-meta tl-detail">{info.detail}</div>}
      </div>
    </div>
  )
}

function openTab(campaignId: string, tab: string) {
  window.dispatchEvent(new CustomEvent('aurora:open-project-tab', { detail: { campaignId, tab } }))
}

function SystemEventRow({ info, campaignId }: { info: Described; campaignId: string }) {
  const icon = TONE_ICONS[info.tone]
  return (
    <div className="tl-stage-row">
      <div className={`sys-event tl-sys tone-${info.tone}`}>
        <div className={`ev-icon ${icon.cls}`}>{icon.char}</div>
        <div className="tl-sys-body">
          <span><CiteText text={info.text} /></span>
          {info.detail && <div className="tl-detail"><CiteText text={info.detail} /></div>}
          {info.link && <button className="link-btn tl-link" onClick={() => openTab(campaignId, info.link![1])}>{info.link[0]}</button>}
        </div>
      </div>
    </div>
  )
}

/** Screening's verdict on the proposal: novelty, the closest prior work and how the proposal differs,
 *  and, while the run waits for it, the decision to start or change it. */
function ScreeningCard({ event, index, events, campaignId, experimentId }: {
  event: StreamEvent; index: number; events: StreamEvent[]; campaignId: string; experimentId: string
}) {
  const d = event.data ?? {}
  const [changing, setChanging] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const closest = Array.isArray(d.closest) ? (d.closest as Record<string, unknown>[]) : []
  const indexes = Array.isArray(d.indexes) ? (d.indexes as string[]) : []
  // the decision waiting right after screening (until the next phase starts)
  const after: StreamEvent[] = []
  for (const e of events.slice(index + 1)) {
    if (e.event_type === 'phase_started') break
    after.push(e)
  }
  const resolved = new Set(after.filter((e) => e.event_type === 'human_gate_resolved').map((e) => str(e.data?.gate_id)))
  const gate = [...after].reverse().find((e) => e.event_type === 'human_gate_opened' && !resolved.has(str(e.data?.gate_id)))
  const next = events.slice(index + 1).find((e) => e.event_type === 'phase_started')
  const decide = async (action: 'continue' | 'instruct', message = '') => {
    if (!gate) return
    setBusy(true)
    setError(null)
    try { await api.experiments.decide(campaignId, experimentId, action, message, str(gate.data?.gate_id)); setChanging(false) }
    catch (e) { setError(describeError(e)) } finally { setBusy(false) }
  }
  const num = (v: unknown) => typeof v === 'number' ? (Math.round(v * 10) / 10).toString() : '–'
  return (
    <div className="tl-stage-row">
      <div className={`screen-card${d.selected ? '' : ' rejected'}`}>
        <div className="screen-head">
          <div>
            <div className="screen-eyebrow">Proposal · after screening</div>
            <div className="screen-title">{str(d.title)}</div>
          </div>
          <div className="screen-novelty">
            <div className="screen-eyebrow">Novelty</div>
            <div className="screen-score">{num(d.novelty)}<span> / 10</span></div>
          </div>
        </div>
        <div className="screen-axes">Feasibility {num(d.feasibility)} · significance {num(d.significance)} · overall {num(d.score)}
          {d.selected ? '' : ' · below the bar: ideation tries again'}</div>
        {closest.length > 0 && (
          <>
            <div className="screen-eyebrow">Closest prior work, and how this differs</div>
            {closest.map((c) => (
              <div key={str(c.key)} className="screen-prior">
                <div className="screen-prior-head"><CiteText text={`[${str(c.key)}]`} /><span className="screen-prior-title">{str(c.title)}</span>
                  <span className="lit-sub">{str(c.byline)}{c.mine ? ' · your paper' : ''}</span></div>
                {c.differs ? <div className="screen-prior-diff">{str(c.differs)}</div> : null}
              </div>
            ))}
            <div className="lit-sub">Novelty is scored against these papers, found in {indexes.length ? `${indexes.length} index${indexes.length === 1 ? '' : 'es'}` : 'the indexes'} and this project’s Literature.</div>
          </>
        )}
        {d.explanation ? <details className="screen-why"><summary>Why these scores</summary><div><CiteText text={str(d.explanation)} /></div></details> : null}
        {error && <div className="modal-error">{error}</div>}
        {changing && (
          <form className="screen-change" onSubmit={(e) => { e.preventDefault(); if (text.trim()) decide('instruct', text.trim()) }}>
            <textarea autoFocus rows={2} placeholder="What should change? Planning follows what you write." value={text} onChange={(e) => setText(e.target.value)} />
            <div className="bs-row"><button className="btn-sm btn-approve" disabled={busy || !text.trim()}>Send</button>
              <button type="button" className="btn-sm" onClick={() => setChanging(false)}>Cancel</button></div>
          </form>
        )}
        <div className="screen-actions">
          {gate && !changing && <>
            <button className="btn-sm btn-approve" disabled={busy} onClick={() => decide('continue')}>Start the experiment</button>
            <button className="btn-sm" disabled={busy} onClick={() => setChanging(true)}>Change it</button>
          </>}
          {!gate && next && <span className="lit-sub">{str(next.data?.phase) === 'planning' ? 'Planning started from it.' : `Then: ${phaseLabel(str(next.data?.phase)) || str(next.data?.phase)}.`}</span>}
          <button className="btn-sm lit-btn" onClick={() => openTab(campaignId, 'literature')}>All references</button>
        </div>
      </div>
    </div>
  )
}

const TURN_LABELS: Record<string, string> = {
  presentation: 'Presentation',
  remarks: 'Remarks on {item}',
  response: 'Answers the remarks',
  open: 'Open discussion',
}

function isPass(text: string): boolean {
  return /^\W*pass\W*$/i.test(text.replace(/HAND:\s*(yes|no)/i, '').trim())
}

/** Text that scrolls to its end as it grows (a turn being written). */
function LiveText({ text, className }: { text: string; className: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [text])
  return <div ref={ref} className={className}>{text}</div>
}

/** One turn of a seat in the ideation meeting: live while being written, then collapsible. */
function MeetingTurn({ event, live = false }: { event: StreamEvent; live?: boolean }) {
  const [open, setOpen] = useState(false)
  const d = event.data ?? {}
  const text = str(live ? d.text : d.content)
  const model = str(d.model).split('/').pop() || str(d.model)
  const kind = (TURN_LABELS[str(d.kind)] ?? str(d.kind)).replace('{item}', str(d.item))
  const round = Number(d.round) > 1 ? ` · round ${String(d.round)}` : ''
  const error = str(d.error)
  const passed = !live && !error && isPass(text)
  const summary = error ? `failed: ${error}`
    : passed ? 'passes'
    : str(d.headline) || clip(text.replace(/\s+/g, ' ').trim(), 180)
  const expandable = !live && !passed && !error && text.length > 0

  return (
    <div className="tl-stage-row meeting-row">
      <div className={`meeting-turn${live ? ' live' : ''}${open ? ' open' : ''}${passed || error ? ' quiet' : ''}${error ? ' failed' : ''}`}>
        <div className={`meeting-head${expandable ? ' clickable' : ''}`} onClick={() => expandable && setOpen(!open)}>
          <span className="seat-tag">{str(d.seat)}</span>
          <span className="meeting-model">{model}</span>
          <span className="meeting-kind">{kind}{round}</span>
          {live && <span className="spinner meeting-spinner" />}
          {expandable && <span className="agent-chevron">{'▶'}</span>}
        </div>
        {live ? (
          text ? <LiveText text={text} className="meeting-body live" />
            : d.thinking ? <LiveText text={str(d.thinking)} className="meeting-body thinking" />
            : <div className="meeting-wait">{d.kind === 'presentation' ? 'Looking into the literature…' : 'Preparing…'}</div>
        ) : open ? (
          <div className="meeting-body"><CiteText text={text} /></div>
        ) : (
          <div className="meeting-summary"><CiteText text={summary} />{d.citations ? <span className="meeting-cites"> · {String(d.citations)} sources</span> : null}</div>
        )}
        {!live && <FlaggedNote text={text} />}
      </div>
    </div>
  )
}

/** Under a turn: the references in it that could not be found. */
function FlaggedNote({ text }: { text: string }) {
  const flagged = useFlagged(text)
  const ctxCampaign = useContext(StreamCampaign)
  if (!flagged.length) return null
  return (
    <div className="meeting-flag">
      <span className="meeting-flag-head">? {flagged.map((f) => `“${clip(f, 60)}”`).join(', ')} could not be found.</span>{' '}
      The meeting was told before the chair decided, and what {flagged.length > 1 ? 'they support is' : 'it supports is'} treated as unsupported.{' '}
      <button className="link-btn" onClick={() => openTab(ctxCampaign, 'literature')}>Fix it in Literature</button>
    </div>
  )
}

const StreamCampaign = createContext('')

type SkillOutcome =
  | { state: 'open' }
  | { state: 'saving' }
  | { state: 'added'; where: string }
  | { state: 'dismissed' }
  | { state: 'error'; message: string }

/** A rule the engine distilled from a phase: add it to the skill library, to this project only, or dismiss it. */
const SKILL_KIND: Record<string, string> = {
  procedure: 'procedure', failure: 'failure mode', convention: 'convention', correction: 'correction',
}

/** A skill learned from your correction or from a run: add it, keep it to this project, or dismiss it;
 * a revision of an existing skill can be applied or dismissed. */
/** A stable ID for a suggestion (events from before suggestion IDs: their time and name). */
function suggestionId(event: StreamEvent): string {
  return str(event.data?.suggestion_id) || `${event.timestamp}-${str(event.data?.name)}`
}

function answeredOutcome(answer: SuggestionOutcome | undefined): SkillOutcome | null {
  if (!answer) return null
  if (answer.state === 'dismissed') return { state: 'dismissed' }
  const where = answer.scope === 'global' ? 'the skill library' : 'this project'
  return { state: 'added', where: `${where} as ${answer.name ?? 'the skill'} v${answer.version ?? 1}` }
}

function SkillSuggestionCard({ event, campaignId, experimentId, answer }: {
  event: StreamEvent
  campaignId: string
  experimentId: string
  /** What was done with it before (from the server), if anything. */
  answer?: SuggestionOutcome
}) {
  const [local, setOutcome] = useState<SkillOutcome>({ state: 'open' })
  const [scope, setScope] = useState<ScopeValue>({ scope: 'global', projects: [] })
  const [editing, setEditing] = useState(false)
  // what was done here now, else what was done before (the answer may arrive after the card is shown)
  const outcome = local.state !== 'open' ? local : (answeredOutcome(answer) ?? local)
  const [showText, setShowText] = useState(false)
  const d = event.data ?? {}
  const [draft, setDraft] = useState(() => ({
    name: str(d.name), description: str(d.description), text: str(d.text) || str(d.rule),
  }))
  const sid = suggestionId(event)
  const revise = d.action === 'revise' && !!d.skill_id
  const fromYou = d.origin === 'human'

  const dismiss = async () => {
    setOutcome({ state: 'dismissed' })
    try {
      await api.skills.dismiss(sid)
    } catch {
      // shown as dismissed anyway; it may reappear after a reload
    }
  }

  const where = (v: ScopeValue) => v.scope === 'global' ? 'the skill library (all projects)'
    : v.scope === 'experiment' ? 'this run only'
    : v.scope === 'campaign' ? 'this project'
    : `${v.projects.length} selected project${v.projects.length === 1 ? '' : 's'}`

  const add = async () => {
    if (!revise && scope.scope === 'projects' && scope.projects.length === 0) {
      setOutcome({ state: 'error', message: 'Choose at least one project' })
      return
    }
    setOutcome({ state: 'saving' })
    try {
      await api.skills.suggest({
        name: draft.name.trim() || str(d.name),
        rule: draft.text,
        description: draft.description,
        source_phase: str(d.source_phase),
        source_experiment: str(d.source_experiment) || experimentId,
        source_campaign: campaignId,
        scope: scope.scope,
        projects: scope.scope === 'projects' ? scope.projects : [],
        origin: str(d.origin),
        kind: str(d.kind),
        ...(revise ? { skill_id: str(d.skill_id), reason: str(d.reason) } : {}),
        suggestion_id: sid,
      })
      setOutcome({ state: 'added', where: revise ? `v${Number(d.version || 1) + 1} of ${str(d.name)}` : where(scope) })
    } catch (err) {
      setOutcome({ state: 'error', message: describeError(err) })
    }
  }

  if (outcome.state === 'dismissed') {
    return <SystemEventRow campaignId={campaignId} info={{ lane: 'system', tone: 'muted', text: `Skill suggestion dismissed: ${str(d.name)}` }} />
  }

  return (
    <div className="tl-stage-row">
      <div className="skill-suggest" style={{ flex: 1 }}>
        <div className="skill-suggest-header">
          <span className="ss-icon">✦</span>
          {revise ? `Revise skill: ${str(d.name)} (v${String(d.version || 1)} → v${Number(d.version || 1) + 1})` : `New skill: ${str(d.name)}`}
          {d.kind && <span className="ss-phase">{SKILL_KIND[str(d.kind)] ?? str(d.kind)}</span>}
          <span className="ss-phase">{fromYou ? 'from your correction' : `learned in ${phaseLabel(d.source_phase) || 'this run'}`}</span>
        </div>
        <div className="skill-suggest-body">
          <div className="skill-suggest-rule">{str(d.description)}</div>
          {fromYou && d.feedback && <div className="skill-suggest-source">You said: “{clip(str(d.feedback), 300)}”</div>}
          {d.reason && <div className="skill-suggest-source">Why: {str(d.reason)}</div>}
          {editing ? (
            <div className="skill-edit">
              {!revise && <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="name" />}
              <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="one line: what it is for" />
              <textarea rows={14} value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
              {revise && (
                <details className="skill-history"><summary>Current text (v{String(d.version || 1)})</summary><pre>{str(d.previous_text)}</pre></details>
              )}
            </div>
          ) : (
            <>
              <button className="proposal-detail-toggle" onClick={() => setShowText(!showText)}>
                {showText ? '▾' : '▸'} {revise ? 'New and current text' : 'The skill'}
              </button>
              {showText && (
                <div className={revise ? 'skill-compare' : ''}>
                  <pre className="skill-text">{draft.text}</pre>
                  {revise && <pre className="skill-text old">{str(d.previous_text)}</pre>}
                </div>
              )}
            </>
          )}
          {outcome.state !== 'added' && !revise && (
            <ScopePicker value={scope} onChange={setScope} run={experimentId} project={campaignId} idPrefix={sid} />
          )}
          {outcome.state === 'added' ? (
            <div className="skill-suggest-source" style={{ color: 'var(--green)', marginBottom: 0 }}>✓ Added to {outcome.where}</div>
          ) : (
            <div className="skill-suggest-actions">
              <button className="ss-accept" disabled={outcome.state === 'saving'} onClick={add}>
                {revise ? (editing ? 'Apply edited revision' : 'Apply revision') : (editing ? 'Add edited skill' : 'Add')}
              </button>
              <button className="ss-edit" disabled={outcome.state === 'saving'} onClick={() => { setEditing(!editing); setShowText(false) }}>
                {editing ? 'Done editing' : 'Edit first'}
              </button>
              <button className="ss-dismiss" disabled={outcome.state === 'saving'} onClick={dismiss}>Dismiss</button>
            </div>
          )}
          {outcome.state === 'error' && <div className="tl-detail tone-error">Could not save the skill: {outcome.message}</div>}
        </div>
      </div>
    </div>
  )
}

function stageDescription(phase: string): string {
  return PHASES.find((p) => p.key === phase)?.desc ?? ''
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function AgentStreamView({ events, live = [], campaignId, experimentId }: AgentStreamViewProps) {
  const endRef = useRef<HTMLDivElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  // Follow new output only while the view is scrolled to the end, so reading earlier rows is not interrupted
  const followRef = useRef(true)
  const [timeWidth, setTimeWidth] = useState(44)
  const [mainPct, setMainPct] = useState(55)
  const timelineRows = useMemo(() => buildTimeline(events), [events])
  // what was done with the skill suggestions in the stream, from the server (one request)
  const suggestionIds = useMemo(
    () => events.filter((e) => e.event_type === 'skill_suggestion').map(suggestionId).join(','), [events])
  const [answers, setAnswers] = useState<Record<string, SuggestionOutcome>>({})
  useEffect(() => {
    if (!suggestionIds) return
    let cancelled = false
    api.skills.outcomes(suggestionIds.split(','))
      .then((got) => { if (!cancelled) setAnswers(got) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [suggestionIds])
  const liveLength = live.reduce((n, e) => n + str(e.data?.text).length + str(e.data?.thinking).length, 0)

  useEffect(() => {
    if (followRef.current) endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [events.length, live.length, liveLength])

  const onScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }, [])

  const gridCols = `${timeWidth}px ${mainPct}fr ${100 - mainPct}fr`

  const startDrag = useCallback((onMove: (ev: MouseEvent) => void) => {
    document.body.classList.add('resizing')
    const onUp = () => {
      document.body.classList.remove('resizing')
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [])

  const handleTimeResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const wrap = wrapRef.current
    if (!wrap) return
    const left = wrap.getBoundingClientRect().left
    startDrag((ev) => setTimeWidth(Math.max(36, Math.min(120, ev.clientX - left))))
  }, [startDrag])

  const handleColResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const wrap = wrapRef.current
    if (!wrap) return
    const rect = wrap.getBoundingClientRect()
    const start = rect.left + timeWidth
    const width = rect.width - timeWidth
    startDrag((ev) => setMainPct(Math.max(15, Math.min(85, ((ev.clientX - start) / width) * 100))))
  }, [startDrag, timeWidth])

  if (events.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '48px 0', color: 'var(--fg-dim)' }}>
        <span className="spinner" style={{ width: 20, height: 20, marginBottom: 12 }} />
        <span style={{ fontSize: 12 }}>Waiting for agent activity...</span>
      </div>
    )
  }

  return (
    <StreamCampaign.Provider value={campaignId}><CiteProvider key={campaignId} campaignId={campaignId}>
    <div ref={wrapRef} style={{ display: 'flex', flexDirection: 'column', height: '100%', position: 'relative' }}>
      {/* Column headers */}
      <div className="tl-col-headers" style={{ gridTemplateColumns: gridCols }}>
        <div style={{ padding: '6px 4px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <span style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--fg-dim)' }}>Time</span>
        </div>
        <div className="tl-main-h" style={{ padding: '6px 10px', borderLeft: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className="agent-tag main" style={{ fontSize: 9 }}>Research agent</span>
        </div>
        <div className="tl-sup-h" style={{ padding: '6px 10px', borderLeft: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className="agent-tag auditor" style={{ fontSize: 9 }}>Auditors &amp; decisions</span>
        </div>
      </div>

      {/* Draggable column borders */}
      <div className="tl-col-resize" style={{ left: timeWidth }} onMouseDown={handleTimeResize} />
      <div
        className="tl-col-resize"
        style={{ left: `calc(${timeWidth}px + (100% - ${timeWidth}px) * ${mainPct / 100})` }}
        onMouseDown={handleColResize}
      />

      {/* Scrollable grid */}
      <div style={{ flex: 1, overflowY: 'auto' }} onScroll={onScroll}>
        <div className="timeline-grid" style={{ gridTemplateColumns: gridCols }}>
          {timelineRows.map((row, i) => {
            switch (row.kind) {
              case 'stage':
                return (
                  <div key={i} className="tl-stage-row">
                    <span className={`tl-stage-badge ${row.phase}`}>{phaseLabel(row.phase) || 'Phase'}</span>
                    <span className="tl-stage-label">{stageDescription(row.phase)}</span>
                  </div>
                )
              case 'job':
                return (
                  <div key={i} className="tl-round-row">
                    <span className="tl-round-badge">{row.badge}</span>
                    <span className="tl-round-label">{row.title}</span>
                  </div>
                )
              case 'system':
                return <SystemEventRow key={i} info={row.info} campaignId={campaignId} />
              case 'meeting':
                return <MeetingTurn key={i} event={row.event} />
              case 'screening':
                return <ScreeningCard key={i} event={row.event} index={row.index} events={events} campaignId={campaignId} experimentId={experimentId} />
              case 'skill':
                return (
                  <SkillSuggestionCard
                    key={`skill-${suggestionId(row.event)}`}
                    event={row.event}
                    campaignId={campaignId}
                    experimentId={experimentId}
                    answer={answers[suggestionId(row.event)]}
                  />
                )
              case 'event-row': {
                const timeClass = `tl-time${row.isLatest ? ' active' : ''}${row.hasAlert ? ' alert' : ''}`
                const hasAgent = row.agentEvents.length > 0
                const hasOversight = row.oversightEvents.length > 0
                return (
                  <Fragment key={i}>
                    <div className={timeClass}>{row.ts > 0 ? formatClock(row.ts) : ''}</div>
                    <div className={`tl-cell${hasAgent ? '' : ' empty'}`}>
                      {hasAgent && <AgentBlock events={row.agentEvents} />}
                    </div>
                    <div className={`tl-cell${hasOversight ? '' : ' empty'}`}>
                      {row.oversightEvents.map((e, j) => <OversightCard key={j} event={e} />)}
                    </div>
                  </Fragment>
                )
              }
            }
          })}
          {live.map((e) => <MeetingTurn key={`live-${turnKey(e.data ?? {})}`} event={e} live />)}
          <div ref={endRef} />
        </div>
      </div>
    </div>
    </CiteProvider></StreamCampaign.Provider>
  )
}


/** One line per step of the ideation meeting (`ideation_progress` events). */
function ideationProgress(d: Record<string, unknown>): Described {
  const system = (tone: Tone, text: string, detail?: string): Described => ({ lane: 'system', tone, text, detail })
  const ideas = Array.isArray(d.ideas) ? (d.ideas as Array<Record<string, unknown>>) : []
  const seats = Array.isArray(d.seats) ? (d.seats as Array<Record<string, unknown>>) : []
  switch (d.step) {
    case 'preparing':
      return system('muted', `Ideation: ${seats.length} seats prepare their ideas`,
        seats.map((s) => `${str(s.seat)}: ${str(s.model)}`).join('\n'))
    case 'prepared':
      return system('info', `Ideation: ${ideas.length} ideas ready${d.failed ? ` (${String(d.failed)} seats failed)` : ''}`,
        ideas.map((i) => `${str(i.seat)} (${str(i.model)}): ${str(i.headline)}`).join('\n'))
    case 'agenda_item':
      return system('muted', `Meeting item ${String(d.item)}/${String(d.of)}: ${str(d.presenter)} presents`)
    case 'item_notes':
      return system('muted', `Chair's notes on ${str(d.presenter)} — ${String(d.threads ?? 0)} threads`, str(d.summary) || undefined)
    case 'open_discussion':
      return system('muted', 'Meeting: open discussion')
    case 'chair_selecting':
      return system('muted', `The chair compares ${String(d.candidates ?? '')} ideas`)
    case 'chair_deciding':
      return system('muted', 'The chair decides the direction')
    case 'resumed':
      return system('info', `Ideation resumed: ${String(d.prepared ?? 0)} ideas kept${d.items_done ? `, ${String(d.items_done)} meeting items already discussed` : ''}`)
    case 'decided':
      return system('success', `Chair chose ${str(d.chosen)}: ${clip(str(d.headline), 200)}`, str(d.rationale) || undefined)
    default:
      return system('muted', `Ideation: ${str(d.step)}`)
  }
}

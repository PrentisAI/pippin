import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, describeError } from '../api/client'
import { clip, formatCost, formatDuration } from '../format'
import { GATE_SOURCE_LABELS, phaseLabel, PHASES } from '../types'
import { turnKey, type StreamEvent } from '../types/chat'

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
      return system('info', `Skill suggested: ${str(d.name)}`, str(d.rule) || undefined)

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
      const by = DECIDED_BY_LABELS[d.decided_by] ?? str(d.decided_by)
      return oversight(action === 'stop' ? 'error' : 'success',
        `Decision · ${GATE_SOURCE_LABELS[d.source] ?? str(d.source)}`,
        `${GATE_ACTION_LABELS[action] ?? action}, decided by ${by}`, action,
        lines(d.title, d.message ? `“${str(d.message)}”` : ''))
    }

    // -- the researcher and the front desk -------------------------------------------
    case 'chat_message':
      return system('info', `${d.role === 'user' ? 'You' : 'Assistant'}: ${str(d.text)}`)
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

  for (const evt of events) {
    const ts = evt.timestamp || 0

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
              {info.text}
              {info.detail && <div className="tl-detail">{info.detail}</div>}
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

function SystemEventRow({ info }: { info: Described }) {
  const icon = TONE_ICONS[info.tone]
  return (
    <div className="tl-stage-row">
      <div className={`sys-event tl-sys tone-${info.tone}`}>
        <div className={`ev-icon ${icon.cls}`}>{icon.char}</div>
        <div className="tl-sys-body">
          <span>{info.text}</span>
          {info.detail && <div className="tl-detail">{info.detail}</div>}
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
          <div className="meeting-body">{text}</div>
        ) : (
          <div className="meeting-summary">{summary}{d.citations ? <span className="meeting-cites"> · {String(d.citations)} sources</span> : null}</div>
        )}
      </div>
    </div>
  )
}

type SkillOutcome =
  | { state: 'open' }
  | { state: 'saving' }
  | { state: 'added'; where: string }
  | { state: 'dismissed' }
  | { state: 'error'; message: string }

/** A rule the engine distilled from a phase: add it to the skill library, to this project only, or dismiss it. */
function SkillSuggestionCard({ event, campaignId, experimentId }: {
  event: StreamEvent
  campaignId: string
  experimentId: string
}) {
  const [outcome, setOutcome] = useState<SkillOutcome>({ state: 'open' })
  const d = event.data ?? {}

  const add = async (scope: 'global' | 'campaign') => {
    setOutcome({ state: 'saving' })
    try {
      await api.skills.suggest({
        name: str(d.name),
        rule: str(d.rule),
        description: str(d.description),
        source_phase: str(d.source_phase),
        source_experiment: str(d.source_experiment) || experimentId,
        ...(scope === 'campaign' ? { source_campaign: campaignId } : {}),
        scope,
      })
      setOutcome({ state: 'added', where: scope === 'global' ? 'the skill library' : 'this project' })
    } catch (err) {
      setOutcome({ state: 'error', message: describeError(err) })
    }
  }

  if (outcome.state === 'dismissed') {
    return <SystemEventRow info={{ lane: 'system', tone: 'muted', text: `Skill suggestion dismissed: ${str(d.name)}` }} />
  }

  return (
    <div className="tl-stage-row">
      <div className="skill-suggest" style={{ flex: 1 }}>
        <div className="skill-suggest-header">
          <span className="ss-icon">✦</span>
          Suggested skill: {str(d.name)}
          {d.source_phase && <span className="ss-phase">{phaseLabel(d.source_phase)}</span>}
        </div>
        <div className="skill-suggest-body">
          <div className="skill-suggest-rule">{str(d.rule)}</div>
          {outcome.state === 'added' ? (
            <div className="skill-suggest-source" style={{ color: 'var(--green)', marginBottom: 0 }}>✓ Added to {outcome.where}</div>
          ) : (
            <div className="skill-suggest-actions">
              <button className="ss-accept" disabled={outcome.state === 'saving'} onClick={() => add('global')}>Add to library</button>
              <button className="ss-edit" disabled={outcome.state === 'saving'} onClick={() => add('campaign')}>Only this project</button>
              <button className="ss-dismiss" disabled={outcome.state === 'saving'} onClick={() => setOutcome({ state: 'dismissed' })}>Dismiss</button>
            </div>
          )}
          {outcome.state === 'error' && <div className="tl-detail tone-error">Could not add the skill: {outcome.message}</div>}
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
                return <SystemEventRow key={i} info={row.info} />
              case 'meeting':
                return <MeetingTurn key={i} event={row.event} />
              case 'skill':
                return (
                  <SkillSuggestionCard
                    key={`skill-${row.event.timestamp}-${str(row.event.data?.name)}`}
                    event={row.event}
                    campaignId={campaignId}
                    experimentId={experimentId}
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

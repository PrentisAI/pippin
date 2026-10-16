import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { StreamEvent } from '../types/chat'

interface AgentStreamViewProps {
  events: StreamEvent[]
}

// ---------------------------------------------------------------------------
// Classification helpers
// ---------------------------------------------------------------------------

const SUPERVISOR_EVENTS = new Set([
  'watchdog_interrupt', 'watchdog_cycle_start', 'watchdog_verdict',
  'watchdog_tool_call', 'watchdog_escalation', 'watchdog_checkpoint',
  'escalation', 'escalation_resolved', 'cerebellum_response',
])

const SYSTEM_EVENTS = new Set([
  'phase_started', 'phase_ended',
  'iteration_started', 'iteration_complete',
  'campaign_started', 'campaign_completed', 'campaign_failed',
  'autonomy_changed', 'workspace_extracted',
  'screening_complete', 'planning_complete', 'analysis_complete', 'writing_complete',
])

function isSupervisor(type: string): boolean {
  return SUPERVISOR_EVENTS.has(type)
}

function isSystemEvent(type: string): boolean {
  return SYSTEM_EVENTS.has(type)
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
}

function toolIconClass(toolName: string): string {
  const t = toolName.toLowerCase()
  if (t.includes('bash') || t.includes('shell') || t.includes('exec')) return 'bash'
  if (t.includes('read') || t.includes('file')) return 'read'
  if (t.includes('edit') || t.includes('write')) return 'edit'
  if (t.includes('think') || t.includes('reason')) return 'think'
  return 'bash'
}

function toolIconChar(toolName: string): string {
  const t = toolName.toLowerCase()
  if (t.includes('bash') || t.includes('shell') || t.includes('exec')) return '$'
  if (t.includes('read') || t.includes('file')) return '\u{1F4C4}'
  if (t.includes('edit') || t.includes('write')) return '✎'
  if (t.includes('think') || t.includes('reason')) return '\u{1F9E0}'
  return '$'
}

function stageBadgeClass(phase: string): string {
  const p = phase.toLowerCase().replace(/_/g, '')
  if (p.includes('screen')) return 'screening'
  if (p.includes('plan')) return 'planning'
  if (p.includes('research') || p.includes('loop')) return 'research'
  if (p.includes('analy')) return 'analysis'
  if (p.includes('complet') || p.includes('done')) return 'completed'
  if (p.includes('roundtable')) return 'roundtable'
  return 'research'
}

function stageLabel(phase: string): string {
  const p = phase.replace(/_/g, ' ')
  switch (p.toLowerCase()) {
    case 'screening': return 'Task feasibility check'
    case 'planning': return 'Execution DAG generation'
    case 'research loop': case 'researcher': return 'Agent execution + verifier'
    case 'analysis': return 'Results analysis'
    case 'writing': return 'Manuscript generation'
    case 'roundtable': return 'Multi-agent discussion'
    case 'preflight': return 'Pre-condition checks'
    default: return ''
  }
}

function formatEventText(event: StreamEvent): string {
  const d = event.data
  switch (event.event_type) {
    case 'loop_started':
      return 'Agent started'
    case 'turn_start':
      return `Turn ${d.turn ?? '?'}`
    case 'reasoning':
      return d.text ?? ''
    case 'tool_call_start': {
      const tName = d.tool_name ?? '?'
      const inputPreview = d.input_preview ? `: ${d.input_preview}` : ''
      return `${tName}${inputPreview}`
    }
    case 'tool_call_result': {
      const name = d.tool_name ?? '?'
      const ok = d.success === false ? 'FAILED' : 'OK'
      const dur = d.duration_seconds ? ` (${Number(d.duration_seconds).toFixed(1)}s)` : ''
      const preview = d.result_preview ? ` — ${d.result_preview}` : ''
      return `${name}: ${ok}${dur}${preview}`
    }
    case 'tool_progress': {
      const name = d.tool_name ?? ''
      const msg = d.message ?? ''
      const pct = d.progress_pct != null ? ` ${d.progress_pct}%` : ''
      return `${name}: ${msg}${pct}`
    }
    case 'agent_done':
      return d.text ?? 'Agent finished'
    case 'user_message_injected':
      return `User message delivered: "${d.text ?? ''}"`
    case 'agent_stderr':
      return `[stderr] ${d.text ?? ''}`
    case 'error':
      return d.error ?? 'Unknown error'
    case 'loop_finished':
      return `Loop complete — ${d.turns ?? '?'} turns, ${d.tool_calls ?? '?'} tool calls`
    case 'duplex_started':
      return `Duplex mode — Cerebellum: ${d.cerebellum_model ?? '?'}, Brain: ${d.brain_model ?? '?'}`
    case 'duplex_finished':
      return `Duplex complete — ${d.tasks_completed ?? 0}/${d.tasks_total ?? 0} tasks`
    case 'cerebellum_response':
      return d.text ?? ''
    case 'brain_task_started':
      return `Brain task started: ${d.description ?? d.task_id ?? '?'}`
    case 'brain_tool_call_start':
      return `[Brain ${d.task_id ?? ''}] Calling ${d.tool_name ?? '?'}...`
    case 'brain_tool_call_result': {
      const name = d.tool_name ?? '?'
      const ok = d.success ? 'OK' : 'FAILED'
      const dur = d.duration_seconds ? ` (${Number(d.duration_seconds).toFixed(1)}s)` : ''
      return `[Brain ${d.task_id ?? ''}] ${name}: ${ok}${dur}`
    }
    case 'brain_reasoning':
      return `[Brain] ${d.text ?? ''}`
    case 'brain_agent_done':
      return `[Brain ${d.task_id ?? ''}] Task complete`
    case 'brain_loop_finished':
      return `[Brain ${d.task_id ?? ''}] ${d.turns ?? '?'} turns, ${d.tool_calls ?? '?'} tool calls`
    case 'brain_progress':
      return `[Brain ${d.task_id ?? ''}] ${d.tool_name ?? ''}: ${d.result_preview ?? '...'}`
    case 'user_message_received':
      return `User: "${d.text ?? ''}"`
    case 'phase_started': {
      const phaseName = typeof d.phase === 'string'
        ? d.phase.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())
        : `Phase ${d.phase ?? '?'}`
      return phaseName
    }
    case 'phase_ended': {
      const phaseName = typeof d.phase === 'string'
        ? d.phase.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())
        : `Phase ${d.phase ?? '?'}`
      return `${phaseName} — ${d.turns ?? 0} turns, ${d.wall_time ?? 0}s`
    }
    case 'verifier_started':
      return 'Running verifier...'
    case 'verifier_result': {
      const pct = d.reward != null ? `${(d.reward * 100).toFixed(1)}%` : '?'
      return `Verifier: ${d.passed ? 'PASSED' : 'FAILED'} (reward: ${pct})`
    }
    case 'docker_started':
      return `Docker container ${d.container_id ?? '?'} started`
    case 'run_finished': {
      const pct = d.final_reward != null ? `${(d.final_reward * 100).toFixed(1)}%` : '?'
      return `Run complete — ${d.status ?? '?'}, reward: ${pct}, ${d.total_turns ?? 0} turns`
    }
    case 'workspace_extracted':
      return `Workspace saved — ${(d.artifacts_found as string[] ?? []).length} artifacts found`
    case 'run_error':
      return `Run error: ${d.error ?? 'Unknown'}`
    case 'watchdog_interrupt':
      return `${d.message ?? 'Supervisor interrupted the agent'}`
    case 'watchdog_cycle_start':
      return `Cycle ${d.cycle ?? '?'} — analyzing ${d.events_in_window ?? '?'} events`
    case 'watchdog_verdict': {
      const lvl = (d.level ?? 'normal').toUpperCase()
      const conf = d.confidence ?? ''
      return `Verdict: ${lvl}${conf ? ` (${conf})` : ''} — ${d.reason ?? ''}`
    }
    case 'watchdog_tool_call':
      return `Inspecting: ${d.tool_name ?? '?'}`
    case 'watchdog_escalation':
      return `Escalating to L2 (${d.l2_model ?? '?'}) — L1 was ${d.l1_confidence ?? '?'} on ${(d.l1_level ?? '').toUpperCase()}`
    case 'watchdog_checkpoint': {
      const lvl = (d.level ?? 'normal').toUpperCase()
      return `Checkpoint [${d.checkpoint_context ?? ''}]: ${lvl} — ${d.reason ?? ''}`
    }
    case 'escalation':
      return `AWAITING HUMAN DECISION — ${d.source ?? 'watchdog'}: ${d.context ?? ''}`
    case 'escalation_resolved': {
      const action = d.response?.action ?? 'unknown'
      return `Human decided: ${action.toUpperCase()}${d.response?.message ? ` — "${d.response.message}"` : ''}`
    }
    case 'autonomy_changed':
      return `Mode switched: ${d.old_mode ?? '?'} → ${d.new_mode ?? '?'}`
    case 'frontdesk_response':
      return d.text ?? ''
    case 'frontdesk_tool_call':
      return `Calling ${d.tool_name ?? '?'}...`
    case 'frontdesk_tool_result':
      return d.content ?? ''
    case 'frontdesk_error':
      return `Error: ${d.error ?? 'unknown'}`
    case 'campaign_started':
      return `Campaign started`
    case 'campaign_completed':
      return `Campaign completed`
    case 'campaign_failed':
      return `Campaign failed: ${d.error ?? 'unknown error'}`
    case 'iteration_started':
      return `Iteration ${d.iteration_id ?? ''} started (${d.mode ?? 'unknown'} mode)`
    case 'iteration_complete':
      return `Iteration complete — reward: ${d.reward != null ? (d.reward * 100).toFixed(1) + '%' : '?'}`
    case 'screening_complete':
      return `Screening complete — proposal ${d.selected ? 'accepted' : 'rejected'}`
    case 'planning_complete':
      return `Planning complete — ${d.job_count ?? '?'} jobs planned`
    case 'analysis_complete':
      return d.loop_back_to
        ? `Analysis complete — looping back to ${d.loop_back_to}`
        : 'Analysis complete — results sufficient, proceeding to writing'
    case 'writing_complete':
      return `Writing complete — manuscript v${d.version ?? '?'}`
    default:
      return event.event_type
  }
}

// ---------------------------------------------------------------------------
// Timeline row model
// ---------------------------------------------------------------------------

type TimelineRow =
  | { kind: 'stage'; phase: string; label: string }
  | { kind: 'round'; iteration: string; label: string }
  | { kind: 'event-row'; ts: number; mainEvents: StreamEvent[]; supEvents: StreamEvent[]; isLatest: boolean; hasAlert: boolean; streamingText?: string; streamingDone?: boolean }
  | { kind: 'system'; event: StreamEvent; ts: number }

function buildTimeline(events: StreamEvent[]): TimelineRow[] {
  const rows: TimelineRow[] = []
  let currentMainBatch: StreamEvent[] = []
  let currentSupBatch: StreamEvent[] = []
  let currentTs = 0
  let deltaBuffer: string[] = []
  let deltaDone = false

  function flushRow(isLatest: boolean) {
    if (currentMainBatch.length === 0 && currentSupBatch.length === 0 && deltaBuffer.length === 0) return
    const hasAlert = [...currentMainBatch, ...currentSupBatch].some(e =>
      e.event_type === 'escalation' || e.event_type === 'error' || e.event_type === 'run_error' ||
      (e.event_type === 'verifier_result' && !e.data?.passed)
    )
    rows.push({
      kind: 'event-row',
      ts: currentTs,
      mainEvents: [...currentMainBatch],
      supEvents: [...currentSupBatch],
      isLatest,
      hasAlert,
      streamingText: deltaBuffer.length > 0 ? deltaBuffer.join('') : undefined,
      streamingDone: deltaDone,
    })
    currentMainBatch = []
    currentSupBatch = []
    deltaBuffer = []
    deltaDone = false
  }

  for (let i = 0; i < events.length; i++) {
    const evt = events[i]
    const evtTs = evt.timestamp || 0

    if (evt.event_type === 'phase_started') {
      flushRow(false)
      const phase = typeof evt.data?.phase === 'string' ? evt.data.phase : `phase_${evt.data?.phase ?? '?'}`
      rows.push({ kind: 'stage', phase, label: stageLabel(phase) })
      continue
    }

    if (evt.event_type === 'iteration_started') {
      flushRow(false)
      const iterId = evt.data?.iteration_id ?? ''
      const mode = evt.data?.mode ?? ''
      rows.push({ kind: 'round', iteration: iterId, label: mode ? `${mode} mode` : '' })
      continue
    }

    if (isSystemEvent(evt.event_type)) {
      flushRow(false)
      rows.push({ kind: 'system', event: evt, ts: evtTs })
      continue
    }

    if (evt.event_type === 'reasoning_delta' || evt.event_type === 'brain_reasoning_delta') {
      deltaBuffer.push(evt.data?.text ?? '')
      currentTs = evtTs || currentTs
      continue
    }

    if (evt.event_type === 'reasoning' || evt.event_type === 'brain_reasoning') {
      deltaBuffer = []
      deltaDone = true
    }

    // Decide if we need a new row (timestamp gap > 30s or turn change)
    const prevTurn = currentMainBatch.length > 0 ? currentMainBatch[0].turn : currentSupBatch.length > 0 ? currentSupBatch[0].turn : undefined
    const newTurn = evt.turn !== undefined && prevTurn !== undefined && evt.turn !== prevTurn
    const tsGap = evtTs > 0 && currentTs > 0 && (evtTs - currentTs) > 30
    if ((newTurn || tsGap) && (currentMainBatch.length > 0 || currentSupBatch.length > 0)) {
      flushRow(false)
    }

    currentTs = evtTs || currentTs

    if (isSupervisor(evt.event_type)) {
      currentSupBatch.push(evt)
    } else {
      currentMainBatch.push(evt)
    }
  }

  flushRow(true)
  return rows
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ToolCallRow({ event }: { event: StreamEvent }) {
  const [open, setOpen] = useState(false)
  const d = event.data
  const toolName = d.tool_name ?? '?'
  const iconCls = toolIconClass(toolName)
  const iconChar = toolIconChar(toolName)

  if (event.event_type === 'tool_call_start') {
    return (
      <div className={`tool-call${open ? ' open' : ''}`} onClick={(e) => { e.stopPropagation(); setOpen(!open) }}>
        <div className="tool-call-header">
          <span className={`tool-icon ${iconCls}`}>{iconChar}</span>
          <span className="tool-name">{toolName}</span>
          <span className="tool-summary">{d.input_preview ?? ''}</span>
          <span className="tool-dur"><span className="spinner" /></span>
        </div>
      </div>
    )
  }

  const dur = d.duration_seconds ? `${Number(d.duration_seconds).toFixed(1)}s` : ''
  const preview = d.result_preview ?? ''

  return (
    <div className={`tool-call${open ? ' open' : ''}`} onClick={(e) => { e.stopPropagation(); setOpen(!open) }}>
      <div className="tool-call-header">
        <span className={`tool-icon ${iconCls}`}>{iconChar}</span>
        <span className="tool-name">{toolName}</span>
        <span className="tool-summary">{d.success === false ? 'FAILED' : preview}</span>
        <span className="tool-dur">{dur}</span>
      </div>
      {d.result_preview && (
        <div className="tool-output">{d.result_preview}</div>
      )}
    </div>
  )
}

function AgentBlock({ events, streamingText, streamingDone }: {
  events: StreamEvent[]
  streamingText?: string
  streamingDone?: boolean
}) {
  const [open, setOpen] = useState(false)

  const reasoningEvents = events.filter(e => e.event_type === 'reasoning' || e.event_type === 'brain_reasoning')
  const toolEvents = events.filter(e => e.event_type === 'tool_call_start' || e.event_type === 'tool_call_result' || e.event_type === 'tool_progress')
  const otherEvents = events.filter(e =>
    e.event_type !== 'reasoning' && e.event_type !== 'brain_reasoning' &&
    e.event_type !== 'tool_call_start' && e.event_type !== 'tool_call_result' && e.event_type !== 'tool_progress' &&
    e.event_type !== 'turn_start'
  )

  const lastEvent = events[events.length - 1]
  const isRunning = lastEvent?.event_type === 'tool_call_start' || !!streamingText && !streamingDone

  // Find a good label
  let label = ''
  if (reasoningEvents.length > 0) {
    const text = reasoningEvents[0].data?.text ?? ''
    label = text.length > 60 ? text.slice(0, 57) + '...' : text
  } else if (toolEvents.length > 0) {
    const first = toolEvents[0]
    label = `${first.data?.tool_name ?? 'tool'}: ${first.data?.input_preview ?? ''}`
    if (label.length > 60) label = label.slice(0, 57) + '...'
  } else if (otherEvents.length > 0) {
    label = formatEventText(otherEvents[0])
  } else if (streamingText) {
    label = streamingText.length > 60 ? streamingText.slice(0, 57) + '...' : streamingText
  }
  if (!label) label = 'Agent activity'

  const totalDur = events.reduce((sum, e) => {
    if (e.event_type === 'tool_call_result' && e.data?.duration_seconds) {
      return sum + Number(e.data.duration_seconds)
    }
    return sum
  }, 0)

  // Pair tool_call_start with tool_call_result
  const pairedTools: StreamEvent[] = []
  const startMap = new Map<string, StreamEvent>()
  for (const e of toolEvents) {
    if (e.event_type === 'tool_call_start') {
      startMap.set(e.data?.tool_name ?? '', e)
    } else if (e.event_type === 'tool_call_result') {
      startMap.delete(e.data?.tool_name ?? '')
      pairedTools.push(e)
    } else {
      pairedTools.push(e)
    }
  }
  // Any unmatched starts are still in-progress
  for (const e of startMap.values()) {
    pairedTools.push(e)
  }

  return (
    <div className={`agent-block${open ? ' open' : ''}`}>
      <div className="agent-block-header" onClick={() => setOpen(!open)}>
        <span className="agent-label">{label}</span>
        <span className={`agent-status ${isRunning ? 'running' : 'done'}`}>
          {isRunning ? <span className="spinner" /> : totalDur > 0 ? `${totalDur.toFixed(0)}s` : ''}
        </span>
        <span className="agent-chevron">{'▶'}</span>
      </div>
      <div className="agent-block-body">
        {streamingText && (
          <div className="agent-text thinking">
            {streamingText}
            {!streamingDone && <span className="streaming-cursor" />}
          </div>
        )}
        {reasoningEvents.map((e, i) => (
          <div key={`r-${i}`} className="agent-text thinking">{e.data?.text ?? ''}</div>
        ))}
        {pairedTools.map((e, i) => (
          <ToolCallRow key={`t-${i}`} event={e} />
        ))}
        {otherEvents.map((e, i) => (
          <div key={`o-${i}`} className="agent-text">{formatEventText(e)}</div>
        ))}
      </div>
    </div>
  )
}

function SupervisorBlock({ events }: { events: StreamEvent[] }) {
  if (events.length === 0) return null

  return (
    <>
      {events.map((evt, i) => {
        const d = evt.data
        let label = 'Observation'
        let borderColor = ''

        if (evt.event_type === 'watchdog_verdict') {
          const lvl = (d.level ?? 'normal').toLowerCase()
          label = lvl === 'normal' ? 'Observation' : lvl === 'warning' ? 'Warning' : 'Analysis'
        } else if (evt.event_type === 'watchdog_escalation') {
          label = 'Escalation → Front Desk'
          borderColor = 'var(--amber)'
        } else if (evt.event_type === 'escalation') {
          label = 'Escalation → Human'
          borderColor = 'var(--amber)'
        } else if (evt.event_type === 'escalation_resolved') {
          label = 'Resolved'
          borderColor = 'var(--green)'
        } else if (evt.event_type === 'watchdog_interrupt') {
          label = 'Interrupt'
          borderColor = 'var(--red)'
        } else if (evt.event_type === 'cerebellum_response') {
          label = 'Cerebellum'
        } else {
          label = 'Review'
        }

        const text = formatEventText(evt)
        const meta = evt.event_type === 'watchdog_verdict' ? `confidence: ${d.confidence ?? 'high'}` :
          evt.event_type === 'escalation' ? 'awaiting human' :
          evt.event_type === 'escalation_resolved' ? 'resolved' : ''

        return (
          <div
            key={i}
            className="auditor-verdict"
            style={borderColor ? { borderLeftColor: borderColor } : undefined}
          >
            <div className="auditor-content">
              <div className="auditor-label" style={borderColor ? { color: borderColor } : undefined}>{label}</div>
              <div className="auditor-text">{text}</div>
              {meta && <div className="auditor-meta">{meta}</div>}
            </div>
          </div>
        )
      })}
    </>
  )
}

function SystemEventRow({ event }: { event: StreamEvent }) {
  const d = event.data
  const text = formatEventText(event)
  const isSuccess = event.event_type.includes('complete') || event.event_type === 'campaign_completed'
  const isError = event.event_type === 'campaign_failed'
  const iconClass = isError ? 'amber' : isSuccess ? 'green' : 'blue'
  const iconChar = isError ? '⚠' : isSuccess ? '✓' : '▶'

  // Check if it's a phase_ended that should show as a stage row
  if (event.event_type === 'phase_ended') {
    const phase = typeof d.phase === 'string' ? d.phase : 'phase'
    return (
      <div className="tl-stage-row">
        <span className={`tl-stage-badge ${stageBadgeClass(phase)}`}>
          {phase.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())} ✓
        </span>
        <span className="tl-stage-label">{d.turns ?? 0} turns, {d.wall_time ?? 0}s</span>
      </div>
    )
  }

  // Check if it's iteration_complete
  if (event.event_type === 'iteration_complete') {
    const reward = d.reward != null ? `${(d.reward * 100).toFixed(1)}%` : '?'
    return (
      <div className="tl-round-row">
        <span className="tl-round-badge">Complete</span>
        <span className="tl-round-label">reward: {reward}</span>
      </div>
    )
  }

  return (
    <div className="tl-stage-row">
      <div className="sys-event" style={{ fontSize: 10, padding: '4px 8px' }}>
        <div className={`ev-icon ${iconClass}`} style={{ width: 14, height: 14, fontSize: 8 }}>{iconChar}</div>
        <span>{text}</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function AgentStreamView({ events }: AgentStreamViewProps) {
  const endRef = useRef<HTMLDivElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [timeWidth, setTimeWidth] = useState(44)
  const [mainPct, setMainPct] = useState(50)
  const timelineRows = useMemo(() => buildTimeline(events), [events])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  const gridCols = `${timeWidth}px ${mainPct}fr ${100 - mainPct}fr`

  const handleTimeResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const wrap = wrapRef.current
    if (!wrap) return
    const wrapLeft = wrap.getBoundingClientRect().left
    document.body.classList.add('resizing')
    const onMove = (ev: MouseEvent) => {
      setTimeWidth(Math.max(36, Math.min(120, ev.clientX - wrapLeft)))
    }
    const onUp = () => {
      document.body.classList.remove('resizing')
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [])

  const handleColResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const wrap = wrapRef.current
    if (!wrap) return
    const wrapRect = wrap.getBoundingClientRect()
    const flexStart = wrapRect.left + timeWidth
    const flexWidth = wrapRect.width - timeWidth

    document.body.classList.add('resizing')
    const onMove = (ev: MouseEvent) => {
      const pct = ((ev.clientX - flexStart) / flexWidth) * 100
      setMainPct(Math.max(15, Math.min(85, pct)))
    }
    const onUp = () => {
      document.body.classList.remove('resizing')
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [timeWidth])

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
          <span className="agent-tag main" style={{ fontSize: 9 }}>Main Agent</span>
        </div>
        <div className="tl-sup-h" style={{ padding: '6px 10px', borderLeft: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className="agent-tag auditor" style={{ fontSize: 9 }}>Supervisor</span>
        </div>
      </div>

      {/* Draggable column border overlays */}
      <div
        className="tl-col-resize"
        style={{ left: timeWidth }}
        onMouseDown={handleTimeResize}
      />
      <div
        className="tl-col-resize"
        style={{ left: `calc(${timeWidth}px + (100% - ${timeWidth}px) * ${mainPct / 100})` }}
        onMouseDown={handleColResize}
      />

      {/* Scrollable grid */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <div className="timeline-grid" style={{ gridTemplateColumns: gridCols }}>
          {timelineRows.map((row, i) => {
            if (row.kind === 'stage') {
              return (
                <div key={`stage-${i}`} className="tl-stage-row">
                  <span className={`tl-stage-badge ${stageBadgeClass(row.phase)}`}>
                    {row.phase.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())}
                  </span>
                  {row.label && <span className="tl-stage-label">{row.label}</span>}
                </div>
              )
            }

            if (row.kind === 'round') {
              return (
                <div key={`round-${i}`} className="tl-round-row">
                  <span className="tl-round-badge">{row.iteration || 'Iter'}</span>
                  {row.label && <span className="tl-round-label">{row.label}</span>}
                </div>
              )
            }

            if (row.kind === 'system') {
              return <SystemEventRow key={`sys-${i}`} event={row.event} />
            }

            // event-row: 3-column grid row
            const timeClass = `tl-time${row.isLatest ? ' active' : ''}${row.hasAlert ? ' alert' : ''}`
            const hasMain = row.mainEvents.length > 0 || row.streamingText
            const hasSup = row.supEvents.length > 0

            return (
              <Fragment key={`row-${i}`}>
                <div className={timeClass}>{row.ts > 0 ? formatTime(row.ts) : ''}</div>
                <div className={`tl-cell${!hasMain ? ' empty' : ''}`}>
                  {hasMain && (
                    <AgentBlock
                      events={row.mainEvents}
                      streamingText={row.streamingText}
                      streamingDone={row.streamingDone}
                    />
                  )}
                </div>
                <div className={`tl-cell${!hasSup ? ' empty' : ''}`}>
                  {hasSup && <SupervisorBlock events={row.supEvents} />}
                </div>
              </Fragment>
            )
          })}
          <div ref={endRef} />
        </div>
      </div>
    </div>
  )
}


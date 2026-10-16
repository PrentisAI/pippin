import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, describeError } from '../api/client'
import { AgentStreamView } from '../components/AgentStreamView'
import { WorkspacePanel } from '../components/WorkspacePanel'
import { formatCost, formatDuration } from '../format'
import {
  GATE_SOURCE_LABELS,
  isActiveStatus,
  phaseLabel,
  PHASE_PRESETS,
  PHASES,
  type CampaignSummary,
  type ConversationMessage,
  type ExperimentDetail,
  type ExperimentEventRecord,
  type ExperimentSummary,
  type GateAction,
  type PendingDecision,
  type Phase,
  type Proposal,
  type Skill,
} from '../types'
import type { StreamEvent } from '../types/chat'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(ts: number): string {
  const diff = Date.now() - ts * 1000
  const min = Math.floor(diff / 60_000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min}m`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h`
  return `${Math.floor(hr / 24)}d`
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  const now = new Date()
  const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000)
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (diffDays === 0) return time
  if (diffDays === 1) return `Yesterday ${time}`
  return `${diffDays}d ago`
}

function expDotColor(status: string): string {
  switch (status) {
    case 'running': case 'starting': return 'var(--green)'
    case 'awaiting_human': return 'var(--amber)'
    case 'failed': case 'stopped': return 'var(--red)'
    default: return 'var(--fg-dim)'
  }
}

function expBadgeClass(status: string): string {
  switch (status) {
    case 'running': case 'starting': return 'running'
    case 'awaiting_human': return 'attention'
    case 'failed': case 'stopped': return 'error'
    default: return 'done'
  }
}

const STATUS_LABELS: Record<string, string> = {
  starting: 'starting',
  running: 'running',
  awaiting_human: 'needs input',
  completed: 'completed',
  failed: 'failed',
  stopped: 'stopped',
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

/** The last path segment of an experiment's name (falls back to its id). */
function experimentName(exp: Pick<ExperimentSummary, 'task_name' | 'run_id'> | undefined | null): string {
  if (!exp) return ''
  return (exp.task_name || exp.run_id).split('/').pop() || exp.run_id
}

function ErrorBanner({ message, onDismiss }: { message: string | null; onDismiss: () => void }) {
  if (!message) return null
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      <button title="Dismiss" onClick={onDismiss}>{'×'}</button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SVG Icons
// ---------------------------------------------------------------------------

const FolderIcon = () => (
  <svg viewBox="0 0 24 24"><path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>
)
const SearchIcon = () => (
  <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
)
const ChartIcon = () => (
  <svg viewBox="0 0 24 24"><path d="M18 20V10M12 20V4M6 20v-6"/></svg>
)
const SettingsIcon = () => (
  <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
)
const ExplorerIcon = () => (
  <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
)
const SkillsIcon = () => (
  <svg viewBox="0 0 24 24"><path d="M20 16V8a2 2 0 00-2-2h-3a3 3 0 00-6 0H6a2 2 0 00-2 2v3a3 3 0 000 6v3a2 2 0 002 2h3a3 3 0 006 0h3a2 2 0 002-2v-3a3 3 0 000-6z"/></svg>
)
const SendSvgIcon = () => (
  <svg viewBox="0 0 24 24" style={{ width: 16, height: 16, stroke: 'currentColor', fill: 'none', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }}>
    <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
  </svg>
)
const StopSvg = () => (
  <svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1" fill="currentColor" stroke="none"/></svg>
)
const RerunSvg = () => (
  <svg viewBox="0 0 24 24"><path d="M1 4v6h6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M3.51 15a9 9 0 102.13-9.36L1 10" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
)
const PlaySvg = () => (
  <svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3" fill="currentColor"/></svg>
)
const StreamSvg = () => (
  <svg viewBox="0 0 24 24" style={{ width: 14, height: 14, stroke: 'currentColor', fill: 'none', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round' }}>
    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
  </svg>
)
const FilesSvg = () => (
  <svg viewBox="0 0 24 24" style={{ width: 14, height: 14, stroke: 'currentColor', fill: 'none', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round' }}>
    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" /><polyline points="14 2 14 8 20 8" />
  </svg>
)

// ---------------------------------------------------------------------------
// Activity Bar
// ---------------------------------------------------------------------------

function ActivityBar({
  activePanel,
  explorerActive,
  onTogglePanel,
}: {
  activePanel: string
  explorerActive: boolean
  onTogglePanel: (panel: string) => void
}) {
  return (
    <div className="activity-bar">
      <button className={`ab-btn${activePanel === 'projects' ? ' active' : ''}`} title="Projects" onClick={() => onTogglePanel('projects')}><FolderIcon /></button>
      <button className={`ab-btn${activePanel === 'search' ? ' active' : ''}`} title="Search" onClick={() => onTogglePanel('search')}><SearchIcon /></button>
      <button className={`ab-btn${activePanel === 'analytics' ? ' active' : ''}`} title="Analytics" onClick={() => onTogglePanel('analytics')}><ChartIcon /></button>
      <button className={`ab-btn${explorerActive ? ' active' : ''}`} title="Explorer" onClick={() => onTogglePanel('explorer')}><ExplorerIcon /></button>
      <button className={`ab-btn${activePanel === 'skills' ? ' active' : ''}`} title="Skills" onClick={() => onTogglePanel('skills')}><SkillsIcon /></button>
      <button className={`ab-btn${activePanel === 'settings' ? ' active' : ''}`} title="Settings" onClick={() => onTogglePanel('settings')}><SettingsIcon /></button>
      <div className="ab-spacer" />
      <button className="ab-btn" title="Account" style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)' }}>Y</button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Resize Handle
// ---------------------------------------------------------------------------

function ResizeHandle({
  targetRef,
  side,
  minWidth = 180,
  maxWidth = 600,
  onResize,
}: {
  targetRef?: React.RefObject<HTMLDivElement | null>
  side: 'right' | 'left'
  minWidth?: number
  maxWidth?: number
  onResize?: (w: number) => void
}) {
  const handleRef = useRef<HTMLDivElement>(null)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = targetRef?.current?.offsetWidth ?? 280

    document.body.classList.add('resizing')
    handleRef.current?.classList.add('dragging')

    const onMove = (ev: MouseEvent) => {
      const delta = side === 'right' ? ev.clientX - startX : startX - ev.clientX
      const newW = Math.min(maxWidth, Math.max(minWidth, startW + delta))
      if (targetRef?.current) targetRef.current.style.width = `${newW}px`
      onResize?.(newW)
    }
    const onUp = () => {
      document.body.classList.remove('resizing')
      handleRef.current?.classList.remove('dragging')
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [targetRef, side, minWidth, maxWidth, onResize])

  return <div ref={handleRef} className="resize-handle" onMouseDown={onMouseDown} />
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

function SidebarPanel({
  campaigns,
  activeCampaignId,
  activeExperimentId,
  experiments,
  collapsed,
  sidebarRef,
  onSelectCampaign,
  onNewProject,
  onSelectExperiment,
  onStopExperiment,
  onRerunExperiment,
}: {
  campaigns: CampaignSummary[]
  activeCampaignId: string | null
  activeExperimentId: string | null
  experiments: ExperimentSummary[]
  collapsed: boolean
  sidebarRef: React.RefObject<HTMLDivElement | null>
  onSelectCampaign: (id: string) => void
  onNewProject: () => void
  onSelectExperiment: (expId: string) => void
  onStopExperiment: (campaignId: string, expId: string) => void
  onRerunExperiment: (campaignId: string, expId: string) => void
}) {
  return (
    <div className={`sidebar${collapsed ? ' collapsed' : ''}`} ref={sidebarRef}>
      <div className="sidebar-brand">
        <div className="logo">M</div>
        <div className="name">AURORA</div>
        <button className="new-btn" title="New project" onClick={onNewProject}>+</button>
      </div>
      <div className="sidebar-list">
        <div className="sidebar-section">Projects</div>
        {campaigns.length === 0 && (
          <div style={{ padding: '24px 16px', textAlign: 'center', fontSize: 11, color: 'var(--fg-dim)' }}>
            No projects yet
          </div>
        )}
        {campaigns.map((c) => {
          const isActive = c.campaign_id === activeCampaignId
          const hasRunning = c.active_experiments > 0
          return (
            <div key={c.campaign_id}>
              <div
                className={`camp-item${isActive ? ' active' : ''}`}
                onClick={() => onSelectCampaign(c.campaign_id)}
              >
                <div className={`camp-dot ${c.needs_attention ? 'attention' : hasRunning ? 'running' : 'idle'}`} />
                <div className="camp-body">
                  <div className="camp-name">{c.name}</div>
                  <div className="camp-meta">{c.objective || 'No objective'}</div>
                </div>
                <div className="camp-time">{relativeTime(c.updated_at)}</div>
              </div>
              {isActive && experiments.length > 0 && (
                <div className="exp-list show">
                  {experiments.map((exp) => {
                    const isActive = isActiveStatus(exp.status)
                    const isExpActive = exp.run_id === activeExperimentId
                    return (
                      <div
                        key={exp.run_id}
                        className={`exp-item${isExpActive ? ' active' : ''}`}
                        onClick={(e) => { e.stopPropagation(); onSelectExperiment(exp.run_id) }}
                      >
                        <div className="exp-dot" style={{ background: expDotColor(exp.status) }} />
                        <div className="exp-name">{experimentName(exp)}</div>
                        <span className={`exp-badge ${expBadgeClass(exp.status)}`}>{statusLabel(exp.status)}</span>
                        <div className="exp-actions">
                          {isActive && (
                            <span className="exp-action-btn stop" title="Stop" onClick={(e) => { e.stopPropagation(); onStopExperiment(c.campaign_id, exp.run_id) }}>
                              &#9632;
                            </span>
                          )}
                          <span className="exp-action-btn resume" title="Re-run with the same settings" onClick={(e) => { e.stopPropagation(); onRerunExperiment(c.campaign_id, exp.run_id) }}>
                            &#8635;
                          </span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Welcome View
// ---------------------------------------------------------------------------

function WelcomeView({ onSend }: { onSend: (text: string) => Promise<void> }) {
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => { textareaRef.current?.focus() }, [])

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + 'px'
    }
  }, [input])

  const handleSubmit = async () => {
    if (!input.trim() || sending) return
    setSending(true)
    setError(null)
    try {
      await onSend(input.trim())
    } catch (err) {
      setError(`Could not create the project: ${describeError(err)}`)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="welcome">
      <div className="welcome-inner">
        <h1>What would you like to research?</h1>
        <p>Describe your research goal, and I'll set up a project for you.</p>
        <div className="input-wrap">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit() }
            }}
            placeholder="e.g. Investigate why the model's accuracy drops on longer inputs..."
            rows={1}
          />
          <button className="send-btn" disabled={!input.trim() || sending} onClick={handleSubmit}>
            {sending ? <span className="spinner" /> : <SendSvgIcon />}
          </button>
        </div>
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
        <div className="hint-chips">
          {['Compare two methods on my dataset', 'Reproduce a result from a paper', 'Investigate why training diverges'].map((hint) => (
            <button key={hint} className="hint-chip" onClick={() => setInput(hint)}>{hint}</button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Chat Area (shared between campaign-level and experiment-level chat)
// ---------------------------------------------------------------------------

function ChatArea({
  campaignId,
  experimentId,
  experiments,
  onSelectExperiment,
}: {
  campaignId: string
  experimentId: string | null
  experiments: ExperimentSummary[]
  onSelectExperiment: (expId: string | null) => void
}) {
  const [messages, setMessages] = useState<ConversationMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [pendingDecisions, setPendingDecisions] = useState<(PendingDecision & { experiment_id: string })[]>([])
  const [activeTab, setActiveTab] = useState<string>('project')
  const [showSkillForm, setShowSkillForm] = useState(false)
  const [skillName, setSkillName] = useState('')
  const [skillRule, setSkillRule] = useState('')
  const [skillScope, setSkillScope] = useState('campaign')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const chatAreaRef = useRef<HTMLDivElement>(null)
  const isNearBottom = useRef(true)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const isProjectTab = activeTab === 'project'

  useEffect(() => {
    if (experimentId) {
      setActiveTab(experimentId)
    }
  }, [experimentId])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const fetchHistory = useCallback(async () => {
    try {
      if (!isProjectTab) {
        const history = await api.expChat.history(campaignId, activeTab)
        setMessages(history)
      } else {
        const history = await api.chat.history(campaignId)
        setMessages(history)
      }
    } catch {}
  }, [campaignId, activeTab, isProjectTab])

  const fetchStatus = useCallback(async () => {
    try {
      const status = await api.chat.status(campaignId)
      setPendingDecisions(status.pending_decisions ?? [])
    } catch {}
  }, [campaignId])

  useEffect(() => {
    fetchHistory()
    fetchStatus()
    const interval = setInterval(() => { fetchHistory(); fetchStatus() }, 3000)
    return () => clearInterval(interval)
  }, [fetchHistory, fetchStatus])

  useEffect(() => {
    if (isNearBottom.current) scrollToBottom()
  }, [messages])

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + 'px'
    }
  }, [input])

  const handleSend = async () => {
    if (!input.trim() || sending) return
    const text = input.trim()
    setInput('')
    setSending(true)
    setSendError(null)
    setMessages((prev) => [...prev, { role: 'user', text, timestamp: Date.now() / 1000 }])
    try {
      if (!isProjectTab) {
        await api.expChat.send(campaignId, activeTab, text)
      } else {
        const resp = await api.chat.send(campaignId, text)
        if (resp?.proposal) {
          setMessages((prev) => {
            const last = prev[prev.length - 1]
            if (last?.role === 'assistant' && !last.proposal) {
              return [...prev.slice(0, -1), { ...last, proposal: resp.proposal }]
            }
            return prev
          })
        }
      }
      setTimeout(fetchHistory, 1000)
    } catch (err) {
      setSendError(`Message not sent: ${describeError(err)}`)
    } finally {
      setSending(false)
    }
  }

  const handleSkillSubmit = async () => {
    if (!skillName.trim() || !skillRule.trim()) return
    try {
      await fetch('/api/skills/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: skillName.trim(),
          rule: skillRule.trim(),
          scope: skillScope,
          source_phase: 'manual',
          source_experiment: isProjectTab ? '' : activeTab,
          source_campaign: campaignId,
        }),
      })
      setShowSkillForm(false)
      setSkillName('')
      setSkillRule('')
      setSkillScope('campaign')
    } catch (err) {
      console.error('Failed to create skill:', err)
    }
  }

  const tabTaskName = isProjectTab ? '' : experimentName(experiments.find((e) => e.run_id === activeTab))
  const pendingDecision = pendingDecisions[0]

  return (
    <div className="camp-content">
      {/* Context bar */}
      <div className="context-bar">
        <div
          className={`context-tab${isProjectTab ? ' active' : ''}`}
          onClick={() => { setActiveTab('project'); setMessages([]); onSelectExperiment(null) }}
        >
          <span className="tab-dot" style={{ background: isProjectTab ? 'var(--accent)' : 'var(--fg-dim)' }} />
          Project Chat
          {pendingDecisions.length > 0 && (
            <span style={{
              fontSize: 9, fontWeight: 600, padding: '1px 6px', borderRadius: 10,
              background: 'var(--amber-dim)', color: 'var(--amber)', marginLeft: 2,
            }}>
              {pendingDecisions.length}
            </span>
          )}
        </div>
        {experiments.map((exp) => {
          const isActive = activeTab === exp.run_id
          return (
            <div
              key={exp.run_id}
              className={`context-tab${isActive ? ' active' : ''}`}
              onClick={() => { setActiveTab(exp.run_id); setMessages([]); onSelectExperiment(exp.run_id) }}
            >
              <span className="tab-dot" style={{ background: expDotColor(exp.status) }} />
              {experimentName(exp)}
            </div>
          )
        })}
      </div>

      {/* Chat messages */}
      <div className="chat-area" ref={chatAreaRef} onScroll={() => {
        const el = chatAreaRef.current
        if (el) isNearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
      }}>
        {messages.length === 0 && (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ fontSize: 12, color: 'var(--fg-dim)' }}>
              {isProjectTab
                ? 'Describe the experiment you want; the front desk drafts a proposal for you to confirm.'
                : `Talking to ${tabTaskName}. Its front desk answers and passes instructions on to the agent.`}
            </span>
          </div>
        )}
        {messages.map((msg, i) => {
          if (msg.role === 'system') {
            const isSuccess = msg.text.includes('completed') || msg.text.includes('started')
            const isError = msg.text.includes('error') || msg.text.includes('stopped')
            const iconClass = isError ? 'red' : isSuccess ? 'green' : 'blue'
            return (
              <div key={i} className="sys-event">
                <div className={`ev-icon ${iconClass}`}>
                  {isError ? '✕' : isSuccess ? '✓' : '▶'}
                </div>
                <span>{msg.text}</span>
              </div>
            )
          }
          const isUser = msg.role === 'user'
          return (
            <div key={i} className={`msg-row${isUser ? ' user' : ''}`}>
              <div className={`msg-avatar ${isUser ? 'human' : 'bot'}`}>
                {isUser ? 'Y' : 'M'}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className={`msg-bubble ${isUser ? 'user' : 'bot'}`}>{msg.text}</div>
                {msg.proposal && isProjectTab && (
                  <ProposalCard
                    proposal={msg.proposal}
                    campaignId={campaignId}
                    onConfirmed={(expId) => {
                      onSelectExperiment(expId)
                      setTimeout(fetchHistory, 1000)
                    }}
                  />
                )}
                <div className="msg-time" style={isUser ? { textAlign: 'right' } : undefined}>
                  {formatTime(msg.timestamp)}
                </div>
              </div>
            </div>
          )
        })}
        <div ref={messagesEndRef} />
      </div>

      {/* A decision one of the project's experiments is waiting for */}
      {isProjectTab && pendingDecision && (
        <ApprovalBar
          campaignId={campaignId}
          experimentId={pendingDecision.experiment_id}
          experimentLabel={experimentName(experiments.find((e) => e.run_id === pendingDecision.experiment_id))
            || pendingDecision.experiment_id}
          decision={pendingDecision}
          onResolved={fetchStatus}
        />
      )}

      <ErrorBanner message={sendError} onDismiss={() => setSendError(null)} />

      {/* Skill form */}
      {showSkillForm && (
        <div style={{ padding: '12px 20px', borderTop: '1px solid var(--border-subtle)' }}>
          <div className="skill-create-form" style={{ maxWidth: 'none' }}>
            <div className="skill-create-header">
              <span>New Skill</span>
              <button onClick={() => setShowSkillForm(false)} style={{ border: 'none', background: 'transparent', color: 'var(--fg-dim)', cursor: 'pointer', fontSize: 14, marginLeft: 'auto' }}>{'✕'}</button>
            </div>
            <div className="skill-create-body">
              <label>Name</label>
              <input value={skillName} onChange={(e) => setSkillName(e.target.value)} placeholder="Skill name" />
              <label>Rule</label>
              <textarea value={skillRule} onChange={(e) => setSkillRule(e.target.value)} placeholder="Rule / instruction..." rows={2} />
              <div className="skill-create-scope">
                {(['experiment', 'campaign', 'global'] as const).map((s) => (
                  <label key={s}><input type="radio" name="skill-scope" checked={skillScope === s} onChange={() => setSkillScope(s)} /> {s === 'experiment' ? 'This experiment' : s === 'campaign' ? 'This project' : 'Global'}</label>
                ))}
              </div>
              <div className="skill-create-actions">
                <button className="ss-accept" disabled={!skillName.trim() || !skillRule.trim()} onClick={handleSkillSubmit}>Save Skill</button>
                <button className="ss-dismiss" onClick={() => setShowSkillForm(false)}>Cancel</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Input bar */}
      <div className="input-bar">
        <div className="input-wrap">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
            }}
            placeholder={isProjectTab ? 'Message the project...' : `Message ${tabTaskName}...`}
            rows={1}
          />
          <button className="send-btn" disabled={!input.trim() || sending} onClick={handleSend}>
            {sending ? <span className="spinner" /> : <SendSvgIcon />}
          </button>
        </div>
        <div className="input-hint" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button className="add-skill-btn" onClick={() => setShowSkillForm(!showSkillForm)}>+ Skill</button>
          <span>
            Talking to <span className="target">{isProjectTab ? 'the project front desk' : tabTaskName}</span>
            {isProjectTab && ' · open an experiment tab to see its activity'}
          </span>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Experiment View
// ---------------------------------------------------------------------------

/** Events after which the experiment summary (status, phase, pending decision, report) is refetched. */
const REFRESH_EVENTS = new Set([
  'phase_started', 'human_gate_opened', 'human_gate_resolved', 'autonomy_changed',
  'writing_complete', 'run_finished', 'run_error',
])
const MAX_STREAM_EVENTS = 2000

function ExperimentView({
  campaignId,
  experimentId,
  experiments,
  onSelectExperiment,
  streamRef,
  rightPanel,
  setRightPanel,
}: {
  campaignId: string
  experimentId: string
  experiments: ExperimentSummary[]
  onSelectExperiment: (expId: string | null) => void
  streamRef: React.RefObject<HTMLDivElement | null>
  rightPanel: string
  setRightPanel: (v: string) => void
}) {
  const [run, setRun] = useState<ExperimentDetail | null>(null)
  const [events, setEvents] = useState<StreamEvent[]>([])
  // Bumped to reconnect the event stream (after a resume, the experiment has a new live state)
  const [connection, setConnection] = useState(0)
  const [streamCollapsed, setStreamCollapsed] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [openRequest, setOpenRequest] = useState<{ path: string } | null>(null)

  const fetchRun = useCallback(async () => {
    try {
      setRun(await api.experiments.get(campaignId, experimentId))
    } catch {}
  }, [campaignId, experimentId])

  useEffect(() => {
    fetchRun()
    const interval = setInterval(fetchRun, 5000)
    return () => clearInterval(interval)
  }, [fetchRun])

  // The WebSocket replays the experiment's events, then streams new ones.
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/campaigns/${campaignId}/experiments/${experimentId}/ws`)

    ws.onmessage = (msg) => {
      let record: ExperimentEventRecord
      try {
        record = JSON.parse(msg.data)
      } catch {
        return
      }
      if (!record.event_type) return
      const event: StreamEvent = {
        event_type: record.event_type,
        source: record.source,
        data: record.data ?? {},
        timestamp: record.timestamp ?? Date.now() / 1000,
        turn: record.data?.turn,
      }
      setEvents((prev) => {
        const next = [...prev, event]
        return next.length > MAX_STREAM_EVENTS ? next.slice(-MAX_STREAM_EVENTS) : next
      })
      if (REFRESH_EVENTS.has(event.event_type)) fetchRun()
    }
    return () => ws.close()
  }, [campaignId, experimentId, connection, fetchRun])

  /** Run a control action, showing its error (e.g. a missing Docker or API key) instead of failing silently. */
  const act = async (what: string, action: () => Promise<void>) => {
    setActionError(null)
    try {
      await action()
    } catch (err) {
      setActionError(`${what} failed: ${describeError(err)}`)
    }
  }

  const handleStop = () => act('Stop', async () => {
    await api.experiments.stop(campaignId, experimentId)
    await fetchRun()
  })

  const handleRerun = () => act('Re-run', async () => {
    const started = await api.experiments.rerun(campaignId, experimentId)
    onSelectExperiment(started.run_id)
  })

  const handleResume = () => act('Resume', async () => {
    await api.experiments.resume(campaignId, experimentId)
    setEvents([])
    setConnection((n) => n + 1)
    await fetchRun()
  })

  const handleToggleAutonomy = () => act('Switching mode', async () => {
    if (!run) return
    await api.experiments.setAutonomyMode(campaignId, experimentId,
      run.autonomy_mode === 'autonomous' ? 'supervised' : 'autonomous')
    await fetchRun()
  })

  const handleOpenReport = () => {
    if (!run?.report) return
    setRightPanel('files')
    setStreamCollapsed(false)
    setOpenRequest({ path: run.report })
  }

  const isActive = isActiveStatus(run?.status)
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!isActive) return
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(timer)
  }, [isActive])
  const elapsed = run ? (run.finished_at || now) - run.started_at : 0
  const canResume = run?.status === 'failed' || run?.status === 'stopped'

  return (
    <div className="exp-content">
      {/* Status strip */}
      <div className="status-strip">
        <div className="stat">
          <div className="stat-label">Status</div>
          <div className="stat-value" style={{ color: isActive ? 'var(--green)' : run?.status === 'failed' ? 'var(--red)' : 'var(--fg-muted)' }}>
            {run ? statusLabel(run.status) : '—'}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Phase</div>
          <div className="stat-value">{phaseLabel(run?.phase) || '—'}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Tool calls</div>
          <div className="stat-value mono">{run?.turn ?? 0}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Elapsed</div>
          <div className="stat-value mono">{formatDuration(elapsed)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Cost</div>
          <div className="stat-value mono">{formatCost(run?.cost_usd)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Model</div>
          <div className="stat-value">{run?.model?.split('/').pop() || '—'}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Mode</div>
          <div
            className="stat-value"
            title={isActive ? 'Click to switch between supervised and autonomous' : undefined}
            style={{ cursor: isActive ? 'pointer' : 'default', color: run?.autonomy_mode === 'supervised' ? 'var(--amber)' : 'var(--green)' }}
            onClick={isActive ? handleToggleAutonomy : undefined}
          >
            {run?.autonomy_mode || '—'}
          </div>
        </div>
        <div className="exp-controls">
          {run?.report && (
            <button className="exp-ctrl-btn rerun" title={run.report} onClick={handleOpenReport}>
              <FilesSvg /> Report
            </button>
          )}
          {isActive && (
            <button className="exp-ctrl-btn stop" onClick={handleStop}>
              <StopSvg /> Stop
            </button>
          )}
          {canResume && (
            <button className="exp-ctrl-btn resume" title="Continue from the last checkpoint" onClick={handleResume}>
              <PlaySvg /> Resume
            </button>
          )}
          {run && !isActive && (
            <button className="exp-ctrl-btn rerun" title="Start a new experiment with the same settings" onClick={handleRerun}>
              <RerunSvg /> Re-run
            </button>
          )}
        </div>
      </div>

      {run?.status === 'failed' && run.error && (
        <div className="error-banner">Failed: {run.error}</div>
      )}
      <ErrorBanner message={actionError} onDismiss={() => setActionError(null)} />

      {/* A human gate the experiment is waiting at */}
      {run?.pending_decision && !run.pending_decision.resolved && (
        <ApprovalBar
          key={run.pending_decision.decision_id}
          campaignId={campaignId}
          experimentId={experimentId}
          decision={run.pending_decision}
          onResolved={fetchRun}
        />
      )}

      {/* Dual-column body */}
      <div className="exp-body" style={{ position: 'relative' }}>
        {/* Front desk chat (center) */}
        <div className="fd-col">
          <ChatArea
            campaignId={campaignId}
            experimentId={experimentId}
            experiments={experiments}
            onSelectExperiment={onSelectExperiment}
          />
        </div>

        {!streamCollapsed && <ResizeHandle targetRef={streamRef} side="left" minWidth={200} maxWidth={900} />}

        {/* Event stream / files (right) */}
        <div className={`tl-wrap${streamCollapsed ? ' collapsed' : ''}`} ref={streamRef}>
          {rightPanel === 'files' ? (
            <div style={{ flex: 1, overflow: 'auto', padding: 0 }}>
              <WorkspacePanel campaignId={campaignId} expId={experimentId} openRequest={openRequest} />
            </div>
          ) : (
            <div style={{ flex: 1, overflowY: 'auto' }}>
              <AgentStreamView events={events} campaignId={campaignId} experimentId={experimentId} />
            </div>
          )}
        </div>

        {streamCollapsed && (
          <button className="stream-restore" style={{ display: 'flex' }} onClick={() => setStreamCollapsed(false)}>
            STREAM & FILE
          </button>
        )}

        {/* Right activity bar */}
        <div className="right-bar">
          <button className={`rb-btn${rightPanel === 'stream' ? ' active' : ''}`} title="Stream" onClick={() => { setRightPanel('stream'); setStreamCollapsed(false) }}>
            <StreamSvg />
            <span className="rb-label">Stream</span>
          </button>
          <div className="rb-sep" />
          <button className={`rb-btn${rightPanel === 'files' ? ' active' : ''}`} title="Files" onClick={() => setRightPanel('files')}>
            <FilesSvg />
            <span className="rb-label">File</span>
          </button>
          <div className="rb-sep" />
          <button className="rb-btn" title="Collapse" onClick={() => setStreamCollapsed(!streamCollapsed)}>
            {streamCollapsed ? '◀' : '▶'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Approval Bar: answer a human gate (continue / send instruction / stop)
// ---------------------------------------------------------------------------

function ApprovalBar({
  campaignId,
  experimentId,
  experimentLabel,
  decision,
  onResolved,
}: {
  campaignId: string
  experimentId: string
  /** Shown when the bar is outside the experiment's own view. */
  experimentLabel?: string
  decision: PendingDecision
  onResolved: () => void
}) {
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const source = GATE_SOURCE_LABELS[decision.source] ?? decision.source
  const title = decision.detail?.title || decision.context.split('\n')[0]
  const body = decision.detail?.detail ?? ''
  const gateId = decision.detail?.gate_id || decision.decision_id
  const offers = (action: GateAction) => decision.options.includes(action)

  const decide = async (action: GateAction) => {
    setSubmitting(true)
    setError(null)
    try {
      await api.experiments.decide(campaignId, experimentId, action, message.trim(), gateId)
      setMessage('')
      onResolved()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="approval-bar show">
      <div className="approval-label">
        <span style={{ fontSize: 14 }}>{'⚠'}</span>
        {source} needs your decision{experimentLabel ? ` · ${experimentLabel}` : ''}
      </div>
      <div className="approval-title">{title}</div>
      {body && <div className="approval-desc">{body}</div>}
      <div className="approval-actions">
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Instruction for the agent (required for “Send instruction”)"
          rows={2}
        />
        {offers('continue') && (
          <button className="btn-sm btn-approve" disabled={submitting} title="Go on as planned" onClick={() => decide('continue')}>
            {'✓'} Continue
          </button>
        )}
        {offers('instruct') && (
          <button className="btn-sm btn-modify" disabled={submitting || !message.trim()} title="Go on, following your instruction" onClick={() => decide('instruct')}>
            {'✎'} Send instruction
          </button>
        )}
        <span className="approval-spacer" />
        {offers('stop') && (
          <button className="btn-sm btn-reject" disabled={submitting} title="End the experiment" onClick={() => decide('stop')}>
            {'■'} Stop experiment
          </button>
        )}
      </div>
      {error && <div className="tl-detail tone-error">Could not send the decision: {error}</div>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Skills Panel
// ---------------------------------------------------------------------------

function skillIconType(skill: Skill): string {
  const n = (skill.name + ' ' + (skill.description || '')).toLowerCase()
  if (n.includes('code') || n.includes('implement')) return 'code'
  if (n.includes('test') || n.includes('verify')) return 'test'
  if (n.includes('debug') || n.includes('fix')) return 'debug'
  if (n.includes('docker') || n.includes('container')) return 'docker'
  return 'default'
}

function skillIconChar(type: string): string {
  switch (type) {
    case 'code': return '<>'
    case 'test': return '✓'
    case 'debug': return '⚡'
    case 'docker': return '🐳'
    default: return '✦'
  }
}

function SkillsPanel({ collapsed, panelRef }: { collapsed: boolean; panelRef: React.RefObject<HTMLDivElement | null> }) {
  const [skills, setSkills] = useState<Skill[]>([])
  const [search, setSearch] = useState('')
  const [activeTab, setActiveTab] = useState<'all' | 'enabled' | 'distilled'>('all')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [enabledMap, setEnabledMap] = useState<Record<string, boolean>>({})
  const [editingRule, setEditingRule] = useState('')

  const fetchSkills = useCallback(async () => {
    try {
      const res = await fetch('/api/skills/')
      if (res.ok) {
        const data = await res.json()
        setSkills(data.skills || data || [])
      }
    } catch {}
  }, [])

  useEffect(() => { fetchSkills() }, [fetchSkills])

  const filtered = skills.filter((s) => {
    if (search) {
      const q = search.toLowerCase()
      if (!s.name.toLowerCase().includes(q) && !(s.description || '').toLowerCase().includes(q)) return false
    }
    if (activeTab === 'enabled') return enabledMap[s.skill_id] !== false
    if (activeTab === 'distilled') return s.source_phase === 'auto' || s.source_phase === 'distilled'
    return true
  })

  const handleToggle = (id: string) => {
    setEnabledMap((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const handleExpand = (id: string) => {
    if (expandedId === id) {
      setExpandedId(null)
    } else {
      setExpandedId(id)
      const skill = skills.find((s) => s.skill_id === id)
      if (skill) setEditingRule(skill.rule)
    }
  }

  const handleSave = async (id: string) => {
    try {
      await fetch(`/api/skills/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rule: editingRule }),
      })
      fetchSkills()
    } catch {}
  }

  const handleDelete = async (id: string) => {
    try {
      await fetch(`/api/skills/${id}`, { method: 'DELETE' })
      setExpandedId(null)
      fetchSkills()
    } catch {}
  }

  const handleDuplicate = async (skill: Skill) => {
    try {
      await fetch('/api/skills/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: `${skill.name} (copy)`,
          rule: skill.rule,
          description: skill.description,
          scope: skill.scope,
          source_phase: skill.source_phase,
          tags: skill.tags,
        }),
      })
      fetchSkills()
    } catch {}
  }

  return (
    <div className={`skills-panel${collapsed ? ' collapsed' : ''}`} ref={panelRef}>
      <div className="skills-header">
        <div className="skills-title">Skills</div>
        <input
          className="skills-search"
          placeholder="Search skills..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="skills-tabs">
        {(['all', 'enabled', 'distilled'] as const).map((tab) => (
          <button
            key={tab}
            className={`skills-tab${activeTab === tab ? ' active' : ''}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab === 'all' ? 'All' : tab === 'enabled' ? 'Enabled' : 'Auto-distilled'}
          </button>
        ))}
      </div>
      <div className="skills-list">
        {filtered.length === 0 && (
          <div style={{ padding: '24px 16px', textAlign: 'center', fontSize: 11, color: 'var(--fg-dim)' }}>
            {search ? 'No matching skills' : 'No skills yet'}
          </div>
        )}
        {filtered.map((skill) => {
          const iconType = skillIconType(skill)
          const isExpanded = expandedId === skill.skill_id
          const isEnabled = enabledMap[skill.skill_id] !== false
          const isAuto = skill.source_phase === 'auto' || skill.source_phase === 'distilled'
          return (
            <div key={skill.skill_id} className={`skill-card${isExpanded ? ' expanded' : ''}`} onClick={() => handleExpand(skill.skill_id)}>
              <div className="skill-card-top">
                <div className={`skill-icon ${iconType}`}>{skillIconChar(iconType)}</div>
                <div className="skill-info">
                  <div className="skill-name">
                    {skill.name}
                    {isAuto && <span className="skill-auto-badge">Auto</span>}
                  </div>
                  <div className="skill-desc">{skill.description || skill.rule}</div>
                  <div className="skill-meta">
                    <span className="skill-scope-badge">{skill.scope}</span>
                  </div>
                </div>
                <label className="skill-toggle" onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" checked={isEnabled} onChange={() => handleToggle(skill.skill_id)} />
                  <span className="slider" />
                </label>
              </div>
              {isExpanded && (
                <div className="skill-detail" onClick={(e) => e.stopPropagation()}>
                  <div className="skill-detail-section">
                    <div className="skill-detail-label">Rule</div>
                    <div
                      className="skill-detail-content"
                      contentEditable
                      suppressContentEditableWarning
                      onBlur={(e) => setEditingRule(e.currentTarget.textContent || '')}
                    >
                      {skill.rule}
                    </div>
                  </div>
                  <div className="skill-actions">
                    <button className="primary" onClick={() => handleSave(skill.skill_id)}>Save</button>
                    <button onClick={() => handleDuplicate(skill)}>Duplicate</button>
                    <button className="danger" onClick={() => handleDelete(skill.skill_id)}>Delete</button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Phase Pipeline Selector
// ---------------------------------------------------------------------------

function PhasePipelineSelector({
  skipPhases,
  onChange,
}: {
  skipPhases: Phase[]
  onChange: (skip: Phase[]) => void
}) {
  const togglePhase = (key: Phase) => {
    if (PHASES.find((p) => p.key === key)?.required) return
    onChange(skipPhases.includes(key) ? skipPhases.filter((p) => p !== key) : [...skipPhases, key])
  }

  const activePreset = Object.entries(PHASE_PRESETS).find(
    ([, preset]) => preset.skip.length === skipPhases.length && preset.skip.every((p) => skipPhases.includes(p)),
  )?.[0]

  return (
    <div className="phase-selector">
      <div className="phase-selector-header">
        <span className="phase-selector-title">Pipeline Phases</span>
        <div className="phase-presets">
          {Object.entries(PHASE_PRESETS).map(([key, preset]) => (
            <button
              key={key}
              className={`phase-preset-btn${activePreset === key ? ' active' : ''}`}
              onClick={() => onChange([...preset.skip])}
              title={preset.desc}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>
      <div className="phase-chips">
        {PHASES.map((phase) => (
          <button
            key={phase.key}
            className={`phase-chip${skipPhases.includes(phase.key) ? '' : ' active'}${phase.required ? ' required' : ''}`}
            onClick={() => togglePhase(phase.key)}
            title={phase.required ? `${phase.desc} (always runs)` : phase.desc}
          >
            <span className="phase-chip-dot" />
            <span className="phase-chip-label">{phase.label}</span>
          </button>
        ))}
      </div>
      <div className="phase-flow">
        {PHASES.filter((p) => !skipPhases.includes(p.key)).map((p, i, arr) => (
          <span key={p.key} className="phase-flow-item">
            {p.label}{i < arr.length - 1 ? ' → ' : ''}
          </span>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Proposal Card: an experiment the front desk drafted, to confirm and start
// ---------------------------------------------------------------------------

function ProposalCard({
  proposal,
  campaignId,
  onConfirmed,
}: {
  proposal: Proposal
  campaignId: string
  onConfirmed: (expId: string) => void
}) {
  const [skipPhases, setSkipPhases] = useState<Phase[]>(proposal.skip_phases)
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(proposal.confirmed)
  const [error, setError] = useState<string | null>(null)
  const [showInstruction, setShowInstruction] = useState(false)

  const handleConfirm = async () => {
    setConfirming(true)
    setError(null)
    try {
      const result = await api.proposals.confirm(campaignId, proposal.proposal_id, { skip_phases: skipPhases })
      setConfirmed(true)
      onConfirmed(result.experiment_id)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setConfirming(false)
    }
  }

  if (proposal.superseded) {
    return (
      <div className="proposal-card superseded">
        <div className="proposal-status superseded-text">Replaced by updated proposal</div>
      </div>
    )
  }

  if (confirmed) {
    return (
      <div className="proposal-card confirmed">
        <div className="proposal-status">Experiment started</div>
      </div>
    )
  }

  const { instruction, model, inputs } = proposal.params

  return (
    <div className="proposal-card">
      <div className="proposal-header">
        <span className="proposal-icon">{'⟡'}</span>
        <span className="proposal-title">Experiment Proposal</span>
      </div>
      <div className="proposal-summary">{proposal.summary}</div>

      {instruction && (
        <div className="proposal-detail">
          <button className="proposal-detail-toggle" onClick={() => setShowInstruction(!showInstruction)}>
            {showInstruction ? '▾' : '▸'} Instruction
          </button>
          {showInstruction && <div className="proposal-instruction">{instruction}</div>}
        </div>
      )}
      {inputs && inputs.length > 0 && <div className="proposal-meta">Inputs: {inputs.join(', ')}</div>}
      {model && <div className="proposal-meta">Model: {model}</div>}

      <PhasePipelineSelector skipPhases={skipPhases} onChange={setSkipPhases} />

      {error && <div className="error-banner" style={{ margin: '0 0 8px' }}>Could not start the experiment: {error}</div>}

      <div className="proposal-actions">
        <div className="proposal-hint">Adjust phases above, or tell the front desk what to change</div>
        <button className="proposal-confirm" disabled={confirming} onClick={handleConfirm}>
          {confirming ? 'Starting...' : 'Confirm & Start'}
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Campaign Chat View (header + chat area, no experiment)
// ---------------------------------------------------------------------------

function CampaignChatView({
  campaignId,
  campaign,
  experiments,
  onSelectExperiment,
}: {
  campaignId: string
  campaign: CampaignSummary | null
  experiments: ExperimentSummary[]
  onSelectExperiment: (expId: string | null) => void
}) {
  return (
    <>
      <div className="header">
        <div className="header-title">
          {campaign?.name || campaignId}
          {campaign?.objective && campaign.name !== campaign.objective && (
            <div className="header-sub">{campaign.objective}</div>
          )}
        </div>
        {experiments.length > 0 && (
          <span className="badge" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--fg-muted)' }}>
            {experiments.length} exp
          </span>
        )}
      </div>
      <ChatArea
        campaignId={campaignId}
        experimentId={null}
        experiments={experiments}
        onSelectExperiment={onSelectExperiment}
      />
    </>
  )
}

// ---------------------------------------------------------------------------
// Home (Shell)
// ---------------------------------------------------------------------------

export default function Home() {
  const navigate = useNavigate()
  const location = useLocation()

  // Parse URL for deep links
  const urlMatch = location.pathname.match(/\/campaigns\/([^/]+)(?:\/experiments\/([^/]+))?/)
  const urlCampaignId = urlMatch?.[1] || null
  const urlExperimentId = urlMatch?.[2] || null

  const [campaigns, setCampaigns] = useState<CampaignSummary[]>([])
  const [activeCampaignId, setActiveCampaignId] = useState<string | null>(urlCampaignId)
  const [activeExperimentId, setActiveExperimentId] = useState<string | null>(urlExperimentId)
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [activePanel, setActivePanel] = useState<string>('projects')
  const [rightPanel, setRightPanel] = useState<string>('stream')

  const sidebarRef = useRef<HTMLDivElement>(null)
  const skillsPanelRef = useRef<HTMLDivElement>(null)
  const streamRef = useRef<HTMLDivElement>(null)

  const sidebarCollapsed = activePanel === '' || activePanel === 'skills'
  const showSkills = activePanel === 'skills'

  // Sync URL → state on URL change
  useEffect(() => {
    if (urlCampaignId && urlCampaignId !== activeCampaignId) {
      setActiveCampaignId(urlCampaignId)
    }
    if (urlExperimentId !== activeExperimentId) {
      setActiveExperimentId(urlExperimentId)
    }
  }, [urlCampaignId, urlExperimentId])

  // Sync state → URL
  const selectCampaign = useCallback((id: string) => {
    setActiveCampaignId(id)
    setActiveExperimentId(null)
    navigate(`/campaigns/${id}`, { replace: true })
  }, [navigate])

  const selectExperiment = useCallback((expId: string | null) => {
    setActiveExperimentId(expId)
    if (activeCampaignId && expId) {
      navigate(`/campaigns/${activeCampaignId}/experiments/${expId}`, { replace: true })
    } else if (activeCampaignId) {
      navigate(`/campaigns/${activeCampaignId}`, { replace: true })
    }
  }, [activeCampaignId, navigate])

  const handleTogglePanel = (panel: string) => {
    if (panel === 'explorer') {
      setRightPanel((prev) => (prev === 'files' ? 'stream' : 'files'))
      return
    }
    setActivePanel((prev) => (prev === panel ? '' : panel))
  }

  // Fetch campaigns
  const fetchCampaigns = useCallback(async () => {
    try {
      const data = await api.campaigns.list()
      setCampaigns(data)
    } catch {}
  }, [])

  const fetchExperiments = useCallback(async () => {
    if (!activeCampaignId) return
    try {
      const data = await api.campaigns.get(activeCampaignId)
      setExperiments(data.experiments ?? [])
    } catch {}
  }, [activeCampaignId])

  useEffect(() => {
    fetchCampaigns()
    const interval = setInterval(fetchCampaigns, 5000)
    return () => clearInterval(interval)
  }, [fetchCampaigns])

  useEffect(() => {
    if (activeCampaignId) {
      fetchExperiments()
      const interval = setInterval(fetchExperiments, 3000)
      return () => clearInterval(interval)
    } else {
      setExperiments([])
    }
  }, [activeCampaignId, fetchExperiments])

  const handleNewProject = async (prompt: string) => {
    const result = await api.campaigns.quickCreate(prompt)
    await fetchCampaigns()
    selectCampaign(result.campaign_id)
    // Fire-and-forget: send the first message to FrontDesk in the background.
    // The 3s polling in ChatArea will pick up both the user message and the
    // assistant response once the backend appends them to chat_history.
    api.chat.send(result.campaign_id, prompt).catch((err: unknown) => {
      console.error('Failed to send initial message:', err)
    })
  }

  const handleStopExperiment = async (campaignId: string, expId: string) => {
    try {
      await api.experiments.stop(campaignId, expId)
      fetchExperiments()
    } catch (err) {
      setNotice(`Stop failed: ${describeError(err)}`)
    }
  }

  const handleRerunExperiment = async (campaignId: string, expId: string) => {
    try {
      const started = await api.experiments.rerun(campaignId, expId)
      await fetchExperiments()
      selectExperiment(started.run_id)
    } catch (err) {
      setNotice(`Re-run failed: ${describeError(err)}`)
    }
  }

  const activeCampaign = campaigns.find((c) => c.campaign_id === activeCampaignId) || null

  // Determine view
  const view = !activeCampaignId ? 'welcome' : activeExperimentId ? 'experiment' : 'campaign'

  return (
    <div className="shell">
      <ActivityBar activePanel={activePanel} explorerActive={rightPanel === 'files'} onTogglePanel={handleTogglePanel} />

      <SidebarPanel
        campaigns={campaigns}
        activeCampaignId={activeCampaignId}
        activeExperimentId={activeExperimentId}
        experiments={experiments}
        collapsed={sidebarCollapsed}
        sidebarRef={sidebarRef}
        onSelectCampaign={selectCampaign}
        onNewProject={() => {
          setActiveCampaignId(null)
          setActiveExperimentId(null)
          navigate('/', { replace: true })
        }}
        onSelectExperiment={(expId) => selectExperiment(expId)}
        onStopExperiment={handleStopExperiment}
        onRerunExperiment={handleRerunExperiment}
      />

      <ResizeHandle targetRef={sidebarRef} side="right" />

      <SkillsPanel collapsed={!showSkills} panelRef={skillsPanelRef} />
      {showSkills && <ResizeHandle targetRef={skillsPanelRef} side="right" />}

      <div className="main" style={{ position: 'relative' }}>
        <ErrorBanner message={notice} onDismiss={() => setNotice(null)} />
        {view === 'welcome' && (
          <WelcomeView onSend={handleNewProject} />
        )}

        {view === 'campaign' && activeCampaignId && (
          <CampaignChatView
            key={activeCampaignId}
            campaignId={activeCampaignId}
            campaign={activeCampaign}
            experiments={experiments}
            onSelectExperiment={selectExperiment}
          />
        )}

        {view === 'experiment' && activeCampaignId && activeExperimentId && (
          <>
            <div className="header">
              <div className="header-title">
                {activeCampaign?.name || activeCampaignId}
                <div className="header-sub">
                  <span className="chat-target">{experimentName(experiments.find((e) => e.run_id === activeExperimentId)) || activeExperimentId}</span>
                  <span className="back-link" onClick={() => selectExperiment(null)}>{'←'} back to project</span>
                </div>
              </div>
            </div>
            <ExperimentView
              key={activeExperimentId}
              campaignId={activeCampaignId}
              experimentId={activeExperimentId}
              experiments={experiments}
              onSelectExperiment={selectExperiment}
              streamRef={streamRef}
              rightPanel={rightPanel}
              setRightPanel={setRightPanel}
            />
          </>
        )}
      </div>
    </div>
  )
}

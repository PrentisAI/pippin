import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { AgentStreamView } from '../components/AgentStreamView'
import { WorkspacePanel } from '../components/WorkspacePanel'
import type { StreamEvent } from '../types/chat'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CampaignSummary {
  campaign_id: string
  name: string
  objective: string
  created_at: number
  updated_at: number
  experiment_count: number
  active_experiments: number
  completed_experiments: number
  best_reward: number
  needs_attention: boolean
}

interface ProposalData {
  proposal_id: string
  kind: 'predefined' | 'custom'
  params: Record<string, any>
  skip_phases: string[]
  active_phases: string[]
  summary: string
  confirmed?: boolean
  superseded?: boolean
}

interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  text: string
  timestamp: number
  type?: string
  proposal?: ProposalData
}

interface ExperimentInfo {
  run_id: string
  task_name: string
  status: string
  reward?: number
}

interface RunDetail {
  run_id: string
  campaign_id?: string
  task_name: string
  model: string
  autonomy_mode: string
  status: string
  phase: number
  campaign_phase?: string
  turn: number
  reward: number
  started_at: number
  finished_at: number | null
  event_count: number
  error: string | null
  watchdog_active: boolean
  watchdog_verdicts: number
  watchdog_cycles: number
  pending_decision: {
    decision_id: string
    source: string
    context: string
    detail: Record<string, any>
    options: string[]
    created_at: number
    resolved: boolean
  } | null
  decisions_made: number
}

interface Skill {
  skill_id: string
  name: string
  rule: string
  description: string
  source_phase: string
  source_experiment: string
  scope: string
  tags: string[]
  created_at: string
  updated_at: string
}

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

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function expDotColor(status: string): string {
  switch (status) {
    case 'running': case 'starting': return 'var(--green)'
    case 'awaiting_human': return 'var(--amber)'
    case 'completed': return 'var(--fg-dim)'
    case 'error': case 'stopped': return 'var(--red)'
    default: return 'var(--fg-dim)'
  }
}

function expBadgeClass(status: string): string {
  switch (status) {
    case 'running': case 'starting': return 'running'
    case 'awaiting_human': return 'attention'
    case 'error': case 'stopped': return 'error'
    default: return 'reward'
  }
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
  experiments: ExperimentInfo[]
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
                    const slug = (exp.task_name || exp.run_id).split('/').pop()
                    const isRunning = ['running', 'starting'].includes(exp.status)
                    const isAttention = exp.status === 'awaiting_human'
                    const reward = exp.reward != null ? `${(exp.reward * 100).toFixed(0)}%` : null
                    const isExpActive = exp.run_id === activeExperimentId
                    return (
                      <div
                        key={exp.run_id}
                        className={`exp-item${isExpActive ? ' active' : ''}`}
                        onClick={(e) => { e.stopPropagation(); onSelectExperiment(exp.run_id) }}
                      >
                        <div className="exp-dot" style={{ background: expDotColor(exp.status) }} />
                        <div className="exp-name">{slug}</div>
                        <span className={`exp-badge ${expBadgeClass(exp.status)}`}>
                          {isRunning ? 'running' : isAttention ? 'needs input' : reward || exp.status}
                        </span>
                        <div className="exp-actions">
                          {isRunning && (
                            <span className="exp-action-btn stop" title="Stop" onClick={(e) => { e.stopPropagation(); onStopExperiment(c.campaign_id, exp.run_id) }}>
                              &#9632;
                            </span>
                          )}
                          <span className="exp-action-btn resume" title="Rerun" onClick={(e) => { e.stopPropagation(); onRerunExperiment(c.campaign_id, exp.run_id) }}>
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

function WelcomeView({ onSend }: { onSend: (text: string) => void }) {
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
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
    try { await onSend(input.trim()) } finally { setSending(false) }
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
            placeholder="e.g. Run the LHTB benchmark on the latest model..."
            rows={1}
          />
          <button className="send-btn" disabled={!input.trim() || sending} onClick={handleSubmit}>
            {sending ? <span className="spinner" /> : <SendSvgIcon />}
          </button>
        </div>
        <div className="hint-chips">
          {['Run LHTB benchmark', 'Analyze experiment data', 'Debug failing task'].map((hint) => (
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
  experiments: ExperimentInfo[]
  onSelectExperiment: (expId: string | null) => void
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [pendingApprovals, setPendingApprovals] = useState<string[]>([])
  const [approvalMessage, setApprovalMessage] = useState('')
  const [submittingApproval, setSubmittingApproval] = useState(false)
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
      await api.chat.status(campaignId)
    } catch {}
    try {
      const d = await api.chat.pendingApprovals(campaignId)
      setPendingApprovals(d.pending || [])
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
      console.error('Failed to send:', err)
    } finally {
      setSending(false)
    }
  }

  const handleApproval = async (approvalId: string, decision: string) => {
    setSubmittingApproval(true)
    try {
      await api.chat.approve(campaignId, approvalId, decision, approvalMessage)
      setApprovalMessage('')
      fetchStatus()
      setTimeout(fetchHistory, 1000)
    } catch (err) {
      console.error('Failed to approve:', err)
    } finally {
      setSubmittingApproval(false)
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

  const activeExpTab = !isProjectTab ? experiments.find((e) => e.run_id === activeTab) : null
  const tabTaskName = activeExpTab ? (activeExpTab.task_name || '').split('/').pop() : ''

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
          {pendingApprovals.length > 0 && (
            <span style={{
              fontSize: 9, fontWeight: 600, padding: '1px 6px', borderRadius: 10,
              background: 'var(--amber-dim)', color: 'var(--amber)', marginLeft: 2,
            }}>
              {pendingApprovals.length}
            </span>
          )}
        </div>
        {experiments.map((exp) => {
          const slug = (exp.task_name || exp.run_id).split('/').pop()
          const isActive = activeTab === exp.run_id
          return (
            <div
              key={exp.run_id}
              className={`context-tab${isActive ? ' active' : ''}`}
              onClick={() => { setActiveTab(exp.run_id); setMessages([]); onSelectExperiment(exp.run_id) }}
            >
              <span className="tab-dot" style={{ background: expDotColor(exp.status) }} />
              {slug}
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
                ? 'Send a message to interact with running experiments.'
                : `Chatting with ${tabTaskName}. Messages go directly to this experiment's agent.`}
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

      {/* Approval bar */}
      {isProjectTab && pendingApprovals.length > 0 && (
        <div className="approval-bar show">
          <div className="approval-label">
            <span style={{ fontSize: 14 }}>{'⚠'}</span>
            Approval Required
          </div>
          <div className="approval-actions">
            <textarea
              value={approvalMessage}
              onChange={(e) => setApprovalMessage(e.target.value)}
              placeholder="Optional: add guidance for the agent..."
              rows={2}
            />
            <button className="btn-sm btn-approve" disabled={submittingApproval} onClick={() => handleApproval(pendingApprovals[0], 'approve')}>
              {'✓'} Approve
            </button>
            <button className="btn-sm btn-modify" disabled={submittingApproval || !approvalMessage.trim()} onClick={() => handleApproval(pendingApprovals[0], 'modify')}>
              {'✎'} Modify
            </button>
            <button className="btn-sm btn-reject" disabled={submittingApproval} onClick={() => handleApproval(pendingApprovals[0], 'reject')}>
              {'✕'} Reject
            </button>
          </div>
        </div>
      )}

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
            Chatting with <span className="target">{isProjectTab ? 'Front Desk' : tabTaskName}</span>
            {isProjectTab && ' · switch to an experiment tab to see agent activity'}
          </span>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Experiment View (embedded, replaces ExperimentMonitor page)
// ---------------------------------------------------------------------------

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
  experiments: ExperimentInfo[]
  onSelectExperiment: (expId: string | null) => void
  streamRef: React.RefObject<HTMLDivElement | null>
  rightPanel: string
  setRightPanel: (v: string) => void
}) {
  const [run, setRun] = useState<RunDetail | null>(null)
  const [liveEvents, setLiveEvents] = useState<StreamEvent[]>([])
  const [streamCollapsed, setStreamCollapsed] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const fetchRun = useCallback(async () => {
    try {
      const data = await api.experiments.get(campaignId, experimentId)
      if (!data.error) {
        setRun(data)
        if (data.events?.length) {
          setLiveEvents(prev => {
            if (prev.length > 0) return prev
            return data.events.map((e: any) => ({
              event_type: e.event_type,
              data: e.data || {},
              timestamp: e.timestamp || 0,
              turn: e.data?.turn,
            }))
          })
        }
      }
    } catch {}
  }, [campaignId, experimentId])

  useEffect(() => {
    setLiveEvents([])
    setRun(null)
  }, [campaignId, experimentId])

  useEffect(() => {
    fetchRun()
    const interval = setInterval(fetchRun, 5000)
    return () => clearInterval(interval)
  }, [fetchRun])

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/api/campaigns/${campaignId}/experiments/${experimentId}/ws`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data)
        const streamEvent: StreamEvent = {
          event_type: data.event_type,
          data: data.data || {},
          timestamp: data.timestamp || Date.now() / 1000,
          turn: data.data?.turn,
        }
        setLiveEvents((prev) => {
          const next = [...prev, streamEvent]
          return next.length > 2000 ? next.slice(-2000) : next
        })
        if (['escalation', 'escalation_resolved', 'autonomy_changed'].includes(data.event_type)) {
          fetchRun()
        }
      } catch {}
    }
    ws.onclose = () => { wsRef.current = null }
    return () => { ws.close(); wsRef.current = null }
  }, [campaignId, experimentId, fetchRun])

  const handleStop = async () => {
    await api.experiments.stop(campaignId, experimentId)
    fetchRun()
  }

  const handleRerun = async () => {
    try {
      const result = await api.experiments.rerun(campaignId, experimentId)
      if (result.error) return
      onSelectExperiment(result.run_id)
    } catch {}
  }

  const handleResume = async () => {
    try {
      const result = await api.experiments.resume(campaignId, experimentId)
      if (result.error) return
      onSelectExperiment(result.run_id)
    } catch {}
  }

  const handleToggleAutonomy = async () => {
    if (!run) return
    const newMode = run.autonomy_mode === 'autonomous' ? 'supervised' : 'autonomous'
    await api.experiments.setAutonomyMode(campaignId, experimentId, newMode)
    fetchRun()
  }

  const elapsed = run ? ((run.finished_at || Date.now() / 1000) - run.started_at) : 0
  const isRunning = run?.status === 'running' || run?.status === 'starting' || run?.status === 'awaiting_human'
  const isFinished = run?.status === 'completed' || run?.status === 'stopped' || run?.status === 'error'

  return (
    <div className="exp-content">
      {/* Status strip */}
      <div className="status-strip">
        <div className="stat">
          <div className="stat-label">Status</div>
          <div className="stat-value" style={{ color: isRunning ? 'var(--green)' : isFinished ? 'var(--fg-muted)' : 'var(--fg)' }}>
            {run?.status || '—'}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Iteration</div>
          <div className="stat-value mono">{run?.turn ?? 0}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Elapsed</div>
          <div className="stat-value mono">{formatDuration(elapsed)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Reward</div>
          <div className="stat-value mono">
            {((run?.reward || 0) * 100).toFixed(1)}%
            <span className="reward-bar">
              <span className="reward-fill" style={{ width: `${(run?.reward || 0) * 100}%` }} />
            </span>
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Model</div>
          <div className="stat-value">{run?.model?.split('/').pop() || '—'}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Mode</div>
          <div className="stat-value" style={{ cursor: 'pointer', color: run?.autonomy_mode === 'supervised' ? 'var(--amber)' : 'var(--green)' }} onClick={handleToggleAutonomy}>
            {run?.autonomy_mode || 'autonomous'}
          </div>
        </div>
        <div className="exp-controls">
          {isRunning && (
            <button className="exp-ctrl-btn stop" onClick={handleStop}>
              <StopSvg /> Stop
            </button>
          )}
          {isFinished && (
            <>
              <button className="exp-ctrl-btn resume" onClick={handleResume}>
                <PlaySvg /> Resume
              </button>
              <button className="exp-ctrl-btn rerun" onClick={handleRerun}>
                <RerunSvg /> Re-run
              </button>
            </>
          )}
        </div>
      </div>

      {/* Approval bar (experiment-level escalation) */}
      {run?.pending_decision && !run.pending_decision.resolved && (
        <ExperimentApprovalBar
          campaignId={campaignId}
          decision={run.pending_decision}
          onResolved={fetchRun}
        />
      )}

      {/* Dual-column body */}
      <div className="exp-body" style={{ position: 'relative' }}>
        {/* Front Desk chat (center) */}
        <div className="fd-col">
          <ChatArea
            campaignId={campaignId}
            experimentId={experimentId}
            experiments={experiments}
            onSelectExperiment={onSelectExperiment}
          />
        </div>

        {/* Resize handle between fd-col and tl-wrap */}
        {!streamCollapsed && <ResizeHandle targetRef={streamRef} side="left" minWidth={200} maxWidth={900} />}

        {/* Timeline Stream / Workspace (right) */}
        <div className={`tl-wrap${streamCollapsed ? ' collapsed' : ''}`} ref={streamRef}>
          {rightPanel === 'files' ? (
            <div style={{ flex: 1, overflow: 'auto', padding: 0 }}>
              <WorkspacePanel campaignId={campaignId} expId={experimentId} />
            </div>
          ) : (
            <div style={{ flex: 1, overflowY: 'auto' }}>
              <AgentStreamView events={liveEvents} />
            </div>
          )}
        </div>

        {/* Stream restore button when collapsed */}
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
// Experiment Approval Bar
// ---------------------------------------------------------------------------

function ExperimentApprovalBar({
  campaignId,
  decision,
  onResolved,
}: {
  campaignId: string
  decision: RunDetail['pending_decision']
  onResolved: () => void
}) {
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (!decision) return null

  const handleDecide = async (action: string) => {
    setSubmitting(true)
    try {
      await api.experiments.decide(campaignId, decision.decision_id, action, message)
      setMessage('')
      onResolved()
    } catch (err) {
      console.error('Decision failed:', err)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="approval-bar show">
      <div className="approval-label">
        <span style={{ fontSize: 14 }}>{'⚠'}</span>
        {decision.source} needs your decision
      </div>
      <div className="approval-desc">{decision.context}</div>
      <div className="approval-actions">
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Optional: add guidance..."
          rows={2}
        />
        {decision.options.map((opt) => {
          const cls = opt === 'approve' ? 'btn-approve' : opt === 'reject' ? 'btn-reject' : 'btn-modify'
          return (
            <button key={opt} className={`btn-sm ${cls}`} disabled={submitting} onClick={() => handleDecide(opt)}>
              {opt}
            </button>
          )
        })}
      </div>
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
// Campaign Chat View (header + chat area, no experiment)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Phase Pipeline Selector
// ---------------------------------------------------------------------------

const ALL_PHASES: readonly { key: string; label: string; desc: string; required?: boolean }[] = [
  { key: 'roundtable', label: 'Roundtable', desc: 'Multi-agent brainstorming' },
  { key: 'screening', label: 'Screening', desc: 'Feasibility evaluation' },
  { key: 'planning', label: 'Planning', desc: 'Structured execution plan' },
  { key: 'research_loop', label: 'Research', desc: 'Agent executes the task', required: true },
  { key: 'analysis', label: 'Analysis', desc: 'Result analysis & follow-up' },
  { key: 'writing', label: 'Writing', desc: 'Report generation' },
]

const PRESETS: Record<string, { label: string; skip: string[]; desc: string }> = {
  quick: { label: 'Quick', skip: ['roundtable', 'screening', 'planning', 'analysis', 'writing'], desc: 'Direct execution only' },
  standard: { label: 'Standard', skip: ['roundtable', 'writing'], desc: 'Screen → Plan → Execute → Analyze' },
  full: { label: 'Full', skip: [], desc: 'Complete research pipeline' },
}

function PhasePipelineSelector({
  skipPhases,
  onChange,
  onClose,
}: {
  skipPhases: string[]
  onChange: (skip: string[]) => void
  onClose: () => void
}) {
  const togglePhase = (key: string) => {
    if (key === 'research_loop') return
    if (skipPhases.includes(key)) {
      onChange(skipPhases.filter((p) => p !== key))
    } else {
      onChange([...skipPhases, key])
    }
  }

  const activePreset = Object.entries(PRESETS).find(
    ([, v]) => v.skip.length === skipPhases.length && v.skip.every((p) => skipPhases.includes(p))
  )?.[0] || null

  return (
    <div className="phase-selector">
      <div className="phase-selector-header">
        <span className="phase-selector-title">Pipeline Phases</span>
        <div className="phase-presets">
          {Object.entries(PRESETS).map(([key, preset]) => (
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
        <button className="phase-selector-close" onClick={onClose}>{'×'}</button>
      </div>
      <div className="phase-chips">
        {ALL_PHASES.map((phase) => {
          const active = !skipPhases.includes(phase.key)
          const isRequired = phase.required
          return (
            <button
              key={phase.key}
              className={`phase-chip${active ? ' active' : ''}${isRequired ? ' required' : ''}`}
              onClick={() => togglePhase(phase.key)}
              title={phase.desc}
            >
              <span className="phase-chip-dot" />
              <span className="phase-chip-label">{phase.label}</span>
            </button>
          )
        })}
      </div>
      <div className="phase-flow">
        {ALL_PHASES.filter((p) => !skipPhases.includes(p.key)).map((p, i, arr) => (
          <span key={p.key} className="phase-flow-item">
            {p.label}{i < arr.length - 1 ? ' → ' : ''}
          </span>
        ))}
      </div>
    </div>
  )
}


function ProposalCard({
  proposal,
  campaignId,
  onConfirmed,
}: {
  proposal: ProposalData
  campaignId: string
  onConfirmed: (expId: string) => void
}) {
  const [skipPhases, setSkipPhases] = useState<string[]>(proposal.skip_phases)
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(proposal.confirmed || false)
  const [showInstruction, setShowInstruction] = useState(false)

  const handleConfirm = async () => {
    setConfirming(true)
    try {
      const resp = await fetch(`/api/lhtb/campaigns/${campaignId}/proposals/${proposal.proposal_id}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skip_phases: skipPhases }),
      })
      const data = await resp.json()
      if (data.error) {
        console.error('Confirm failed:', data.error)
      } else {
        setConfirmed(true)
        if (data.experiment_id) onConfirmed(data.experiment_id)
      }
    } catch (err) {
      console.error('Confirm failed:', err)
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

  const instruction = proposal.params.instruction || ''
  const taskDir = proposal.params.task_dir || ''

  return (
    <div className="proposal-card">
      <div className="proposal-header">
        <span className="proposal-icon">{'⟡'}</span>
        <span className="proposal-title">Experiment Proposal</span>
        <span className="proposal-kind">{proposal.kind === 'predefined' ? 'Benchmark' : 'Custom'}</span>
      </div>
      <div className="proposal-summary">{proposal.summary}</div>

      {/* Show instruction or task path */}
      {instruction && (
        <div className="proposal-detail">
          <button
            className="proposal-detail-toggle"
            onClick={() => setShowInstruction(!showInstruction)}
          >
            {showInstruction ? '▾' : '▸'} Instruction
          </button>
          {showInstruction && (
            <div className="proposal-instruction">{instruction}</div>
          )}
        </div>
      )}
      {taskDir && (
        <div className="proposal-meta">Task: {taskDir}</div>
      )}
      {proposal.params.model && (
        <div className="proposal-meta">Model: {proposal.params.model}</div>
      )}

      <PhasePipelineSelector
        skipPhases={skipPhases}
        onChange={setSkipPhases}
        onClose={() => {}}
      />

      <div className="proposal-actions">
        <div className="proposal-hint">Adjust phases above, or tell Front Desk what to change</div>
        <button
          className="proposal-confirm"
          disabled={confirming}
          onClick={handleConfirm}
        >
          {confirming ? 'Starting...' : 'Confirm & Start'}
        </button>
      </div>
    </div>
  )
}


function CampaignChatView({
  campaignId,
  campaign,
  experiments,
  onSelectExperiment,
}: {
  campaignId: string
  campaign: CampaignSummary | null
  experiments: ExperimentInfo[]
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
  const [experiments, setExperiments] = useState<ExperimentInfo[]>([])
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
      if (data.experiments) {
        setExperiments(data.experiments.map((e: any) => ({
          run_id: e.run_id,
          task_name: e.task_name,
          status: e.status,
          reward: e.reward,
        })))
      }
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
    } catch (err) { console.error('Failed to stop:', err) }
  }

  const handleRerunExperiment = async (campaignId: string, expId: string) => {
    try {
      await api.experiments.rerun(campaignId, expId)
      fetchExperiments()
    } catch (err) { console.error('Failed to rerun:', err) }
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
                  <span className="chat-target">{experiments.find(e => e.run_id === activeExperimentId)?.task_name?.split('/').pop() || activeExperimentId}</span>
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

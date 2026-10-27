import { Fragment, Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, describeError } from '../api/client'
import { AgentStreamView } from '../components/AgentStreamView'
import { AnalyticsPanel, SearchPanel, SettingsPanel } from '../components/ConsolePanels'
import { ProjectFilesDialog } from '../components/ProjectFiles'
import { useFilePickers } from '../useFilePickers'
import { fromDrop } from '../upload'
import { useAttachments } from '../useAttachments'
import { AttachmentCards, SentAttachments } from '../components/AttachmentCards'
import { ConfirmDelete, InlineRename, ItemMenu, type MenuItem, PencilIcon, RerunIcon, StopIcon, TrashIcon } from '../components/ItemMenu'
import { ScopePicker, type ScopeValue } from '../components/SkillScope'
import { WorkspacePanel } from '../components/WorkspacePanel'
import { ShareDialog } from '../components/ShareDialog'
import { TeamView } from '../components/TeamView'
import { LiteratureView, RULE_HINTS, RULE_LABELS, RuleSelect } from '../components/LiteratureView'
import { LibraryView } from '../components/LibraryView'
import { MemoryView } from '../components/memory/MemoryView'
import { CiteProvider, CiteText } from '../components/Cite'
import { SetupCard, TEMPLATES } from '../components/Welcome'
import { CompareView } from '../components/results/CompareView'
import { ReportView } from '../components/results/ReportView'
import { ForkDialog } from '../components/results/ForkDialog'
import { BrainstormView } from '../components/brainstorm/BrainstormView'
import { AccountButton } from '../components/AccountMenu'
import { useAuth } from '../auth'
import { ClusterJobsPanel } from '../components/ClusterJobsPanel'
import { formatCost, formatDuration } from '../format'
import { WelcomeWizard } from '../onboarding/WelcomeWizard'
import { Tour } from '../onboarding/Tour'
import { DocViewer, HelpPanel } from '../onboarding/HelpPanel'
import { ExamplesView } from '../onboarding/ExamplesView'
import { FirstTip } from '../onboarding/FirstTip'
import { loadOnboarding, startTour, updateOnboarding, useOnboarding } from '../onboarding/store'
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
  type AddFound,
  type AutonomyMode,
  type ConfirmedSettings,
  type ConsoleSettings,
  type ContainerResources,
  type GateAction,
  type IdeationMode,
  type ModelSettings,
  type PendingDecision,
  type Phase,
  type Proposal,
  type Skill,
  type LibraryRef,
  type OtherProject,
  type RunEstimate,
} from '../types'
import { turnKey, type StreamEvent } from '../types/chat'
import type { ReadRequest } from '../components/reader/PaperReader'
import type { MapRequest } from '../components/review/FieldMapView'

// the paper reader (and pdf.js) load when a paper is first opened
const PaperReader = lazy(() => import('../components/reader/PaperReader'))
const FieldMapView = lazy(() => import('../components/review/FieldMapView'))

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
const TeamIcon = () => (
  <svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 19a5.5 5.5 0 0111 0"/><path d="M16 5.2a3 3 0 010 5.6M17.5 14a5 5 0 013.5 5"/></svg>
)
const BulbIcon = () => (
  <svg viewBox="0 0 24 24"><path d="M9 18h6M10 21h4M12 3a6 6 0 00-3.6 10.8c.7.5 1.1 1.3 1.1 2.2h5c0-.9.4-1.7 1.1-2.2A6 6 0 0012 3z"/></svg>
)
const WaveIcon = () => (
  <svg viewBox="0 0 24 24"><path d="M3 9c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2 2.5 2 3 2M3 15c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2 2.5 2 3 2"/></svg>
)
const BooksIcon = () => (
  <svg viewBox="0 0 24 24"><path d="M4 4h4v16H4zM10 4h4v16h-4z"/><path d="M16.5 4.6l3.8-1 3.7 15.5-3.8.9z" transform="translate(-2 1)"/></svg>
)
const ShareIcon = () => svgIcon(<><circle cx="18" cy="5" r="2.5" /><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="19" r="2.5" /><path d="M8.2 10.9l7.6-4.4M8.2 13.1l7.6 4.4" /></>)
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

const svgIcon = (path: React.ReactNode, size = 15) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{path}</svg>
)
const PlusIcon = () => svgIcon(<><path d="M12 5v14" /><path d="M5 12h14" /></>, 18)
const FileIcon = () => svgIcon(<><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></>)
const ClipIcon = () => svgIcon(<path d="M21 11.5 12.5 20a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l7.9-7.9" />)

/** A page that takes the main area instead of a project. */
type MainPage = 'team' | 'library' | 'brainstorm' | 'examples' | 'memory' | null

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).map((x) => x[0]).join('').slice(0, 2).toUpperCase() || '?'
}

/** A small mark after a project's name: shared with others, or confidential. */
function sharedMark(c: CampaignSummary) {
  const s = c.sharing
  if (!s) return null
  if (s.confidential) return <span className="camp-share" title="Confidential">{svgIcon(<><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 018 0v4" /></>, 12)}</span>
  if (s.members > 1 || s.team_access !== 'none') {
    const who = s.team_access !== 'none' ? 'the team' : `${s.members} people`
    return <span className="camp-share" title={`Shared with ${who}`}>{svgIcon(<><circle cx="9" cy="8" r="3.2" /><path d="M3.5 19a5.5 5.5 0 0111 0" /><path d="M16 5.2a3 3 0 010 5.6M17.5 14a5 5 0 013.5 5" /></>, 12)}</span>
  }
  return null
}

// ---------------------------------------------------------------------------
// Activity Bar
// ---------------------------------------------------------------------------

function ActivityBar({
  activePanel,
  explorerActive,
  page,
  onTogglePanel,
  onOpenPage,
  helpBadge,
}: {
  activePanel: string
  explorerActive: boolean
  page: MainPage
  onTogglePanel: (panel: string) => void
  onOpenPage: (page: MainPage) => void
  /** Things of the getting-started checklist not tried yet. */
  helpBadge: number
}) {
  return (
    <div className="activity-bar">
      <button className={`ab-btn${activePanel === 'projects' ? ' active' : ''}`} title="Projects" onClick={() => onTogglePanel('projects')}><FolderIcon /></button>
      <button className={`ab-btn${page === 'brainstorm' ? ' active' : ''}`} title="Brainstorm" onClick={() => onOpenPage(page === 'brainstorm' ? null : 'brainstorm')}><BulbIcon /></button>
      <button className={`ab-btn${page === 'library' ? ' active' : ''}`} title="Library" onClick={() => onOpenPage(page === 'library' ? null : 'library')}><BooksIcon /></button>
      <button className={`ab-btn${page === 'memory' ? ' active' : ''}`} title="Memory" onClick={() => onOpenPage(page === 'memory' ? null : 'memory')}><WaveIcon /></button>
      <button className={`ab-btn${activePanel === 'search' ? ' active' : ''}`} title="Search" onClick={() => onTogglePanel('search')}><SearchIcon /></button>
      <button className={`ab-btn${activePanel === 'analytics' ? ' active' : ''}`} title="Analytics" onClick={() => onTogglePanel('analytics')}><ChartIcon /></button>
      <button className={`ab-btn${explorerActive ? ' active' : ''}`} title="Explorer" onClick={() => onTogglePanel('explorer')}><ExplorerIcon /></button>
      <button className={`ab-btn${activePanel === 'skills' ? ' active' : ''}`} title="Skills" onClick={() => onTogglePanel('skills')}><SkillsIcon /></button>
      <button className={`ab-btn${activePanel === 'settings' ? ' active' : ''}`} title="Settings" onClick={() => onTogglePanel('settings')}><SettingsIcon /></button>
      <div className="ab-spacer" />
      <button className={`ab-btn ab-help${activePanel === 'help' ? ' active' : ''}`} title="Help" aria-label="Help, the getting-started checklist and the tours"
        onClick={() => onTogglePanel('help')}>?{helpBadge > 0 && <span className="ab-badge" aria-label={`${helpBadge} things to try`}>{helpBadge}</span>}</button>
      <button className={`ab-btn${page === 'team' ? ' active' : ''}`} title="Team" onClick={() => onOpenPage(page === 'team' ? null : 'team')}><TeamIcon /></button>
      <AccountButton onOpenTeam={() => onOpenPage('team')} />
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
  onRenameCampaign,
  onDeleteCampaign,
  onRenameExperiment,
  onDeleteExperiment,
  onOpenFiles,
  onShare,
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
  onRenameCampaign: (campaignId: string, name: string) => void
  onDeleteCampaign: (campaign: CampaignSummary) => void
  onRenameExperiment: (campaignId: string, expId: string, name: string) => void
  onDeleteExperiment: (campaignId: string, exp: ExperimentSummary) => void
  onOpenFiles: (campaignId: string) => void
  onShare: (campaign: CampaignSummary) => void
}) {
  const auth = useAuth()
  const accounts = auth.accounts
  // the project or run whose name is being edited ("c:<id>" or "e:<id>")
  const [renaming, setRenaming] = useState<string | null>(null)
  // projects of the team you are not in, when the team lets members see that they exist
  const [others, setOthers] = useState<OtherProject[]>([])
  const [otherOpen, setOtherOpen] = useState<string | null>(null)
  const [showOthers, setShowOthers] = useState(false)
  const memberCount = campaigns.length
  useEffect(() => {
    if (!accounts || auth.user.team_role === 'guest') return
    api.others().then(setOthers).catch(() => setOthers([]))
  }, [accounts, auth.user.team_role, memberCount])
  return (
    <div className={`sidebar${collapsed ? ' collapsed' : ''}`} ref={sidebarRef} data-tour="sidebar">
      <div className="sidebar-brand">
        <div className="logo">A</div>
        <div className="name">AURORA</div>
        <button className="new-btn" title="New project" onClick={onNewProject}>+</button>
      </div>
      <div className="sidebar-list">
        <div className="sidebar-section">Projects</div>
        {campaigns.length === 0 && (
          <div className="sidebar-empty">
            <span>No projects yet. A project holds the runs on one question.</span>
            <button className="link-btn" onClick={onNewProject}>Start one</button>
            <button className="link-btn" onClick={() => window.dispatchEvent(new CustomEvent('aurora:open-page', { detail: 'examples' }))}>Look at the examples</button>
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
                  {renaming === `c:${c.campaign_id}` ? (
                    <InlineRename value={c.name} onCancel={() => setRenaming(null)}
                      onSave={(name) => { setRenaming(null); onRenameCampaign(c.campaign_id, name) }} />
                  ) : (
                    <div className="camp-name">{sharedMark(c)}{c.name}</div>
                  )}
                  {c.example
                    ? <div className="camp-meta"><span className="camp-example">example · read-only</span></div>
                    : <div className="camp-meta">{c.objective || 'No objective'}</div>}
                </div>
                <div className="camp-time">{relativeTime(c.updated_at)}</div>
                {c.example ? <ItemMenu label="Example actions" items={[
                  { label: 'Duplicate it as my project', icon: <PencilIcon />, onSelect: () => window.dispatchEvent(new CustomEvent('aurora:duplicate-example', { detail: c.campaign_id })) },
                  { label: 'Hide the examples', icon: <TrashIcon />, divided: true, onSelect: () => updateOnboarding({ examples_hidden: true }).then(() => window.dispatchEvent(new Event('aurora:projects-changed'))).catch(() => {}) },
                ]} /> : <ItemMenu label="Project actions" items={[
                  { label: 'Files', icon: <FolderIcon />, onSelect: () => onOpenFiles(c.campaign_id) },
                  ...(accounts ? [{ label: 'Share', icon: <ShareIcon />, onSelect: () => onShare(c) }] : []),
                  { label: 'Rename', icon: <PencilIcon />, onSelect: () => setRenaming(`c:${c.campaign_id}`) },
                  { label: 'Delete', icon: <TrashIcon />, danger: true, divided: true, onSelect: () => onDeleteCampaign(c) },
                ]} />}
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
                        {renaming === `e:${exp.run_id}` ? (
                          <InlineRename value={experimentName(exp)} onCancel={() => setRenaming(null)}
                            onSave={(name) => { setRenaming(null); onRenameExperiment(c.campaign_id, exp.run_id, name) }} />
                        ) : (
                          <div className="exp-name">{experimentName(exp)}</div>
                        )}
                        <span className={`exp-badge ${expBadgeClass(exp.status)}`}>{statusLabel(exp.status)}</span>
                        {!c.example && <ItemMenu label="Run actions" items={[
                          { label: 'Rename', icon: <PencilIcon />, onSelect: () => setRenaming(`e:${exp.run_id}`) },
                          ...(isActive ? [{ label: 'Stop', icon: <StopIcon />, onSelect: () => onStopExperiment(c.campaign_id, exp.run_id) }] : []),
                          { label: 'Re-run with the same settings', icon: <RerunIcon />, onSelect: () => onRerunExperiment(c.campaign_id, exp.run_id) },
                          { label: 'Delete', icon: <TrashIcon />, danger: true, divided: true, onSelect: () => onDeleteExperiment(c.campaign_id, exp) },
                        ]} />}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
        {others.length > 0 && (
          <>
            <button className="sidebar-section sidebar-others" aria-expanded={showOthers} onClick={() => setShowOthers(!showOthers)}>
              {showOthers ? '▾' : '▸'} Other team projects · {others.length}
            </button>
            {showOthers && others.map((o) => (
              <div key={o.campaign_id} className="camp-item locked" onClick={() => setOtherOpen(otherOpen === o.campaign_id ? null : o.campaign_id)}>
                <div className="camp-dot idle" />
                <div className="camp-body">
                  <div className="camp-name"><span className="camp-share" aria-hidden="true">{svgIcon(<><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 018 0v4" /></>, 12)}</span>{o.name}</div>
                  <div className="camp-meta">{otherOpen === o.campaign_id
                    ? <>You are not in this project. Ask {o.owner || 'its owner'}{o.owner_email ? ` (${o.owner_email})` : ''} to invite you.</>
                    : `${o.owner || 'Someone'}’s project`}</div>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Welcome View
// ---------------------------------------------------------------------------

// the templates of a field first
const FIELD_TEMPLATES: Record<string, string[]> = {
  materials: ['simulation', 'open', 'reproduce'], ml: ['ablation', 'reproduce', 'data'], bio: ['data', 'reproduce', 'open'],
  data: ['data', 'open', 'reproduce'],
}

function WelcomeView({ onSend, onOpenRun, onOpenExample }: {
  onSend: (text: string) => Promise<void>
  onOpenRun: (cid: string, runId: string) => void
  /** The finished example, with the tour. */
  onOpenExample: () => void
}) {
  const ob = useOnboarding()
  const order = FIELD_TEMPLATES[ob?.field ?? ''] ?? []
  const templates = TEMPLATES.slice().sort((a, b) => (order.indexOf(a.id) + 1 || 99) - (order.indexOf(b.id) + 1 || 99))
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [template, setTemplate] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const demo = async () => {
    setError(null)
    try { const r = await api.setup.demo(); onOpenRun(r.campaign_id, r.experiment_id) } catch (e) { setError(`Could not start the demo: ${describeError(e)}`) }
  }
  const applyTemplate = (id: string) => {
    const t = TEMPLATES.find((x) => x.id === id)
    if (!t) return
    setTemplate(id)
    setInput(t.text)
    window.setTimeout(() => {
      const el = textareaRef.current
      if (!el) return
      el.focus()
      const start = t.text.indexOf('[')
      if (start >= 0) el.setSelectionRange(start, t.text.indexOf(']', start) + 1)
    }, 0)
  }

  useEffect(() => { textareaRef.current?.focus() }, [])

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 260) + 'px'
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
        <SetupCard onDemo={demo} />
        <div className="input-wrap" data-tour="brief">
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
        {template && input.includes('[') && <div className="welcome-hint">Fill in the parts in [brackets] (the first is selected), then send. The front desk asks about anything unclear before it proposes a run.</div>}
        <div className="templates" role="group" aria-label="Start from a template" data-tour="templates">
          {templates.map((t) => (
            <button key={t.id} className={`template${template === t.id ? ' on' : ''}`} onClick={() => applyTemplate(t.id)}>
              <b>{t.title}</b><span>{t.blurb}</span><span className="lit-sub">{t.phases}</span>
            </button>
          ))}
        </div>
        <div className="welcome-more" aria-label="Not sure where to start">
          <span className="lit-sub">Not sure where to start?</span>
          <button className="link-btn" onClick={onOpenExample}>Walk through a finished run</button>
          <button className="link-btn" onClick={demo}>Watch a run happen</button>
          <button className="link-btn" onClick={() => startTour('app')}>Tour the console</button>
          <button className="link-btn" onClick={() => window.dispatchEvent(new CustomEvent('aurora:open-panel', { detail: 'help' }))}>Help and checklist</button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Chat Area (shared between campaign-level and experiment-level chat)
// ---------------------------------------------------------------------------

// What other pages hand to a project's chat: an item from the Library for its front desk, a tab to open
const projectMailbox: Record<string, { refs: LibraryRef[]; tab?: string; draft?: string }> = {}
function toProject(campaignId: string, what: { ref?: LibraryRef; tab?: string; draft?: string }) {
  const box = projectMailbox[campaignId] ??= { refs: [] }
  if (what.draft) box.draft = what.draft
  if (what.ref && !box.refs.some((r) => (r.item_id ?? r.paper_id) === (what.ref!.item_id ?? what.ref!.paper_id))) box.refs.push(what.ref)
  if (what.tab) box.tab = what.tab
  window.dispatchEvent(new CustomEvent('aurora:project-mail', { detail: campaignId }))
}

/** Items from the Library to attach to a message: what you saved from the feed, and your papers. */
function LibraryPicker({ onPick, onClose }: { onPick: (r: LibraryRef) => void; onClose: () => void }) {
  const [items, setItems] = useState<LibraryRef[] | null>(null)
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.feed.attachable().then((r) => setItems(r.items)).catch((e) => setError(describeError(e)))
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  const shown = (items ?? []).filter((r) => !q || (r.title + r.by + (r.where ?? '')).toLowerCase().includes(q.toLowerCase()))
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal lit-modal" role="dialog" aria-modal="true" aria-label="Attach from the Library" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">Attach from the Library</div>
        <div className="modal-body">
          <input autoFocus placeholder="Filter" aria-label="Filter" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="lib-pick-list">
            {shown.map((r) => (
              <button key={(r.item_id ?? r.paper_id) + (r.where ?? '')} className="lib-pick" onClick={() => { onPick(r); onClose() }}>
                <span className="lit-title">{r.title}</span>
                <span className="lit-sub">{r.label}{r.ok ? ' ✓' : ''} · {r.by ? `${r.by} · ` : ''}{r.where}</span>
              </button>
            ))}
            {items && !shown.length && <div className="modal-hint">Nothing saved yet. Use “Save to project” on the Library’s feed, or save papers from a search.</div>}
            {!items && !error && <div className="modal-hint">Loading…</div>}
          </div>
          {error && <div className="modal-error">{error}</div>}
          <div className="modal-hint">The front desk reads what you attach (the summary, the abstract and why the feed picked it) with your message.</div>
        </div>
        <div className="modal-actions"><button className="btn-sm" onClick={onClose}>Close</button></div>
      </div>
    </div>
  )
}

function LibraryRefCards({ refs, onRemove }: { refs: LibraryRef[]; onRemove?: (r: LibraryRef) => void }) {
  return (
    <div className="lib-refs">
      {refs.map((r) => (
        <div key={(r.item_id ?? r.paper_id) ?? r.title} className="lib-ref">
          <div className="lib-ref-title">{r.title}</div>
          <div className="lib-ref-meta"><span className={`fd-badge k-${r.label.toLowerCase() === 'paper' ? 'paper' : r.label.toLowerCase()}`}>{r.label}{r.ok ? ' ✓' : ''}</span>
            {r.by && <span className="lit-sub">{r.by}</span>}
            {onRemove && <button className="link-btn" aria-label="Remove" onClick={() => onRemove(r)}>×</button>}</div>
        </div>
      ))}
    </div>
  )
}

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
  const [showFiles, setShowFiles] = useState<'manage' | 'pick' | null>(null)
  const [chatDragging, setChatDragging] = useState(false)
  const chatAreaRef = useRef<HTMLDivElement>(null)
  const isNearBottom = useRef(true)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [libRefs, setLibRefs] = useState<LibraryRef[]>([])
  const [pickLibrary, setPickLibrary] = useState(false)

  const isProjectTab = activeTab === 'project'

  useEffect(() => {
    if (experimentId) {
      setActiveTab(experimentId)
    }
  }, [experimentId])

  // what another page handed to this project: an item for the front desk, a tab to open
  useEffect(() => {
    const take = () => {
      const box = projectMailbox[campaignId]
      if (!box) return
      delete projectMailbox[campaignId]
      if (box.refs.length) {
        setActiveTab('project')
        setLibRefs((prev) => [...prev, ...box.refs.filter((r) => !prev.some((p) => (p.item_id ?? p.paper_id) === (r.item_id ?? r.paper_id)))])
        setTimeout(() => textareaRef.current?.focus(), 50)
      }
      if (box.tab) setActiveTab(box.tab)
      if (box.draft) {
        setActiveTab('project')
        setInput(box.draft)
        setTimeout(() => textareaRef.current?.focus(), 50)
      }
    }
    take()
    const on = (e: Event) => { if ((e as CustomEvent<string>).detail === campaignId) take() }
    window.addEventListener('aurora:project-mail', on)
    return () => window.removeEventListener('aurora:project-mail', on)
  }, [campaignId])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const fetchHistory = useCallback(async () => {
    try {
      if (activeTab === 'literature' || activeTab === 'compare') return
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
    const files = att.ready
    const refs = isProjectTab ? libRefs : []
    if ((!input.trim() && !files.length && !refs.length) || sending || att.uploading) return
    const text = input.trim()
    setInput('')
    att.clear()
    setLibRefs([])
    setSending(true)
    setSendError(null)
    setMessages((prev) => [...prev, { role: 'user', text, timestamp: Date.now() / 1000, attachments: files, ...(refs.length ? { library: refs } : {}) }])
    try {
      if (!isProjectTab) {
        const resp = await api.expChat.send(campaignId, activeTab, text, files)
        if (resp.status === 'frontdesk_off') {
          setMessages((prev) => [...prev, { role: 'system', text: resp.response, timestamp: Date.now() / 1000 }])
        }
      } else {
        const resp = await api.chat.send(campaignId, text, files, refs)
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

  const tabExp = isProjectTab ? undefined : experiments.find((e) => e.run_id === activeTab)
  const tabTaskName = isProjectTab ? '' : experimentName(tabExp)
  const tabRunning = !!tabExp && isActiveStatus(tabExp.status)
  const deskOff = !!tabExp && tabExp.frontdesk_enabled === false
  const chatClosed = deskOff && !tabRunning          // nobody would answer
  const placeholder = isProjectTab ? (libRefs.length ? 'What would you like to know about it? (or just send)' : 'Message the project...')
    : chatClosed ? 'The front desk of this experiment is off (switch it on in the header)'
    : deskOff ? `Front desk off: messages go straight to the research agent of ${tabTaskName}`
    : tabRunning ? `Message ${tabTaskName}...` : `Ask about ${tabTaskName}...`
  const pendingDecision = pendingDecisions[0]

  // Files attached to the message: in the project chat they go to the project's files, in an
  // experiment's chat to its inputs/ (the agents' /app/inputs); they are sent with the message
  const uploadUrl = useCallback((path: string) => (isProjectTab
    ? api.projectFiles.uploadUrl(campaignId, path)
    : api.workspace.uploadUrl(campaignId, activeTab, `workspace/inputs/${path}`)), [isProjectTab, campaignId, activeTab])
  const discard = useCallback((path: string) => {
    if (isProjectTab) api.projectFiles.remove(campaignId, path).catch(() => {})
  }, [isProjectTab, campaignId])
  const att = useAttachments(uploadUrl, discard)
  const chatPickers = useFilePickers(att.add)
  const attachMenu: MenuItem[] = [
    { label: 'Upload files', icon: <FileIcon />, onSelect: () => chatPickers.pick('files') },
    { label: 'Upload a folder', icon: <FolderIcon />, onSelect: () => chatPickers.pick('folder') },
    ...(isProjectTab ? [
      { label: 'From the project’s files', icon: <ClipIcon />, divided: true, onSelect: () => setShowFiles('pick') },
      { label: 'From the Library', icon: <BooksIcon />, onSelect: () => setPickLibrary(true) },
      { label: 'Manage project files', icon: <ClipIcon />, onSelect: () => setShowFiles('manage') },
    ] : []),
  ]

  return (
    <CiteProvider key={campaignId} campaignId={campaignId}>
    <div className="camp-content">
      {/* Context bar */}
      <div className="context-bar" data-tour="tabs">
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
        <div className="context-tab" onClick={() => window.dispatchEvent(new CustomEvent('aurora:open-brainstorm', { detail: campaignId }))}>
          <span className="tab-dot" style={{ background: 'var(--amber)' }} />
          Brainstorm
        </div>
        <div className={`context-tab lit-tab${activeTab === 'literature' ? ' active' : ''}`} onClick={() => setActiveTab('literature')}>
          <span className="tab-dot" style={{ background: activeTab === 'literature' ? 'var(--accent)' : 'var(--green)' }} />
          Literature
        </div>
        {experiments.length > 1 && (
          <div className={`context-tab${activeTab === 'compare' ? ' active' : ''}`}
            onClick={() => {
              if (!experimentId) { setActiveTab('compare'); return }
              // the project's own chat view opens on the comparison (this view goes away)
              const box = projectMailbox[campaignId] ??= { refs: [] }
              box.tab = 'compare'
              onSelectExperiment(null)
            }}>
            <span className="tab-dot" style={{ background: activeTab === 'compare' ? 'var(--accent)' : 'var(--fg-dim)' }} />
            Compare
          </div>
        )}
      </div>

      {activeTab === 'compare' ? <CompareView campaignId={campaignId} onOpenRun={(id) => { setActiveTab(id); onSelectExperiment(id) }} />
        : activeTab === 'literature' ? <LiteratureView campaignId={campaignId} /> : <>
      {/* Chat messages (files can be dropped here) */}
      {chatPickers.inputs}
      {pickLibrary && <LibraryPicker onPick={(r) => setLibRefs((prev) => prev.some((p) => (p.item_id ?? p.paper_id) === (r.item_id ?? r.paper_id)) ? prev : [...prev, r])} onClose={() => setPickLibrary(false)} />}
      {showFiles && (
        <ProjectFilesDialog campaignId={campaignId} mode={showFiles}
          onPick={(picked) => att.addExisting(picked.map((x) => x.replace(/^project:/, '')))}
          onClose={() => { setShowFiles(null); fetchHistory() }} />
      )}
      <div className={`chat-area${chatDragging ? ' dragging' : ''}`} ref={chatAreaRef}
        onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setChatDragging(true) } }}
        onDragLeave={(e) => { if (e.currentTarget === e.target) setChatDragging(false) }}
        onDrop={async (e) => { e.preventDefault(); setChatDragging(false); if (!chatClosed) att.add(await fromDrop(e.dataTransfer)) }}
        onScroll={() => {
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
            <div key={`${msg.timestamp}-${i}`} className={`msg-row${isUser ? ' user' : ''}`}>
              <div className={`msg-avatar ${isUser ? 'human' : 'bot'}`} title={isUser ? msg.by : undefined}>
                {isUser ? (msg.by ? initials(msg.by) : 'Y') : 'M'}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                {msg.library && msg.library.length > 0 && <LibraryRefCards refs={msg.library} />}
                {(msg.text || (!msg.attachments?.length && !msg.library?.length)) && <div className={`msg-bubble ${isUser ? 'user' : 'bot'}`}>{isUser ? msg.text : <CiteText text={msg.text} />}</div>}
                {msg.attachments && msg.attachments.length > 0 && <SentAttachments paths={msg.attachments} />}
                {msg.proposal && isProjectTab && (
                  <ProposalCard
                    key={msg.proposal.proposal_id}
                    proposal={msg.proposal}
                    campaignId={campaignId}
                    onConfirmed={(expId) => {
                      onSelectExperiment(expId)
                      setTimeout(fetchHistory, 1000)
                    }}
                    onOpen={onSelectExperiment}
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
      <div className="input-bar" data-tour="input">
        <div className="input-wrap with-cards">
          <AttachmentCards attachments={att.attachments} onRemove={att.remove} />
          {isProjectTab && libRefs.length > 0 && <LibraryRefCards refs={libRefs} onRemove={(r) => setLibRefs(libRefs.filter((x) => x !== r))} />}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
            }}
            placeholder={placeholder}
            disabled={chatClosed}
            rows={1}
          />
          <div className="input-actions">
            <ItemMenu label={isProjectTab ? 'Add files to the project and this message' : 'Add files to this experiment (inputs/)'}
              trigger={<PlusIcon />} buttonClass="attach-btn" items={attachMenu} disabled={chatClosed} />
            {att.uploading && <span className="input-note">uploading…</span>}
            <span style={{ flex: 1 }} />
            <button className="send-btn" disabled={(!input.trim() && !att.ready.length && !(isProjectTab && libRefs.length)) || att.uploading || sending || chatClosed}
              onClick={handleSend} title={att.uploading ? 'Wait for the uploads to finish' : 'Send'}>
              {sending ? <span className="spinner" /> : <SendSvgIcon />}
            </button>
          </div>
        </div>
        <div className="input-hint" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button className="add-skill-btn" onClick={() => setShowSkillForm(!showSkillForm)}>+ Skill</button>
          <span>
            Talking to <span className="target">{isProjectTab ? 'the project front desk' : tabTaskName}</span>
            {isProjectTab && ' · it can look into every run of the project'}
            {!isProjectTab && !deskOff && !tabRunning && ' · finished: its front desk answers from its files'}
          </span>
        </div>
      </div>
      </>}
    </div>
    </CiteProvider>
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
/** Events after which no ideation turn is still being written. */
const LIVE_END_EVENTS = new Set(['phase_started', 'ideation_complete', 'run_finished', 'run_error', 'campaign_stopped', 'campaign_failed'])

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
  // Ideation turns being written now, by turn; not part of the stored stream
  const [live, setLive] = useState<Record<string, StreamEvent>>({})
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
      if (event.event_type === 'ideation_stream') {
        setLive((prev) => ({ ...prev, [turnKey(event.data)]: event }))
        return
      }
      if (event.event_type === 'ideation_turn') {
        setLive((prev) => {
          const { [turnKey(event.data)]: _done, ...rest } = prev
          return rest
        })
      } else if (LIVE_END_EVENTS.has(event.event_type)) {
        setLive({})
      }
      setEvents((prev) => {
        const next = [...prev, event]
        return next.length > MAX_STREAM_EVENTS ? next.slice(-MAX_STREAM_EVENTS) : next
      })
      if (REFRESH_EVENTS.has(event.event_type)) fetchRun()
    }
    return () => {
      ws.close()
      setLive({})
    }
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

  const handleToggleFrontdesk = () => act('Switching the front desk', async () => {
    if (!run) return
    await api.experiments.setFrontdesk(campaignId, experimentId, run.frontdesk_enabled === false)
    await fetchRun()
  })

  // cluster job events: the Jobs panel appears with the first one and reloads on each
  const jobEvents = useMemo(() => events.filter((e) => e.event_type.startsWith('compute_job')).length, [events])

  const [showReport, setShowReport] = useState(false)
  const [forking, setForking] = useState(false)
  const handleOpenReport = () => setShowReport(true)

  const isActive = isActiveStatus(run?.status)
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!isActive) return
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(timer)
  }, [isActive])
  const elapsed = run ? (run.finished_at || now) - run.started_at : 0
  const canResume = run?.status === 'failed' || run?.status === 'stopped'
  const { accounts } = useAuth()
  // an example that comes with AURORA: everything can be looked at, nothing changed
  const example = campaignId.startsWith('ex_')

  return (
    <div className="exp-content">
      {/* Status strip */}
      <div className={`status-strip${example ? ' read-only' : ''}`} data-tour="status">
        <div className="stat">
          <div className="stat-label">Status</div>
          <div className="stat-value" style={{ color: isActive ? 'var(--green)' : run?.status === 'failed' ? 'var(--red)' : 'var(--fg-muted)' }}>
            {run ? statusLabel(run.status) : '—'}
          </div>
        </div>
        {accounts && run?.started_by && (
          <div className="stat" title="Their key pays for the run; their notification channels hear about it">
            <div className="stat-label">Started by</div>
            <div className="stat-value">{run.started_by}</div>
          </div>
        )}
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
        <BudgetStat campaignId={campaignId} experimentId={experimentId} cost={run?.cost_usd}
          budget={run?.max_cost_usd} estimate={run?.estimate ?? null} onChanged={fetchRun} />
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
        <div className="stat">
          <div className="stat-label">Front desk</div>
          <div
            className="stat-value"
            title={run?.frontdesk_enabled === false
              ? 'Off: no model calls for this experiment. Click to switch it on.'
              : isActive ? 'On: answers your messages and relays them to the agent. Click to switch it off (messages then go straight to the agent).'
                : 'On: answers questions about this experiment from its files. Click to switch it off.'}
            style={{ cursor: example ? 'default' : 'pointer', color: run?.frontdesk_enabled === false ? 'var(--fg-dim)' : 'var(--green)' }}
            onClick={example ? undefined : handleToggleFrontdesk}
          >
            {run ? (run.frontdesk_enabled === false ? 'off' : 'on') : '—'}
          </div>
        </div>
        {run?.resources?.docker && (
          <ResourcesStat
            campaignId={campaignId}
            experimentId={experimentId}
            resources={run.resources}
            live={isActive}
            onChanged={fetchRun}
          />
        )}
        <div className="exp-controls" data-tour="controls">
          {run && (
            <button className="exp-ctrl-btn rerun" title={run.report ? 'The report, its results and where each came from' : 'Results so far (no report yet)'}
              onClick={handleOpenReport} data-tour="report-btn">
              <FilesSvg /> {run.report ? 'Report' : 'Results'}
            </button>
          )}
          {example && (
            <button className="exp-ctrl-btn rerun" title="A project of your own with this example’s question, ready to run"
              onClick={() => window.dispatchEvent(new CustomEvent('aurora:duplicate-example', { detail: campaignId }))}>
              <RerunSvg /> Duplicate
            </button>
          )}
          {!example && isActive && (
            <button className="exp-ctrl-btn stop" onClick={handleStop}>
              <StopSvg /> Stop
            </button>
          )}
          {!example && canResume && (
            <button className="exp-ctrl-btn resume" title="Continue from the last checkpoint" onClick={handleResume}>
              <PlaySvg /> Resume
            </button>
          )}
          {!example && run && !isActive && (
            <button className="exp-ctrl-btn rerun" title="Start a new experiment with the same settings" onClick={handleRerun}>
              <RerunSvg /> Re-run
            </button>
          )}
          {!example && run && (
            <button className="exp-ctrl-btn rerun" title="Start a new run from a phase or a job of this one, doing something differently"
              onClick={() => setForking(true)}>
              <RerunSvg /> Fork…
            </button>
          )}
        </div>
      </div>

      {run?.status === 'failed' && run.error && (
        <div className="error-banner">Failed: {run.error}</div>
      )}
      <ErrorBanner message={actionError} onDismiss={() => setActionError(null)} />
      {example && (
        <div className="example-bar">
          <span><b>An example that comes with AURORA.</b> Look at everything — the stream, the files, the report — and ask its
            front desk about it; nothing here can be changed. Duplicate it to run the question yourself.</span>
          <button className="link-btn" onClick={() => startTour('run')}>Take the tour</button>
        </div>
      )}
      {!example && run?.pending_decision && !run.pending_decision.resolved && (
        <FirstTip id="decision" doc="concepts/human-in-the-loop.md">
          <b>Your first decision.</b> The run is waiting for you: <b>approve</b> lets it go on, an <b>instruction</b> tells the
          agent what to change, <b>stop</b> ends it after it saves its work. Notifications can bring these to your phone.
        </FirstTip>
      )}
      {!example && isActive && !run?.pending_decision && (
        <FirstTip id="watching" doc="concepts/auditors.md">
          <b>A run at work.</b> On the right, the research agent’s steps and, next to them, the auditors: the live auditor
          reads what the agent does and advises, interrupts, or asks you. Type below to ask about the run or tell the agent
          something — it does not stop.
        </FirstTip>
      )}

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
        {showReport && <ReportView campaignId={campaignId} runId={experimentId} onClose={() => setShowReport(false)} />}
        {forking && <ForkDialog campaignId={campaignId} runId={experimentId} runName={run?.task_name || experimentId}
          onClose={() => setForking(false)} onForked={(id) => { setForking(false); onSelectExperiment(id) }} />}
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
        <div className={`tl-wrap${streamCollapsed ? ' collapsed' : ''}`} ref={streamRef} data-tour="stream">
          {rightPanel === 'files' ? (
            <div style={{ flex: 1, overflow: 'auto', padding: 0 }}>
              <WorkspacePanel campaignId={campaignId} expId={experimentId} openRequest={openRequest} />
            </div>
          ) : rightPanel === 'jobs' ? (
            <div style={{ flex: 1, overflow: 'auto', padding: 0 }}>
              <ClusterJobsPanel campaignId={campaignId} expId={experimentId} tick={jobEvents}
                onOpenLog={(path) => { setRightPanel('files'); setOpenRequest({ path }) }} />
            </div>
          ) : (
            <div style={{ flex: 1, overflowY: 'auto' }}>
              <AgentStreamView events={events} live={Object.values(live)} campaignId={campaignId} experimentId={experimentId} />
            </div>
          )}
        </div>

        {streamCollapsed && (
          <button className="stream-restore" style={{ display: 'flex' }} onClick={() => setStreamCollapsed(false)}>
            STREAM & FILE
          </button>
        )}

        {/* Right activity bar */}
        <div className="right-bar" data-tour="rightbar">
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
          {(jobEvents > 0 || rightPanel === 'jobs') && (
            <>
              <button className={`rb-btn${rightPanel === 'jobs' ? ' active' : ''}`} title="Cluster jobs"
                onClick={() => { setRightPanel('jobs'); setStreamCollapsed(false) }}>
                <span style={{ fontSize: 13 }}>▦</span>
                <span className="rb-label">Jobs</span>
              </button>
              <div className="rb-sep" />
            </>
          )}
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
  // For "Choose the direction": run it through these phases again first
  const [rerun, setRerun] = useState<string[]>([])
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
      await api.experiments.decide(campaignId, experimentId, action, message.trim(), gateId,
        action === 'propose' ? rerun : [])
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
          placeholder={offers('propose')
            ? 'Optional note for “Continue”; guidance for a new ideation round (“Send instruction”); or which direction to pursue, in your words (“Choose the direction”): one of the proposals, with changes, or your own idea'
            : 'Optional note for “Continue”; required for “Send instruction”'}
          rows={offers('propose') ? 4 : 2}
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
        {offers('propose') && (
          <button className="btn-sm btn-modify" disabled={submitting || !message.trim()}
            title="Your text says which direction to pursue (one of the proposals, with changes, or your own idea); it is interpreted and shown in the stream, then planning starts from it, without more ideation or screening"
            onClick={() => decide('propose')}>
            {'➜'} Choose the direction
          </button>
        )}
        {offers('propose') && (
          <span className="approval-rerun" title="Before planning, run your direction through these phases again (by default neither)">
            then also:
            {['ideation', 'screening'].map((ph) => (
              <label key={ph}>
                <input
                  type="checkbox"
                  checked={rerun.includes(ph)}
                  onChange={(e) => setRerun((prev) => e.target.checked ? [...prev, ph] : prev.filter((x) => x !== ph))}
                />
                {ph}
              </label>
            ))}
          </span>
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

const SKILL_KIND_LABELS: Record<string, string> = {
  procedure: 'procedure',
  failure: 'failure mode',
  convention: 'convention',
  correction: 'correction',
}

const SKILL_ORIGIN_LABELS: Record<string, string> = {
  human: 'from your corrections',
  self: 'learned from runs',
  manual: 'written by hand',
}

/** Standard sections a skill's text lacks (the template's headings). */
function missingSections(text: string, sections: string[]): string[] {
  const low = text.toLowerCase()
  return sections.filter((h) => !low.includes(`## ${h.toLowerCase()}`))
}

type SkillsTab = 'all' | 'human' | 'self' | 'manual'

/** The skill library: what the agents may use; skills learned from your corrections and from runs. */
function SkillsPanel({ collapsed, panelRef, initialSearch = '' }: {
  collapsed: boolean
  panelRef: React.RefObject<HTMLDivElement | null>
  /** Start filtered (opened from a search result). */
  initialSearch?: string
}) {
  const [skills, setSkills] = useState<Skill[]>([])
  const [sections, setSections] = useState<string[]>([])
  const [template, setTemplate] = useState('')
  const [search, setSearch] = useState(initialSearch)
  const [tab, setTab] = useState<SkillsTab>('all')
  const [openId, setOpenId] = useState<string | null>(null)
  const [draft, setDraft] = useState({ text: '', description: '' })
  const [scopeDraft, setScopeDraft] = useState<ScopeValue>({ scope: 'global', projects: [] })
  const [creating, setCreating] = useState<{ name: string; description: string; text: string } | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchSkills = useCallback(async () => {
    try {
      setSkills(await api.skills.list())
    } catch (err) {
      setError(describeError(err))
    }
  }, [])

  useEffect(() => {
    if (collapsed) return
    fetchSkills()
    api.skills.template().then((t) => {
      setSections(t.sections.map((x) => x.name))
      setTemplate(t.template)
    }).catch(() => {})
  }, [collapsed, fetchSkills])

  const filtered = skills.filter((sk) => {
    if (tab !== 'all' && (sk.origin || 'manual') !== tab) return false
    if (!search) return true
    const q = search.toLowerCase()
    return [sk.name, sk.description, sk.rule].some((x) => (x || '').toLowerCase().includes(q))
  })

  const open = (sk: Skill) => {
    if (openId === sk.skill_id) {
      setOpenId(null)
      return
    }
    setOpenId(sk.skill_id)
    setDraft({ text: sk.rule, description: sk.description })
    setScopeDraft({ scope: (sk.scope || 'global') as ScopeValue['scope'], projects: sk.projects ?? [] })
    setShowHistory(false)
    setError(null)
  }

  const act = async (what: () => Promise<unknown>) => {
    setError(null)
    try {
      await what()
      await fetchSkills()
    } catch (err) {
      setError(describeError(err))
    }
  }

  const saveScope = (sk: Skill) => act(async () => {
    if (scopeDraft.scope === 'projects' && scopeDraft.projects.length === 0) throw new Error('Choose at least one project')
    await api.skills.update(sk.skill_id, { scope: scopeDraft.scope, projects: scopeDraft.projects })
  })
  const scopeChanged = (sk: Skill) => scopeDraft.scope !== sk.scope
    || scopeDraft.projects.slice().sort().join(',') !== (sk.projects ?? []).slice().sort().join(',')
  const save = (sk: Skill) => act(() => api.skills.revise(sk.skill_id, draft.text, 'edited in the console', draft.description))
  const remove = (sk: Skill) => act(async () => {
    await api.skills.remove(sk.skill_id)
    setOpenId(null)
  })
  const create = () => creating && act(async () => {
    const name = creating.name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
    if (!name) throw new Error('Give the skill a name')
    await api.skills.create({ name, rule: creating.text, description: creating.description })
    setCreating(null)
  })

  return (
    <div className={`skills-panel${collapsed ? ' collapsed' : ''}`} ref={panelRef}>
      <div className="skills-header">
        <div className="skills-title">
          Skills
          <button className="skills-new" title="Write a new skill" onClick={() => setCreating({ name: '', description: '', text: template })}>+ New</button>
        </div>
        <input className="skills-search" placeholder="Search skills..." value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      <div className="skills-tabs">
        {(['all', 'human', 'self', 'manual'] as const).map((t) => (
          <button key={t} className={`skills-tab${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>
            {t === 'all' ? 'All' : t === 'human' ? 'From you' : t === 'self' ? 'Learned' : 'Written'}
          </button>
        ))}
      </div>
      {error && <div className="tl-detail tone-error" style={{ padding: '6px 12px' }}>{error}</div>}
      <div className="skills-list">
        {creating && (
          <div className="skill-card expanded">
            <div className="skill-edit">
              <input placeholder="name (kebab-case)" value={creating.name} onChange={(e) => setCreating({ ...creating, name: e.target.value })} />
              <input placeholder="one line: what it is for and when to use it" value={creating.description}
                onChange={(e) => setCreating({ ...creating, description: e.target.value })} />
              <textarea rows={16} value={creating.text} onChange={(e) => setCreating({ ...creating, text: e.target.value })} />
              <div className="skill-actions">
                <button className="btn-sm" onClick={() => setCreating(null)}>Cancel</button>
                <button className="btn-sm btn-approve" onClick={create}>Add to library</button>
              </div>
            </div>
          </div>
        )}
        {filtered.length === 0 && !creating && (
          <div style={{ padding: '24px 16px', textAlign: 'center', fontSize: 11, color: 'var(--fg-dim)' }}>
            {search || tab !== 'all' ? 'No matching skills' : 'No skills yet. They are learned from runs and from your corrections, or write one with + New.'}
          </div>
        )}
        {filtered.map((sk) => {
          const isOpen = openId === sk.skill_id
          const missing = missingSections(isOpen ? draft.text : sk.rule, sections)
          return (
            <div key={sk.skill_id} className={`skill-card${isOpen ? ' expanded' : ''}`}>
              <div className="skill-card-top" onClick={() => open(sk)}>
                <div className="skill-info">
                  <div className="skill-name">
                    {sk.name}
                    <span className="skill-badge">v{sk.version || 1}</span>
                    {sk.kind && <span className="skill-badge">{SKILL_KIND_LABELS[sk.kind] ?? sk.kind}</span>}
                    {sk.scope === 'experiment' && <span className="skill-badge">one run</span>}
                    {sk.scope === 'campaign' && <span className="skill-badge">one project</span>}
                    {sk.scope === 'projects' && <span className="skill-badge">{sk.projects?.length ?? 0} project{sk.projects?.length === 1 ? '' : 's'}</span>}
                    {sk.approval === 'pending' && <span className="skill-badge pending" title="Used only in the project it was learned in until a team admin approves it">waiting for an admin</span>}
                    {sk.approval === 'rejected' && <span className="skill-badge" title="A team admin kept it in the project it was learned in">kept in its project</span>}
                  </div>
                  <div className="skill-desc">{sk.description}</div>
                  <div className="skill-meta">{SKILL_ORIGIN_LABELS[sk.origin] ?? sk.origin}{sk.source_experiment ? ` · ${sk.source_experiment}` : ''}</div>
                </div>
              </div>
              {isOpen && (
                <div className="skill-edit">
                  <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
                  <textarea rows={16} value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
                  {missing.length > 0 && sections.length > 0 && (
                    <div className="skill-missing">Missing sections: {missing.join(', ')}</div>
                  )}
                  <ScopePicker value={scopeDraft} onChange={setScopeDraft} idPrefix={sk.skill_id}
                    run={sk.source_experiment || undefined} project={sk.source_campaign || undefined} />
                  {scopeChanged(sk) && (
                    <div className="skill-actions">
                      <span style={{ flex: 1 }} />
                      <button className="btn-sm btn-approve" onClick={() => saveScope(sk)}>Save where it is used</button>
                    </div>
                  )}
                  <div className="skill-actions">
                    <button className="btn-sm btn-reject" onClick={() => remove(sk)}>Delete</button>
                    {sk.history?.length > 0 && (
                      <button className="btn-sm" onClick={() => setShowHistory(!showHistory)}>History ({sk.history.length})</button>
                    )}
                    <span style={{ flex: 1 }} />
                    <button className="btn-sm btn-approve" disabled={draft.text === sk.rule && draft.description === sk.description}
                      onClick={() => save(sk)}>Save as v{(sk.version || 1) + 1}</button>
                  </div>
                  {showHistory && sk.history.slice().reverse().map((h) => (
                    <details key={h.version} className="skill-history">
                      <summary>v{h.version}{h.replaced_because ? ` — replaced: ${h.replaced_because}` : ''}</summary>
                      <pre>{h.text}</pre>
                    </details>
                  ))}
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

function money(x: number): string {
  return x >= 10 ? `$${x.toFixed(0)}` : x >= 1 ? `$${x.toFixed(1)}` : `$${x.toFixed(2)}`
}

function hoursText([h0, h1]: [number, number]): string {
  const fmt = (h: number) => h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : `${h.toFixed(h < 10 ? 1 : 0)} h`
  return h1 < 1 ? `${Math.max(1, Math.round(h0 * 60))}–${Math.max(1, Math.round(h1 * 60))} min` : `${fmt(h0)}–${fmt(h1)}`
}

function ProposalCard({
  proposal,
  campaignId,
  onConfirmed,
  onOpen,
}: {
  proposal: Proposal
  campaignId: string
  onConfirmed: (expId: string) => void
  onOpen: (expId: string) => void
}) {
  const [skipPhases, setSkipPhases] = useState<Phase[]>(proposal.skip_phases)
  const [budget, setBudget] = useState<string>(proposal.max_cost_usd != null ? String(proposal.max_cost_usd) : '')
  const [ideationMode, setIdeationMode] = useState<IdeationMode>(proposal.params.ideation_mode ?? 'roundtable')
  const [live, setLive] = useState<boolean>(!!(proposal.params as { ideation_live?: boolean }).ideation_live)
  // papers the agents find: the console's default until the researcher picks
  const consoleSettings = useConsoleSettings()
  const [addFound, setAddFound] = useState<AddFound | null>(null)
  const litOn = consoleSettings?.literature?.enabled ?? true
  const rule: AddFound = addFound ?? consoleSettings?.literature?.add_found ?? 'cited'
  // the card starts from what the front desk put in from the conversation
  const [advanced, setAdvanced] = useState<AdvancedChoices>(() => ({
    autonomy: proposal.params.autonomy_mode ?? null,
    models: (proposal.params.models ?? {}) as AdvancedChoices['models'],
    cpus: proposal.params.cpus != null ? String(proposal.params.cpus) : undefined,
    memoryGb: proposal.params.memory_gb != null ? String(proposal.params.memory_gb) : undefined,
    parallel: proposal.params.max_parallel_jobs != null ? String(proposal.params.max_parallel_jobs) : undefined,
  }))
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(proposal.confirmed)
  const [started, setStarted] = useState<ConfirmedSettings | null>(proposal.confirmed_settings ?? null)
  const [error, setError] = useState<string | null>(null)
  const [showInstruction, setShowInstruction] = useState(false)
  // what a run with these settings will likely cost and take (refreshed as the settings change)
  const [estimate, setEstimate] = useState<RunEstimate | null>(null)
  const [showPhases, setShowPhases] = useState(false)
  const estimateKey = JSON.stringify([skipPhases, ideationMode, advanced.models, advanced.parallel, proposal.params.model])
  useEffect(() => {
    if (confirmed) return
    const t = window.setTimeout(() => {
      const parallel = advanced.parallel?.trim() ? Number(advanced.parallel) : undefined
      api.results.estimate(campaignId, {
        skip_phases: skipPhases, ideation_mode: ideationMode, models: advanced.models as Record<string, string>,
        model: proposal.params.model ?? '', ...(parallel ? { max_parallel_jobs: parallel } : {}),
      }).then(setEstimate).catch(() => setEstimate(null))
    }, 300)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estimateKey, campaignId, confirmed])

  const handleConfirm = async () => {
    setConfirming(true)
    setError(null)
    try {
      const maxCost = budget.trim() === '' ? null : Number(budget)
      if (maxCost !== null && !(maxCost > 0)) {
        setError('The budget must be a positive number of US$, or empty for no limit')
        return
      }
      const parallel = advanced.parallel?.trim() ? Number(advanced.parallel) : null
      if (parallel !== null && !(Number.isInteger(parallel) && parallel >= 1 && parallel <= 16)) {
        setError('Jobs at the same time must be a whole number from 1 to 16')
        return
      }
      const cpus = advanced.cpus?.trim() ? Number(advanced.cpus) : null
      const memoryGb = advanced.memoryGb?.trim() ? Number(advanced.memoryGb) : null
      if ((cpus !== null && !(cpus > 0)) || (memoryGb !== null && !(memoryGb > 0))) {
        setError('CPUs and memory must be positive numbers, or empty for the defaults')
        return
      }
      const result = await api.proposals.confirm(campaignId, proposal.proposal_id, {
        skip_phases: skipPhases,
        max_cost_usd: maxCost,
        ideation_mode: ideationMode,
        ideation_live: live,
        ...(addFound ? { add_found: addFound } : {}),
        ...(advanced.autonomy ? { autonomy_mode: advanced.autonomy } : {}),
        ...(Object.keys(advanced.models).length ? { models: advanced.models } : {}),
        ...(cpus !== null ? { cpus } : {}),
        ...(memoryGb !== null ? { memory_mb: Math.round(memoryGb * 1024) } : {}),
        ...(parallel !== null ? { max_parallel_jobs: parallel } : {}),
        ...(estimate ? { estimate: { cost: estimate.cost, hours: estimate.hours } } : {}),
        inputs,
      })
      setStarted({
        experiment_id: result.experiment_id,
        skip_phases: skipPhases,
        ideation_mode: ideationMode,
        ideation_live: live,
        add_found: rule,
        max_cost_usd: maxCost,
        autonomy_mode: advanced.autonomy,
        models: advanced.models,
        cpus,
        memory_mb: memoryGb !== null ? Math.round(memoryGb * 1024) : null,
        max_parallel_jobs: parallel,
      })
      setConfirmed(true)
      onConfirmed(result.experiment_id)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setConfirming(false)
    }
  }

  // hooks before the early returns below
  const [inputs, setInputs] = useState<string[]>(proposal.params.inputs ?? [])
  const [pickingInputs, setPickingInputs] = useState(false)

  if (proposal.superseded) {
    return (
      <div className="proposal-card superseded">
        <div className="proposal-status superseded-text">Replaced by updated proposal</div>
      </div>
    )
  }

  if (confirmed) {
    return (
      <StartedProposal
        proposal={proposal}
        started={started}
        onOpen={onOpen}
      />
    )
  }

  const { instruction, model } = proposal.params
  const basedOn: string[] = Array.isArray(proposal.params.based_on) ? proposal.params.based_on : []

  return (
    <div className="proposal-card">
      <FirstTip id="proposal" doc="guide/web-console.md">
        <b>Your first proposal.</b> Untick phases you do not need, change the models or the budget under Advanced, or ask
        the front desk to change the brief. The estimate below updates as you do. Nothing runs, and nothing costs money,
        until you confirm.
      </FirstTip>
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
      <div className="proposal-meta proposal-inputs">
        <span>Inputs:</span>
        {inputs.length === 0 && <span className="proposal-hint">none</span>}
        {inputs.map((x) => (
          <span key={x} className="input-chip" title={x}>
            {x.startsWith('project:') ? x.slice(8) : x}
            <button aria-label={`Remove ${x}`} onClick={() => setInputs(inputs.filter((y) => y !== x))}>×</button>
          </span>
        ))}
        <button className="link-btn" onClick={() => setPickingInputs(true)}>+ files</button>
        <span className="proposal-hint">the agents find them in inputs/</span>
      </div>
      {pickingInputs && (
        <ProjectFilesDialog campaignId={campaignId} mode="pick" initial={inputs}
          onPick={(picked) => setInputs([...inputs.filter((x) => !x.startsWith('project:')), ...picked])}
          onClose={() => setPickingInputs(false)} />
      )}
      {(proposal.params.seed_papers?.length ?? 0) > 0 && (
        <div className="proposal-meta">Starts from: <CiteText text={proposal.params.seed_papers!.map((k) => `[${k}]`).join(' ')} />
          <span className="proposal-hint">ideation develops ideas from these papers; every phase reads them</span></div>
      )}
      {basedOn.length > 0 && (
        <div className="proposal-meta">Builds on: {basedOn.join(', ')} <span className="proposal-hint">their reports and findings go to the agents</span></div>
      )}
      {model && <div className="proposal-meta">Model: {model}</div>}

      <PhasePipelineSelector skipPhases={skipPhases} onChange={setSkipPhases} />

      {!skipPhases.includes('ideation') && (
        <div className="proposal-meta proposal-ideation">
          <span>Ideation:</span>
          <label>
            <input type="radio" name={`ideation-${proposal.proposal_id}`} checked={ideationMode === 'roundtable' && !live}
              onChange={() => { setIdeationMode('roundtable'); setLive(false) }} />
            Round table <span className="proposal-hint">the models discuss their ideas, then the chair decides</span>
          </label>
          <label>
            <input type="radio" name={`ideation-${proposal.proposal_id}`} checked={ideationMode === 'independent' && !live}
              onChange={() => { setIdeationMode('independent'); setLive(false) }} />
            Independent <span className="proposal-hint">the chair picks one of the ideas; about 4× cheaper</span>
          </label>
          <label>
            <input type="radio" name={`ideation-${proposal.proposal_id}`} checked={live}
              onChange={() => { setIdeationMode('roundtable'); setLive(true) }} />
            Brainstorm with me <span className="proposal-hint">the round table meets live in the project’s brainstorm; you can speak in it</span>
          </label>
        </div>
      )}

      {litOn && !(skipPhases.includes('ideation') && skipPhases.includes('screening')) && (
        <label className="proposal-meta proposal-ideation">
          <span>Papers the agents find:</span>
          <RuleSelect value={rule} onChange={setAddFound} />
          <span className="proposal-hint">{RULE_HINTS[rule]} Papers that cannot be found in any index are never added.</span>
        </label>
      )}

      <AdvancedSettings
        ideation={!skipPhases.includes('ideation')}
        value={advanced}
        onChange={setAdvanced}
      />

      <label className="proposal-meta proposal-budget">
        Budget: US$
        <input
          type="number"
          min="1"
          step="1"
          value={budget}
          placeholder="no limit"
          onChange={(e) => setBudget(e.target.value)}
        />
        <span className="proposal-hint">the experiment stops cleanly when it is reached</span>
      </label>

      {estimate && (
        <div className="proposal-meta proposal-estimate">
          <span>Estimate:</span>
          <b>{money(estimate.cost[0])}–{money(estimate.cost[1])}</b>
          <span>· {hoursText(estimate.hours)}</span>
          <span className="proposal-hint">{estimate.basis}. {estimate.note}</span>
          <button className="link-btn" onClick={() => setShowPhases(!showPhases)}>{showPhases ? 'hide' : 'by phase'}</button>
          {budget.trim() !== '' && Number(budget) > 0 && Number(budget) < estimate.cost[0] && (
            <span className="proposal-warn">The budget is below the low end of the estimate: the run will likely stop before it ends.</span>
          )}
          {showPhases && (
            <table className="proposal-estimate-table"><tbody>
              {estimate.per_phase.map((p) => (
                <tr key={p.phase}><td>{phaseLabel(p.phase as Phase) || p.phase}{p.jobs ? ` (${p.jobs[0]}–${p.jobs[1]} jobs)` : ''}</td>
                  <td>{money(p.cost[0])}–{money(p.cost[1])}</td><td>{p.minutes[0]}–{p.minutes[1]} min</td></tr>
              ))}
              {estimate.oversight[1] > 0 && <tr><td>Auditors</td><td>{money(estimate.oversight[0])}–{money(estimate.oversight[1])}</td><td /></tr>}
            </tbody></table>
          )}
        </div>
      )}

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
  onShare,
}: {
  campaignId: string
  campaign: CampaignSummary | null
  experiments: ExperimentSummary[]
  onSelectExperiment: (expId: string | null) => void
  onShare: () => void
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
        {campaign?.sharing && (
          <button className="share-btn" onClick={onShare} title="Who is in this project, and with which rights">
            <ShareIcon />{campaign.sharing.members > 1 ? `Shared · ${campaign.sharing.members}` : 'Share'}
            {campaign.sharing.confidential && <span className="share-conf">Confidential</span>}
          </button>
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
  const [page, setPage] = useState<MainPage>(null)
  // a paper open in the reader, over whatever page it was opened from
  const [reading, setReading] = useState<ReadRequest | null>(null)
  // a map of a field open over the page (the reader opens over it in turn)
  const [mapping, setMapping] = useState<MapRequest | null>(null)
  useEffect(() => {
    const open = (e: Event) => setReading((e as CustomEvent<ReadRequest>).detail)
    const map = (e: Event) => setMapping((e as CustomEvent<MapRequest>).detail)
    window.addEventListener('aurora:read-paper', open)
    window.addEventListener('aurora:open-map', map)
    return () => { window.removeEventListener('aurora:read-paper', open); window.removeEventListener('aurora:open-map', map) }
  }, [])
  // the space the brainstorm page opens on (a project's, from its tab bar; else your own)
  const [bsSpace, setBsSpace] = useState<string | null>(null)
  useEffect(() => {
    const open = (e: Event) => { setBsSpace((e as CustomEvent<string>).detail); setPage('brainstorm') }
    window.addEventListener('aurora:open-brainstorm', open)
    return () => window.removeEventListener('aurora:open-brainstorm', open)
  }, [])
  useEffect(() => {
    const panel = (e: Event) => setActivePanel((e as CustomEvent<string>).detail)
    window.addEventListener('aurora:open-panel', panel)
    return () => window.removeEventListener('aurora:open-panel', panel)
  }, [])
  const [sharing, setSharing] = useState<CampaignSummary | null>(null)

  const sidebarRef = useRef<HTMLDivElement>(null)
  const skillsPanelRef = useRef<HTMLDivElement>(null)
  const searchPanelRef = useRef<HTMLDivElement>(null)
  const analyticsPanelRef = useRef<HTMLDivElement>(null)
  const settingsPanelRef = useRef<HTMLDivElement>(null)
  const [skillQuery, setSkillQuery] = useState('')
  const streamRef = useRef<HTMLDivElement>(null)

  const helpPanelRef = useRef<HTMLDivElement>(null)
  const sidePanels = ['skills', 'search', 'analytics', 'settings', 'help']
  // the brainstorm page has its own list of spaces and conversations
  const sidebarCollapsed = activePanel === '' || sidePanels.includes(activePanel) || page === 'brainstorm' || page === 'memory'
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
    setPage(null)
    setActiveCampaignId(id)
    setActiveExperimentId(null)
    navigate(`/campaigns/${id}`, { replace: true })
  }, [navigate])

  // from the Library: ask a project's front desk about an item, or open a project's tab
  useEffect(() => {
    const ask = (e: Event) => {
      const { campaignId, ref } = (e as CustomEvent<{ campaignId: string; ref: LibraryRef }>).detail
      toProject(campaignId, { ref })
      setActivePanel((p) => p === '' ? 'projects' : p)
      selectCampaign(campaignId)
    }
    const tab = (e: Event) => {
      const { campaignId, tab } = (e as CustomEvent<{ campaignId: string; tab: string }>).detail
      toProject(campaignId, { tab })
    }
    window.addEventListener('aurora:ask-frontdesk', ask)
    window.addEventListener('aurora:open-project-tab', tab)
    return () => { window.removeEventListener('aurora:ask-frontdesk', ask); window.removeEventListener('aurora:open-project-tab', tab) }
  }, [selectCampaign])

  const selectExperiment = useCallback((expId: string | null) => {
    setActiveExperimentId(expId)
    if (activeCampaignId && expId) {
      navigate(`/campaigns/${activeCampaignId}/experiments/${expId}`, { replace: true })
    } else if (activeCampaignId) {
      navigate(`/campaigns/${activeCampaignId}`, { replace: true })
    }
  }, [activeCampaignId, navigate])

  const openProject = useCallback((campaignId: string) => {
    setActiveCampaignId(campaignId)
    setActiveExperimentId(null)
    navigate(`/campaigns/${campaignId}`, { replace: true })
  }, [navigate])

  const openRun = useCallback((campaignId: string, runId: string) => {
    setActiveCampaignId(campaignId)
    setActiveExperimentId(runId)
    navigate(`/campaigns/${campaignId}/experiments/${runId}`, { replace: true })
  }, [navigate])

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

  // the first minutes: the welcome wizard, the tour, help, the examples
  const ob = useOnboarding()
  useEffect(() => { loadOnboarding() }, [])
  useEffect(() => {
    const id = setInterval(() => { loadOnboarding() }, 60000)      // the checklist follows what you do
    return () => clearInterval(id)
  }, [])
  const openExample = useCallback(async (tour: boolean) => {
    try {
      const r = await api.examples.list()
      const ex = r.examples.find((e) => e.kind === 'real run' && e.installed && !e.removed)
      if (!ex?.campaign_id || !ex.run_id) { setNotice('No example is installed in this console.'); return }
      if (r.hidden) await updateOnboarding({ examples_hidden: false })
      await fetchCampaigns()
      setPage(null)
      setActivePanel('projects')
      openRun(ex.campaign_id, ex.run_id)
      if (tour) setTimeout(() => startTour('run'), 300)
    } catch (e) { setNotice(`Could not open the example: ${describeError(e)}`) }
  }, [fetchCampaigns, openRun])
  const runDemo = useCallback(async () => {
    try {
      const r = await api.setup.demo()
      await fetchCampaigns()
      setPage(null)
      openRun(r.campaign_id, r.experiment_id)
      loadOnboarding()
    } catch (e) { setNotice(`Could not start the demo: ${describeError(e)}`) }
  }, [fetchCampaigns, openRun])
  const goHome = useCallback(() => {
    setPage(null)
    setActiveCampaignId(null)
    setActiveExperimentId(null)
    navigate('/', { replace: true })
  }, [navigate])
  const duplicateExample = useCallback(async (campaignId: string) => {
    try {
      const r = await api.examples.list()
      const ex = r.examples.find((e) => e.campaign_id === campaignId)
      if (!ex) return
      const copy = await api.examples.duplicate(ex.id)
      await fetchCampaigns()
      setPage(null)
      toProject(copy.campaign_id, { draft: copy.brief, tab: 'project' })
      openProject(copy.campaign_id)
    } catch (e) { setNotice(`Could not duplicate the example: ${describeError(e)}`) }
  }, [fetchCampaigns, openProject])
  useEffect(() => {
    const page = (e: Event) => setPage((e as CustomEvent<MainPage>).detail)
    const dup = (e: Event) => { duplicateExample((e as CustomEvent<string>).detail) }
    const changed = () => { fetchCampaigns() }
    window.addEventListener('aurora:open-page', page)
    window.addEventListener('aurora:duplicate-example', dup)
    window.addEventListener('aurora:projects-changed', changed)
    return () => {
      window.removeEventListener('aurora:open-page', page)
      window.removeEventListener('aurora:duplicate-example', dup)
      window.removeEventListener('aurora:projects-changed', changed)
    }
  }, [duplicateExample, fetchCampaigns])
  const helpAction = (action: string) => {
    if (action === 'demo') runDemo()
    else if (action === 'start') goHome()
    else if (action === 'brainstorm' || action === 'library' || action === 'team' || action === 'examples' || action === 'memory') setPage(action)
    else if (action.startsWith('settings')) setActivePanel('settings')
    else if (action === 'refresh') fetchCampaigns()
  }
  const tourActions = {
    openRun, goHome, accounts: !!ob?.accounts,
    openPanel: (panel: string) => setActivePanel(panel),
    openPage: (p: MainPage) => setPage(p),
    setRight: (panel: string) => setRightPanel(panel),
  }

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

  const handleRenameCampaign = async (campaignId: string, name: string) => {
    try {
      await api.campaigns.rename(campaignId, name)
      fetchCampaigns()
    } catch (err) {
      setNotice(`Rename failed: ${describeError(err)}`)
    }
  }

  const handleRenameExperiment = async (campaignId: string, expId: string, name: string) => {
    try {
      await api.experiments.rename(campaignId, expId, name)
      fetchExperiments()
    } catch (err) {
      setNotice(`Rename failed: ${describeError(err)}`)
    }
  }

  // the project whose files are open
  const [filesFor, setFilesFor] = useState<string | null>(null)

  // the project or run waiting for the delete confirmation
  const [deleting, setDeleting] = useState<
    | { kind: 'project'; campaign: CampaignSummary }
    | { kind: 'run'; campaignId: string; exp: ExperimentSummary }
    | null>(null)
  const cancelDelete = useCallback(() => setDeleting(null), [])
  const deletingUsage = useCallback(async () => {
    if (!deleting) return 0
    const r = deleting.kind === 'project'
      ? await api.campaigns.diskUsage(deleting.campaign.campaign_id)
      : await api.experiments.diskUsage(deleting.campaignId, deleting.exp.run_id)
    return r.bytes
  }, [deleting])

  const confirmDelete = async (deleteFiles: boolean) => {
    if (!deleting) return
    try {
      if (deleting.kind === 'project') {
        const id = deleting.campaign.campaign_id
        await api.campaigns.delete(id, deleteFiles)
        if (id === activeCampaignId) {
          setActiveCampaignId(null)
          setActiveExperimentId(null)
          navigate('/', { replace: true })
        }
      } else {
        await api.experiments.remove(deleting.campaignId, deleting.exp.run_id, deleteFiles)
        if (deleting.exp.run_id === activeExperimentId) selectExperiment(null)
      }
    } catch (err) {
      throw new Error(describeError(err))
    }
    setDeleting(null)
    fetchCampaigns()
    fetchExperiments()
  }

  const activeCampaign = campaigns.find((c) => c.campaign_id === activeCampaignId) || null

  // Determine view
  const view = page ?? (!activeCampaignId ? 'welcome' : activeExperimentId ? 'experiment' : 'campaign')

  return (
    <div className="shell">
      <ActivityBar activePanel={activePanel} explorerActive={rightPanel === 'files'} onTogglePanel={handleTogglePanel}
        page={page} onOpenPage={setPage} helpBadge={ob ? ob.checklist.filter((c) => !c.done).length : 0} />

      <SidebarPanel
        campaigns={campaigns}
        activeCampaignId={activeCampaignId}
        activeExperimentId={activeExperimentId}
        experiments={experiments}
        collapsed={sidebarCollapsed}
        sidebarRef={sidebarRef}
        onSelectCampaign={selectCampaign}
        onNewProject={() => {
          setPage(null)
          setActiveCampaignId(null)
          setActiveExperimentId(null)
          navigate('/', { replace: true })
        }}
        onSelectExperiment={(expId) => selectExperiment(expId)}
        onStopExperiment={handleStopExperiment}
        onRerunExperiment={handleRerunExperiment}
        onRenameCampaign={handleRenameCampaign}
        onDeleteCampaign={(campaign) => setDeleting({ kind: 'project', campaign })}
        onRenameExperiment={handleRenameExperiment}
        onDeleteExperiment={(campaignId, exp) => setDeleting({ kind: 'run', campaignId, exp })}
        onOpenFiles={setFilesFor}
        onShare={setSharing}
      />
      {sharing && <ShareDialog campaignId={sharing.campaign_id} projectName={sharing.name}
        onClose={() => { setSharing(null); fetchCampaigns() }} />}
      {filesFor && <ProjectFilesDialog campaignId={filesFor} onClose={() => setFilesFor(null)} />}

      {deleting && (
        <ConfirmDelete
          kind={deleting.kind}
          name={deleting.kind === 'project' ? deleting.campaign.name : experimentName(deleting.exp)}
          detail={deleting.kind === 'project' && deleting.campaign.experiment_count
            ? `Its ${deleting.campaign.experiment_count} run${deleting.campaign.experiment_count === 1 ? ' is' : 's are'} removed too.` : undefined}
          blocked={deleting.kind === 'project'
            ? (deleting.campaign.active_experiments ? 'Stop its running experiments first.' : undefined)
            : (isActiveStatus(deleting.exp.status) ? 'Stop the run first.' : undefined)}
          diskUsage={deletingUsage}
          onConfirm={confirmDelete}
          onCancel={cancelDelete}
        />
      )}

      <ResizeHandle targetRef={sidebarRef} side="right" />

      <SkillsPanel key={skillQuery} collapsed={!showSkills} panelRef={skillsPanelRef} initialSearch={skillQuery} />
      {showSkills && <ResizeHandle targetRef={skillsPanelRef} side="right" />}
      <SearchPanel collapsed={activePanel !== 'search'} panelRef={searchPanelRef}
        onOpenProject={openProject} onOpenRun={openRun}
        onOpenSkill={(name) => { setSkillQuery(name); setActivePanel('skills') }} />
      {activePanel === 'search' && <ResizeHandle targetRef={searchPanelRef} side="right" />}
      <AnalyticsPanel collapsed={activePanel !== 'analytics'} panelRef={analyticsPanelRef}
        onOpenProject={openProject} onOpenRun={openRun} />
      {activePanel === 'analytics' && <ResizeHandle targetRef={analyticsPanelRef} side="right" />}
      <SettingsPanel collapsed={activePanel !== 'settings'} panelRef={settingsPanelRef} />
      {activePanel === 'settings' && <ResizeHandle targetRef={settingsPanelRef} side="right" />}
      <HelpPanel collapsed={activePanel !== 'help'} panelRef={helpPanelRef} onAction={helpAction} />
      {activePanel === 'help' && <ResizeHandle targetRef={helpPanelRef} side="right" />}

      <div className="main" style={{ position: 'relative' }}>
        <ErrorBanner message={notice} onDismiss={() => setNotice(null)} />
        {mapping && (
          <Suspense fallback={<div className="rv"><div className="rd-loading">Opening the map…</div></div>}>
            <FieldMapView key={`${mapping.space}|${mapping.mapId ?? ''}`} req={mapping} onClose={() => setMapping(null)} />
          </Suspense>
        )}
        {reading && (
          <Suspense fallback={<div className="rd"><div className="rd-loading">Opening the paper…</div></div>}>
            <PaperReader key={`${reading.paper}|${reading.space}|${reading.cardId ?? ''}`} req={reading} onClose={() => setReading(null)} />
          </Suspense>
        )}
        {view === 'team' && <TeamView />}
        {view === 'memory' && <MemoryView />}
        {view === 'brainstorm' && <BrainstormView initialSpace={bsSpace ?? activeCampaignId} onOpenProject={(cid) => { setPage(null); selectCampaign(cid) }}
          onOpenLibrary={() => setPage('library')} />}
        {view === 'library' && <LibraryView onOpenProject={(cid) => { setPage(null); selectCampaign(cid) }} />}
        {view === 'examples' && <ExamplesView onOpen={(cid, run) => { setPage(null); openRun(cid, run) }} onDemo={runDemo}
          onDuplicated={async (cid, brief) => { await fetchCampaigns(); setPage(null); toProject(cid, { draft: brief, tab: 'project' }); openProject(cid) }} />}
        {view === 'welcome' && (
          <WelcomeView onSend={handleNewProject} onOpenRun={openRun} onOpenExample={() => openExample(true)} />
        )}

        {view === 'campaign' && activeCampaignId && (
          <CampaignChatView
            key={activeCampaignId}
            campaignId={activeCampaignId}
            campaign={activeCampaign}
            experiments={experiments}
            onSelectExperiment={selectExperiment}
            onShare={() => activeCampaign && setSharing(activeCampaign)}
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
      {ob && !ob.wizard.done && (
        <WelcomeWizard ob={ob} onExample={() => openExample(true)} onDemo={runDemo} onOwn={goHome} />
      )}
      <Tour actions={tourActions} />
      <DocViewer />
    </div>
  )
}


// ---------------------------------------------------------------------------
// Cost and budget of an experiment; the budget can be changed at any time
// ---------------------------------------------------------------------------

function BudgetStat({ campaignId, experimentId, cost, budget, estimate, onChanged }: {
  estimate?: { cost: [number, number]; hours: [number, number] } | null
  campaignId: string
  experimentId: string
  cost?: number
  budget?: number | null
  onChanged: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const open = () => {
    setText(budget != null ? String(budget) : '')
    setError(null)
    setEditing(true)
  }
  const apply = async () => {
    const value = text.trim() === '' ? null : Number(text)
    if (value !== null && !(value > 0)) {
      setError('A positive amount, or empty for no limit')
      return
    }
    setSaving(true)
    try {
      const r = await api.experiments.setBudget(campaignId, experimentId, value)
      setEditing(false)
      if (r.note) setError(r.note)
      onChanged()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="stat resources-stat">
      <div className="stat-label">Cost</div>
      <div className="stat-value mono" style={{ cursor: 'pointer' }} onClick={editing ? undefined : open}
        title="Click to change the budget (it applies at once)">
        {formatCost(cost)}<span className="stat-sub"> / {budget != null ? formatCost(budget) : 'no limit'}</span>
      </div>
      {estimate && <div className={`stat-est${(cost ?? 0) > estimate.cost[1] ? ' over' : ''}`}
        title={`Estimated before it started: ${money(estimate.cost[0])}–${money(estimate.cost[1])}, ${hoursText(estimate.hours)}`}>
        est. {money(estimate.cost[0])}–{money(estimate.cost[1])}{(cost ?? 0) > estimate.cost[1] ? ' · over' : ''}</div>}
      {editing && (
        <div className="resources-pop">
          <label>Budget (US$) <input type="number" min="1" step="1" value={text} placeholder="no limit"
            onChange={(e) => setText(e.target.value)} /></label>
          <div className="resources-hint">The experiment stops cleanly when it is reached; empty for no limit.</div>
          {error && <div className="tl-detail tone-error">{error}</div>}
          <div className="resources-actions">
            <button className="btn-sm" disabled={saving} onClick={() => setEditing(false)}>Cancel</button>
            <button className="btn-sm btn-approve" disabled={saving} onClick={apply}>{saving ? 'Applying…' : 'Apply'}</button>
          </div>
        </div>
      )}
      {!editing && error && <div className="tl-detail tone-error" style={{ maxWidth: 220 }}>{error}</div>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Container limits of an experiment, changeable at any time
// ---------------------------------------------------------------------------

function ResourcesStat({ campaignId, experimentId, resources, live, onChanged }: {
  campaignId: string
  experimentId: string
  resources: ContainerResources
  live: boolean
  onChanged: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [cpus, setCpus] = useState('')
  const [memGb, setMemGb] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const gb = resources.memory_mb / 1024

  const open = () => {
    setCpus(String(resources.cpus))
    setMemGb(String(gb))
    setError(null)
    setEditing(true)
  }
  const apply = async () => {
    const c = Number(cpus)
    const m = Number(memGb)
    if (!(c > 0) || !(m > 0)) {
      setError('Positive numbers, please')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await api.experiments.setResources(campaignId, experimentId, c, Math.round(m * 1024))
      setEditing(false)
      onChanged()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="stat resources-stat">
      <div className="stat-label">Container</div>
      <div className="stat-value mono" style={{ cursor: 'pointer' }} onClick={editing ? undefined : open}
        title={live ? 'Click to change the CPU and memory limits now; nothing restarts'
          : 'Click to change the limits a resume or re-run will use'}>
        {resources.cpus} CPU · {Number.isInteger(gb) ? gb : gb.toFixed(1)} GB
      </div>
      {editing && (
        <div className="resources-pop">
          <label>CPUs <input type="number" min="0.5" step="0.5" value={cpus} onChange={(e) => setCpus(e.target.value)} /></label>
          <label>Memory (GB) <input type="number" min="1" step="1" value={memGb} onChange={(e) => setMemGb(e.target.value)} /></label>
          <div className="resources-hint">{live ? 'Applied to the running container at once. Less memory than it uses now can kill running processes.'
            : 'Used when the experiment is resumed or re-run.'}</div>
          {error && <div className="tl-detail tone-error">{error}</div>}
          <div className="resources-actions">
            <button className="btn-sm" disabled={saving} onClick={() => setEditing(false)}>Cancel</button>
            <button className="btn-sm btn-approve" disabled={saving} onClick={apply}>{saving ? 'Applying…' : 'Apply'}</button>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Advanced settings of a proposal: autonomy and the model of each role
// ---------------------------------------------------------------------------

interface AdvancedChoices {
  autonomy: AutonomyMode | null
  /** Only the roles the researcher changed; the rest keep the server defaults. */
  models: Partial<Record<keyof ModelSettings, string | string[]>>
  /** Container limits, as typed; empty: the server defaults. */
  cpus?: string
  memoryGb?: string
  /** Research jobs at the same time, as typed; empty: the server default. */
  parallel?: string
}

let settingsRequest: Promise<ConsoleSettings> | null = null

function useConsoleSettings(): ConsoleSettings | null {
  const [settings, setSettings] = useState<ConsoleSettings | null>(null)
  useEffect(() => {
    settingsRequest ??= api.settings.get()
    settingsRequest.then(setSettings).catch(() => { settingsRequest = null })
  }, [])
  return settings
}

const PHASE_MODEL_ROLES: Array<[keyof ModelSettings, string]> = [
  ['screening', 'Screening'],
  ['planning', 'Planning'],
  ['preflight', 'Pre-flight'],
  ['research', 'Research'],
  ['analysis', 'Analysis'],
  ['writing', 'Writing'],
]

const OVERSIGHT_MODEL_ROLES: Array<[keyof ModelSettings, string]> = [
  ['auditor', 'Milestone auditor'],
  ['supervisor', 'Live auditor'],
  ['frontdesk', 'Front desk'],
]

function AdvancedSettings({
  ideation,
  value,
  onChange,
}: {
  ideation: boolean
  value: AdvancedChoices
  onChange: (value: AdvancedChoices) => void
}) {
  const settings = useConsoleSettings()
  const [open, setOpen] = useState(false)
  if (!settings) return null
  const d = settings.models
  const current = (role: keyof ModelSettings): string => {
    const v = value.models[role] ?? d[role]
    return Array.isArray(v) ? v.join(', ') : v
  }
  const setModel = (role: keyof ModelSettings, text: string) => {
    const models = { ...value.models }
    const parsed = role === 'ideation_seats' ? text.split(',').map((m) => m.trim()).filter(Boolean) : text.trim()
    const unchanged = role === 'ideation_seats'
      ? (parsed as string[]).join(',') === d.ideation_seats.join(',')
      : parsed === d[role]
    if (unchanged || (role !== 'ideation_seats' && parsed === '' && d[role] === '')) delete models[role]
    else models[role] = parsed
    onChange({ ...value, models })
  }
  const field = (role: keyof ModelSettings, label: string, placeholder = '') => (
    <label key={role} className="advanced-field">
      <span>{label}</span>
      <input list="aurora-models" value={current(role)} placeholder={placeholder}
        onChange={(e) => setModel(role, e.target.value)} />
    </label>
  )
  const autonomy = value.autonomy ?? settings.autonomy_mode
  const sb = settings.sandbox
  const changed = Object.keys(value.models).length + (value.autonomy ? 1 : 0) + (value.cpus ? 1 : 0)
    + (value.memoryGb ? 1 : 0) + (value.parallel ? 1 : 0)

  return (
    <div className="proposal-advanced">
      <button className="proposal-detail-toggle" onClick={() => setOpen(!open)}>
        {open ? '▾' : '▸'} Advanced settings{changed ? ` (${changed} changed)` : ''}
      </button>
      {open && (
        <div className="advanced-body">
          <datalist id="aurora-models">
            {settings.model_suggestions.map((m) => <option key={m} value={m} />)}
          </datalist>
          <div className="advanced-group">
            <div className="advanced-title">When a decision is needed</div>
            <label>
              <input type="radio" checked={autonomy === 'supervised'}
                onChange={() => onChange({ ...value, autonomy: 'supervised' })} />
              Supervised <span className="proposal-hint">the experiment waits for you</span>
            </label>
            <label>
              <input type="radio" checked={autonomy === 'autonomous'}
                onChange={() => onChange({ ...value, autonomy: 'autonomous' })} />
              Autonomous <span className="proposal-hint">it records the question and continues</span>
            </label>
          </div>
          <div className="advanced-group">
            <div className="advanced-title">Models (OpenRouter IDs)</div>
            {field('agent', 'Agents (default for all phases)')}
            {PHASE_MODEL_ROLES.map(([role, label]) => field(role, label, `same as agents: ${current('agent')}`))}
            {ideation && field('ideation_seats', 'Ideation seats (comma-separated)')}
            {ideation && field('ideation_chair', 'Ideation chair')}
            {OVERSIGHT_MODEL_ROLES.map(([role, label]) => field(role, label))}
            {field('skills', 'Skill drafting (from runs and your corrections)', `same as milestone auditor: ${current('auditor')}`)}
          </div>
          <div className="advanced-group">
            <div className="advanced-title">Research jobs</div>
            <label className="advanced-field">
              <span>At the same time</span>
              <input type="number" min="1" max="16" step="1" value={value.parallel ?? ''}
                placeholder={String(settings.max_parallel_jobs)} onChange={(e) => onChange({ ...value, parallel: e.target.value })} />
            </label>
            <div className="proposal-hint">jobs whose inputs are ready run together, each with its own agent, sharing the container</div>
          </div>
          {sb.docker && (
            <div className="advanced-group">
              <div className="advanced-title">Container <span className="proposal-hint">
                can also be changed while the experiment runs · this machine: {sb.host_cpus} CPUs, {Math.round(sb.host_memory_mb / 1024)} GB
              </span></div>
              <label className="advanced-field">
                <span>CPUs</span>
                <input type="number" min="0.5" step="0.5" max={sb.host_cpus} value={value.cpus ?? ''}
                  placeholder={String(sb.cpus)} onChange={(e) => onChange({ ...value, cpus: e.target.value })} />
              </label>
              <label className="advanced-field">
                <span>Memory (GB)</span>
                <input type="number" min="1" step="1" max={Math.floor(sb.host_memory_mb / 1024)} value={value.memoryGb ?? ''}
                  placeholder={String(sb.memory_mb / 1024)} onChange={(e) => onChange({ ...value, memoryGb: e.target.value })} />
              </label>
            </div>
          )}
        </div>
      )}
    </div>
  )
}


// ---------------------------------------------------------------------------
// A confirmed proposal: what was started, read-only
// ---------------------------------------------------------------------------

const MODEL_ROLE_LABELS: Record<string, string> = {
  agent: 'Agents',
  screening: 'Screening',
  planning: 'Planning',
  preflight: 'Pre-flight',
  research: 'Research',
  analysis: 'Analysis',
  writing: 'Writing',
  ideation_seats: 'Ideation seats',
  ideation_chair: 'Ideation chair',
  auditor: 'Milestone auditor',
  skills: 'Skill drafting',
  supervisor: 'Live auditor',
  frontdesk: 'Front desk',
}

function StartedProposal({
  proposal,
  started,
  onOpen,
}: {
  proposal: Proposal
  started: ConfirmedSettings | null
  onOpen: (expId: string) => void
}) {
  const [showInstruction, setShowInstruction] = useState(false)
  const skip = started?.skip_phases ?? proposal.skip_phases
  const active = PHASES.filter((p) => !skip.includes(p.key)).map((p) => p.label)
  const models = Object.entries(started?.models ?? {}).filter(([role, v]) =>
    v !== '' && v != null && !(Array.isArray(v) && v.length === 0) && role in MODEL_ROLE_LABELS)
  const budget = started && 'max_cost_usd' in started
    ? (started.max_cost_usd == null ? 'no limit' : `US$${started.max_cost_usd}`)
    : proposal.max_cost_usd != null ? `US$${proposal.max_cost_usd}` : null

  return (
    <div className="proposal-card confirmed">
      <div className="proposal-header">
        <span className="proposal-icon">{'✓'}</span>
        <span className="proposal-title">Experiment started</span>
        {started?.experiment_id && (
          <button className="proposal-open" onClick={() => onOpen(started.experiment_id)}>Open experiment →</button>
        )}
      </div>
      <div className="proposal-summary">{proposal.summary}</div>
      {proposal.params.instruction && (
        <div className="proposal-detail">
          <button className="proposal-detail-toggle" onClick={() => setShowInstruction(!showInstruction)}>
            {showInstruction ? '▾' : '▸'} Instruction
          </button>
          {showInstruction && <div className="proposal-instruction">{proposal.params.instruction}</div>}
        </div>
      )}
      <dl className="proposal-settings">
        <dt>Phases</dt><dd>{active.join(' → ')}</dd>
        {!skip.includes('ideation') && started?.ideation_mode && (
          <><dt>Ideation</dt><dd>{started.ideation_live ? 'Round table, live in the brainstorm' : started.ideation_mode === 'independent' ? 'Independent' : 'Round table'}</dd></>
        )}
        {started?.add_found && (
          <><dt>Papers found</dt><dd>{RULE_LABELS[started.add_found]}</dd></>
        )}
        {budget && <><dt>Budget</dt><dd>{budget}</dd></>}
        {started?.autonomy_mode && (
          <><dt>Decisions</dt><dd>{started.autonomy_mode === 'autonomous' ? 'Autonomous' : 'Supervised'}</dd></>
        )}
        {started?.max_parallel_jobs != null && started.max_parallel_jobs > 1 && (
          <><dt>Parallel jobs</dt><dd>{started.max_parallel_jobs}</dd></>
        )}
        {(started?.cpus || started?.memory_mb) && (
          <><dt>Container</dt><dd>{[started.cpus ? `${started.cpus} CPUs` : '', started.memory_mb ? `${started.memory_mb / 1024} GB` : '']
            .filter(Boolean).join(', ')}</dd></>
        )}
        {models.map(([role, v]) => (
          <Fragment key={role}>
            <dt>{MODEL_ROLE_LABELS[role]}</dt><dd>{Array.isArray(v) ? v.join(', ') : v}</dd>
          </Fragment>
        ))}
        {started?.experiment_id && <><dt>Experiment</dt><dd>{started.experiment_id}</dd></>}
      </dl>
    </div>
  )
}

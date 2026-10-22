import { useEffect, useMemo, useState } from 'react'
import { api, describeError } from '../api/client'
import { formatCost } from '../format'
import type { Analytics, ChannelType, ConsoleSettings, EditableSetting, NotificationSettings, NotifyChannel, SearchHit } from '../types'

type PanelProps = { collapsed: boolean; panelRef: React.RefObject<HTMLDivElement | null> }

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

const SEARCH_GROUPS: [key: 'projects' | 'runs' | 'conversations' | 'reports' | 'skills', label: string][] = [
  ['projects', 'Projects'],
  ['runs', 'Runs'],
  ['reports', 'Reports'],
  ['conversations', 'Conversations'],
  ['skills', 'Skills'],
]

/** Search projects, runs, reports, conversations and skills; a click opens what was found. */
export function SearchPanel({ collapsed, panelRef, onOpenProject, onOpenRun, onOpenSkill }: PanelProps & {
  onOpenProject: (campaignId: string) => void
  onOpenRun: (campaignId: string, runId: string) => void
  onOpenSkill: (name: string) => void
}) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Partial<Record<string, SearchHit[]>>>({})
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) return
    let live = true
    const timer = setTimeout(async () => {
      setSearching(true)
      setError(null)
      try {
        const r = await api.search(q)
        if (live) setResults(r.results)
      } catch (err) {
        if (live) setError(describeError(err))
      } finally {
        if (live) setSearching(false)
      }
    }, 300)
    return () => { live = false; clearTimeout(timer) }
  }, [query])

  const open = (kind: string, hit: SearchHit) => {
    if (kind === 'skills') onOpenSkill(hit.title)
    else if (hit.run_id && hit.campaign_id) onOpenRun(hit.campaign_id, hit.run_id)
    else if (hit.campaign_id) onOpenProject(hit.campaign_id)
  }
  const q = query.trim()
  const shown = q.length >= 2 ? results : {}
  const total = Object.values(shown).reduce((n, list) => n + (list?.length ?? 0), 0)

  return (
    <div className={`skills-panel side-panel${collapsed ? ' collapsed' : ''}`} ref={panelRef}>
      <div className="skills-header">
        <div className="skills-title">Search</div>
        <input className="skills-search" autoFocus placeholder="Projects, runs, reports, chats, skills..."
          value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <div className="skills-list">
        {error && <div className="tl-detail tone-error" style={{ padding: '6px 12px' }}>{error}</div>}
        {q.length >= 2 && !searching && total === 0 && <div className="panel-empty">Nothing found for “{q}”</div>}
        {q.length < 2 && <div className="panel-empty">Type at least two characters.</div>}
        {SEARCH_GROUPS.map(([kind, label]) => {
          const hits = shown[kind]
          if (!hits?.length) return null
          return (
            <div key={kind} className="search-group">
              <div className="search-group-title">{label}</div>
              {hits.map((hit, i) => (
                <button key={`${kind}-${i}`} className="search-hit" onClick={() => open(kind, hit)}>
                  <div className="search-hit-title">
                    {hit.title}
                    {hit.project && kind !== 'projects' && <span className="search-hit-meta"> · {hit.project}</span>}
                    {hit.status && <span className="search-hit-meta"> · {hit.status}</span>}
                  </div>
                  <div className="search-hit-snippet">{hit.snippet}</div>
                </button>
              ))}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------------

/** What the experiments cost and how they went. */
export function AnalyticsPanel({ collapsed, panelRef, onOpenProject, onOpenRun }: PanelProps & {
  onOpenProject: (campaignId: string) => void
  onOpenRun: (campaignId: string, runId: string) => void
}) {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<Analytics | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (collapsed) return
    let live = true
    api.analytics(days)
      .then((d) => { if (live) { setData(d); setError(null) } })
      .catch((err) => { if (live) setError(describeError(err)) })
    return () => { live = false }
  }, [collapsed, days])

  const maxDay = useMemo(() => Math.max(0.01, ...(data?.cost_by_day.map((d) => d.cost_usd) ?? [0])), [data])
  const maxProject = useMemo(() => Math.max(0.01, ...(data?.cost_by_project.map((p) => p.cost_usd) ?? [0])), [data])

  return (
    <div className={`skills-panel side-panel${collapsed ? ' collapsed' : ''}`} ref={panelRef}>
      <div className="skills-header">
        <div className="skills-title">
          Analytics
          <select className="panel-select" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>last 7 days</option>
            <option value={30}>last 30 days</option>
            <option value={90}>last 90 days</option>
            <option value={365}>last year</option>
          </select>
        </div>
      </div>
      <div className="skills-list analytics">
        {error && <div className="tl-detail tone-error" style={{ padding: '6px 12px' }}>{error}</div>}
        {data && (
          <>
            <div className="stat-tiles">
              <div className="stat-tile"><div className="stat-tile-value">{formatCost(data.total_cost_usd)}</div><div className="stat-tile-label">spent</div></div>
              <div className="stat-tile"><div className="stat-tile-value">{data.runs}</div><div className="stat-tile-label">runs</div></div>
              <div className="stat-tile"><div className="stat-tile-value">{data.success_rate == null ? '—' : `${Math.round(data.success_rate * 100)}%`}</div><div className="stat-tile-label">completed</div></div>
              <div className="stat-tile"><div className="stat-tile-value">{data.mean_hours == null ? '—' : `${data.mean_hours}h`}</div><div className="stat-tile-label">mean duration</div></div>
            </div>
            {Object.keys(data.by_status).length > 0 && (
              <div className="analytics-status">
                {Object.entries(data.by_status).map(([status, n]) => <span key={status} className="skill-badge">{status}: {n}</span>)}
              </div>
            )}

            <div className="search-group-title">Cost per day</div>
            <div className="day-bars" role="img" aria-label="Cost per day">
              {data.cost_by_day.map((d) => (
                <div key={d.day} className="day-bar" title={`${d.day}: ${formatCost(d.cost_usd)}`}>
                  <div className="day-bar-fill" style={{ height: `${(d.cost_usd / maxDay) * 100}%` }} />
                </div>
              ))}
            </div>
            <div className="day-bars-axis"><span>{data.cost_by_day[0]?.day}</span><span>{data.cost_by_day[data.cost_by_day.length - 1]?.day}</span></div>

            <div className="search-group-title">By project</div>
            {data.cost_by_project.length === 0 && <div className="panel-empty">No runs in this period.</div>}
            {data.cost_by_project.map((p) => (
              <button key={p.campaign_id} className="bar-row" onClick={() => onOpenProject(p.campaign_id)}>
                <span className="bar-row-label">{p.project}</span>
                <span className="bar-row-track"><span className="bar-row-fill" style={{ width: `${(p.cost_usd / maxProject) * 100}%` }} /></span>
                <span className="bar-row-value">{formatCost(p.cost_usd)} · {p.runs}</span>
              </button>
            ))}

            <div className="search-group-title">Most expensive runs</div>
            {data.top_runs.map((r) => (
              <button key={r.run_id} className="search-hit" onClick={() => onOpenRun(r.campaign_id, r.run_id)}>
                <div className="search-hit-title">{r.name}<span className="search-hit-meta"> · {r.project}</span></div>
                <div className="search-hit-snippet">{formatCost(r.cost_usd)} · {r.status}{r.hours != null ? ` · ${r.hours}h` : ''}</div>
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

function show(value: unknown, kind = ''): string {
  if (value == null) return ''
  if (Array.isArray(value)) return value.join(kind === 'words' ? ' ' : ', ')
  return String(value)
}

function parse(setting: EditableSetting, text: string): unknown {
  switch (setting.kind) {
    case 'models': return text.split(',').map((x) => x.trim()).filter(Boolean)
    case 'words': return text.split(/\s+/).filter(Boolean)
    case 'number': return Number(text)
    case 'int': return Math.round(Number(text))
    case 'optional-number': return text.trim() === '' ? null : Number(text)
    default: return text.trim()
  }
}

/** Defaults for new experiments, over the server's base config; and the state of the environment. */
export function SettingsPanel({ collapsed, panelRef }: PanelProps) {
  const [settings, setSettings] = useState<ConsoleSettings | null>(null)
  const [draft, setDraft] = useState<Record<string, string | boolean>>({})
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)

  const load = () => api.settings.get().then(setSettings).catch((err) => setMessage({ tone: 'error', text: describeError(err) }))
  useEffect(() => { if (!collapsed) load() }, [collapsed])

  const groups = useMemo(() => {
    const out: Record<string, EditableSetting[]> = {}
    for (const s of settings?.editable ?? []) (out[s.group] ??= []).push(s)
    return out
  }, [settings])

  const valueOf = (s: EditableSetting): string | boolean =>
    s.key in draft ? draft[s.key] : (s.kind === 'bool' ? Boolean(s.value) : show(s.value, s.kind))
  const dirty = (settings?.editable ?? []).filter((s) => s.key in draft
    && (s.kind === 'bool' ? draft[s.key] !== Boolean(s.value) : draft[s.key] !== show(s.value, s.kind)))

  const save = async (reset: string[] = []) => {
    setSaving(true)
    setMessage(null)
    try {
      const changes = Object.fromEntries(dirty.map((s) => [s.key, s.kind === 'bool' ? draft[s.key] : parse(s, String(draft[s.key]))]))
      await api.settings.update(reset.length ? {} : changes, reset)
      setDraft((d) => {
        const next = { ...d }
        for (const k of reset.length ? reset : Object.keys(changes)) delete next[k]
        return next
      })
      await load()
      setMessage({ tone: 'ok', text: reset.length ? 'Back to the server’s value.' : 'Saved. New experiments start from these defaults.' })
    } catch (err) {
      setMessage({ tone: 'error', text: describeError(err) })
    } finally {
      setSaving(false)
    }
  }

  const env = settings?.environment
  const check = (ok: boolean) => <span className={ok ? 'env-ok' : 'env-bad'}>{ok ? '✓' : '✗'}</span>

  return (
    <div className={`skills-panel side-panel settings-panel${collapsed ? ' collapsed' : ''}`} ref={panelRef}>
      <div className="skills-header">
        <div className="skills-title">
          Settings
          <button className="skills-new" disabled={!dirty.length || saving} onClick={() => save()}>
            {saving ? 'Saving…' : dirty.length ? `Save ${dirty.length}` : 'Saved'}
          </button>
        </div>
        <div className="panel-hint">Defaults for new experiments. A proposal card can still change them for one experiment.</div>
      </div>
      <div className="skills-list">
        {message && <div className={`panel-message ${message.tone}`}>{message.text}</div>}
        {env && (
          <div className="settings-group">
            <div className="search-group-title">Environment</div>
            <div className="env-row">{check(env.api_key)} API key {env.api_key ? 'set' : 'missing (OPENROUTER_API_KEY)'}</div>
            <div className="env-row">{check(env.docker.ok)} Docker {env.docker.ok ? 'available' : env.docker.detail}</div>
            <div className="env-row">{check(env.claude.ok)} Claude Code {env.claude.ok ? env.claude.version : 'not found'}</div>
            <div className="env-row muted">{env.host_cpus} CPUs · {Math.round(env.host_memory_mb / 1024)} GB · {env.disk_free_gb} GB free disk</div>
            <div className="env-row muted" title={env.console_settings}>AURORA_HOME: {env.aurora_home}</div>
            <div className="env-row muted">Server config: {env.server_config ?? 'none (built-in defaults)'}</div>
          </div>
        )}
        {!collapsed && <NotificationsSection />}
        <datalist id="settings-models">
          {settings?.model_suggestions.map((m) => <option key={m} value={m} />)}
        </datalist>
        {Object.entries(groups).map(([group, items]) => (
          <div key={group} className="settings-group">
            <div className="search-group-title">{group}</div>
            {items.map((s) => {
              const v = valueOf(s)
              const changed = dirty.some((d) => d.key === s.key)
              return (
                <div key={s.key} className={`settings-field${changed ? ' changed' : ''}`} title={s.help}>
                  <label>
                    <span className="settings-label">{s.label}</span>
                    {s.kind === 'bool' ? (
                      <input type="checkbox" checked={Boolean(v)} onChange={(e) => setDraft({ ...draft, [s.key]: e.target.checked })} />
                    ) : s.kind === 'choice' ? (
                      <select value={String(v)} onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })}>
                        {s.choices?.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    ) : (
                      <input
                        list={s.kind === 'model' || s.kind === 'models' ? 'settings-models' : undefined}
                        type={['number', 'int', 'optional-number'].includes(s.kind) ? 'number' : 'text'}
                        value={String(v)}
                        placeholder={s.kind === 'optional-number' ? 'no limit' : s.kind === 'model' && s.key !== 'models.agent' ? 'same as agents' : ''}
                        onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })} />
                    )}
                  </label>
                  {s.overridden && (
                    <div className="settings-note">
                      changed here · server: {show(s.server_value, s.kind) || 'none'}
                      <button className="link-btn" disabled={saving} onClick={() => save([s.key])}>reset</button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ))}
        {env && <div className="panel-hint" style={{ padding: '8px 12px 16px' }}>Kept in {env.console_settings}; the server’s config file is not changed.</div>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

const CHANNEL_TYPES: { type: ChannelType; label: string; placeholder: string }[] = [
  { type: 'wecom', label: 'WeCom group robot (企业微信)', placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…' },
  { type: 'serverchan', label: 'Server酱 (personal WeChat)', placeholder: 'SendKey, e.g. SCT…' },
  { type: 'feishu', label: 'Feishu / Lark bot', placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/…' },
  { type: 'slack', label: 'Slack', placeholder: 'https://hooks.slack.com/services/…' },
  { type: 'webhook', label: 'Webhook (JSON POST)', placeholder: 'https://your-server/aurora-hook' },
]
const typeLabel = (t: ChannelType) => CHANNEL_TYPES.find((c) => c.type === t)?.label ?? t

/** Where notifications go: channels, what each one gets, a test message. */
function NotificationsSection() {
  const [saved, setSaved] = useState<NotificationSettings | null>(null)
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [consoleUrl, setConsoleUrl] = useState('')
  const [quiet, setQuiet] = useState({ start: '', end: '', except: [] as string[], links: true })
  const [adding, setAdding] = useState<NotifyChannel | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)

  const reset = (n: NotificationSettings) => {
    setSaved(n)
    setChannels(n.channels)
    setConsoleUrl(n.console_url)
    setQuiet({ start: n.quiet_start ?? '', end: n.quiet_end ?? '', except: n.quiet_except ?? [], links: n.action_links ?? true })
  }
  useEffect(() => {
    api.settings.notifications().then(reset).catch((err) => setMessage({ tone: 'error', text: describeError(err) }))
  }, [])
  if (!saved) return message ? <div className={`panel-message ${message.tone}`}>{message.text}</div> : null

  const defaults = saved.events.filter((e) => e.default).map((e) => e.key)
  const dirty = consoleUrl !== saved.console_url || JSON.stringify(channels) !== JSON.stringify(saved.channels)
    || quiet.start !== (saved.quiet_start ?? '') || quiet.end !== (saved.quiet_end ?? '') || quiet.links !== (saved.action_links ?? true)
    || JSON.stringify(quiet.except) !== JSON.stringify(saved.quiet_except ?? [])
  const update = (id: string, patch: Partial<NotifyChannel>) =>
    setChannels((list) => list.map((c) => (c.id === id ? { ...c, ...patch } : c)))

  const save = async (list = channels) => {
    setBusy(true)
    setMessage(null)
    try {
      reset(await api.settings.saveNotifications({ console_url: consoleUrl, budget_warning_at: saved.budget_warning_at, channels: list,
        quiet_start: quiet.start, quiet_end: quiet.end, quiet_except: quiet.except, action_links: quiet.links }))
      setMessage({ tone: 'ok', text: 'Saved. Running experiments use these settings too.' })
    } catch (err) {
      setMessage({ tone: 'error', text: describeError(err) })
    } finally {
      setBusy(false)
    }
  }
  const test = async (c: NotifyChannel) => {
    setBusy(true)
    setMessage(null)
    try {
      const r = await api.settings.testNotification(c)
      setMessage(r.ok ? { tone: 'ok', text: `Test sent to ${c.name || typeLabel(c.type)}. Check that it arrived.` }
        : { tone: 'error', text: `${c.name || typeLabel(c.type)}: ${r.error}` })
    } catch (err) {
      setMessage({ tone: 'error', text: describeError(err) })
    } finally {
      setBusy(false)
    }
  }
  const events = (c: NotifyChannel, onChange: (events: string[]) => void) => (
    <div className="notify-events">
      {saved.events.map((e) => (
        <label key={e.key} title={e.label}>
          <input type="checkbox" checked={c.events.includes(e.key)}
            onChange={(ev) => onChange(ev.target.checked ? [...c.events, e.key] : c.events.filter((x) => x !== e.key))} />
          {e.label}
        </label>
      ))}
    </div>
  )

  return (
    <div className="settings-group">
      <div className="search-group-title">Notifications</div>
      <div className="panel-hint" style={{ padding: '0 0 6px' }}>
        Messages when a decision waits for you or an experiment ends, so you need not watch the console.
      </div>
      {message && <div className={`panel-message ${message.tone}`}>{message.text}</div>}
      {channels.map((c) => (
        <div key={c.id} className={`notify-channel${c.enabled ? '' : ' off'}`}>
          <div className="notify-channel-head">
            <input type="checkbox" checked={c.enabled} title="On" onChange={(e) => update(c.id, { enabled: e.target.checked })} />
            <span className="notify-channel-name">{c.name || typeLabel(c.type)}</span>
            <span className="notify-channel-type">{c.name ? typeLabel(c.type) : ''} {c.target_hint}</span>
            <button className="link-btn" disabled={busy} onClick={() => test(c)}>test</button>
            <button className="link-btn danger" disabled={busy}
              onClick={() => setChannels((list) => list.filter((x) => x.id !== c.id))}>remove</button>
          </div>
          {events(c, (ev) => update(c.id, { events: ev }))}
        </div>
      ))}
      {adding ? (
        <div className="notify-channel adding">
          <div className="settings-field"><label>
            <span className="settings-label">Type</span>
            <select value={adding.type} onChange={(e) => setAdding({ ...adding, type: e.target.value as ChannelType })}>
              {CHANNEL_TYPES.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
            </select>
          </label></div>
          <div className="settings-field"><label>
            <span className="settings-label">{adding.type === 'serverchan' ? 'SendKey' : 'Webhook URL'}</span>
            <input value={adding.target ?? ''} placeholder={CHANNEL_TYPES.find((t) => t.type === adding.type)?.placeholder}
              onChange={(e) => setAdding({ ...adding, target: e.target.value })} />
          </label></div>
          <div className="settings-field"><label>
            <span className="settings-label">Name (optional)</span>
            <input value={adding.name} placeholder="e.g. my phone" onChange={(e) => setAdding({ ...adding, name: e.target.value })} />
          </label></div>
          {events(adding, (ev) => setAdding({ ...adding, events: ev }))}
          <div className="notify-actions">
            <button className="btn-sm" disabled={busy || !adding.target?.trim()} onClick={() => test(adding)}>Send a test</button>
            <button className="btn-sm" disabled={busy} onClick={() => setAdding(null)}>Cancel</button>
            <button className="btn-sm btn-approve" disabled={busy || !adding.target?.trim()}
              onClick={async () => { const list = [...channels, adding]; setAdding(null); await save(list) }}>Add</button>
          </div>
        </div>
      ) : (
        <button className="btn-sm" style={{ marginTop: 4 }}
          onClick={() => setAdding({ id: '', type: 'wecom', name: '', target: '', events: defaults, enabled: true })}>
          + Add a channel
        </button>
      )}
      <div className="settings-field" title="Notifications link to the experiment at this address">
        <label>
          <span className="settings-label">Console address in links</span>
          <input value={consoleUrl} placeholder={saved.console_url_default || 'http://localhost:8000'}
            onChange={(e) => setConsoleUrl(e.target.value)} />
        </label>
      </div>
      <div className="settings-field" title="Messages in these hours are held and sent together when they end">
        <span className="settings-label">Quiet hours</span>
        <div className="notify-quiet">
          <input aria-label="Quiet from" placeholder="22:00" value={quiet.start} onChange={(e) => setQuiet({ ...quiet, start: e.target.value })} />
          <span>to</span>
          <input aria-label="Quiet until" placeholder="08:00" value={quiet.end} onChange={(e) => setQuiet({ ...quiet, end: e.target.value })} />
        </div>
        <label className="notify-check"><input type="checkbox" checked={quiet.except.includes('decision_needed')}
          onChange={(e) => setQuiet({ ...quiet, except: e.target.checked ? [...quiet.except, 'decision_needed'] : quiet.except.filter((x) => x !== 'decision_needed') })} />
          Decisions still come through</label>
        <div className="panel-hint" style={{ padding: 0 }}>What arrives in quiet hours is held and sent as one message when they end. A run waiting
          for a decision goes on by itself after the time set under Decisions.</div>
      </div>
      <label className="notify-check" title="The links open a page that asks to confirm; group chats: anyone in the chat could use them">
        <input type="checkbox" checked={quiet.links} onChange={(e) => setQuiet({ ...quiet, links: e.target.checked })} />
        “Approve” and “Stop” links in decision messages{!consoleUrl && !saved.console_url_default ? ' (needs the console address below)' : ''}
      </label>
      {dirty && (
        <div className="notify-actions">
          <button className="btn-sm" disabled={busy} onClick={() => reset(saved)}>Discard</button>
          <button className="btn-sm btn-approve" disabled={busy} onClick={() => save()}>Save notifications</button>
        </div>
      )}
    </div>
  )
}

/** ⌘K, anywhere: ask Pippin about what you are looking at (the project, the run, the text you selected), jump to a
 *  page, a project or a run, or find what you wrote and what the agents did. Enter does the first thing listed;
 *  ↑ ↓ choose; Esc closes. An answer opens here, with buttons to the pages it names. */
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, describeError } from '../api/client'
import type { AssistantTurn, CampaignSummary, ExperimentSummary, SearchHit } from '../types'

export interface PaletteGo {
  page: (p: 'brainstorm' | 'library' | 'memory' | 'team') => void
  panel: (p: 'search' | 'analytics' | 'skills' | 'settings' | 'help') => void
  home: () => void
  newQuestion: () => void
  project: (campaignId: string) => void
  run: (campaignId: string, runId: string) => void
  skill: (name: string) => void
}

interface Item { key: string; group: string; label: string; hint?: string; run: () => void }

const PAGES: { label: string; hint: string; go: (g: PaletteGo) => void }[] = [
  { label: 'Home', hint: 'what waits for you today', go: (g) => g.home() },
  { label: 'New question', hint: 'start a project from a question', go: (g) => g.newQuestion() },
  { label: 'Brainstorm', hint: 'think it through with the Mentor', go: (g) => g.page('brainstorm') },
  { label: 'Library', hint: 'papers and the feed', go: (g) => g.page('library') },
  { label: 'Memory', hint: 'what you read and saved', go: (g) => g.page('memory') },
  { label: 'Analytics', hint: 'cost and runs over time', go: (g) => g.panel('analytics') },
  { label: 'Skills', hint: 'the skill library and catalog', go: (g) => g.panel('skills') },
  { label: 'Settings', hint: 'models, budgets, notifications', go: (g) => g.panel('settings') },
  { label: 'Team', hint: 'people and access', go: (g) => g.page('team') },
  { label: 'Help', hint: 'the checklist and the tours', go: (g) => g.panel('help') },
  { label: 'Search everything', hint: 'the full search panel', go: (g) => g.panel('search') },
]

const HIT_GROUP: Record<string, string> = { runs: 'Runs', reports: 'Reports', conversations: 'Conversations', skills: 'Skills', projects: 'Projects' }

export function CommandPalette({ onClose, go, where, campaigns, experiments, activeCampaignId }: {
  onClose: () => void
  go: PaletteGo
  /** What is in front, in a few words ("Cu–Zn segregation · run 3"); empty on a page that is not about one thing. */
  where: string
  campaigns: CampaignSummary[]
  experiments: ExperimentSummary[]
  activeCampaignId: string | null
}) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const [hits, setHits] = useState<Partial<Record<string, SearchHit[]>>>({})
  const [asking, setAsking] = useState<string | null>(null)
  const [answer, setAnswer] = useState<(AssistantTurn & { reply: string; said: string }) | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selection] = useState(() => (window.getSelection()?.toString() ?? '').trim().slice(0, 600))
  const input = useRef<HTMLInputElement | null>(null)
  const list = useRef<HTMLDivElement | null>(null)

  useEffect(() => { input.current?.focus() }, [])
  useEffect(() => {
    const t = q.trim()
    if (t.length < 2) return
    let live = true
    const timer = window.setTimeout(() => {
      api.search(t, 5).then((r) => { if (live) setHits(r.results) }).catch(() => { /* search is a bonus here */ })
    }, 250)
    return () => { live = false; window.clearTimeout(timer) }
  }, [q])

  const ask = async (text: string) => {
    setAsking(text); setAnswer(null); setError(null)
    const context = [where && `the console, ${where}`, selection && `the text they selected: “${selection}”`].filter(Boolean).join('; ')
    try {
      setAnswer(await api.assistant.say(text, context))
    } catch (e) {
      setError(describeError(e))
    } finally {
      setAsking(null)
    }
  }

  const items = useMemo<Item[]>(() => {
    const t = q.trim().toLowerCase()
    const done = (fn: () => void) => () => { fn(); onClose() }
    const out: Item[] = []
    if (t) out.push({ key: 'ask', group: 'Ask Pippin', label: q.trim(), hint: where ? `about ${where}` : 'about anything', run: () => ask(q.trim()) })
    const pages = PAGES.filter((p) => !t || p.label.toLowerCase().includes(t) || p.hint.includes(t))
    out.push(...pages.slice(0, t ? 4 : 6).map((p) => ({ key: `page:${p.label}`, group: 'Go to', label: p.label, hint: p.hint, run: done(() => p.go(go)) })))
    const projects = campaigns.filter((c) => !t || c.name.toLowerCase().includes(t)).slice(0, t ? 5 : 4)
    out.push(...projects.map((c) => ({ key: `p:${c.campaign_id}`, group: 'Projects', label: c.name,
      hint: c.active_experiments ? `${c.active_experiments} running` : c.needs_attention ? 'waits for you' : '', run: done(() => go.project(c.campaign_id)) })))
    if (activeCampaignId) {
      const runs = experiments.filter((e) => !t || (e.task_name || e.run_id).toLowerCase().includes(t)).slice(0, t ? 5 : 3)
      out.push(...runs.map((e) => ({ key: `r:${e.run_id}`, group: 'Runs in this project', label: e.task_name || e.run_id, hint: e.status,
        run: done(() => go.run(activeCampaignId, e.run_id)) })))
    }
    if (t.length >= 2) {
      for (const [kind, list] of Object.entries(hits)) {
        if (kind === 'projects') continue
        for (const h of (list ?? []).slice(0, 4)) {
          out.push({ key: `h:${kind}:${h.campaign_id}:${h.run_id}:${h.title}`, group: HIT_GROUP[kind] ?? kind, label: h.title,
            hint: h.project || h.snippet?.slice(0, 80),
            run: done(() => kind === 'skills' ? go.skill(h.title) : h.run_id && h.campaign_id ? go.run(h.campaign_id, h.run_id) : h.campaign_id ? go.project(h.campaign_id) : undefined) })
        }
      }
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, hits, campaigns, experiments, activeCampaignId, where])

  const cur = Math.min(sel, Math.max(0, items.length - 1))
  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [cur])

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose() } else if (e.key === 'ArrowDown') { e.preventDefault(); setSel(Math.min(cur + 1, items.length - 1)) } else if (e.key === 'ArrowUp') { e.preventDefault(); setSel(Math.max(cur - 1, 0)) } else if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); items[cur]?.run() }
  }

  const openPath = (path: string) => {
    const m = path.match(/^\/campaigns\/([^/?]+)(?:\/experiments\/([^/?]+))?/)
    const page = new URLSearchParams(path.split('?')[1] ?? '').get('page')
    if (m && m[2]) go.run(m[1], m[2])
    else if (m) go.project(m[1])
    else if (page === 'library' || page === 'memory' || page === 'brainstorm') go.page(page)
    else go.home()
    onClose()
  }

  let lastGroup = ''
  return (
    <div className="cp-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="cp" role="dialog" aria-label="Ask Pippin, or go somewhere" onKeyDown={onKey}>
        {(where || selection) && (
          <div className="cp-context">
            {where && <span className="cp-chip"><span className="cp-chip-dot" />Looking at · {where}</span>}
            {selection && <span className="cp-chip sel">Selected · “{selection.length > 70 ? `${selection.slice(0, 70)}…` : selection}”</span>}
          </div>
        )}
        <div className="cp-input">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
          <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setSel(0) }} placeholder="Ask Pippin, or go to a page, project or run"
            aria-label="Ask Pippin, or go somewhere" aria-controls="cp-list" aria-activedescendant={items[cur] ? `cp-${cur}` : undefined} />
          <kbd>esc</kbd>
        </div>

        {(asking || answer || error) && (
          <div className="cp-answer" aria-live="polite">
            <p className="cp-answer-q">{asking ?? answer?.said}</p>
            {asking && <p className="cp-answer-a muted">Looking…</p>}
            {answer && <p className="cp-answer-a">{answer.reply}</p>}
            {answer && (answer.links ?? []).length > 0 && (
              <div className="cp-answer-links">{answer.links!.map((l) => <button key={l.path} className="cp-btn" onClick={() => openPath(l.path)}>{l.label}</button>)}</div>
            )}
            {error && <p className="cp-answer-a bad">{error}</p>}
          </div>
        )}

        <div className="cp-list" id="cp-list" role="listbox" ref={list}>
          {items.map((it, i) => {
            const head = it.group !== lastGroup ? it.group : ''
            lastGroup = it.group
            return (
              <div key={it.key}>
                {head && <div className="cp-group">{head}</div>}
                <div id={`cp-${i}`} role="option" aria-selected={i === cur} className={`cp-item${i === cur ? ' on' : ''}${it.key === 'ask' ? ' ask' : ''}`}
                  onMouseMove={() => setSel(i)} onClick={() => it.run()}>
                  <span className="cp-label">{it.label}</span>
                  {it.hint && <span className="cp-hint">{it.hint}</span>}
                  {i === cur && <span className="cp-enter">⏎</span>}
                </div>
              </div>
            )
          })}
        </div>
        <div className="cp-foot"><span>↑ ↓ choose</span><span>⏎ do it</span><span>esc close</span><span className="cp-foot-r">Answers come from Pippin, the same as the orb</span></div>
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import type { HelpAnswer, HelpPage } from '../types'
import { Markdown } from './Markdown'
import { openDoc, startTour, updateOnboarding, useOnboarding } from './store'

/** Help in the console: ask how to do something, the getting-started checklist, the tours, the
 *  examples and the guides. */
export function HelpPanel({ collapsed, panelRef, onAction }: {
  collapsed: boolean
  panelRef: React.RefObject<HTMLDivElement | null>
  /** demo | start | brainstorm | library | examples | team | settings:<section> | key */
  onAction: (action: string) => void
}) {
  const ob = useOnboarding()
  const [q, setQ] = useState('')
  const [asking, setAsking] = useState(false)
  const [answer, setAnswer] = useState<HelpAnswer | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pages, setPages] = useState<HelpPage[] | null>(null)
  useEffect(() => { if (!collapsed && !pages) api.help.pages().then((r) => setPages(r.pages)).catch(() => setPages([])) }, [collapsed, pages])

  const ask = async () => {
    if (!q.trim()) return
    setAsking(true)
    setError(null)
    try { setAnswer(await api.help.ask(q.trim())) } catch (e) { setError(describeError(e)) } finally { setAsking(false) }
  }
  const act = (action: string) => {
    if (action.startsWith('tour:')) startTour(action.slice(5) as 'run' | 'app' | 'start')
    else if (action.startsWith('doc:')) openDoc(action.slice(4))
    else if (action === 'key') updateOnboarding({ done: false, step: 'connect' }).catch(() => {})
    else onAction(action)
  }
  const done = ob?.checklist.filter((c) => c.done).length ?? 0
  const total = ob?.checklist.length ?? 0
  const sections = (pages ?? []).reduce<Record<string, HelpPage[]>>((acc, p) => {
    (acc[p.section || 'AURORA'] ??= []).push(p)
    return acc
  }, {})

  return (
    <div className={`skills-panel side-panel settings-panel help-panel${collapsed ? ' collapsed' : ''}`} ref={panelRef} aria-label="Help">
      <div className="skills-header"><div className="skills-title">Help</div></div>
      <div className="help-body">
        <form className="help-ask" onSubmit={(e) => { e.preventDefault(); ask() }}>
          <label htmlFor="help-q" className="help-label">Ask how to do something</label>
          <div className="help-row">
            <input id="help-q" className="fd-input" value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="e.g. how do I send training to our cluster?" />
            <button className="btn-sm lit-btn" disabled={asking || !q.trim()}>{asking ? 'Looking…' : 'Ask'}</button>
          </div>
          <span className="help-hint">Answered from the documentation, with links to it.</span>
        </form>
        {error && <div className="panel-message error">{error}</div>}
        {answer && (
          <div className="help-answer">
            {answer.answer && <Markdown text={answer.answer} onLink={(h) => openDoc(h)} imageUrl={(s) => s} />}
            {answer.note && <div className="help-hint">{answer.note}</div>}
            {answer.sources.length > 0 && (
              <ol className="help-sources">
                {answer.sources.map((s) => (
                  <li key={s.n}><button className="link-btn" onClick={() => openDoc(s.path)}>{s.page} — {s.heading}</button>
                    {!answer.answer && <div className="help-hint">{s.excerpt.slice(0, 200)}…</div>}</li>
                ))}
              </ol>
            )}
          </div>
        )}

        {ob && (
          <section className="help-section">
            <div className="help-head"><b>Getting started</b><span className="mono">{done} of {total}</span></div>
            <div className="help-bar"><div style={{ width: `${total ? (done / total) * 100 : 0}%` }} /></div>
            <ul className="help-checklist">
              {ob.checklist.map((c) => (
                <li key={c.key} className={c.done ? 'done' : ''}>
                  <span className="help-check" aria-hidden="true">{c.done ? '✓' : ''}</span>
                  <div className="help-item">
                    <span className="help-item-label">{c.label}</span>
                    <span className="help-hint">{c.hint}</span>
                  </div>
                  {!c.done && <button className="link-btn" onClick={() => act(c.action)}>{c.action.startsWith('doc:') ? 'How' : 'Go'}</button>}
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="help-section">
          <b>Guided tours</b>
          {([['run', 'A finished run', 'eleven steps over the Citi Bike example'], ['app', 'Around AURORA', 'every button of the left bar'],
            ['start', 'Your own research', 'from a question to a report']] as const).map(([id, label, hint]) => (
            <button key={id} className="help-tour" onClick={() => startTour(id)}>
              <span>{ob?.tours.includes(id) ? '✓ ' : ''}{label}</span><span className="help-hint">{hint}</span>
            </button>
          ))}
          <button className="help-tour" onClick={() => onAction('examples')}>
            <span>Examples</span><span className="help-hint">finished runs to look at, and the scripted demo</span>
          </button>
        </section>

        <section className="help-section">
          <b>Guides</b>
          {pages === null && <span className="help-hint">Loading…</span>}
          {pages?.length === 0 && <span className="help-hint">The documentation is not installed with this console.</span>}
          {Object.entries(sections).map(([section, list]) => (
            <div key={section} className="help-guides">
              <span className="help-label">{section}</span>
              {list.map((p) => <button key={p.path} className="link-btn help-guide" onClick={() => openDoc(p.path)}>{p.title}</button>)}
            </div>
          ))}
        </section>

        <section className="help-section help-more">
          <button className="link-btn" onClick={() => onAction('start')}>Back to the start page</button>
          <button className="link-btn" onClick={() => updateOnboarding({ tips_reset: true }).catch(() => {})}>Show the first-time tips again</button>
          {ob && <button className="link-btn" onClick={() => updateOnboarding({ examples_hidden: !ob.examples_hidden }).then(() => onAction('refresh')).catch(() => {})}>
            {ob.examples_hidden ? 'Show the examples in my projects' : 'Hide the examples from my projects'}</button>}
        </section>
      </div>
    </div>
  )
}

/** A page of the documentation, over the console. */
export function DocViewer() {
  const [path, setPath] = useState<string | null>(null)
  const [history, setHistory] = useState<string[]>([])
  const [loaded, setLoaded] = useState<{ path: string; title: string; markdown: string } | null>(null)
  const [failed, setFailed] = useState<{ path: string; error: string } | null>(null)
  useEffect(() => {
    const on = (e: Event) => { const p = (e as CustomEvent<string>).detail; setHistory([]); setPath(p.split('#')[0]) }
    window.addEventListener('aurora:open-doc', on)
    return () => window.removeEventListener('aurora:open-doc', on)
  }, [])
  useEffect(() => {
    if (!path) return
    api.help.page(path).then(setLoaded).catch((e) => setFailed({ path, error: describeError(e) }))
  }, [path])
  const doc = loaded?.path === path ? loaded : null
  const error = failed?.path === path ? failed.error : null
  useEffect(() => {
    if (!path) return
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setPath(null) }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [path])
  if (!path) return null
  const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/') + 1) : ''
  const resolve = (href: string) => {
    const parts = (dir + href.split('#')[0]).split('/')
    const out: string[] = []
    for (const p of parts) { if (p === '..') out.pop(); else if (p && p !== '.') out.push(p) }
    return out.join('/')
  }
  const follow = (href: string) => {
    if (href.startsWith('#')) return
    if (!href.split('#')[0].endsWith('.md')) return
    setHistory([...history, path])
    setPath(resolve(href))
  }
  return (
    <div className="modal-backdrop" onClick={() => setPath(null)}>
      <div className="modal doc-viewer" role="dialog" aria-modal="true" aria-label={doc?.title ?? 'Documentation'} onClick={(e) => e.stopPropagation()}>
        <div className="doc-bar">
          {history.length > 0 && <button className="link-btn" onClick={() => { setPath(history[history.length - 1]); setHistory(history.slice(0, -1)) }}>← Back</button>}
          <span className="doc-path mono">{path}</span>
          <div className="wiz-spacer" />
          <button className="link-btn" aria-label="Close" onClick={() => setPath(null)}>Close</button>
        </div>
        <div className="doc-body">
          {error && <div className="panel-message error">{error}</div>}
          {!doc && !error && <div className="help-hint">Loading…</div>}
          {doc && <Markdown text={doc.markdown} onLink={follow}
            imageUrl={(src) => `/api/help/assets/${encodeURIComponent(src.split('/').pop() ?? '')}`} />}
        </div>
      </div>
    </div>
  )
}

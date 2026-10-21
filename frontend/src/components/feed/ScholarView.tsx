import { useEffect, useState } from 'react'
import { api, describeError } from '../../api/client'
import type { ScholarPage } from '../../types'
import { ago, IdChip } from './common'

const TABS: [string, string][] = [['all', 'All'], ['paper', 'Papers'], ['post', 'Posts'], ['blog', 'Blog'], ['about', 'About their work']]

/** Open a project on one of its tabs (the Literature page, say). */
export function openProjectTab(campaignId: string, tab: string) {
  window.dispatchEvent(new CustomEvent('aurora:open-project-tab', { detail: { campaignId, tab } }))
}

/** A followed person: what they published and posted, what others say about their work, and
 *  where their work is in your projects. */
export function ScholarView({ personId, onBack, onFollowing, onOpenProject }: {
  personId: string; onBack: () => void; onFollowing: () => void; onOpenProject: (campaignId: string) => void
}) {
  const [page, setPage] = useState<ScholarPage | null>(null)
  const [tab, setTab] = useState('all')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.feed.person(personId).then(setPage).catch((e) => setError(describeError(e))) }, [personId])
  if (!page) return <div className="team-page fd-page"><button className="link-btn" onClick={onBack}>← Library</button><div className="lit-sub">{error ?? 'Loading…'}</div></div>
  const p = page.person
  const list = page.timeline.filter((e) => tab === 'all' || e.k === tab || (tab === 'about' && e.k === 'discussion'))
  const projects = [...new Map(page.theirs.map((t) => [t.campaign_id, t.project])).entries()]
  return (
    <div className="team-page fd-page">
      <div><button className="link-btn" onClick={onBack}>← Library</button></div>
      <div className="bs-row fd-scholar-head">
        <span className="bs-avatar fd-avatar-lg">{p.initials}</span>
        <div style={{ flex: 1, minWidth: 220, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <h1 style={{ margin: 0 }}>{p.name}</h1>
          {p.aff && <div className="lit-sub">{p.aff}</div>}
          <div className="bs-row">{p.ids.map((i) => <IdChip key={i.kind + (i.handle || i.url)} state={i.state === 'ok' && !i.follow ? 'muted' : i.state}
            label={i.url && i.state !== 'muted' ? i.label : i.label} note={i.note} />)}</div>
        </div>
        <button className="btn-sm lit-btn" onClick={onFollowing}>Edit what to follow</button>
      </div>
      <div className="fd-cols">
        <div className="fd-main">
          <div className="team-tabs" role="tablist">
            {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>)}
          </div>
          <ol className="fd-timeline">
            {list.map((e) => (
              <li key={e.item_id}>
                <div className="fd-when"><span className={`fd-badge k-${e.k === 'about' ? 'discussion' : e.k}`}>{e.label}</span><span className="fd-mono lit-sub">{ago(e.when)}</span></div>
                <div className="fd-tl-main">
                  <div className="lit-sub">{e.source}</div>
                  <div className="fd-tl-title">{e.url ? <a href={e.url} target="_blank" rel="noreferrer">{e.title}</a> : e.title}</div>
                  {e.body && <div className="fd-summary">{e.body}</div>}
                  {e.points.length > 0 && <ul className="fd-points">{e.points.map((x, i) => <li key={i}>{x}</li>)}</ul>}
                  {e.in_project && <div className="fd-inproject">{e.in_project}</div>}
                </div>
              </li>
            ))}
            {!list.length && <li className="lit-sub">Nothing of this kind yet. New work shows up here as the feed finds it.</li>}
          </ol>
        </div>
        <aside className="fd-side">
          <section className="fd-box">
            <h3>Their work in your projects</h3>
            {page.theirs.map((t) => (
              <div key={t.campaign_id + t.paper_id} className="fd-small">
                <div className="fd-bright">{t.title}</div>
                <div className="lit-sub">{t.project} · {t.how}{t.cited_version ? ` · the cited version is v${t.cited_version}` : ''}</div>
              </div>
            ))}
            {!page.theirs.length && <div className="lit-sub">None of their papers is in your projects yet.</div>}
            {projects.map(([cid, name]) => (
              <button key={cid} className="btn-sm lit-btn" style={{ alignSelf: 'flex-start' }}
                onClick={() => { onOpenProject(cid); openProjectTab(cid, 'literature') }}>Open {name}’s Literature</button>
            ))}
          </section>
          {page.coauthors.length > 0 && (
            <section className="fd-box"><h3>Often writes with</h3><div className="fd-small">{page.coauthors.join(' · ')}</div></section>
          )}
        </aside>
      </div>
    </div>
  )
}

import { useState } from 'react'
import type { FeedItem } from '../../types'
import { ago, Dots, MenuButton } from './common'

export interface FeedActions {
  projects: { campaign_id: string; name: string }[]
  spaces: { space: string; name: string }[]
  onSave: (it: FeedItem, campaignId: string) => void
  onUnsave: (it: FeedItem) => void
  onBring: (it: FeedItem, space: string, name: string) => void
  onAsk: (it: FeedItem, campaignId: string) => void
  onHide: (it: FeedItem) => void
  onPerson: (personId: string) => void
  /** keep it (in the project it fits) and open it in Pippin's reader */
  onRead: (it: FeedItem, campaignId: string) => void
}

/** What Pippin's reader can read: a paper, and a page of text (a post, a blog, a thread, a README); not a video. */
export const readable = (it: FeedItem) => !!it.url && it.kind !== 'video'

/** One item of the feed: what it is, who it is by, why it matters for a project, and what to do with it. */
export function FeedCard({ it, project, actions }: { it: FeedItem; project: string; actions: FeedActions }) {
  const [points, setPoints] = useState(false)
  // under a project filter, the card speaks for that project
  const fit = (project !== 'all' && it.fits.find((f) => f.campaign_id === project)) || it.fits[0]
  const others = it.fits.filter((f) => f !== fit)
  const open = it.kind === 'paper' && it.pdf_url ? it.pdf_url : it.url
  const askTo = fit?.campaign_id || actions.projects[0]?.campaign_id
  return (
    <article className="fd-card">
      <div className="fd-meta">
        <span className={`fd-badge k-${it.kind}`}>{it.label}</span>
        <span>{it.source}</span><span aria-hidden="true">·</span>
        <span>{ago(it.published_at || it.fetched_at)}</span>
        {it.idv && <span className="fd-idv lit-ok">✓ {it.idv}</span>}
      </div>
      <h2 className="fd-title">{it.url ? <a href={it.url} target="_blank" rel="noreferrer">{it.title}</a> : it.title}</h2>
      {it.people.length > 0 && (
        <div className="fd-people">
          {it.people.map(([name, pid], i) => (
            <span key={i}>{i > 0 && ', '}{pid
              ? <button className="fd-followed" onClick={() => actions.onPerson(pid)} title="Someone you follow">{name}</button>
              : name}</span>
          ))}
        </div>
      )}
      {it.summary && <div className="fd-summary">{it.summary}</div>}
      {it.points.length > 0 && (
        <>
          <button className="link-btn fd-points-btn" aria-expanded={points} onClick={() => setPoints(!points)}>
            {points ? 'Hide the points' : `Show the ${it.points.length} point${it.points.length > 1 ? 's' : ''}`}</button>
          {points && (
            <>
              <ul className="fd-points">{it.points.map((p, i) => <li key={i}>{p}</li>)}</ul>
              {it.places.length > 0 && <div className="fd-places">{it.places.map(([label, url]) => (
                <a key={label + url} href={url} target="_blank" rel="noreferrer">{label}</a>))}</div>}
            </>
          )}
        </>
      )}
      {fit && fit.why && (
        <div className="fd-why">
          <div className="bs-eyebrow">Why for {fit.project}</div>
          <div>{fit.why}</div>
          {others.length > 0 && <div className="lit-sub">Also fits {others.map((o) => o.project).join(', ')}.</div>}
        </div>
      )}
      <div className="fd-actions">
        <div className="fd-buttons">
          {readable(it) && <button className="btn-sm btn-approve" onClick={() => actions.onRead(it, fit?.campaign_id ?? '')}
            title="Read it in Pippin: Pippin's notes, gaps to write in, pieces into your notebook">Read with Pippin</button>}
          <MenuButton label={it.saved ? (it.saved.campaign_id ? `Saved to ${it.saved.project}` : 'Saved for later') : 'Save to project'}
            className={`btn-sm lit-btn${it.saved ? ' on' : ''}`}>
            {(close) => <>
              {actions.projects.map((p) => (
                <button key={p.campaign_id} role="menuitem" onClick={() => { close(); actions.onSave(it, p.campaign_id) }}>
                  {it.saved?.campaign_id === p.campaign_id ? '✓ ' : ''}{p.name}</button>
              ))}
              <button role="menuitem" onClick={() => { close(); actions.onSave(it, '') }}>{it.saved && !it.saved.campaign_id ? '✓ ' : ''}Save for later</button>
              {it.saved && <button role="menuitem" onClick={() => { close(); actions.onUnsave(it) }}>Remove</button>}
            </>}
          </MenuButton>
          <MenuButton label="Bring to brainstorm">
            {(close) => actions.spaces.map((s) => (
              <button key={s.space} role="menuitem" onClick={() => { close(); actions.onBring(it, s.space, s.name) }}>
                {s.name}{s.space === 'global' ? ' (not tied to a project)' : ''}</button>
            ))}
          </MenuButton>
          {actions.projects.length > 1 ? (
            <MenuButton label="Ask the front desk">
              {(close) => [...actions.projects].sort((a, b) => (a.campaign_id === askTo ? -1 : b.campaign_id === askTo ? 1 : 0)).map((p) => (
                <button key={p.campaign_id} role="menuitem" onClick={() => { close(); actions.onAsk(it, p.campaign_id) }}>{p.name}’s front desk</button>
              ))}
            </MenuButton>
          ) : askTo ? <button className="btn-sm lit-btn" onClick={() => actions.onAsk(it, askTo)}>Ask the front desk</button> : null}
          {open && <a className="btn-sm lit-btn" href={open} target="_blank" rel="noreferrer">{it.open}</a>}
          <button className="btn-sm fd-ghost" onClick={() => actions.onHide(it)}>Not interested</button>
        </div>
        <div className="fd-extra">{it.extra && <span>{it.extra}</span>}<Dots rel={fit?.rel ?? it.rel} /></div>
      </div>
    </article>
  )
}

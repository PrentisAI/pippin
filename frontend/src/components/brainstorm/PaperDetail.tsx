import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { BsSpace, LitItem, LitPaper } from '../../types'
import { short } from './common'

const REL_LABEL: Record<string, string> = {
  supports: 'Supports', challenges: 'Challenges', builds_on: 'Builds on', asks_about: 'Asks about', related: 'Related to',
}

/** One paper, opened from the Papers tab, a key in the conversation or an idea's evidence: what it is,
 *  where to read it, what it means for the ideas here, and what to do with it. */
export function PaperDetail({ paper, space, onBack, onIdea, onAsk, onRead, onStar }: {
  paper: LitPaper & Partial<LitItem>
  space: BsSpace
  /** The project ('' in one's own space). */
  campaignId: string
  onBack: () => void
  onIdea: (cardId: string) => void
  /** Put a question about it into the message box. */
  onAsk: (text: string) => void
  /** Read it in the reader, for an idea it bears on. */
  onRead: (key: string, cardId?: string) => void
  onStar?: () => void
}) {
  const [copied, setCopied] = useState(false)
  const [allAuthors, setAllAuthors] = useState(false)
  const p = paper
  const readable = !!(p.pdf_file || p.pdf_url || p.arxiv_id)
  // the PDF is fetched while the paper is looked at, so "Read the PDF" opens it at once
  useEffect(() => { if (readable && p.key) api.reader.prefetch(p.key, space.space).catch(() => {}) }, [readable, p.key, space.space])
  const page = p.doi ? `https://doi.org/${p.doi}` : p.arxiv_id ? `https://arxiv.org/abs/${p.arxiv_id}` : p.url
  const where = p.doi ? 'DOI' : p.arxiv_id ? 'arXiv' : 'the paper’s page'
  // the ideas of this space it is evidence for or against
  const own = space.cards.filter((c) => c.paper_id === p.paper_id).map((c) => c.card_id)
  const links = space.links.filter((l) => own.includes(l.from_card))
    .map((l) => ({ rel: l.rel, idea: space.cards.find((c) => c.card_id === l.to_card) }))
    .filter((x) => x.idea)
  const authors = p.authors ?? []
  const copy = () => {
    navigator.clipboard?.writeText(p.key).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => {})
  }
  return (
    <div className="pd" aria-label={`Paper ${p.key}`}>
      <button className="link-btn pd-back" onClick={onBack}>← All papers</button>
      <div className="pd-meta">
        <span className="pd-ok" title={`Found in ${p.found_in?.join(', ') || 'the indexes'}`}>✓ checked</span>
        <span>{[p.venue, p.year].filter(Boolean).join(' · ')}</span>
        {p.cites_per_year != null && <span>{p.cites_per_year} citations a year</span>}
      </div>
      <h3 className="pd-title">{p.title}</h3>
      <div className="pd-authors">
        {(allAuthors ? authors : authors.slice(0, 6)).join(', ')}
        {authors.length > 6 && !allAuthors && <button className="link-btn" onClick={() => setAllAuthors(true)}>+{authors.length - 6} more</button>}
      </div>
      <div className="pd-actions">
        {readable && <button className="btn-sm btn-approve" onClick={() => onRead(p.key, links[0]?.idea?.card_id)}
          title="Read it here: highlight, write notes and ask the agents about a passage">Read the PDF</button>}
        {page && <a className="btn-sm lit-btn" href={page} target="_blank" rel="noreferrer">Open on {where}</a>}
        <button className="btn-sm lit-btn" onClick={copy} title="Its key, to cite it in a message">{copied ? 'Copied' : `Copy [${p.key}]`}</button>
        {onStar && <button className="btn-sm lit-btn" onClick={onStar}>{p.starred ? '★ Starred' : '☆ Star'}</button>}
      </div>

      {links.length > 0 && (
        <section className="pd-section">
          <b>In this brainstorm</b>
          {links.map((x, i) => (
            <div key={i} className="pd-link">
              <span className={`pd-rel ${x.rel}`}>{REL_LABEL[x.rel] ?? x.rel}</span>
              <button className="bs-ichip" onClick={() => onIdea(x.idea!.card_id)}>{short(x.idea!.text, 70)}</button>
            </div>
          ))}
        </section>
      )}

      <section className="pd-section">
        <b>Abstract</b>
        {p.abstract ? <p className="pd-abstract">{p.abstract}</p>
          : <span className="lit-sub">No abstract in the indexes. {page ? `Open it on ${where} to read it.` : ''}</span>}
      </section>

      {(p.notes?.length ?? 0) > 0 && (
        <section className="pd-section">
          <b>Highlights and notes</b>
          {p.notes!.map((n, i) => (
            <div key={i} className="pd-note">
              <span className="pd-quote">{n.text}</span>
              {n.comment && <span className="lit-sub">{n.comment}</span>}
              <span className="lit-sub">{[n.page && `p. ${n.page}`, n.by].filter(Boolean).join(' · ')}</span>
            </div>
          ))}
        </section>
      )}

      <section className="pd-section">
        <b>Talk about it</b>
        <div className="pd-asks">
          <button className="bs-ichip" onClick={() => onAsk(`@Librarian What does [${p.key}] tell us about my idea?`)}>What does it mean for my idea?</button>
          <button className="bs-ichip" onClick={() => onAsk(`@Skeptic Does [${p.key}] undermine anything here?`)}>Does it undermine anything?</button>
          <button className="bs-ichip" onClick={() => onAsk(`@Methods Could we build on the method of [${p.key}]?`)}>Could we build on its method?</button>
        </div>
      </section>
    </div>
  )
}

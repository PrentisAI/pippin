import { Fragment } from 'react'
import type { BsCard, BsLink } from '../../types'
import { readPage } from '../reader/open'

const svg = (d: React.ReactNode, size = 12) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true" style={{ verticalAlign: '-1px', flexShrink: 0 }}>{d}</svg>
)
export const LOCK = svg(<><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 018 0v4" /></>)
export const GROUP = svg(<><circle cx="9" cy="8" r="3.2" /><path d="M3.5 19a5.5 5.5 0 0111 0" /><path d="M16 5.2a3 3 0 010 5.6M17.5 14a5 5 0 013.5 5" /></>)
export const BULB = svg(<path d="M9 18h6M10 21h4M12 3a6 6 0 00-3.6 10.8c.7.5 1.1 1.3 1.1 2.2h5c0-.9.4-1.7 1.1-2.2A6 6 0 0012 3z" />)
export const LENS = svg(<><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></>)
export const CHAT = svg(<path d="M4 5h16v11H8l-4 4z" />, 14)
export const PDF = svg(<><path d="M14 3H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V9z" /><path d="M14 3v6h6" /></>)

export const REL_COLOR: Record<string, string> = {
  supports: 'var(--green)', challenges: 'var(--amber)', 'builds on': 'var(--accent)', 'asks about': 'var(--amber)', related: 'var(--fg-dim)',
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).map((x) => x[0]).join('').slice(0, 2).toUpperCase() || '?'
}
export function short(text: string, n: number): string {
  return text.length > n ? text.slice(0, n - 1).trimEnd() + '…' : text
}
export function ago(ts: number): string {
  const m = Math.floor((Date.now() / 1000 - ts) / 60)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  if (m < 60 * 24) return `${Math.floor(m / 60)} h ago`
  return new Date(ts * 1000).toLocaleDateString()
}

export function Avatar({ name, agent, online }: { name: string; agent?: { initials: string; role?: string }; online?: boolean }) {
  return <span className={`bs-avatar${agent ? ' agent' : ''}${online ? ' online' : ''}`} title={agent?.role ? `${name}, ${agent.role}` : name}>
    {agent ? agent.initials : /^p\d\d$/.test(name) ? `S${Number(name.slice(1))}` : initials(name)}</span>
}

export function VisPill({ vis }: { vis: 'private' | 'project' }) {
  return <span className={`bs-vis${vis === 'project' ? ' shared' : ''}`}>{vis === 'project' ? <>{GROUP} Shared with the project</> : <>{LOCK} Only you</>}</span>
}

export interface Analysis {
  forIt: BsCard[]
  against: BsCard[]
  questions: BsCard[]
  open: BsCard[]
  next: BsCard[]
  parents: BsCard[]
  status: ['wait' | 'ready' | 'early', string]
  stand: string
  people: string[]
  rank: number
}

/** An idea as an argument: what supports it, what could be wrong, what comes next. */
export function analyse(idea: BsCard, cards: Map<string, BsCard>, links: BsLink[]): Analysis {
  const into = links.filter((l) => l.to_card === idea.card_id).map((l) => ({ rel: l.rel, it: cards.get(l.from_card) }))
    .filter((x): x is { rel: BsLink['rel']; it: BsCard } => !!x.it)
  const forIt = into.filter((x) => (x.rel === 'supports' || (x.rel === 'related' && x.it.type === 'material')) && x.it.type !== 'question').map((x) => x.it)
  const against = into.filter((x) => x.rel === 'challenges' && x.it.type !== 'question').map((x) => x.it)
  const questions = into.filter((x) => x.it.type === 'question').map((x) => x.it)
  const next = into.filter((x) => x.rel === 'builds on' && x.it.type === 'idea').map((x) => x.it)
  const parents = links.filter((l) => l.from_card === idea.card_id && l.rel === 'builds on').map((l) => cards.get(l.to_card)).filter((x): x is BsCard => !!x)
  const open = questions.filter((q) => !q.answer)
  const status: Analysis['status'] = open.length ? ['wait', `Waiting on ${open.length} question${open.length > 1 ? 's' : ''}`]
    : forIt.length ? ['ready', 'Ready to test'] : ['early', 'Early: no evidence yet']
  const stand = idea.stand || (open.length ? 'Open questions need an answer before this can be tested.'
    : forIt.length ? 'The open questions are answered and there is evidence for it. It can become a proposal.'
      : 'No evidence for or against it yet.')
  const people = [...new Set([idea.by_name, ...into.map((x) => x.it.by_name)])]
  return { forIt, against, questions, open, next, parents, status, stand, people, rank: open.length ? 0 : forIt.length ? 1 : 2 }
}

const KEYS = /(\[[A-Z][A-Za-z\-']{1,16}\d{2}[a-z]?(?:\s*[,;]\s*[A-Z][A-Za-z\-']{1,16}\d{2}[a-z]?)*\]|\bc_[0-9a-f]{12}\b)/g

/** Text with **bold**, [Key] papers and c_… idea references made into chips. */
/** A web address in a message, up to the punctuation that ends a sentence. */
const URL_RE = /(https?:\/\/[^\s<>()\]]*[^\s<>()\].,;:!?'"])/g

/** A web address in a message: opens in Pippin's reader (kept in the Library), ↗ on the web. */
function WebLink({ url, space }: { url: string; space: string }) {
  let label = url
  try { const u = new URL(url); label = u.hostname.replace(/^www\./, '') + (u.pathname.length > 1 ? u.pathname.slice(0, 28) + (u.pathname.length > 28 ? '…' : '') : '') } catch { /* as written */ }
  return (
    <span className="bs-weblink">
      <button type="button" className="bs-kchip" title={`Read it in Pippin: ${url}`} onClick={() => readPage(url, space, 'Brainstorm')}>{label}</button>
      <a href={url} target="_blank" rel="noreferrer" title="Open it on the web" aria-label="Open it on the web">↗</a>
    </span>
  )
}

export function Rich({ text, cards, onIdea, onPaper, space = 'global' }: { text: string; cards: Map<string, BsCard>; onIdea: (id: string) => void; onPaper?: (key: string) => void; space?: string }) {
  const bold = text.split(/(\*\*[^*\n]+\*\*)/g)
  if (bold.length > 1) {
    return <>{bold.map((b, i) => /^\*\*[^*]+\*\*$/.test(b) ? <b key={i}><Rich text={b.slice(2, -2)} cards={cards} onIdea={onIdea} onPaper={onPaper} space={space} /></b>
      : <Rich key={i} text={b} cards={cards} onIdea={onIdea} onPaper={onPaper} space={space} />)}</>
  }
  return (
    <>{text.split(KEYS).map((p, i) => {
      if (/^\[.*\]$/.test(p)) {
        return <Fragment key={i}>{p.slice(1, -1).split(/\s*[,;]\s*/).map((k) => (
          <button key={k} type="button" className="bs-kchip" title="Show the paper" onClick={() => onPaper?.(k)}>{k}</button>))}</Fragment>
      }
      if (/^c_[0-9a-f]{12}$/.test(p)) {
        const c = cards.get(p)
        return c ? <button key={i} type="button" className="bs-ichip" onClick={() => onIdea(p)}>{short(c.text, 40)}</button> : <Fragment key={i}>an idea</Fragment>
      }
      return <Fragment key={i}>{p.split(URL_RE).map((q, j) => j % 2 ? <WebLink key={j} url={q} space={space} /> : q)}</Fragment>
    })}</>
  )
}

export const ACTIVITY: Record<'off' | 'moderate' | 'high', [string, string]> = {
  off: ['Only when asked', 'Agents speak when you @mention them or press Ask. Nothing runs in the background.'],
  moderate: ['Moderately', 'A few suggestions a day, when new material or an unanswered question calls for one; the most useful agent answers a message that calls for it. A few cents a day.'],
  high: ['Very', 'Agents keep reading, extending and debating in the background, and up to two answer most messages. Can cost a few dollars a day.'],
}
export const AGENT_ASK: [string, string][] = [['Skeptic', 'What could be wrong?'], ['Librarian', 'Find evidence'], ['Methods', 'How to test it?'], ['Mentor', 'Help me think it through']]

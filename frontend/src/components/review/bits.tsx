/** Pieces the map and the deep read share: what kind of claim a paragraph makes, paragraphs whose paper keys
 *  can be clicked, and sections that lead with their conclusion and keep the evidence one click away. */
import { Fragment, useState, type ReactNode } from 'react'
import type { ClaimLabel, Para } from '../../types'

export const LABEL: Record<ClaimLabel, { text: string; title: string }> = {
  paper: { text: 'Paper says', title: 'The paper itself states or shows it' },
  lit: { text: 'Literature', title: 'Established in other literature' },
  inf: { text: 'Inference', title: 'A reasonable inference from evidence, not stated anywhere' },
  spec: { text: 'Guess', title: 'A guess without direct evidence' },
}

export function Label({ l }: { l: ClaimLabel | string }) {
  const x = LABEL[l as ClaimLabel] ?? LABEL.spec
  return <span className={`rv-label rv-l-${l in LABEL ? l : 'spec'}`} title={x.title}>{x.text}</span>
}

/** Text with paper keys ([Gu24]) as chips that open the paper. */
export function Keyed({ text, onKey }: { text: string; onKey?: (key: string) => void }) {
  const parts = text.split(/(\[[A-Za-z][\w-]*\d{2}[a-z]?\])/g)
  return <>{parts.map((p, i) => {
    const m = /^\[([A-Za-z][\w-]*\d{2}[a-z]?)\]$/.exec(p)
    if (!m) return <Fragment key={i}>{p}</Fragment>
    return onKey ? <button key={i} className="rv-key" onClick={() => onKey(m[1])}>{m[1]}</button>
      : <span key={i} className="rv-key static">{m[1]}</span>
  })}</>
}

export function Paras({ items, onKey, onQuote }: {
  items?: Para[]
  onKey?: (key: string) => void
  /** Show a quoted passage in the paper. */
  onQuote?: (quote: string, page: number) => void
}) {
  if (!items?.length) return null
  return <div className="rv-paras">{items.map((p, i) => (
    <p key={i} className="rv-p"><Keyed text={p.text} onKey={onKey} /> <Label l={p.label} />
      {p.quote && onQuote && <button className="rv-quote" title={`“${p.quote}”`} onClick={() => onQuote(p.quote!, p.page ?? 0)}>
        p. {(p.page ?? 0) + 1}</button>}
    </p>
  ))}</div>
}

/** A section that says its conclusion first; the evidence opens below it. */
export function Section({ n, title, headline, children, details, open: initial = false, id, tools }: {
  n: number | string
  title: string
  headline?: ReactNode
  children?: ReactNode
  details?: ReactNode
  open?: boolean
  id?: string
  tools?: ReactNode
}) {
  const [open, setOpen] = useState(initial)
  return (
    <section className="rv-sec" id={id}>
      <div className="rv-sec-h">
        <span className="rv-num">{n}</span>
        <h2>{title}</h2>
        {tools && <span className="rv-tools">{tools}</span>}
      </div>
      {headline && <div className="rv-headline">{headline}</div>}
      {children}
      {details && (
        <>
          <button className="rv-more" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Hide the evidence' : 'Show the evidence'}</button>
          {open && <div className="rv-details">{details}</div>}
        </>
      )}
    </section>
  )
}

export function Bar({ value, label, kind }: { value?: number; label: string; kind?: 'computed' | 'judged' }) {
  const v = Math.max(0, Math.min(1, value ?? 0))
  return (
    <div className="rv-bar-row" title={kind === 'judged' ? 'Judged by a model, with its reason' : 'Computed from the placements'}>
      <span>{label}{kind === 'judged' && <span className="rv-judged"> · judged</span>}</span>
      <span className="rv-bar"><span style={{ width: `${v * 100}%` }} /></span>
      <span className="rv-bar-v">{value == null ? '–' : Math.round(v * 100) + '%'}</span>
    </div>
  )
}

/** What is written in a gap opened in the page: your own words, at whatever length, saved as you write; or a note
 *  of Pippin's with the figure it made to try the passage out. */
import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { ReaderNote } from '../../types'
import { Figure } from '../Figure'
import { KIND, avatar } from './NotesPanel'

export interface GapActions {
  keep: (n: ReaderNote) => void
  dismiss: (n: ReaderNote) => void
  remove: (n: ReaderNote) => void
  cancel: (n: ReaderNote) => void
  /** Your words, saved (no reload of the notes). */
  write: (n: ReaderNote, comment: string) => Promise<void>
}

export function GapNote({ n, focus, onFocus, actions }: { n: ReaderNote; focus: boolean; onFocus: () => void; actions: GapActions }) {
  if (n.author) return <AgentGap n={n} actions={actions} />
  if (!n.mine) {
    return (
      <div className="rd-gn other">
        <div className="rd-gn-who">{avatar('', n.owner_name)}<span>{n.owner_name}</span></div>
        <p className="rd-gn-read">{n.comment}</p>
      </div>
    )
  }
  return <OwnGap n={n} focus={focus} onFocus={onFocus} actions={actions} />
}

function OwnGap({ n, focus, onFocus, actions }: { n: ReaderNote; focus: boolean; onFocus: () => void; actions: GapActions }) {
  const [text, setText] = useState(n.comment)
  const [state, setState] = useState<'saved' | 'saving' | 'dirty'>('saved')
  const box = useRef<HTMLTextAreaElement>(null)
  const timer = useRef<number | null>(null)
  const latest = useRef(text)
  const draft = useRef(n.state === 'draft')
  const [isDraft, setIsDraft] = useState(n.state === 'draft')
  // a gap just opened: write in it straight away
  useEffect(() => { if (n.state === 'draft') { const t = window.setTimeout(() => box.current?.focus(), 60); return () => window.clearTimeout(t) } },
    []) // eslint-disable-line react-hooks/exhaustive-deps
  // the box grows with what is written in it
  useEffect(() => {
    const t = box.current
    if (t) { t.style.height = 'auto'; t.style.height = `${Math.max(72, t.scrollHeight)}px` }
  }, [text])
  const save = async () => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null }
    const v = latest.current.trim()
    if (!v) return
    setState('saving')
    try { await actions.write(n, v); draft.current = false; setIsDraft(false) } finally { setState('saved') }
  }
  // what was typed last is saved when the gap goes away
  useEffect(() => () => { if (timer.current) { window.clearTimeout(timer.current); const v = latest.current.trim(); if (v) actions.write(n, v) } },
    []) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className={`rd-gn mine${focus ? ' focus' : ''}`} onClick={onFocus}>
      <textarea ref={box} className="rd-gn-text" value={text} autoFocus={n.state === 'draft'} aria-label="Your note, in the page"
        placeholder="Write here, as much as you like: what this means, what it reminds you of, what to try."
        onChange={(e) => {
          setText(e.target.value); latest.current = e.target.value; setState('dirty')
          if (timer.current) window.clearTimeout(timer.current)
          timer.current = window.setTimeout(save, 900)
        }}
        onBlur={() => { if (!latest.current.trim() && draft.current) actions.cancel(n); else save() }}
        onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); (e.target as HTMLTextAreaElement).blur() } }} />
      <div className="rd-gn-row">
        <span className="rd-sub">{state === 'saving' ? 'Saving…' : state === 'dirty' ? '' : text.trim() ? 'Saved' : ''}</span>
        {!isDraft && <button className="rd-gn-btn" onClick={(e) => { e.stopPropagation(); actions.remove(n) }}
          title="Delete the note and close the gap">Close the gap</button>}
      </div>
    </div>
  )
}

function AgentGap({ n, actions }: { n: ReaderNote; actions: GapActions }) {
  const sug = n.state === 'suggested'
  return (
    <div className={`rd-gn agent${sug ? ' sug' : ''}`}>
      <div className="rd-gn-who">{avatar(n.author, n.author)}<span>{n.author}</span>
        {n.kind && <span className="rd-mn-rel">{KIND[n.kind] ?? ''}</span>}</div>
      {n.comment && <p className="rd-gn-read">{n.comment}</p>}
      {n.figure && <Figure src={api.reader.figureUrl(n.ann_id)} title={`A figure for the passage on page ${n.page_index + 1}`} />}
      <div className="rd-gn-row">
        {sug && <button className="rd-gn-btn keep" onClick={() => actions.keep(n)}>Keep</button>}
        <button className="rd-gn-btn" onClick={() => (sug ? actions.dismiss(n) : actions.remove(n))}>{sug ? 'Dismiss' : 'Close the gap'}</button>
      </div>
    </div>
  )
}

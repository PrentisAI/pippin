/** Notes in the margin of the page, beside the words they are about: yours, and what the agents have to say.
 *  A note you start is written here; an agent's suggestion is kept or dismissed here. The whole conversation
 *  on a note (replies, sharing, Zotero) is one click away in the notes panel. Highlights without words stay
 *  on the page only. */
import { useState } from 'react'
import type { ReaderNote } from '../../types'
import { KIND, avatar } from './NotesPanel'

const REL: Record<string, string> = { supports: 'Supports', challenges: 'Challenges', 'asks about': 'Asks about', 'builds on': 'Builds on' }
// room a note takes before the next one may start (px); the one in focus opens over those under it
const ROOM = 96
const GAP = 8

export interface MarginActions {
  keep: (n: ReaderNote) => void
  dismiss: (n: ReaderNote) => void
  save: (n: ReaderNote, comment: string) => void
  cancel: (n: ReaderNote) => void
}

/** Whether a note has something to say in the margin. */
export function inMargin(n: ReaderNote, placed = n.rects.length > 0): boolean {
  return placed && !n.inline && (!!n.comment.trim() || n.state === 'draft' || !!n.author)
}

export function MarginNotes({ notes, top, focusId, dimmed, onFocus, onMore, actions, placed }: {
  notes: ReaderNote[]
  /** Notes found on the page, when that is not known from their rectangles (a web page's). */
  placed?: Set<string>
  /** Where a note's words start on the page (px from its top). */
  top: (n: ReaderNote) => number
  focusId: string | null
  dimmed: Set<string>
  onFocus: (annId: string) => void
  /** The note's whole conversation, in the notes panel. */
  onMore: (annId: string) => void
  actions: MarginActions
}) {
  const shown = notes.filter((n) => inMargin(n, placed ? placed.has(n.ann_id) : n.rects.length > 0)).map((n) => ({ n, at: top(n) })).sort((a, b) => a.at - b.at)
    .reduce<{ n: ReaderNote; y: number }[]>((out, { n, at }) => {
      const prev = out[out.length - 1]
      return [...out, { n, y: Math.max(at, prev ? prev.y + ROOM + GAP : 0) }]
    }, [])
  return (
    <>
      {shown.map(({ n, y }) => (
        <MarginNote key={n.ann_id} n={n} y={y} focus={n.ann_id === focusId} dim={dimmed.has(n.ann_id)}
          onFocus={() => onFocus(n.ann_id)} onMore={() => onMore(n.ann_id)} actions={actions} />
      ))}
    </>
  )
}

function MarginNote({ n, y, focus, dim, onFocus, onMore, actions }: {
  n: ReaderNote; y: number; focus: boolean; dim: boolean; onFocus: () => void; onMore: () => void; actions: MarginActions
}) {
  const [text, setText] = useState(n.comment)
  const who = n.author || (n.mine ? 'You' : n.owner_name || n.author_name)
  const sug = n.state === 'suggested'
  const draft = n.state === 'draft'
  return (
    <aside className={`rd-mn${focus || draft ? ' focus' : ''}${dim ? ' dim' : ''}${sug ? ' sug' : ''}`} style={{ top: y, ['--mn' as string]: n.color }}
      onMouseUp={(e) => e.stopPropagation()} onClick={() => { if (!focus) onFocus() }}>
      <div className="rd-mn-who">{avatar(n.author, n.author_name || n.owner_name, true)}<span>{who}</span>
        {n.kind ? <span className="rd-mn-rel">{KIND[n.kind] ?? ''}</span> : n.rel && <span className="rd-mn-rel">{REL[n.rel] ?? n.rel}</span>}
        {n.replies.length > 0 && <span className="rd-mn-n">{n.replies.length} repl{n.replies.length === 1 ? 'y' : 'ies'}</span>}
      </div>
      {draft ? (
        <form className="rd-mn-edit" onSubmit={(e) => { e.preventDefault(); actions.save(n, text.trim()) }}>
          <textarea rows={3} value={text} autoFocus placeholder="Your note on these words" aria-label="Your note"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); actions.save(n, text.trim()) } if (e.key === 'Escape') { e.stopPropagation(); actions.cancel(n) } }} />
          <div className="rd-mn-row">
            <button className="rd-mn-btn keep">Save</button>
            <button type="button" className="rd-mn-btn" onClick={() => actions.cancel(n)}>Cancel</button>
            <span className="rd-mn-key">⌘⏎</span>
          </div>
        </form>
      ) : (
        <p className="rd-mn-text">{n.comment || <span className="rd-mn-quote">“{n.text}”</span>}</p>
      )}
      {!draft && focus && (
        <div className="rd-mn-row">
          {sug && <button className="rd-mn-btn keep" onClick={(e) => { e.stopPropagation(); actions.keep(n) }}>Keep</button>}
          {sug && <button className="rd-mn-btn" onClick={(e) => { e.stopPropagation(); actions.dismiss(n) }}>Dismiss</button>}
          <button className="rd-mn-btn quiet" onClick={(e) => { e.stopPropagation(); onMore() }}>{n.replies.length ? 'The conversation' : 'Reply, share, more'}</button>
        </div>
      )}
    </aside>
  )
}

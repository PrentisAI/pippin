/** The notes on a paper, beside it: yours and the agents', each a conversation, in the order of the paper. */
import { useEffect, useRef, useState } from 'react'
import type { ReaderAgent, ReaderNote, ReaderView } from '../../types'

export const AGENTS: { name: ReaderAgent; ini: string; cls: string; role: string }[] = [
  { name: 'Librarian', ini: 'Li', cls: 'b', role: 'brings evidence from the Library' },
  { name: 'Skeptic', ini: 'Sk', cls: 'o', role: 'looks for what would break the idea' },
  { name: 'Methods', ini: 'Me', cls: 'p', role: 'turns it into a test' },
]
const AGENT = Object.fromEntries(AGENTS.map((a) => [a.name, a]))

export function avatar(who: string, name: string, small = false) {
  const a = AGENT[who]
  return <span className={`rd-av ${a ? `av-${a.cls}` : 'av-y'}${small ? ' sm' : ''}`} aria-hidden="true">
    {a ? a.ini : (name || '?').slice(0, 1).toUpperCase()}</span>
}

const REL: Record<string, string> = { supports: 'Supports', challenges: 'Challenges', 'asks about': 'Asks about', 'builds on': 'Builds on' }
const STATUS: Record<ReaderNote['status'], string> = {
  synced: '✓ In Zotero', saving: 'Saving to Zotero…', suggested: 'Suggestion · not in Zotero', local: 'In Pippin only',
  error: 'Not in Zotero', draft: 'Not saved yet', shared: 'Shared',
}

export type Filter = 'all' | 'you' | 'shared' | ReaderAgent

export function inFilter(n: ReaderNote, f: Filter): boolean {
  if (f === 'all') return true
  if (f === 'you') return n.mine && !n.author
  if (f === 'shared') return !n.mine
  return n.author === f
}

function short(t: string, n: number) { return t.length > n ? t.slice(0, n - 1).trimEnd() + '…' : t }

export function NotesPanel({ view, notes, filter, setFilter, focusId, unplaced, onFocus, onGoto, actions }: {
  view: ReaderView
  notes: ReaderNote[]
  filter: Filter
  setFilter: (f: Filter) => void
  focusId: string | null
  /** Notes whose words could not be found on the page. */
  unplaced: Set<string>
  onFocus: (annId: string | null) => void
  onGoto: (annId: string) => void
  actions: {
    keep: (n: ReaderNote) => void
    dismiss: (n: ReaderNote) => void
    remove: (n: ReaderNote) => void
    save: (n: ReaderNote, comment: string) => void
    cancel: (n: ReaderNote) => void
    reply: (n: ReaderNote, text: string) => Promise<void>
    share: (n: ReaderNote, shared: boolean) => void
    setMark: (on: boolean) => void
  }
}) {
  const counts = (f: Filter) => notes.filter((n) => inFilter(n, f)).length
  const others = notes.some((n) => !n.mine)
  const filters: { f: Filter; label: string; dot?: string }[] = [
    { f: 'all', label: 'All' }, { f: 'you', label: 'You', dot: 'y' },
    ...AGENTS.map((a) => ({ f: a.name as Filter, label: a.name, dot: a.cls })),
    ...(others ? [{ f: 'shared' as Filter, label: 'Shared with you' }] : []),
  ]
  const shown = notes.filter((n) => inFilter(n, filter))
  const hidden = view.notes.filter((n) => n.status === 'suggested').length - notes.filter((n) => n.status === 'suggested').length
  return (
    <aside className="rd-panel" aria-label="Notes">
      <div className="rd-panel-head">
        <div className="rd-row-between">
          <b className="rd-h">Notes on this paper</b>
          <span className="rd-sub">{notes.length} note{notes.length === 1 ? '' : 's'}</span>
        </div>
        {view.idea
          ? <div className="rd-idea"><svg className="rd-idea-ic" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z" /></svg>
              {view.idea.kind === 'conversation'
                ? <span>Reading for <b>what you said in this brainstorm</b>. No idea is written down yet; once one is, they read for it.</span>
                : <span>Reading for your idea: <b>{view.idea.text}</b></span>}</div>
          : <div className="rd-idea quiet">Open the paper from a brainstorm and the agents read it with you, for your idea or for
              what you are talking about. You can always ask them about a passage.</div>}
        <div className="rd-chips" role="group" aria-label="Show notes by">
          {filters.map((x) => (
            <button key={x.f} className={`rd-chip${filter === x.f ? ' on' : ''}`} onClick={() => { setFilter(x.f); onFocus(null) }}>
              {x.dot && <span className={`rd-dot c-${x.dot}`} />}{x.label} <span className="rd-n">{counts(x.f)}</span>
            </button>
          ))}
        </div>
        <button className="rd-switch" role="switch" aria-checked={view.agents_mark} onClick={() => actions.setMark(!view.agents_mark)}>
          <span className={`rd-sw${view.agents_mark ? ' on' : ''}`} />
          <span className="rd-switch-text">
            <span>Agents mark passages on their own</span>
            <span className="rd-sub">{view.agents_mark
              ? 'They mark what bears on your idea as they read. Their marks are suggestions until you keep them.'
              : `Off: they answer only when you ask about a passage.${hidden > 0 ? ` ${hidden} suggestion${hidden === 1 ? ' is' : 's are'} hidden.` : ''}`}</span>
          </span>
        </button>
      </div>

      {shown.map((n) => (
        <NoteCard key={n.ann_id} n={n} focused={focusId === n.ann_id} unplaced={unplaced.has(n.ann_id)} canShare={view.can_share}
          onFocus={() => onFocus(n.ann_id)} onGoto={() => onGoto(n.ann_id)} actions={actions} />
      ))}
      {!shown.length && (
        <div className="rd-empty">
          {notes.length ? `No notes from ${filters.find((x) => x.f === filter)?.label ?? ''} yet.`
            : 'Select words in the paper to highlight them, write a note, or ask an agent about them.'}
        </div>
      )}
    </aside>
  )
}

function NoteCard({ n, focused, unplaced, canShare, onFocus, onGoto, actions }: {
  n: ReaderNote
  focused: boolean
  unplaced: boolean
  canShare: boolean
  onFocus: () => void
  onGoto: () => void
  actions: Parameters<typeof NotesPanel>[0]['actions']
}) {
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const agent = AGENT[n.author]
  const editing = n.state === 'draft'
  useEffect(() => { if (focused) ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [focused])
  useEffect(() => { if (editing) input.current?.focus() }, [editing])
  const sub = !n.mine ? `${n.owner_name}’s note · shared with the project`
    : agent ? `${n.state === 'suggested' ? 'suggests' : 'kept by you'} · ${agent.role}`
    : `p. ${n.page_label || n.page_index + 1}${n.source === 'zotero' ? ' · made in Zotero' : ''}`
  const status = n.status === 'synced' && n.author ? `✓ In Zotero, as ${n.author}’s note` : STATUS[n.status]
  const send = async () => {
    const t = draft.trim()
    if (!t || sending) return
    setSending(true)
    try { await actions.reply(n, t); setDraft('') } finally { setSending(false) }
  }
  return (
    <div ref={ref} className={`rd-card${focused ? ' focus' : ''}${n.state === 'suggested' ? ' sug' : ''}`}
      onClick={() => { if (!focused) { onFocus(); onGoto() } }}>
      <div className="rd-card-head">
        {avatar(n.author || 'you', n.mine ? 'You' : n.owner_name)}
        <span className="rd-who"><b>{n.author || (n.mine ? 'You' : n.owner_name)}</b><span className="rd-sub">{sub}</span></span>
        <span className={`rd-st st-${n.status}`} title={n.why}>{status}</span>
      </div>
      <button className="rd-quote" onClick={(e) => { e.stopPropagation(); onFocus(); onGoto() }} title="Show it in the paper">
        <span className="rd-dot" style={{ background: n.color }} />
        <span>“{focused ? n.text : short(n.text, 140)}”</span>
      </button>
      {unplaced && <div className="rd-warn">These words could not be found on the page, so the note is not highlighted (and cannot go to Zotero).</div>}
      {n.comment && !editing && <div className="rd-comment">{n.comment}</div>}
      {n.rel && n.idea_text && (
        <div className="rd-rel"><span className={`rd-rel-k ${n.rel === 'challenges' ? 'neg' : n.rel === 'asks about' ? 'ask' : ''}`}>{REL[n.rel]}</span>
          <span>{short(n.idea_text, 90)}</span></div>
      )}
      {n.replies.map((r) => (
        <div key={r.reply_id} className="rd-reply">
          {avatar(r.agent ? r.who : 'you', r.who_name, true)}
          <span><b>{r.who_name}</b>{r.pending ? <span className="rd-typing"> is reading the passage</span> : <span> {r.text}</span>}</span>
        </div>
      ))}
      {editing && (
        <div className="rd-edit" onClick={(e) => e.stopPropagation()}>
          <label className="rd-sub" htmlFor={`cm-${n.ann_id}`}>Your note (mention @Skeptic, @Methods or @Librarian to ask)</label>
          <textarea id={`cm-${n.ann_id}`} ref={input} className="rd-in" value={draft} onChange={(e) => setDraft(e.target.value)}
            placeholder="What does this passage mean for the idea?"
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing && (e.metaKey || e.ctrlKey)) actions.save(n, draft) }} />
          <div className="rd-actions end">
            <button className="rd-act quiet" onClick={() => actions.cancel(n)}>Cancel</button>
            <button className="rd-act keep" onClick={() => actions.save(n, draft)}>Save note</button>
          </div>
        </div>
      )}
      {focused && !editing && (
        <div className="rd-edit" onClick={(e) => e.stopPropagation()}>
          <textarea className="rd-in small" aria-label="Reply" value={draft} onChange={(e) => setDraft(e.target.value)}
            placeholder={agent ? `Reply to the ${n.author}…` : 'Reply, or ask @Skeptic, @Methods, @Librarian…'}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing && !e.shiftKey) { e.preventDefault(); send() } }} />
          <div className="rd-actions end"><button className="rd-act" disabled={!draft.trim() || sending} onClick={send}>Reply</button></div>
        </div>
      )}
      <div className="rd-actions" onClick={(e) => e.stopPropagation()}>
        {n.state === 'suggested' && n.mine && <>
          <button className="rd-act keep" onClick={() => actions.keep(n)}>Keep</button>
          <button className="rd-act" onClick={() => actions.dismiss(n)}>Dismiss</button>
        </>}
        {n.mine && n.state === 'kept' && canShare && (
          <button className="rd-act quiet" onClick={() => actions.share(n, !n.shared)}
            title={n.shared ? 'The project’s members see it' : 'Only you see it'}>{n.shared ? 'Shared · make private' : 'Share with the project'}</button>
        )}
        {n.mine && n.state === 'kept' && <button className="rd-act quiet" onClick={() => actions.remove(n)}>Delete</button>}
        {!focused && <span className="rd-sub rd-push">{n.replies.length ? `${n.replies.length} repl${n.replies.length === 1 ? 'y' : 'ies'}` : 'Reply'}</span>}
      </div>
    </div>
  )
}

/** Reading a paper with the agents: the PDF on the left, the notes beside it, Zotero in step.
 *  Opened anywhere with the event `aurora:read-paper` ({ paper, space, cardId?, chatId?, from? }). */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { MemDissent, NotePlace, ReaderAgent, ReaderNote, ReaderView } from '../../types'
import { ZoteroDialog } from '../ZoteroDialog'
import './textLayer.css'
import { PdfView, type Picked } from './PdfView'
import { AGENTS, NotesPanel, avatar, inFilter, type Filter } from './NotesPanel'
import { MarginNotes, inMargin } from './MarginNotes'
import { DeepReadPanel } from '../review/DeepReadPanel'
import { DissentPanel } from '../memory/DissentPanel'

export interface ReadRequest {
  /** The paper's key or ID. */
  paper: string
  /** The project, or 'global' for your own space. */
  space: string
  cardId?: string
  chatId?: string
  /** Where it was opened from, for the way back ("Brainstorm"). */
  from?: string
  /** Which side panel opens: the notes, or the deep read. */
  tab?: 'notes' | 'deep'
  /** The map of the field it was opened from (the deep read places the paper in it). */
  mapId?: string
}

const ZOOMS = [0.8, 1, 1.25, 1.5, 1.75]

function ago(t?: number | null): string {
  if (!t) return 'not yet'
  const s = Math.max(0, Date.now() / 1000 - t)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return new Date(t * 1000).toLocaleDateString()
}

export default function PaperReader({ req, onClose, docked, full, onToggleFull }: {
  req: ReadRequest
  onClose: () => void
  /** In a column beside the page it was opened from (the brainstorm), rather than over it. */
  docked?: boolean
  /** Docked, but shown full screen for now. */
  full?: boolean
  onToggleFull?: () => void
}) {
  // in a column: the PDF alone, narrower; the notes open below it when wanted
  const compact = !!docked && !full
  const [view, setView] = useState<ReaderView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pdfError, setPdfError] = useState<string | null>(null)
  const [zoom, setZoom] = useState(compact ? 0.8 : 1.25)
  // the notes in the page's margin, beside their words (or, off, in the panel only); remembered on this browser
  const [margin, setMargin] = useState(() => { try { return localStorage.getItem('pippin.reader.margin') !== 'off' } catch { return true } })
  const inMarginMode = margin && !compact
  const [sideOpen, setSideOpen] = useState((!compact && !margin) || req.tab === 'deep')
  const toggleMargin = () => {
    const on = !margin
    setMargin(on)
    setSideOpen(!on)
    try { localStorage.setItem('pippin.reader.margin', on ? 'on' : 'off') } catch { /* a convenience only */ }
  }
  // going full screen or back to the column: the size that fits it
  const wasFull = useRef(full)
  useEffect(() => {
    if (!docked || wasFull.current === full) return
    wasFull.current = full
    setZoom(full ? 1.25 : 0.8)
    if (full) setSideOpen(true)
  }, [docked, full])
  const [pages, setPages] = useState(0)
  const [page, setPage] = useState(1)
  const [focusId, setFocusId] = useState<string | null>(null)
  const [scrollTo, setScrollTo] = useState<{ annId: string; nonce: number } | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [picked, setPicked] = useState<Picked | null>(null)
  const [zpop, setZpop] = useState(false)
  const [zdialog, setZdialog] = useState(false)
  const [busyZ, setBusyZ] = useState(false)
  const [toast, setToast] = useState<{ text: string; undo?: () => void; key: number } | null>(null)
  const [unplaced, setUnplaced] = useState<Set<string>>(new Set())
  const [side, setSide] = useState<'notes' | 'deep' | 'saves'>(req.tab ?? 'notes')
  const [dissent, setDissent] = useState<MemDissent | null | undefined>(undefined)
  const [flash, setFlash] = useState<{ quote: string; page: number; nonce: number } | null>(null)
  const asked = useRef(false)
  const q = useMemo(() => ({ space: req.space, card_id: req.cardId, chat_id: req.chatId }), [req])

  const load = useCallback(async () => {
    try { setView(await api.reader.get(req.paper, q)); setError(null) } catch (e) { setError((e as Error).message) }
  }, [req.paper, q])
  useEffect(() => { load() }, [load])
  // what you saved before that argues with this paper: the margin marks and the "Your saves" tab
  const paperId = view?.paper.paper_id
  useEffect(() => {
    if (!paperId) return
    let gone = false
    api.memory.dissent(paperId, req.space).then((d) => { if (!gone) setDissent(d) }).catch(() => { if (!gone) setDissent(null) })
    return () => { gone = true }
  }, [paperId, req.space])

  // the agents read along once the paper is open (for the idea, if they mark passages on their own)
  useEffect(() => {
    if (!view || asked.current || !view.idea || !view.agents_mark || !view.pdf.available && !view.pdf.openable) return
    asked.current = true
    api.reader.readAlong(req.paper, { space: req.space, card_id: view.idea.kind === 'conversation' ? undefined : view.idea.card_id, chat_id: req.chatId })
      .then((r) => { if (r.started) load() }).catch(() => {})
  }, [view, req, load])

  // in step: often while the agents are at it or notes are going to Zotero
  const busy = !!view && (view.notes.some((n) => n.status === 'saving' || n.replies.some((r) => r.pending))
    || Object.values(view.reads).some((r) => r?.state === 'reading'))
  useEffect(() => {
    const t = setInterval(load, busy ? 1500 : 8000)
    return () => clearInterval(t)
  }, [load, busy])

  const say = useCallback((text: string, undo?: () => void) => {
    const key = Date.now()
    setToast({ text, undo, key })
    setTimeout(() => setToast((t) => (t?.key === key ? null : t)), 5000)
  }, [])
  const fail = useCallback((e: unknown) => say(`That did not work: ${(e as Error).message}`), [say])

  const notes = useMemo(() => (view?.notes ?? []).filter((n) => view?.agents_mark || n.state !== 'suggested')
    .sort((a, b) => a.page_index - b.page_index || (a.sort_index || '').localeCompare(b.sort_index || '') || a.created_at - b.created_at),
  [view])
  const dimmed = useMemo(() => new Set(notes.filter((n) => !inFilter(n, filter)).map((n) => n.ann_id)), [notes, filter])

  const upsert = useCallback((n: ReaderNote) => setView((v) => v && ({
    ...v, notes: v.notes.some((x) => x.ann_id === n.ann_id) ? v.notes.map((x) => (x.ann_id === n.ann_id ? n : x)) : [...v.notes, n],
  })), [])
  const drop = useCallback((annId: string) => setView((v) => v && ({ ...v, notes: v.notes.filter((x) => x.ann_id !== annId) })), [])

  const focus = useCallback((annId: string | null) => { setFocusId(annId); setPicked(null) }, [])
  const goto = useCallback((annId: string) => setScrollTo({ annId, nonce: Date.now() }), [])
  const onPlace = useCallback((annId: string, place: NotePlace | null) => {
    if (!place) { setUnplaced((s) => new Set(s).add(annId)); return }
    api.reader.change(annId, { place }).then(upsert).catch(() => {})
  }, [upsert])

  // what to do with selected words
  const create = async (kind: 'highlight' | 'comment' | 'ask', agent?: ReaderAgent) => {
    if (!picked) return
    const p = picked
    setPicked(null)
    window.getSelection()?.removeAllRanges()
    try {
      const n = await api.reader.addNote(req.paper, {
        space: req.space, card_id: view?.idea?.card_id, chat_id: req.chatId, text: p.text, ...p.place,
        draft: kind === 'comment', ask: agent,
      })
      upsert(n)
      setFocusId(n.ann_id)
      if (filter !== 'all' && filter !== 'you') setFilter('all')
      if (kind === 'highlight') setTimeout(load, 1600)
    } catch (e) { fail(e) }
  }

  const actions = {
    keep: async (n: ReaderNote) => {
      try {
        upsert(await api.reader.change(n.ann_id, { keep: true }))
        say(view?.zotero.state === 'ok' ? `Kept. The ${n.author}’s note goes to Zotero, in its colour.`
          : 'Kept. It stays in Pippin until Zotero can take it.')
        setTimeout(load, 1600)
      } catch (e) { fail(e) }
    },
    dismiss: async (n: ReaderNote) => {
      try {
        await api.reader.remove(n.ann_id); drop(n.ann_id)
        say(`Dismissed the ${n.author}’s suggestion.`, async () => { upsert(await api.reader.restore(n.ann_id)); setToast(null) })
      } catch (e) { fail(e) }
    },
    remove: async (n: ReaderNote) => {
      try {
        const r = await api.reader.remove(n.ann_id); drop(n.ann_id)
        if (focusId === n.ann_id) setFocusId(null)
        say(r.in_zotero ? 'Deleted here, and in Zotero in a few seconds.' : 'Deleted.',
          async () => { upsert(await api.reader.restore(n.ann_id)); setToast(null) })
      } catch (e) { fail(e) }
    },
    save: async (n: ReaderNote, comment: string) => {
      try { upsert(await api.reader.change(n.ann_id, { comment })); setTimeout(load, 1600) } catch (e) { fail(e) }
    },
    cancel: async (n: ReaderNote) => {
      try { await api.reader.remove(n.ann_id); drop(n.ann_id); setFocusId(null) } catch (e) { fail(e) }
    },
    reply: async (n: ReaderNote, text: string) => {
      try { upsert(await api.reader.reply(n.ann_id, text)) } catch (e) { fail(e) }
    },
    share: async (n: ReaderNote, shared: boolean) => {
      try {
        upsert(await api.reader.change(n.ann_id, { shared }))
        say(shared ? 'Shared: the project’s members see this note.' : 'Private again: only you see this note.')
      } catch (e) { fail(e) }
    },
    setMark: async (on: boolean) => {
      try {
        await api.reader.settings(on)
        setView((v) => v && { ...v, agents_mark: on })
        if (on) { asked.current = false; load() }
      } catch (e) { fail(e) }
    },
  }

  const addToZotero = async () => {
    setBusyZ(true)
    try { await api.reader.toZotero(req.paper, req.space); await load(); say('The paper is in your Zotero; the notes follow.') }
    catch (e) { fail(e) } finally { setBusyZ(false) }
  }
  const syncNow = async () => {
    setBusyZ(true)
    try { await api.reader.sync(); await load() } catch (e) { fail(e) } finally { setBusyZ(false) }
  }

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (picked) setPicked(null)
      else if (zpop) setZpop(false)
      else if (focusId) setFocusId(null)
      else if (docked && full) onToggleFull?.()
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [picked, zpop, focusId, docked, full, onToggleFull])
  const rdClass = `rd${docked ? ' docked' : ''}${docked && full ? ' full' : ''}`

  if (error && !view) {
    return (
      <div className={rdClass}>
        <header className="rd-head"><button className="rd-tb ghost" onClick={onClose}>← {docked ? 'Close' : req.from ?? 'Back'}</button></header>
        <div className="rd-fail">The paper could not be opened: {error}</div>
      </div>
    )
  }
  if (!view) return <div className={rdClass}><div className="rd-loading">Opening the paper…</div></div>

  const p = view.paper
  const z = view.zotero
  const zLabel = z.state === 'off' ? 'Zotero · not connected' : z.state === 'read_only' ? 'Zotero · read-only'
    : z.state === 'writes_off' ? 'Zotero · writing is off'
    : z.state !== 'ok' ? 'Zotero · notes stay here' : z.counts.saving ? 'Saving to Zotero…' : `Zotero · ${z.counts.synced} synced`
  const reading = AGENTS.filter((a) => view.reads[a.name])
  const authors = p.authors.length > 3 ? `${p.authors.slice(0, 3).map((a) => a.split(/[ ,]/).filter(Boolean).pop()).join(', ')} et al.` : p.authors.join(', ')

  return (
    <div className={rdClass}>
      <header className="rd-head">
        <button className="rd-tb ghost" onClick={onClose} title={docked ? 'Close the paper' : undefined}>
          {docked
            ? <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
            : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>}
          {docked ? '' : req.from ?? 'Back'}
        </button>
        <div className="rd-title">
          <span className="rd-title-main" title={p.title}>{p.title}</span>
          <span className="rd-sub"><span className="mono">[{p.key}]</span> · {authors}{p.venue || p.year ? ` · ${[p.venue, p.year].filter(Boolean).join(' ')}` : ''}
            {pages ? ` · page ${page} of ${pages}` : ''}</span>
        </div>
        <div className="rd-zoom">
          <button className="rd-tb" aria-label="Zoom out" disabled={zoom <= ZOOMS[0]} onClick={() => setZoom(ZOOMS[Math.max(0, ZOOMS.indexOf(zoom) - 1)])}>−</button>
          <span className="mono">{Math.round(zoom * 100)}%</span>
          <button className="rd-tb" aria-label="Zoom in" disabled={zoom >= ZOOMS[ZOOMS.length - 1]} onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoom) + 1)])}>+</button>
        </div>
        {reading.length > 0 && (
          <div className="rd-presence" title={reading.map((a) => `${a.name}: ${view.reads[a.name]?.state === 'reading' ? 'reading' : view.reads[a.name]?.state === 'failed' ? `could not read (${view.reads[a.name]?.detail})` : view.reads[a.name]?.detail || 'done'}`).join('\n')}>
            <span className={`rd-live${reading.some((a) => view.reads[a.name]?.state === 'reading') ? ' on' : ''}`} />
            <span className="rd-faces">{reading.map((a) => <span key={a.name}>{avatar(a.name, a.name, true)}</span>)}</span>
            <span className="rd-sub">{reading.some((a) => view.reads[a.name]?.state === 'reading') ? 'reading along' : 'read along'}</span>
          </div>
        )}
        {compact && (
          <button className={`rd-tb${sideOpen ? ' on' : ''}`} onClick={() => setSideOpen(!sideOpen)} aria-expanded={sideOpen}
            title="Your notes, the agents' marks, the deep read">Notes <span className="rd-n">{notes.length}</span></button>
        )}
        {!compact && (
          <button className={`rd-tb${margin ? ' on' : ''}`} onClick={toggleMargin} aria-pressed={margin}
            title={margin ? 'The notes are in the margin, beside their words' : 'Show the notes in the margin, beside their words'}>Margin notes</button>
        )}
        {inMarginMode && (
          <button className={`rd-tb${sideOpen ? ' on' : ''}`} onClick={() => setSideOpen(!sideOpen)} aria-expanded={sideOpen}
            title="All the notes in a list, the deep read, and your saves">Panel <span className="rd-n">{notes.length}</span></button>
        )}
        {!compact && (
          <button className={`rd-tb${zpop ? ' on' : ''}`} onClick={() => setZpop(!zpop)} aria-expanded={zpop}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M5 5h14L5 19h14" /></svg>
            {zLabel}
          </button>
        )}
        {docked && (
          <button className="rd-tb" onClick={onToggleFull} title={full ? 'Back into the column (Esc)' : 'Full screen'}
            aria-label={full ? 'Exit full screen' : 'Full screen'}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              {full ? <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" /> : <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />}</svg>
            {full ? '' : 'Full screen'}
          </button>
        )}
        {zpop && (
          <div className="rd-zpop" role="dialog" aria-label="Notes and Zotero">
            <div className="rd-row-between"><b>Notes and Zotero</b><button className="rd-act quiet" aria-label="Close" onClick={() => setZpop(false)}>✕</button></div>
            {z.state === 'ok' && <span className="rd-text-2">[{p.key}] is in your Zotero ({z.library}). Your notes, and the agents’ notes you keep,
              become annotations on its PDF there: same place, same colour. Replies go into the note’s comment. Notes you make in Zotero show up here too.</span>}
            {z.state === 'off' && <span className="rd-text-2">Connect Zotero and your notes become annotations on the paper’s PDF in your library (and the ones you make in Zotero come here).</span>}
            {z.state === 'writes_off' && <span className="rd-note-warn">Writing to Zotero is off, so these notes stay in Pippin. Turn on “Send the notes you keep on a PDF as Zotero annotations” under the account button → What Pippin may write.</span>}
            {z.state === 'read_only' && <span className="rd-note-warn">Your Zotero key can only read, so these notes stay in Pippin for now. Connect a key that can write and they go to Zotero at the next sync.</span>}
            {(z.state === 'not_in_zotero' || z.state === 'no_pdf') && <span className="rd-note-warn">
              {z.state === 'not_in_zotero' ? 'This paper is not in your Zotero' : 'Your Zotero has this paper but not its PDF'}, so the notes stay in Pippin.
              {z.can_add ? ' Add it and they follow.' : ' Choose a Zotero collection for this project first, then add it.'}</span>}
            <div className="rd-zrows">
              <div><span>In Zotero</span><b>{z.counts.synced}</b></div>
              <div><span>Going to Zotero</span><b>{z.counts.saving}</b></div>
              <div><span>Suggestions you have not kept (stay here)</span><b>{z.counts.suggested}</b></div>
              <div><span>Only in Pippin</span><b>{z.counts.local}</b></div>
              {z.connected && <div><span>Last sync</span><b>{ago(z.last_sync)}</b></div>}
            </div>
            {z.error && <span className="rd-note-warn">Last sync: {z.error}</span>}
            <div className="rd-actions">
              {z.state === 'ok' && <button className="rd-act" disabled={busyZ} onClick={syncNow}>{busyZ ? 'Syncing…' : 'Sync now'}</button>}
              {(z.state === 'not_in_zotero' || z.state === 'no_pdf') && z.can_add &&
                <button className="rd-act keep" disabled={busyZ} onClick={addToZotero}>{busyZ ? 'Adding…' : 'Add it to Zotero'}</button>}
              {(z.state === 'off' || z.state === 'read_only' || ((z.state === 'not_in_zotero' || z.state === 'no_pdf') && !z.can_add)) &&
                <button className="rd-act keep" onClick={() => setZdialog(true)}>{z.state === 'off' ? 'Connect Zotero' : 'Zotero settings'}</button>}
            </div>
            <details className="rd-rules">
              <summary>How notes and Zotero stay in step</summary>
              <dl>
                <dt>You note here</dt><dd>In Zotero within seconds.</dd>
                <dt>You note in Zotero</dt><dd>Here at the next sync (every few minutes, or Sync now), and the agents read it too.</dd>
                <dt>An agent’s suggestion</dt><dd>Stays here until you keep it; then it goes, in the agent’s colour, its comment saying who wrote it.</dd>
                <dt>Edited on both sides</dt><dd>The later edit wins.</dd>
                <dt>Deleted on one side</dt><dd>Deleted on the other (here you can Undo for a few seconds first).</dd>
              </dl>
            </details>
          </div>
        )}
      </header>

      {toast && (
        <div className="rd-toast" role="status">
          <span>{toast.text}</span>
          {toast.undo && <button onClick={toast.undo}>Undo</button>}
        </div>
      )}

      <div className={`rd-body${compact ? ' stacked' : ''}`}>
        <main className="rd-main" aria-label="Paper">
          {view.pdf.available || view.pdf.openable ? (
            !pdfError ? (
              <>
                <PdfView url={api.reader.pdfUrl(p.paper_id, view.space)} paper={{ id: p.paper_id, space: view.space }} scale={zoom} notes={notes} focusId={focusId}
                  dimmed={dimmed} scrollTo={scrollTo} picked={picked} onPick={setPicked}
                  onNote={(id) => {
                    setFocusId(id); setPicked(null)
                    const n = notes.find((x) => x.ann_id === id)
                    if (!(inMarginMode && !sideOpen && n && inMargin(n))) setSideOpen(true)
                    if (dimmed.has(id)) setFilter('all')
                  }}
                  margin={inMarginMode && !sideOpen ? (_i, here, v) => (
                    <MarginNotes notes={here} top={(n) => (v[3] - Math.max(...n.rects.map((r) => r[3]))) * zoom} focusId={focusId} dimmed={dimmed}
                      onFocus={focus} onMore={(id) => { setFocusId(id); setSide('notes'); setSideOpen(true) }} actions={actions} />
                  ) : undefined}
                  onPlace={onPlace} onPages={setPages} onPage={setPage} onError={setPdfError} flash={flash} />
                {(dissent?.items ?? []).some((it) => it.page === page - 1) && (() => {
                  const here = dissent!.items.filter((it) => it.page === page - 1)
                  const n = new Set(here.flatMap((it) => it.units.map((u) => u.unit_id))).size
                  return <button className="mem-margin" onClick={() => { setSide('saves'); setFlash({ quote: here[0].quote, page: here[0].page, nonce: Date.now() }) }}>
                    {n} of your saves argue with this page</button>
                })()}
                {picked && (
                  <SelectionMenu picked={picked} onClose={() => setPicked(null)} onHighlight={() => create('highlight')}
                    onComment={() => create('comment')} onAsk={(a) => create('ask', a)} />
                )}
              </>
            ) : <NoPdf paper={p} message={pdfError} />
          ) : <NoPdf paper={p} message="There is no PDF of this paper here yet." />}
        </main>
        {sideOpen && <div className="rd-side">
          <div className="rd-tabs" role="tablist">
            <button role="tab" aria-selected={side === 'notes'} className={side === 'notes' ? 'on' : ''} onClick={() => setSide('notes')}>
              Notes <span className="rd-n">{notes.length}</span></button>
            <button role="tab" aria-selected={side === 'deep'} className={side === 'deep' ? 'on' : ''} onClick={() => setSide('deep')}>Deep read</button>
            <button role="tab" aria-selected={side === 'saves'} className={side === 'saves' ? 'on' : ''} onClick={() => setSide('saves')}
              title="What you saved before that argues with this paper">Your saves{dissent?.items.length ? <span className="rd-n warn">{dissent.items.length}</span> : null}</button>
          </div>
          {side === 'saves' ? <DissentPanel data={dissent} onQuote={(quote, pg) => setFlash({ quote, page: pg, nonce: Date.now() })} />
            : side === 'notes'
            ? <NotesPanel view={view} notes={notes} filter={filter} setFilter={setFilter} focusId={focusId} unplaced={unplaced}
              onFocus={focus} onGoto={goto} actions={actions} />
            : <DeepReadPanel paper={p.key} space={req.space} mapId={req.mapId}
              onQuote={(quote, pg) => setFlash({ quote, page: pg, nonce: Date.now() })} />}
        </div>}
      </div>
      {zdialog && <ZoteroDialog onClose={() => { setZdialog(false); load() }} />}
    </div>
  )
}

function SelectionMenu({ picked, onClose, onHighlight, onComment, onAsk }: {
  picked: Picked
  onClose: () => void
  onHighlight: () => void
  onComment: () => void
  onAsk: (a: ReaderAgent) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  // placed under the selection, inside the paper's scrolled area
  useEffect(() => {
    const anchor = document.querySelector<HTMLElement>('.rd-anchor[data-picked]')
    const host = ref.current?.parentElement
    if (!anchor || !host) return
    const a = anchor.getBoundingClientRect()
    const h = host.getBoundingClientRect()
    const w = ref.current?.offsetWidth ?? 480
    setPos({ left: Math.max(12, Math.min(a.left - h.left - w / 2, h.width - w - 12)), top: a.top - h.top })
  }, [picked])
  return (
    <div ref={ref} className="rd-pop" style={pos ?? { visibility: 'hidden' }} role="menu" onMouseUp={(e) => e.stopPropagation()}>
      <div className="rd-pop-q">“{picked.text.length > 90 ? picked.text.slice(0, 89) + '…' : picked.text}”</div>
      <div className="rd-pop-row">
        <button className="rd-pb" role="menuitem" onClick={onHighlight}><span className="rd-dot c-y" />Highlight</button>
        <button className="rd-pb" role="menuitem" onClick={onComment}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z" /></svg>Note</button>
        <span className="rd-pop-sep" />
        <span className="rd-pop-ask">Ask</span>
        {AGENTS.map((a) => (
          <button key={a.name} className="rd-pb" role="menuitem" onClick={() => onAsk(a.name)} title={a.role}>{avatar(a.name, a.name, true)}{a.name}</button>
        ))}
        <button className="rd-pb" aria-label="Close" onClick={onClose}>✕</button>
      </div>
    </div>
  )
}

function NoPdf({ paper, message }: { paper: ReaderView['paper']; message: string }) {
  const page = paper.doi ? `https://doi.org/${paper.doi}` : paper.arxiv_id ? `https://arxiv.org/abs/${paper.arxiv_id}` : paper.url
  return (
    <div className="rd-nopdf">
      <b>{message}</b>
      <span>Put the PDF in the project’s papers (Literature › Upload a PDF) or in your Zotero, and it opens here.
        Until then you can read the abstract below and ask the agents about the paper in the brainstorm.</span>
      {page && <a className="rd-act" href={page} target="_blank" rel="noreferrer">Open the paper’s page</a>}
      {paper.abstract && <p className="rd-abstract">{paper.abstract}</p>}
    </div>
  )
}

/** The feed's papers, one at a time: why it was picked, what it says, and four ways on. Skip (J or ←) passes it
 *  by; Less like this (X) hides it and similar ones rank lower; Into memory (S or ↓) keeps what it says among your
 *  saves; Read with me (R or →) puts it in the project it fits and opens it in the reader. U undoes the last one.
 *  On a trackpad or phone, swipe: left to skip, right to read, down to keep. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '../../api/client'
import type { FeedItem } from '../../types'
import { readPaper } from '../reader/open'
import { ago } from './common'

type Choice = 'skip' | 'less' | 'keep' | 'read'
interface Done { it: FeedItem; choice: Choice; undo?: () => Promise<unknown> }

const SKIPPED = 'pippin.triage.skipped'

function skipped(): string[] {
  try { return JSON.parse(localStorage.getItem(SKIPPED) || '[]') as string[] } catch { return [] }
}
function setSkipped(ids: string[]) {
  try { localStorage.setItem(SKIPPED, JSON.stringify(ids.slice(-500))) } catch { /* a convenience only */ }
}

function tally(done: Done[]): string {
  const n = (c: Choice) => done.filter((d) => d.choice === c).length
  const parts = [n('read') && `${n('read')} to read`, n('keep') && `${n('keep')} into memory`, n('skip') + n('less') && `${n('skip') + n('less')} skipped`].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'Nothing chosen yet'
}

export function PaperTriage({ items, onClose, onChanged }: {
  items: FeedItem[]
  onClose: () => void
  /** Something changed in the feed (hidden, saved): the list behind reloads. */
  onChanged: () => void
}) {
  // the queue is fixed when it opens: the most fitting first, without what was saved or passed by before
  const [queue] = useState(() => {
    const passed = new Set(skipped())
    return items.filter((x) => x.kind === 'paper' && !x.saved && !passed.has(x.item_id))
      .sort((a, b) => (b.fits[0]?.rel ?? b.rel) - (a.fits[0]?.rel ?? a.rel))
  })
  const [done, setDone] = useState<Done[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null)
  const start = useRef<{ x: number; y: number } | null>(null)
  const it = queue[done.length] as FeedItem | undefined
  const fit = it?.fits[0]

  const choose = useCallback(async (choice: Choice) => {
    if (!it || busy) return
    setBusy(true); setError(null)
    try {
      let undo: Done['undo']
      if (choice === 'skip') {
        setSkipped([...skipped(), it.item_id])
        undo = async () => setSkipped(skipped().filter((x) => x !== it.item_id))
      } else if (choice === 'less') {
        await api.feed.hide(it.item_id)
        undo = () => api.feed.unhide(it.item_id)
      } else if (choice === 'keep') {
        const src = await api.memory.capture({ url: it.url, title: it.title, text: it.summary, via: 'feed' })
        undo = () => api.memory.forget(src.source_id)
      } else {
        // into the project it fits; one you cannot add to (an example) leaves it in your own library
        let cid = fit?.campaign_id ?? ''
        const r = await api.feed.save(it.item_id, cid).catch((e) => { if (!cid) throw e; cid = ''; return api.feed.save(it.item_id, '') })
        undo = () => api.feed.unsave(it.item_id)
        if (r.paper_id) readPaper({ paper: r.paper_id, space: cid || 'global', from: 'Papers' })
        else if (it.url) window.open(it.url, '_blank', 'noopener')
      }
      setDone((d) => [...d, { it, choice, undo }])
      if (choice !== 'skip') onChanged()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }, [it, fit, busy, onChanged])

  const back = useCallback(async () => {
    const last = done[done.length - 1]
    if (!last || busy) return
    setBusy(true); setError(null)
    try { await last.undo?.(); setDone((d) => d.slice(0, -1)); onChanged() } catch (e) { setError(describeError(e)) } finally { setBusy(false) }
  }, [done, busy, onChanged])

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      // not while a paper is open in the reader over this, or the ⌘K palette
      if (document.querySelector('.rd, .cp-backdrop') || e.metaKey || e.ctrlKey || e.altKey || (e.target instanceof HTMLElement && e.target.closest('input, textarea, select, [contenteditable]'))) return
      const k = e.key.toLowerCase()
      const c: Choice | null = k === 'j' || k === 'arrowleft' ? 'skip' : k === 'x' ? 'less' : k === 's' || k === 'arrowdown' ? 'keep'
        : k === 'r' || k === 'arrowright' ? 'read' : null
      if (c) { e.preventDefault(); choose(c) } else if (k === 'u') { e.preventDefault(); back() } else if (k === 'escape') onClose()
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [choose, back, onClose])

  const release = () => {
    const d = drag
    start.current = null
    setDrag(null)
    if (!d) return
    if (d.x < -90) choose('skip')
    else if (d.x > 90) choose('read')
    else if (d.y > 90) choose('keep')
  }
  const lean = drag ? (drag.x < -90 ? 'Skip' : drag.x > 90 ? 'Read with me' : drag.y > 90 ? 'Into memory' : '') : ''

  return (
    <div className="tr">
      <div className="tr-top">
        <button className="td-link tr-back" onClick={onClose}>← The feed</button>
        <span className="tr-progress">{it ? `${done.length + 1} of ${queue.length}` : ''}</span>
        <span className="tr-tally">{tally(done)}</span>
      </div>

      {it ? (
        <>
          <article className={`tr-card${drag ? ' dragging' : ''}`} aria-live="polite"
            style={drag ? { transform: `translate(${drag.x}px, ${Math.max(0, drag.y)}px) rotate(${drag.x / 40}deg)` } : undefined}
            onPointerDown={(e) => { if ((e.target as HTMLElement).closest('a, button')) return; start.current = { x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId) }}
            onPointerMove={(e) => { if (start.current) setDrag({ x: e.clientX - start.current.x, y: e.clientY - start.current.y }) }}
            onPointerUp={release} onPointerCancel={() => { start.current = null; setDrag(null) }}>
            {lean && <span className="tr-lean">{lean}</span>}
            {fit?.why && <p className="tr-why"><span className="tr-for">For {fit.project}</span> {fit.why}</p>}
            <p className="tr-source">{it.source}{it.published_at ? ` · ${ago(it.published_at)}` : ''}{it.idv ? ` · ${it.idv}` : ''}</p>
            <h2 className="tr-title">{it.url ? <a href={it.url} target="_blank" rel="noreferrer">{it.title}</a> : it.title}</h2>
            {it.people.length > 0 && <p className="tr-authors">{it.people.slice(0, 6).map(([n]) => n).join(', ')}{it.people.length > 6 ? ' et al.' : ''}</p>}
            {it.summary && <p className="tr-gist">{it.summary}</p>}
            {it.fits.length > 1 && <p className="tr-also">Also fits {it.fits.slice(1).map((f) => f.project).join(', ')}.</p>}
          </article>
          <div className="tr-actions">
            <button className="tr-btn" disabled={busy} onClick={() => choose('skip')}>Skip<kbd>J · ←</kbd></button>
            <button className="tr-btn quiet" disabled={busy} onClick={() => choose('less')}>Less like this<kbd>X</kbd></button>
            <button className="tr-btn" disabled={busy} onClick={() => choose('keep')}>Into memory<kbd>S · ↓</kbd></button>
            <button className="tr-btn primary" disabled={busy} onClick={() => choose('read')}>Read with me<kbd>R · →</kbd></button>
          </div>
          {error && <p className="td-error tr-error">{error}</p>}
          <p className="tr-hint">
            {done.length > 0 && <><button className="td-link" disabled={busy} onClick={back}>Undo</button> (U) · </>}
            On a trackpad or phone: swipe left to skip, right to read, down to keep. What you choose teaches the feed what to bring.
          </p>
        </>
      ) : (
        <div className="tr-end">
          <h2 className="tr-title">{queue.length ? 'All caught up.' : 'No new papers to go through.'}</h2>
          <p className="tr-gist">{queue.length ? `${tally(done)}. The ones to read are in their projects’ Literature, and in the reader.`
            : 'The papers you have not saved or passed by come here as the feed brings them.'}</p>
          <div className="tr-actions">
            {done.length > 0 && <button className="tr-btn" disabled={busy} onClick={back}>Undo the last one</button>}
            <button className="tr-btn primary" onClick={onClose}>Back to the feed</button>
          </div>
        </div>
      )}
    </div>
  )
}

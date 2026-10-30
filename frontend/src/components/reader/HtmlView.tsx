/** A page kept in the Library (a blog post, a thread, a README), read as it was saved: its article in a reading
 *  column, with the same notes as a paper's PDF. A note is anchored on its words (found again in the text), its
 *  highlight drawn without touching the page, its card in the margin beside them; a note written in the page opens a
 *  gap after the paragraph it is on. The + left of a paragraph opens one there. */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReaderNote } from '../../types'
import type { Picked } from './PdfView'
import { fold } from './locate'

const BLOCKS = 'p, h1, h2, h3, h4, h5, h6, li, blockquote, pre, figure, table, dd, dt'

/** The article's text, folded (letters and digits, lower case), knowing which text node and offset each folded
 *  character came from: a quote is found across tags, spacing and punctuation. */
function indexOf(root: HTMLElement) {
  const at: { node: Text; off: number }[] = []
  let text = ''
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let n = walk.nextNode() as Text | null; n; n = walk.nextNode() as Text | null) {
    if (n.parentElement?.closest('.rd-gap')) continue
    const s = n.data
    for (let i = 0; i < s.length; i++) {
      const f = fold(s[i])
      for (const c of f) { text += c; at.push({ node: n, off: i }) }
    }
  }
  return { text, at }
}

function find(idx: ReturnType<typeof indexOf>, quote: string): Range | null {
  const q = fold(quote)
  if (q.length < 6) return null
  const i = idx.text.indexOf(q)
  if (i < 0) return null
  const a = idx.at[i], b = idx.at[i + q.length - 1]
  const r = document.createRange()
  r.setStart(a.node, a.off)
  r.setEnd(b.node, b.off + 1)
  return r
}

// the colours of the notes' highlights (registered once; ::highlight() rules in the CSS)
const HUES: Record<string, string> = { '#ffd400': 'y', '#2ea8e5': 'b', '#f19837': 'o', '#a28ae5': 'p', '#5fb236': 'g' }
const hl = (typeof CSS !== 'undefined' && 'highlights' in CSS) ? (CSS as unknown as { highlights: Map<string, unknown> }).highlights : null
const Hl = (globalThis as unknown as { Highlight?: new (...r: Range[]) => unknown }).Highlight

export function HtmlView({ html, scale, notes, focusId, dimmed, scrollTo, picked, onPick, onNote, flash, margin, gap, onGapAt }: {
  html: string
  scale: number
  notes: ReaderNote[]
  focusId: string | null
  dimmed: Set<string>
  scrollTo: { annId: string; nonce: number } | null
  picked: Picked | null
  onPick: (p: Picked | null) => void
  onNote: (annId: string) => void
  flash?: { quote: string; page: number; nonce: number } | null
  /** The notes' cards beside the column: given where each note's words start, and which notes were found. */
  margin?: (top: (n: ReaderNote) => number, placed: Set<string>) => React.ReactNode
  gap?: (n: ReaderNote) => React.ReactNode
  /** Open a gap after a paragraph (anchored on its last words). */
  onGapAt?: (words: string, offset: number) => void
}) {
  const scroll = useRef<HTMLDivElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const art = useRef<HTMLElement>(null)
  const [tick, setTick] = useState(0)           // the layout changed: positions are worked out again
  const [ranges, setRanges] = useState<Map<string, Range>>(new Map())
  const [hosts, setHosts] = useState<Map<string, HTMLElement>>(new Map())
  const [hover, setHover] = useState<{ top: number; el: HTMLElement } | null>(null)
  const [lit, setLit] = useState<Range | null>(null)
  const body = useMemo(() => ({ __html: html }), [html])

  // the article's size changes (images load, gaps grow, the zoom): look again
  useEffect(() => {
    const el = art.current
    if (!el) return
    const ro = new ResizeObserver(() => setTick((t) => t + 1))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // each note found in the text
  useLayoutEffect(() => {
    const el = art.current
    if (!el) return
    const idx = indexOf(el)
    const m = new Map<string, Range>()
    for (const n of notes) { const r = find(idx, n.text); if (r) m.set(n.ann_id, r) }
    setRanges(m)
  }, [notes, html])

  // the gaps: a place after the paragraph a note written in the page is on
  const inlineKey = notes.filter((n) => n.inline).map((n) => n.ann_id).join(',')
  useLayoutEffect(() => {
    const el = art.current
    if (!el) return
    const made = new Map<string, HTMLElement>()
    for (const n of notes.filter((x) => x.inline)) {
      const r = ranges.get(n.ann_id)
      if (!r) continue
      const start = r.endContainer.nodeType === Node.TEXT_NODE ? r.endContainer.parentElement : r.endContainer as HTMLElement
      const block = start?.closest(BLOCKS) ?? start
      if (!block || !el.contains(block)) continue
      const host = document.createElement('div')
      host.className = 'rd-gap rd-webgap'
      host.dataset.gap = n.ann_id
      let after: Element = block
      while (after.nextElementSibling?.classList.contains('rd-webgap')) after = after.nextElementSibling
      after.after(host)
      made.set(n.ann_id, host)
    }
    setHosts(made)
    return () => { for (const h of made.values()) h.remove() }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inlineKey, ranges.size, html])

  // the highlights, drawn over the text (the page itself is not changed)
  useEffect(() => {
    if (!hl || !Hl) return
    const groups = new Map<string, Range[]>()
    const put = (name: string, r: Range) => groups.set(name, [...(groups.get(name) ?? []), r])
    for (const n of notes) {
      const r = ranges.get(n.ann_id)
      if (!r || (n.inline && n.ann_id !== focusId)) continue
      const hue = HUES[n.color?.toLowerCase()] ?? 'y'
      put(dimmed.has(n.ann_id) ? 'pp-dim' : n.ann_id === focusId ? `pp-${hue}-focus` : n.state === 'suggested' ? `pp-${hue}-sug` : `pp-${hue}`, r)
    }
    if (lit) put('pp-flash', lit)
    for (const k of [...hl.keys()]) if (k.startsWith('pp-')) hl.delete(k)
    for (const [k, rs] of groups) hl.set(k, new Hl(...rs))
    return () => { for (const k of [...hl.keys()]) if (k.startsWith('pp-')) hl.delete(k) }
  }, [notes, ranges, focusId, dimmed, lit])

  // where a note's words start, from the top of the column
  const top = useCallback((n: ReaderNote) => {
    const r = ranges.get(n.ann_id)
    const w = wrap.current
    if (!r || !w) return 0
    return r.getBoundingClientRect().top - w.getBoundingClientRect().top
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ranges, tick])
  const placed = useMemo(() => new Set(ranges.keys()), [ranges])

  const scrollToRange = useCallback((r: Range) => {
    const s = scroll.current
    if (!s) return
    s.scrollTo({ top: s.scrollTop + r.getBoundingClientRect().top - s.getBoundingClientRect().top - 140, behavior: 'smooth' })
  }, [])
  useEffect(() => {
    if (!scrollTo) return
    const r = ranges.get(scrollTo.annId)
    if (r) scrollToRange(r)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollTo])
  useEffect(() => {
    if (!flash || !art.current) return
    const r = find(indexOf(art.current), flash.quote)
    if (!r) return
    setLit(r); scrollToRange(r)
    const t = setTimeout(() => setLit(null), 4000)
    return () => clearTimeout(t)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flash, html])

  // words selected: what to do with them; a click on a highlight opens its note
  const onMouseUp = useCallback((e: React.MouseEvent) => {
    const s = scroll.current
    const el = art.current
    if (!s || !el) return
    if ((e.target as HTMLElement).closest('.rd-gap, .rd-margin, .rd-webplus')) return
    const sel = window.getSelection()
    if (sel && !sel.isCollapsed && sel.rangeCount && el.contains(sel.getRangeAt(0).commonAncestorContainer)) {
      const range = sel.getRangeAt(0)
      const text = sel.toString().replace(/\s+/g, ' ').trim()
      if (text.length < 2) return
      const before = document.createRange()
      before.setStart(el, 0); before.setEnd(range.startContainer, range.startOffset)
      const off = fold(before.toString()).length
      const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0)
      const last = rects[rects.length - 1] ?? range.getBoundingClientRect()
      const b = s.getBoundingClientRect()
      onPick({ text, place: { page_index: 0, page_label: '', rects: [], sort_index: `web|${String(off).padStart(8, '0')}` },
        x: last.left - b.left + s.scrollLeft, y: last.bottom - b.top + s.scrollTop + 8 })
      return
    }
    const hit = notes.find((n) => {
      const r = ranges.get(n.ann_id)
      return r && !n.inline && Array.from(r.getClientRects()).some((q) => e.clientX >= q.left - 1 && e.clientX <= q.right + 1 && e.clientY >= q.top - 1 && e.clientY <= q.bottom + 1)
    })
    if (hit) onNote(hit.ann_id)
    else if (picked) onPick(null)
  }, [notes, ranges, onNote, onPick, picked])

  // the + beside the paragraph under the pointer
  const onMove = (e: React.MouseEvent) => {
    if (!onGapAt || !wrap.current) return
    const t = (e.target as HTMLElement).closest(BLOCKS) as HTMLElement | null
    if (!t || !art.current?.contains(t) || t.closest('.rd-gap')) return
    if (hover?.el === t) return
    const top = t.getBoundingClientRect().bottom - wrap.current.getBoundingClientRect().top
    setHover({ top, el: t })
  }
  const openGap = () => {
    if (!hover || !onGapAt || !art.current) return
    const words = (hover.el.textContent ?? '').replace(/\s+/g, ' ').trim().split(' ').slice(-14).join(' ')
    const before = document.createRange()
    before.setStart(art.current, 0); before.setEndAfter(hover.el)
    onGapAt(words, fold(before.toString()).length)
    setHover(null)
  }

  return (
    <div className={`rd-scroll rd-web${margin ? ' with-margin' : ''}`} ref={scroll} onMouseUp={onMouseUp}>
      <div className="rd-hint">Select words to highlight them, write a note or ask about them. The + beside a paragraph opens the page there, to write in.</div>
      <div className="rd-webwrap" ref={wrap} onMouseMove={onMove} onMouseLeave={() => setHover(null)}
        style={{ ['--web-size' as string]: `${Math.round(17 * scale / 1.25)}px` }}>
        <article className="rd-webpage" ref={art} dangerouslySetInnerHTML={body} />
        {hover && onGapAt && (
          <button className="rd-gutbtn rd-webplus" style={{ top: hover.top - 10 }} title="Open a gap here, to write in"
            aria-label="Open a gap after this paragraph" onMouseDown={(e) => e.preventDefault()} onClick={openGap}>+</button>
        )}
        {margin && <div className="rd-margin rd-webmargin">{margin(top, placed)}</div>}
      </div>
      {gap && notes.filter((n) => n.inline && hosts.get(n.ann_id)).map((n) => createPortal(gap(n), hosts.get(n.ann_id)!, n.ann_id))}
      {picked && <div className="rd-anchor" style={{ left: picked.x, top: picked.y }} data-picked="" />}
    </div>
  )
}

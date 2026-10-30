/** The PDF itself: pages drawn as they come into view, with a text layer to select words and the
 *  notes' highlights on top. Selecting words asks what to do with them; clicking a highlight opens its note. */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist'
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { api } from '../../api/client'
import type { NotePlace, PdfProgress, ReaderNote } from '../../types'
import { locate, pageLines, selectionPlace, sortIndex, textOffset, type Line } from './locate'

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc

/** Words selected on a page, and where the menu for them goes (in the scrolled content). */
export interface Picked { text: string; place: NotePlace; x: number; y: number }

export type View = [number, number, number, number]
type Box = [number, number, number, number]

/** A highlight's lines without overlap: the text layer's boxes are taller than the line spacing, and where two
 *  overlap the colour doubles into a dark band across the words. Overlapping lines meet halfway instead. */
function untangle(rects: Box[]): Box[] {
  const rs = rects.map((r) => [...r] as Box)
  for (const a of rs) {
    for (const b of rs) {
      // a is the line above b (PDF points: y grows upwards), and they share some width
      if (a !== b && a[3] > b[3] && a[1] < b[3] && a[0] < b[2] && b[0] < a[2]) {
        const mid = (a[1] + b[3]) / 2
        a[1] = mid
        b[3] = mid
      }
    }
  }
  return rs
}

function rgba(hex: string, a: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '')
  const n = m ? parseInt(m[1], 16) : 0xffd400
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`
}

export function PdfView({ url, paper, scale, notes, focusId, dimmed, scrollTo, picked, onPick, onNote, onPlace, onPages, onPage, onError, flash, margin, gap, onGapAt }: {
  url: string
  /** The paper and its space: how far its download is, shown while it opens. */
  paper?: { id: string; space: string }
  scale: number
  notes: ReaderNote[]
  focusId: string | null
  /** Notes filtered out: drawn grey. */
  dimmed: Set<string>
  /** Scroll to this note (the nonce repeats a request). */
  scrollTo: { annId: string; nonce: number } | null
  picked: Picked | null
  onPick: (p: Picked | null) => void
  onNote: (annId: string) => void
  /** A note quoted by an agent, found on its page (null: the page does not have the words). */
  onPlace: (annId: string, place: NotePlace | null) => void
  onPages: (n: number) => void
  onPage: (n: number) => void
  onError: (message: string) => void
  /** What goes in a page's margin, beside it (the notes): from the page's notes, and where its box is. */
  margin?: (index: number, notes: ReaderNote[], view: View, at: At) => React.ReactNode
  /** What is written in the gap a note opens under its words (notes with ``inline``). */
  gap?: (n: ReaderNote) => React.ReactNode
  /** Open a gap under a line of a page (the + in the page's left gutter). */
  onGapAt?: (pageIndex: number, line: Line) => void
  /** A passage to show for a moment (a quote in a deep read): found on its page, scrolled to, lit. */
  flash?: { quote: string; page: number; nonce: number } | null
}) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null)
  const [lit, setLit] = useState<NotePlace | null>(null)
  const [views, setViews] = useState<View[]>([])
  const scrollRef = useRef<HTMLDivElement>(null)
  const pages = useRef(new Map<number, PDFPageProxy>())
  const tried = useRef(new Set<string>())
  // a paper opened the first time is fetched from where it is published first: say how far that is
  const [fetching, setFetching] = useState<PdfProgress | null>(null)
  useEffect(() => {
    if (doc || !paper) return
    let gone = false
    const look = () => api.reader.pdfProgress(paper.id, paper.space).then((pr) => { if (!gone) setFetching(pr) }).catch(() => {})
    look()
    const t = setInterval(look, 800)
    return () => { gone = true; clearInterval(t) }
  }, [doc, paper?.id, paper?.space]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let gone = false
    const task = pdfjs.getDocument({ url, withCredentials: true })
    task.promise.then(async (d) => {
      if (gone) return
      const vs: View[] = []
      for (let i = 1; i <= d.numPages; i++) {
        const p = await d.getPage(i)
        pages.current.set(i - 1, p)
        vs.push(p.view as View)
      }
      if (gone) return
      setDoc(d); setViews(vs); onPages(d.numPages)
    }).catch((e: Error) => { if (!gone) onError(e?.message?.includes('Missing PDF') || e?.name === 'MissingPDFException'
      ? 'There is no PDF of this paper here, and no open copy could be fetched.' : `The PDF could not be opened: ${e?.message ?? e}`) })
    return () => { gone = true; task.destroy() }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url])

  // an agent's note quotes the paper: find the words on its page (or the pages next to it)
  useEffect(() => {
    if (!doc) return
    for (const n of notes) {
      if (n.rects.length || !n.mine || tried.current.has(n.ann_id)) continue
      tried.current.add(n.ann_id)
      ;(async () => {
        for (const i of [n.page_index, n.page_index + 1, n.page_index - 1]) {
          const page = pages.current.get(i)
          if (!page) continue
          const place = await locate(page, n.text)
          if (place) { onPlace(n.ann_id, { ...place, page_label: String(i + 1) }); return }
        }
        onPlace(n.ann_id, null)
      })()
    }
  }, [doc, notes, onPlace])

  const byPage = useMemo(() => {
    const m = new Map<number, ReaderNote[]>()
    for (const n of notes) if (n.rects.length) m.set(n.page_index, [...(m.get(n.page_index) ?? []), n])
    return m
  }, [notes])
  // the gaps of each page: under the last line of the words of each note written in the page
  const gapsOf = useCallback((i: number): Gap[] => !gap ? [] : (byPage.get(i) ?? []).filter((n) => n.inline)
    .map((n) => ({ key: n.ann_id, y: Math.min(...n.rects.map((r) => r[1])) - 1, node: gap(n) })), [byPage, gap])

  // a place on a page (px from its top) → where it is in the scrolled content, gaps above it included
  const yIn = useCallback((i: number, px: number): number | null => {
    const box = scrollRef.current?.querySelector<HTMLElement>(`[data-pagebox="${i}"]`)
    if (!box) return null
    let y = box.offsetTop
    for (const node of Array.from(box.children) as HTMLElement[]) {
      if (node.classList.contains('rd-slice')) {
        const from = Number(node.dataset.from), to = Number(node.dataset.to)
        if (px < to) return y + Math.max(0, px - from)
        y += to - from
      } else if (node.classList.contains('rd-gap')) y += node.offsetHeight
    }
    return y
  }, [])

  // a quoted passage: find it, scroll to it, light it for a few seconds
  useEffect(() => {
    if (!flash || !doc) return
    let gone = false
    ;(async () => {
      for (const i of [flash.page, flash.page + 1, flash.page - 1, ...Array.from({ length: views.length }, (_, k) => k)]) {
        const page = pages.current.get(i)
        if (!page) continue
        const place = await locate(page, flash.quote)
        if (!place || gone) continue
        setLit(place)
        const v = views[i]
        const y = v ? yIn(i, (v[3] - Math.max(...place.rects.map((r) => r[3]))) * scale) : null
        if (y != null && scrollRef.current) scrollRef.current.scrollTo({ top: y - 140, behavior: 'smooth' })
        setTimeout(() => { if (!gone) setLit(null) }, 4000)
        return
      }
    })()
    return () => { gone = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flash, doc])

  // scroll to a note's place
  useEffect(() => {
    if (!scrollTo || !scrollRef.current) return
    const n = notes.find((x) => x.ann_id === scrollTo.annId)
    const v = n && views[n.page_index]
    if (!n || !v) return
    const y = yIn(n.page_index, n.rects.length ? (v[3] - Math.max(...n.rects.map((r) => r[3]))) * scale : 0)
    if (y != null) scrollRef.current.scrollTo({ top: y - 140, behavior: 'smooth' })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollTo])

  // which page is in view
  const onScroll = useCallback(() => {
    const box = scrollRef.current
    if (!box) return
    const mid = box.scrollTop + box.clientHeight / 3
    let current = 0
    box.querySelectorAll<HTMLElement>('[data-pagebox]').forEach((el) => { if (el.offsetTop <= mid) current = Number(el.dataset.pagebox) })
    onPage(current + 1)
  }, [onPage])

  // words selected: what to do with them
  const onMouseUp = useCallback((e: React.MouseEvent) => {
    const box = scrollRef.current
    if (!box) return
    const sel = window.getSelection()
    if (sel && !sel.isCollapsed && sel.rangeCount) {
      const range = sel.getRangeAt(0)
      const pageEl = (range.startContainer.parentElement)?.closest<HTMLElement>('[data-page]')
      const text = sel.toString().replace(/\s+/g, ' ').trim()
      if (!pageEl || text.length < 2) return
      const i = Number(pageEl.dataset.page)
      const page = pages.current.get(i)
      if (!page) return
      const place = selectionPlace(range, pageEl, page.getViewport({ scale }), i)
      if (!place) return
      const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0)
      const last = rects[rects.length - 1] ?? range.getBoundingClientRect()
      const b = box.getBoundingClientRect()
      const at: Picked = { text, place: { ...place, page_label: String(i + 1) }, x: last.left - b.left + box.scrollLeft, y: last.bottom - b.top + box.scrollTop + 8 }
      onPick(at)
      textOffset(page, text).then((off) => {
        if (off != null) onPick({ ...at, place: { ...at.place, sort_index: sortIndex(i, off, Number(place.sort_index?.split('|')[2] ?? 0)) } })
      }).catch(() => {})
      return
    }
    // a click: on a highlight?
    const pageEl = (e.target as HTMLElement).closest<HTMLElement>('[data-page]')
    if (!pageEl) { if (picked) onPick(null); return }
    const i = Number(pageEl.dataset.page)
    const v = views[i]
    const r = pageEl.getBoundingClientRect()
    const x = (e.clientX - r.left) / scale + v[0]
    const y = v[3] - (e.clientY - r.top) / scale
    const hit = (byPage.get(i) ?? []).find((n) => n.rects.some((q) => x >= q[0] - 1 && x <= q[2] + 1 && y >= q[1] - 1 && y <= q[3] + 1))
    if (hit) onNote(hit.ann_id)
    else if (picked) onPick(null)
  }, [byPage, onNote, onPick, picked, scale, views])

  return (
    <div className={`rd-scroll${margin ? ' with-margin' : ''}`} ref={scrollRef} onScroll={onScroll} onMouseUp={onMouseUp}>
      <div className="rd-hint">Select words to highlight them, write a note or ask an agent about them. The + left of a line opens the page there, to write in.</div>
      {!doc && <div className="rd-loading">{fetching?.state === 'fetching'
        ? `Downloading the PDF from where it is published${fetching.total ? `: ${(fetching.loaded / 1e6).toFixed(1)} of ${(fetching.total / 1e6).toFixed(1)} MB` : '…'}`
        : 'Opening the PDF…'}</div>}
      {doc && views.map((v, i) => (
        <PdfPage key={i} index={i} page={pages.current.get(i)!} view={v} scale={scale} root={scrollRef}
          notes={byPage.get(i) ?? []} focusId={focusId} dimmed={dimmed} lit={lit?.page_index === i ? lit.rects : undefined}
          margin={margin && ((at: At) => margin(i, byPage.get(i) ?? [], v, at))} gaps={gapsOf(i)}
          onGapAt={onGapAt && ((line: Line) => onGapAt(i, line))} />
      ))}
      {picked && <div className="rd-anchor" style={{ left: picked.x, top: picked.y }} data-picked="" />}
    </div>
  )
}

/** A gap opened in a page: where the page is cut (PDF points from the bottom), and what is written in it. */
export interface Gap { key: string; y: number; node: React.ReactNode }

/** Page height (px from its top) → where that is in the page's box, gaps above it included. */
export type At = (px: number) => number

function PdfPage({ index, page, view, scale, root, notes, focusId, dimmed, lit, margin, gaps, onGapAt }: {
  index: number
  page: PDFPageProxy
  view: View
  scale: number
  root: React.RefObject<HTMLDivElement | null>
  notes: ReaderNote[]
  focusId: string | null
  dimmed: Set<string>
  lit?: number[][]
  margin?: (at: At) => React.ReactNode
  gaps: Gap[]
  /** Open a gap under this line of the page. */
  onGapAt?: (line: Line) => void
}) {
  const el = useRef<HTMLDivElement>(null)
  const master = useRef<HTMLCanvasElement | null>(null)
  const [drawn, setDrawn] = useState(0)
  const [seen, setSeen] = useState(false)
  const w = (view[2] - view[0]) * scale
  const h = (view[3] - view[1]) * scale

  // the page is cut at each gap: slices of it, top to bottom, with a gap after all but the last
  const cuts = useMemo(() => {
    const byY = new Map<number, Gap[]>()
    for (const g of gaps) {
      const px = Math.round(Math.max(8, Math.min(h - 8, (view[3] - g.y) * scale)))
      byY.set(px, [...(byY.get(px) ?? []), g])
    }
    return [...byY.entries()].sort((a, b) => a[0] - b[0])
  }, [gaps, view, scale, h])
  const bounds = useMemo(() => [0, ...cuts.map(([px]) => px), h], [cuts, h])
  const shape = bounds.join(',')

  // how tall each gap is now (they grow as they are written in)
  const gapEls = useRef<(HTMLDivElement | null)[]>([])
  const [gapH, setGapH] = useState<number[]>([])
  useEffect(() => {
    const ro = new ResizeObserver(() => setGapH(gapEls.current.slice(0, cuts.length).map((g) => g?.offsetHeight ?? 0)))
    gapEls.current.slice(0, cuts.length).forEach((g) => g && ro.observe(g))
    return () => ro.disconnect()
  }, [cuts.length, shape])
  const at: At = useCallback((px: number) => px + cuts.reduce((sum, [c], k) => sum + (c <= px ? gapH[k] ?? 0 : 0), 0), [cuts, gapH])

  useEffect(() => {
    if (!el.current) return
    const io = new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting)) setSeen(true) },
      { root: root.current, rootMargin: '800px 0px' })
    io.observe(el.current)
    return () => io.disconnect()
  }, [root])

  // the page is drawn once, off screen; each slice shows its part of it
  useEffect(() => {
    if (!seen) return
    const viewport = page.getViewport({ scale })
    const dpr = window.devicePixelRatio || 1
    const c = document.createElement('canvas')
    c.width = Math.floor(viewport.width * dpr); c.height = Math.floor(viewport.height * dpr)
    const ctx = c.getContext('2d')
    if (!ctx) return
    let task: RenderTask | null = page.render({ canvasContext: ctx, viewport, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined })
    task.promise.then(() => { master.current = c; setDrawn((d) => d + 1) }).catch(() => {})
    return () => { task?.cancel(); task = null }
  }, [seen, page, scale])

  // lines of the page, for the gutter: where a gap can open
  const [lines, setLines] = useState<Line[]>([])
  useEffect(() => { if (seen && onGapAt) pageLines(page).then(setLines).catch(() => {}) }, [seen, page, onGapAt])

  return (
    <div className="rd-page" data-pagebox={index} ref={el} style={{ width: w, ['--scale-factor' as string]: scale }}>
      {bounds.slice(0, -1).map((from, k) => {
        const to = bounds[k + 1]
        return (
          <Fragment key={`${k}:${from}`}>
            <Slice index={index} page={page} view={view} scale={scale} w={w} h={h} from={from} to={to} seen={seen}
              master={master} drawn={drawn} notes={notes} focusId={focusId} dimmed={dimmed} lit={lit}
              lines={lines} onGapAt={onGapAt} />
            {k < cuts.length && (
              <div className="rd-gap" ref={(g) => { gapEls.current[k] = g }} onMouseUp={(e) => e.stopPropagation()}>
                {cuts[k][1].map((g) => <Fragment key={g.key}>{g.node}</Fragment>)}
              </div>
            )}
          </Fragment>
        )
      })}
      <div className="rd-pnum">{index + 1}</div>
      {margin && <div className="rd-margin" style={{ left: w + 20 }}>{margin(at)}</div>}
    </div>
  )
}

/** One slice of a page, between two gaps: its part of the drawing, the words to select, the highlights. */
function Slice({ index, page, view, scale, w, h, from, to, seen, master, drawn, notes, focusId, dimmed, lit, lines, onGapAt }: {
  index: number; page: PDFPageProxy; view: View; scale: number; w: number; h: number; from: number; to: number; seen: boolean
  master: React.RefObject<HTMLCanvasElement | null>; drawn: number
  notes: ReaderNote[]; focusId: string | null; dimmed: Set<string>; lit?: number[][]
  lines: Line[]; onGapAt?: (line: Line) => void
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const text = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<Line | null>(null)

  useEffect(() => {
    const src = master.current
    const c = canvas.current
    if (!src || !c) return
    const k = src.height / h
    c.width = src.width
    c.height = Math.max(1, Math.round((to - from) * k))
    c.getContext('2d')?.drawImage(src, 0, Math.round(from * k), src.width, c.height, 0, 0, c.width, c.height)
  }, [drawn, from, to, h, master])

  useEffect(() => {
    if (!seen || !text.current) return
    const viewport = page.getViewport({ scale })
    const layer = text.current
    layer.replaceChildren()
    const tl = new pdfjs.TextLayer({ textContentSource: page.streamTextContent(), container: layer, viewport })
    tl.render().catch(() => {})
    return () => tl.cancel()
  }, [seen, page, scale])

  // the gutter: the line nearest the pointer, in this slice
  const onGutter = (e: React.MouseEvent) => {
    const r = e.currentTarget.getBoundingClientRect()
    const px = from + (e.clientY - r.top)
    let best: Line | null = null
    for (const l of lines) {
      const bottom = (view[3] - l.y) * scale
      if (bottom <= from + 4 || bottom >= to - 4) continue
      if (!best || Math.abs(bottom - px) < Math.abs((view[3] - best.y) * scale - px)) best = l
    }
    setHover(best && Math.abs((view[3] - best.y) * scale - px) < 30 ? best : null)
  }
  const hoverPx = hover ? (view[3] - hover.y) * scale - from : 0

  return (
    <div className="rd-slice" data-from={from} data-to={to} style={{ width: w, height: to - from }}>
      <div className="rd-clip">
      <div className="rd-sheet" data-page={index} style={{ width: w, height: h, transform: `translateY(${-from}px)` }}>
        <canvas ref={canvas} style={{ position: 'absolute', left: 0, top: from, width: w, height: to - from }} />
        <div className="rd-marks" aria-hidden="true">
          {notes.map((n) => (n.inline && n.ann_id !== focusId ? [] : untangle(n.rects as Box[])).map((r, k) => {
            const dim = dimmed.has(n.ann_id)
            const sug = n.state === 'suggested'
            const focus = n.ann_id === focusId
            return (
              <span key={`${n.ann_id}-${k}`} className={`rd-mark${sug ? ' sug' : ''}${n.ann_id === focusId ? ' focus' : ''}${dim ? ' dim' : ''}`}
                style={{
                  left: (r[0] - view[0]) * scale, top: (view[3] - r[3]) * scale,
                  width: (r[2] - r[0]) * scale, height: (r[3] - r[1]) * scale,
                  background: dim ? 'rgba(120,128,145,.12)' : rgba(n.color, focus ? (sug ? 0.3 : 0.6) : sug ? 0.12 : 0.38),
                  borderBottom: sug && !dim ? `2px dashed ${n.color}` : undefined,
                }} />
            )
          }))}
          {(lit ?? []).map((r, k) => (
            <span key={`lit-${k}`} className="rd-mark rd-flash" style={{ left: (r[0] - view[0]) * scale - 2, top: (view[3] - r[3]) * scale - 2,
              width: (r[2] - r[0]) * scale + 4, height: (r[3] - r[1]) * scale + 4 }} />
          ))}
        </div>
        <div className="textLayer rd-text" ref={text} />
      </div>
      </div>
      {onGapAt && (
        <div className="rd-gutter" onMouseMove={onGutter} onMouseLeave={() => setHover(null)}
          onMouseUp={(e) => e.stopPropagation()}>
          {hover && (
            <>
              <span className="rd-gutline" style={{ top: hoverPx, width: w }} aria-hidden="true" />
              <button className="rd-gutbtn" style={{ top: hoverPx }} title="Open a gap here, to write in"
                aria-label="Open a gap under this line" onClick={() => { onGapAt(hover); setHover(null) }}>+</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** Where words are on a PDF page, in PDF points (origin at the bottom left), the way Zotero keeps them:
 *  a passage quoted by an agent is found again in the page's text; a selection becomes rectangles. */
import type { PDFPageProxy, PageViewport } from 'pdfjs-dist'
import type { TextItem } from 'pdfjs-dist/types/src/display/api'
import type { NotePlace } from '../../types'

/** Letters and digits only, lower case: a passage matches across line breaks, hyphens and ligatures. */
export function fold(text: string): string {
  return Array.from(text.normalize('NFKC').toLowerCase()).filter((c) => /[\p{L}\p{N}]/u.test(c)).join('')
}

type Rect = [number, number, number, number]

/** Rectangles on one line side by side become one. */
function mergeLines(rects: Rect[]): Rect[] {
  const out: Rect[] = []
  for (const r of [...rects].sort((a, b) => b[3] - a[3] || a[0] - b[0])) {
    const last = out[out.length - 1]
    const h = r[3] - r[1]
    if (last && Math.abs(last[1] - r[1]) < h * 0.5 && Math.abs(last[3] - r[3]) < h * 0.5 && r[0] - last[2] < h * 1.5) {
      last[0] = Math.min(last[0], r[0]); last[1] = Math.min(last[1], r[1])
      last[2] = Math.max(last[2], r[2]); last[3] = Math.max(last[3], r[3])
    } else out.push([...r])
  }
  return out.map((r) => r.map((x) => Math.round(x * 1000) / 1000) as Rect)
}

/** Zotero's order of notes: page | position in the page's text | distance from the top. */
export function sortIndex(pageIndex: number, offset: number, top: number): string {
  const pad = (n: number, w: number) => String(Math.max(0, Math.min(Math.round(n), 10 ** w - 1))).padStart(w, '0')
  return `${pad(pageIndex, 5)}|${pad(offset, 6)}|${pad(top, 5)}`
}

interface Folded { text: string; at: { item: number; char: number }[]; items: TextItem[]; offsets: number[] }

const cache = new WeakMap<PDFPageProxy, Promise<Folded>>()

/** The page's text, folded, knowing which item and character each folded character came from. */
function folded(page: PDFPageProxy): Promise<Folded> {
  let p = cache.get(page)
  if (!p) {
    p = page.getTextContent().then((tc) => {
      const items = tc.items.filter((x): x is TextItem => 'str' in x)
      const at: Folded['at'] = []
      const offsets: number[] = []
      let text = ''
      let raw = 0
      items.forEach((it, i) => {
        offsets.push(raw)
        Array.from(it.str).forEach((ch, j) => {
          const f = fold(ch)
          for (const c of f) { text += c; at.push({ item: i, char: j }) }
        })
        raw += it.str.length + 1
      })
      return { text, at, items, offsets }
    })
    cache.set(page, p)
  }
  return p
}

function itemRect(it: TextItem, from: number, to: number): Rect {
  const [, , c, d, e, f] = it.transform
  const size = Math.hypot(c, d) || it.height || 10
  const len = Math.max(1, Array.from(it.str).length)
  const x1 = e + (it.width * from) / len
  const x2 = e + (it.width * to) / len
  return [x1, f - size * 0.22, x2, f + size * 0.82]
}

/** Where a quoted passage is on the page, or null when the page does not have it. */
export async function locate(page: PDFPageProxy, quote: string): Promise<NotePlace | null> {
  const q = fold(quote)
  if (q.length < 8) return null
  const t = await folded(page)
  const start = t.text.indexOf(q)
  if (start < 0) return null
  const end = start + q.length - 1
  const first = t.at[start]
  const last = t.at[end]
  const rects: Rect[] = []
  for (let i = first.item; i <= last.item; i++) {
    const it = t.items[i]
    const len = Array.from(it.str).length
    const from = i === first.item ? first.char : 0
    const to = i === last.item ? last.char + 1 : len
    if (to > from && it.str.trim()) rects.push(itemRect(it, from, to))
  }
  if (!rects.length) return null
  const merged = mergeLines(rects)
  const [, , , h] = page.view
  const top = h - Math.max(...merged.map((r) => r[3]))
  return { page_index: page.pageNumber - 1, rects: merged, sort_index: sortIndex(page.pageNumber - 1, t.offsets[first.item] + first.char, top) }
}

/** A selection on one page as rectangles in PDF points (the selection's start page only). */
export function selectionPlace(range: Range, pageEl: HTMLElement, viewport: PageViewport, pageIndex: number): NotePlace | null {
  const box = pageEl.getBoundingClientRect()
  const rects: Rect[] = []
  for (const r of Array.from(range.getClientRects())) {
    if (r.width < 1 || r.height < 1) continue
    // only what lies on this page
    const x1 = Math.max(r.left, box.left) - box.left
    const x2 = Math.min(r.right, box.right) - box.left
    const y1 = Math.max(r.top, box.top) - box.top
    const y2 = Math.min(r.bottom, box.bottom) - box.top
    if (x2 - x1 < 1 || y2 - y1 < 1) continue
    const [px1, py1] = viewport.convertToPdfPoint(x1, y2)
    const [px2, py2] = viewport.convertToPdfPoint(x2, y1)
    rects.push([Math.min(px1, px2), Math.min(py1, py2), Math.max(px1, px2), Math.max(py1, py2)])
  }
  if (!rects.length) return null
  const merged = mergeLines(rects)
  const pageHeight = viewport.viewBox[3] - viewport.viewBox[1]
  const top = pageHeight - Math.max(...merged.map((r) => r[3]))
  return { page_index: pageIndex, rects: merged, sort_index: sortIndex(pageIndex, 0, top) }
}

/** The offset of a passage in the page's text (for Zotero's order of notes), when it can be found. */
export async function textOffset(page: PDFPageProxy, quote: string): Promise<number | null> {
  const t = await folded(page)
  const i = t.text.indexOf(fold(quote))
  if (i < 0) return null
  const a = t.at[i]
  return t.offsets[a.item] + a.char
}

/** A line of a page: its words, where they are (PDF points), and its bottom (where a gap under it opens). */
export interface Line { text: string; rect: Rect; y: number; offset: number }

const linesCache = new WeakMap<PDFPageProxy, Promise<Line[]>>()

/** The page's lines of text, top to bottom. */
export function pageLines(page: PDFPageProxy): Promise<Line[]> {
  let p = linesCache.get(page)
  if (!p) {
    p = folded(page).then((t) => {
      const lines: Line[] = []
      t.items.forEach((it, i) => {
        if (!it.str.trim()) return
        const r = itemRect(it, 0, Math.max(1, Array.from(it.str).length))
        const h = r[3] - r[1]
        const same = lines.find((l) => Math.abs(l.rect[1] - r[1]) < h * 0.5 && Math.abs(l.rect[3] - r[3]) < h * 0.5)
        if (same) {
          same.text += (r[0] > same.rect[2] ? ' ' : '') + it.str
          same.rect = [Math.min(same.rect[0], r[0]), Math.min(same.rect[1], r[1]), Math.max(same.rect[2], r[2]), Math.max(same.rect[3], r[3])]
          same.y = same.rect[1]
        } else lines.push({ text: it.str, rect: r, y: r[1], offset: t.offsets[i] })
      })
      return lines.filter((l) => l.text.trim().length > 1).sort((a, b) => b.rect[3] - a.rect[3])
    })
    linesCache.set(page, p)
  }
  return p
}

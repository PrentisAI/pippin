/** A piece of a paper in a note: the passage cut out of its page as it is printed (its type, its formulas), with
 *  where it comes from, one click from that page. Pieces from one paper or several sit in a note with your words
 *  between them; dragged by their handle to another place, or set side by side to compare. */
import { Node, mergeAttributes, NodeViewWrapper, ReactNodeViewRenderer, type ReactNodeViewProps } from '@tiptap/react'
import { useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { api } from '../../api/client'
import type { NbClip } from '../../types'
import { readPaper } from '../reader/open'

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc

const docs = new Map<string, Promise<PDFDocumentProxy>>()
const cuts = new Map<string, Promise<string>>()

/** The passage cut out of its page, as an image (cached for the visit). */
function cutOut(paper: string, space: string, page: number, rects: number[][]): Promise<string> {
  const key = `${paper}|${page}|${JSON.stringify(rects)}`
  let p = cuts.get(key)
  if (!p) {
    let d = docs.get(paper)
    if (!d) {
      d = pdfjs.getDocument({ url: api.reader.pdfUrl(paper, space), withCredentials: true }).promise
      docs.set(paper, d)
      d.catch(() => docs.delete(paper))
    }
    p = d.then(async (doc) => {
      const pg = await doc.getPage(page + 1)
      const scale = 2.5
      const vp = pg.getViewport({ scale })
      const c = document.createElement('canvas')
      c.width = Math.floor(vp.width); c.height = Math.floor(vp.height)
      await pg.render({ canvasContext: c.getContext('2d')!, viewport: vp }).promise
      // the passage's box, with a little room round it (PDF points: y grows upwards)
      const x1 = Math.min(...rects.map((r) => r[0])) - 4, x2 = Math.max(...rects.map((r) => r[2])) + 4
      const y1 = Math.min(...rects.map((r) => r[1])) - 3, y2 = Math.max(...rects.map((r) => r[3])) + 3
      const [ax, ay] = vp.convertToViewportPoint(x1, y2)
      const [bx, by] = vp.convertToViewportPoint(x2, y1)
      const out = document.createElement('canvas')
      out.width = Math.max(1, Math.round(bx - ax)); out.height = Math.max(1, Math.round(by - ay))
      out.getContext('2d')!.drawImage(c, ax, ay, out.width, out.height, 0, 0, out.width, out.height)
      return out.toDataURL('image/png')
    })
    cuts.set(key, p)
    p.catch(() => cuts.delete(key))
  }
  return p
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** A clip as it goes into a note: the piece of the page with your words under it, then room to write on. */
export function pieceHtml(c: NbClip): string {
  return `<div data-piece="" data-paper="${esc(c.paper_id)}" data-space="${esc(c.space || 'global')}" data-page="${c.page_index}" `
    + `data-rects="${esc(JSON.stringify(c.rects))}" data-quote="${esc(c.quote)}" data-title="${esc(c.paper_title || c.paper_id)}" `
    + `data-words="${esc(c.words || '')}"></div><p></p>`
}

export const PaperPiece = Node.create({
  name: 'paperPiece',
  group: 'block',
  atom: true,
  draggable: true,
  addAttributes() {
    const data = (name: string, parse: (v: string | null) => unknown = (v) => v ?? '') => ({
      default: null, parseHTML: (el: HTMLElement) => parse(el.getAttribute(`data-${name}`)),
      renderHTML: (attrs: Record<string, unknown>) => ({ [`data-${name}`]: typeof attrs[name] === 'string' ? attrs[name] : JSON.stringify(attrs[name]) }),
    })
    return {
      paper: data('paper'), space: data('space'), title: data('title'), quote: data('quote'), words: data('words'),
      page: data('page', (v) => Number(v ?? 0)),
      rects: data('rects', (v) => { try { return JSON.parse(v ?? '[]') } catch { return [] } }),
      side: data('side', (v) => v === 'true'),
    }
  },
  parseHTML() { return [{ tag: 'div[data-piece]' }] },
  renderHTML({ HTMLAttributes }) { return ['div', mergeAttributes({ 'data-piece': '' }, HTMLAttributes)] },
  renderText({ node }) {
    return `“${node.attrs.quote}” (${node.attrs.title}, p. ${Number(node.attrs.page) + 1})${node.attrs.words ? `\n${node.attrs.words}` : ''}`
  },
  addNodeView() { return ReactNodeViewRenderer(PieceView) },
})

function PieceView({ node, updateAttributes, deleteNode, selected, editor, getPos }: ReactNodeViewProps) {
  const a = node.attrs as { paper: string; space: string; title: string; quote: string; page: number; rects: number[][]; side: boolean; words: string }
  const [img, setImg] = useState<string | null>(null)
  const [words, setWords] = useState(a.words || '')
  const box = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { const t = box.current; if (t) { t.style.height = 'auto'; t.style.height = `${t.scrollHeight}px` } }, [words])
  // side by side: with the next piece (an empty line between them goes), or alone at half the width
  const sideBySide = () => {
    const pos = getPos()
    if (a.side || typeof pos !== 'number') { updateAttributes({ side: !a.side }); return }
    const { state } = editor
    const tr = state.tr
    const after = pos + node.nodeSize
    let next = state.doc.nodeAt(after)
    if (next?.type.name === 'paragraph' && next.content.size === 0 && state.doc.nodeAt(after + next.nodeSize)?.type.name === 'paperPiece') {
      tr.delete(after, after + next.nodeSize)
      next = tr.doc.nodeAt(after)
    }
    tr.setNodeMarkup(pos, undefined, { ...node.attrs, side: true })
    if (next?.type.name === 'paperPiece') tr.setNodeMarkup(after, undefined, { ...next.attrs, side: true })
    editor.view.dispatch(tr)
  }
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let gone = false
    if (a.rects?.length) cutOut(a.paper, a.space || 'global', a.page, a.rects).then((u) => { if (!gone) setImg(u) }).catch(() => { if (!gone) setFailed(true) })
    return () => { gone = true }
  }, [a.paper, a.space, a.page, a.rects])
  return (
    <NodeViewWrapper className={`nbp${a.side ? ' side' : ''}${selected ? ' sel' : ''}`} data-piece="">
      <div className="nbp-bar" contentEditable={false}>
        <span className="nbp-grip" data-drag-handle="" title="Drag to move it" aria-label="Drag to move it">⠿</span>
        <button type="button" onClick={() => readPaper({ paper: a.paper, space: a.space || 'global', quote: a.quote, page: a.page, from: 'Notebook' })}
          title="Open the paper at this passage">{a.title}, p. {a.page + 1}</button>
        <span className="nbp-tools">
          <button type="button" onClick={sideBySide} aria-pressed={a.side}
            title={a.side ? 'Back to the full width' : 'Beside the next piece, to compare them'}>{a.side ? 'Full width' : 'Side by side'}</button>
          <button type="button" onClick={() => deleteNode()} aria-label="Take the piece out of the note">✕</button>
        </span>
      </div>
      <div className="nbp-cut" contentEditable={false}>
        {img ? <img src={img} alt={a.quote} draggable={false} />
          : <blockquote className="nbp-text">{a.quote}{!failed && a.rects?.length ? <span className="lit-sub"> · cutting it out of the page…</span> : null}</blockquote>}
      </div>
      <textarea ref={box} className="nbp-words" rows={1} value={words} placeholder="Your words on it" aria-label="Your words on this piece"
        onChange={(e) => setWords(e.target.value)} onBlur={() => { if (words !== (a.words || '')) updateAttributes({ words }) }}
        onKeyDown={(e) => e.stopPropagation()} />
    </NodeViewWrapper>
  )
}

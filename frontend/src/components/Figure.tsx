/** A figure an agent made to try something out (a page of its own, with its script): shown in a sandboxed frame
 *  that may run its script and nothing else (no network, no cookies, nothing of the console's). Its source is a
 *  click away, to read or take into your own work. */
import { useEffect, useRef, useState } from 'react'

// what the frame may do: run its own script and styles, show its own images; nothing from the network
const LOCK = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:">`

export function Figure({ src, title, by = 'Pippin', what = 'from the paper' }: { src: string; title: string; by?: string; what?: string }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(240)
  const [page, setPage] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    let gone = false
    fetch(src, { credentials: 'include' }).then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => { if (!gone) setPage(t) }).catch(() => { if (!gone) setPage('') })
    return () => { gone = true }
  }, [src])
  // the page says how tall it is (a line added when it is served)
  useEffect(() => {
    const on = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow) return
      const h = Number((e.data as { figureHeight?: number } | null)?.figureHeight)
      if (h > 0) setHeight(Math.min(520, Math.ceil(h) + 2))
    }
    window.addEventListener('message', on)
    return () => window.removeEventListener('message', on)
  }, [])
  // the source as the agent wrote it (without the line that reports the height)
  const source = (page ?? '').replace(/<script>\(function\(\)\{function t\(\)\{parent\.postMessage[\s\S]*$/, '')
  if (page === '') return <p className="rd-sub">The figure could not be loaded.</p>
  return (
    <div className="fig">
      {page === null ? <div className="fig-frame fig-wait" style={{ height }}>Loading the figure…</div>
        : <iframe ref={frame} className="fig-frame" srcDoc={LOCK + page} title={title} sandbox="allow-scripts" style={{ height }} />}
      <div className="fig-row">
        <span className="rd-sub">Drawn by {by}{what && by === 'Pippin' ? ` ${what}` : ''}; what it assumes is marked in it.</span>
        <button className="rd-gn-btn" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? 'Hide the source' : 'View the source'}</button>
        {open && page && <button className="rd-gn-btn" onClick={() => navigator.clipboard?.writeText(source)}>Copy</button>}
      </div>
      {open && <pre className="fig-src">{source}</pre>}
    </div>
  )
}

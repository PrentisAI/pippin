/** The memory panel on its own page, for the entry points that show it: the browser extension's side panel and
 *  the desktop app. Drop a link, a selection or a file on it to save it; the entry point tells it when what you
 *  are reading changes (a `aurora:refresh` message). */
import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { MemSource } from '../../types'
import { MemoryPanel } from './MemoryPanel'

export function PanelPage() {
  const [refresh, setRefresh] = useState(0)
  const [over, setOver] = useState(false)
  const [toast, setToast] = useState<{ head: string; body: string } | null>(null)
  const timer = useRef<number | null>(null)
  const via = new URLSearchParams(window.location.search).get('via') || 'orb'

  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.data && typeof e.data === 'object' && (e.data as { type?: string }).type === 'aurora:refresh') setRefresh((x) => x + 1)
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [])

  const follow = async (s: MemSource, what: string) => {
    setToast({ head: 'Swallowed · fetching it and keeping a snapshot', body: what })
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1500))
      const x = await api.memory.source(s.source_id)
      if (x.state !== 'pending') {
        setToast({ head: x.state === 'done' ? 'Sorted, without bothering you' : 'Could not save it', body: x.summary ?? x.error })
        setRefresh((n) => n + 1)
        if (timer.current) window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => setToast(null), 7000)
        return
      }
    }
  }

  const drop = async (e: React.DragEvent) => {
    e.preventDefault(); setOver(false)
    try {
      const file = e.dataTransfer.files?.[0]
      if (file) { follow(await api.memory.captureFile(file, via), file.name); return }
      const uri = (e.dataTransfer.getData('text/uri-list') || '').split('\n').find((l) => l && !l.startsWith('#'))
      const text = e.dataTransfer.getData('text/plain')
      if (uri) { follow(await api.memory.capture({ url: uri.trim(), via }), uri.trim()); return }
      if (text.trim()) {
        const isUrl = /^https?:\/\/\S+$/.test(text.trim())
        follow(await api.memory.capture(isUrl ? { url: text.trim(), via } : { text, selection: text, via }), text.slice(0, 80))
      }
    } catch (err) {
      setToast({ head: 'Could not save it', body: (err as Error).message })
    }
  }

  return (
    <div className={`mem-standalone${over ? ' over' : ''}`} onDragOver={(e) => { e.preventDefault(); setOver(true) }}
      onDragLeave={() => setOver(false)} onDrop={drop}>
      <MemoryPanel refreshKey={refresh} />
      {over && <div className="mem-dropzone">Drop to save into your memory</div>}
      {toast && <div className="mem-toast" role="status"><b>{toast.head}</b><span>{toast.body}</span>
        <button className="link-btn" aria-label="Dismiss" onClick={() => setToast(null)}>×</button></div>}
    </div>
  )
}

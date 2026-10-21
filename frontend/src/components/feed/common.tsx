import { useCallback, useEffect, useRef, useState } from 'react'

/** Ask a project's front desk about an item: the project chat opens with it attached. */
export function askFrontDesk(campaignId: string, ref: unknown) {
  window.dispatchEvent(new CustomEvent('aurora:ask-frontdesk', { detail: { campaignId, ref } }))
}

export function ago(ts: number | null | undefined): string {
  if (!ts) return ''
  const m = Math.floor((Date.now() / 1000 - ts) / 60)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  if (m < 60 * 24) return `${Math.floor(m / 60)} h ago`
  const d = Math.floor(m / 60 / 24)
  if (d === 1) return 'yesterday'
  if (d < 7) return `${d} days ago`
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** How well an item fits, as five dots. */
export function Dots({ rel }: { rel: number }) {
  return (
    <span className="fd-dots" role="img" aria-label={`Fit ${rel} of 5`} title={`Fit ${rel} of 5`}>
      {[1, 2, 3, 4, 5].map((i) => <i key={i} className={i <= rel ? 'on' : ''} />)}
    </span>
  )
}

/** A button with a menu under it, closed by a click elsewhere or Escape. */
export function MenuButton({ label, className = 'btn-sm lit-btn', children, right = false, wide = false }: {
  label: React.ReactNode; className?: string; children: (close: () => void) => React.ReactNode; right?: boolean; wide?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const out = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', out)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', out); document.removeEventListener('keydown', esc) }
  }, [open])
  return (
    <span className="bs-rel" ref={ref}>
      <button className={className} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>{label} ▾</button>
      {open && <div className={`bs-menu${right ? ' right' : ''}${wide ? ' wide' : ''}`} role="menu">{children(() => setOpen(false))}</div>}
    </span>
  )
}

export interface Toast { text: string; action?: [string, () => void] }

/** A message at the bottom of the page for a few seconds, with an optional action (Undo, Open). */
export function useToast(): [Toast | null, (t: Toast | null) => void, React.ReactNode] {
  const [toast, setToast] = useState<Toast | null>(null)
  const timer = useRef<number | null>(null)
  const show = useCallback((t: Toast | null) => {
    setToast(t)
    if (timer.current) window.clearTimeout(timer.current)
    if (t) timer.current = window.setTimeout(() => setToast(null), 6000)
  }, [])
  const node = toast && (
    <div className="bs-toast" role="status">{toast.text}
      {toast.action && <button className="link-btn" style={{ color: 'inherit', textDecoration: 'underline' }}
        onClick={() => { toast.action![1](); show(null) }}>{toast.action[0]}</button>}
    </div>
  )
  return [toast, show, node]
}

export function initialsOf(name: string): string {
  return name.replace('@', '').split(/\s+/).filter(Boolean).map((x) => x[0]).join('').slice(0, 2).toUpperCase() || '?'
}

/** A followed person's place: ✓ followed, ! needs something, — cannot be followed. */
export function IdChip({ state, label, note }: { state: 'ok' | 'warn' | 'muted'; label: string; note?: string }) {
  return (
    <span className={`fd-idchip ${state}`} title={note || undefined}>
      <span className="mark">{state === 'ok' ? '✓' : state === 'warn' ? '!' : '—'}</span>{label}
    </span>
  )
}

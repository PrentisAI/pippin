import { useEffect, useRef, useState, type ReactNode } from 'react'

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

const svg = (path: ReactNode) => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{path}</svg>
)

export const PencilIcon = () => svg(<><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></>)
export const TrashIcon = () => svg(<><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6M14 11v6" /></>)
export const StopIcon = () => svg(<rect x="6" y="6" width="12" height="12" rx="1.5" />)
export const RerunIcon = () => svg(<><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 3v6h6" /></>)

// ---------------------------------------------------------------------------
// The "⋯" menu of a sidebar item
// ---------------------------------------------------------------------------

export interface MenuItem {
  label: string
  icon: ReactNode
  onSelect: () => void
  danger?: boolean
  /** Starts a new group (a divider above it). */
  divided?: boolean
}

export function ItemMenu({ items, label, trigger = '⋮', buttonClass = 'item-menu-btn', disabled = false }: {
  items: MenuItem[]
  label: string
  /** What the button shows (default ⋮). */
  trigger?: ReactNode
  buttonClass?: string
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  // fixed to the button, so a scrolling sidebar does not clip the menu
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 })
  const ref = useRef<HTMLDivElement>(null)

  const toggle = (e: React.MouseEvent<HTMLButtonElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const width = 230
    const height = 44 * items.length + 16
    setPos({
      left: Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8)),
      top: r.bottom + height + 8 > window.innerHeight ? Math.max(8, r.top - height - 4) : r.bottom + 4,
    })
    setOpen(!open)
  }

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  return (
    <div className={`item-menu${open ? ' open' : ''}`} ref={ref} onClick={(e) => e.stopPropagation()}>
      <button className={buttonClass} title={label} aria-label={label} onClick={toggle} disabled={disabled}>{trigger}</button>
      {open && (
        <div className="item-menu-pop" role="menu" style={{ top: pos.top, left: pos.left }}>
          {items.map((it) => (
            <div key={it.label}>
              {it.divided && <div className="item-menu-sep" />}
              <button role="menuitem" className={`item-menu-row${it.danger ? ' danger' : ''}`}
                onClick={() => { setOpen(false); it.onSelect() }}>
                <span className="item-menu-icon">{it.icon}</span>{it.label}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Renaming in place
// ---------------------------------------------------------------------------

/** A name being edited: Enter or leaving the field saves, Escape cancels. */
export function InlineRename({ value, onSave, onCancel }: {
  value: string
  onSave: (name: string) => void
  onCancel: () => void
}) {
  const [text, setText] = useState(value)
  const done = useRef(false)
  const finish = (save: boolean) => {
    if (done.current) return
    done.current = true
    const name = text.trim()
    if (save && name && name !== value) onSave(name)
    else onCancel()
  }
  return (
    <input className="inline-rename" autoFocus value={text} onChange={(e) => setText(e.target.value)}
      onFocus={(e) => e.target.select()} onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && !e.nativeEvent.isComposing) finish(true)
        if (e.key === 'Escape') finish(false)
      }}
      onBlur={() => finish(true)} />
  )
}

// ---------------------------------------------------------------------------
// Deleting, after confirmation
// ---------------------------------------------------------------------------

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`
}

export function ConfirmDelete({ kind, name, detail, blocked, diskUsage, onConfirm, onCancel }: {
  kind: 'project' | 'run'
  name: string
  /** What else goes with it (e.g. "Its 3 runs are removed too."). */
  detail?: string
  /** Why it cannot be deleted now (e.g. it is running). */
  blocked?: string
  /** Bytes its files take on disk. */
  diskUsage: () => Promise<number>
  onConfirm: (deleteFiles: boolean) => Promise<void>
  onCancel: () => void
}) {
  const [deleteFiles, setDeleteFiles] = useState(false)
  const [bytes, setBytes] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    diskUsage().then((b) => { if (live) setBytes(b) }).catch(() => {})
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', esc)
    return () => {
      live = false
      document.removeEventListener('keydown', esc)
    }
  }, [diskUsage, onCancel])

  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await onConfirm(deleteFiles)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onCancel}>
      <div className="modal" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">Delete {kind} “{name}”?</div>
        <div className="modal-body">
          <p>It is removed from AURORA{detail ? `. ${detail}` : '.'}</p>
          <label className="modal-check">
            <input type="checkbox" checked={deleteFiles} onChange={(e) => setDeleteFiles(e.target.checked)} />
            <span>
              Also delete its files on disk{bytes != null ? ` (${formatBytes(bytes)})` : ''}: the report, the
              workspace and the data. This cannot be undone.
            </span>
          </label>
          {!deleteFiles && <p className="modal-hint">Without this, the files stay where they are.</p>}
          {blocked && <p className="modal-error">{blocked}</p>}
          {error && <p className="modal-error">{error}</p>}
        </div>
        <div className="modal-actions">
          <button className="btn-sm" onClick={onCancel}>Cancel</button>
          <button className="btn-sm btn-reject" disabled={busy || !!blocked} onClick={confirm}>
            {busy ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  )
}

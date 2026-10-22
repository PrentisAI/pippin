import { createContext, Fragment, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, describeError } from '../api/client'

/** What a citation points to (from /literature/resolve). */
export interface CiteInfo {
  status: 'verified' | 'not_found' | 'mismatch' | 'unchecked' | 'unknown'
  key?: string
  title?: string
  byline?: string
  year?: number | null
  authors?: string[]
  abstract?: string
  doi?: string
  arxiv_id?: string
  url?: string
  pdf_url?: string
  pdf_file?: string
  in_project?: boolean
  used_in?: string[]
  // a [ref: ...] citation
  label?: string
  ref_id?: string
  reason?: string
  resolution?: '' | 'replaced' | 'removed' | 'have'
  replaced_with?: CiteInfo
  closest?: CiteInfo
}

const KEY = "[A-Z][A-Za-z\\-']{1,16}\\d{2}[a-z]?"
// [Behrens12], [Kuld16; Behrens12] and [ref: title; author; year; id]
const SPLIT = new RegExp(`(\\[${KEY}(?:\\s*[,;]\\s*${KEY})*\\]|\\[ref:\\s*[^\\]]+\\])`, 'g')
const IS_KEYS = new RegExp(`^\\[${KEY}(?:\\s*[,;]\\s*${KEY})*\\]$`)
const REF_BODY = /^\[ref:\s*([^\]]+)\]$/i

/** The keys and [ref: ...] bodies a text cites. */
export function citationsIn(text: string): { keys: string[]; refs: string[] } {
  const keys: string[] = []
  const refs: string[] = []
  for (const part of text.match(SPLIT) ?? []) {
    const m = REF_BODY.exec(part)
    if (m) refs.push(m[1].trim())
    else if (IS_KEYS.test(part)) keys.push(...part.slice(1, -1).split(/\s*[,;]\s*/))
  }
  return { keys: [...new Set(keys)], refs: [...new Set(refs)] }
}

interface Ctx {
  campaignId: string
  canEdit: boolean
  keys: Record<string, CiteInfo>
  refs: Record<string, CiteInfo>
  want: (keys: string[], refs: string[]) => void
  refresh: () => void
}

const CiteContext = createContext<Ctx | null>(null)

/** Looks up the citations shown inside it, a batch at a time, for one project (key it by the project). */
export function CiteProvider({ campaignId, canEdit = true, children }: { campaignId: string; canEdit?: boolean; children: React.ReactNode }) {
  const [keys, setKeys] = useState<Record<string, CiteInfo>>({})
  const [refs, setRefs] = useState<Record<string, CiteInfo>>({})
  const asked = useRef<{ keys: Set<string>; refs: Set<string> }>({ keys: new Set(), refs: new Set() })
  const queue = useRef<{ keys: Set<string>; refs: Set<string> }>({ keys: new Set(), refs: new Set() })
  const timer = useRef<number | null>(null)

  const flush = useCallback(() => {
    timer.current = null
    const k = [...queue.current.keys]
    const r = [...queue.current.refs]
    queue.current = { keys: new Set(), refs: new Set() }
    if (!k.length && !r.length) return
    api.literature.resolve(campaignId, k, r).then((got) => {
      setKeys((prev) => ({ ...prev, ...got.keys }))
      setRefs((prev) => ({ ...prev, ...got.refs }))
    }).catch(() => {})
  }, [campaignId])

  const want = useCallback((k: string[], r: string[]) => {
    let added = false
    for (const x of k) if (!asked.current.keys.has(x)) { asked.current.keys.add(x); queue.current.keys.add(x); added = true }
    for (const x of r) if (!asked.current.refs.has(x)) { asked.current.refs.add(x); queue.current.refs.add(x); added = true }
    if (added && timer.current === null) timer.current = window.setTimeout(flush, 60)
  }, [flush])

  const refresh = useCallback(() => {
    queue.current = { keys: new Set(asked.current.keys), refs: new Set(asked.current.refs) }
    flush()
  }, [flush])

  const value = useMemo(() => ({ campaignId, canEdit, keys, refs, want, refresh }), [campaignId, canEdit, keys, refs, want, refresh])
  return <CiteContext.Provider value={value}>{children}</CiteContext.Provider>
}

/** The citations of a text, resolved (undefined while unknown). */
export function useCitations(text: string): { keys: [string, CiteInfo | undefined][]; refs: [string, CiteInfo | undefined][] } {
  const ctx = useContext(CiteContext)
  const found = useMemo(() => citationsIn(text), [text])
  useEffect(() => { ctx?.want(found.keys, found.refs) }, [ctx, found])
  return {
    keys: found.keys.map((k) => [k, ctx?.keys[k]]),
    refs: found.refs.map((r) => [r, ctx?.refs[r]]),
  }
}

function surnameYear(info: CiteInfo | undefined, fallback: string): string {
  if (!info || info.status !== 'verified') return fallback
  const first = info.authors?.[0] ?? info.byline?.split(/[,·]/)[0] ?? ''
  const surname = first.includes(',') ? first.split(',')[0] : first.trim().split(/\s+/).pop() ?? ''
  return surname && info.year ? `${surname} ${info.year}` : info.key ?? fallback
}

function refLabel(body: string): string {
  const parts = body.split(';').map((p) => p.trim())
  const who = (parts[1] ?? '').split(/[ ,]/).filter(Boolean).pop() ?? ''
  const year = parts.find((p) => /^(19|20)\d\d$/.test(p)) ?? ''
  return who && year ? `${who} ${year}` : (parts[0] ?? body).slice(0, 40)
}

/** Text with its citations as chips: ✓ a real paper, ? one that could not be found. Click one to see it. */
export function CiteText({ text }: { text: string }) {
  const ctx = useContext(CiteContext)
  useCitations(text)
  if (!ctx) return <>{text}</>
  return (
    <>{text.split(SPLIT).map((part, i) => {
      const ref = REF_BODY.exec(part)
      if (ref) {
        const body = ref[1].trim()
        return <RefChip key={i} body={body} info={ctx.refs[body]} />
      }
      if (IS_KEYS.test(part)) {
        return <Fragment key={i}>{part.slice(1, -1).split(/\s*[,;]\s*/).map((k) => <KeyChip key={k} k={k} info={ctx.keys[k]} />)}</Fragment>
      }
      return <Fragment key={i}>{part}</Fragment>
    })}</>
  )
}

function KeyChip({ k, info }: { k: string; info: CiteInfo | undefined }) {
  const ok = info?.status === 'verified'
  return <Chip ok={ok ? true : info ? false : null} label={surnameYear(info, k)} info={info ?? { status: 'unknown', key: k }} body={k} />
}

function RefChip({ body, info }: { body: string; info: CiteInfo | undefined }) {
  if (info?.resolution === 'removed') return <span className="cite removed" title="Removed: it could not be found">{refLabel(body)}</span>
  const real = info?.resolution === 'replaced' ? info.replaced_with : info?.status === 'verified' ? info : undefined
  const ok = real ? true : info && info.status !== 'unchecked' ? false : null
  return <Chip ok={ok} label={real ? surnameYear(real, refLabel(body)) : refLabel(body)} info={info ?? { status: 'unchecked', label: body }} body={body} />
}

function Chip({ ok, label, info, body }: { ok: boolean | null; label: string; info: CiteInfo; body: string }) {
  const [open, setOpen] = useState<DOMRect | null>(null)
  const ref = useRef<HTMLButtonElement>(null)
  return (
    <>
      <button ref={ref} type="button" className={`cite ${ok === true ? 'v' : ok === false ? 'u' : 'p'}`}
        aria-haspopup="dialog" aria-expanded={!!open} title="Show what this reference points to"
        onClick={(e) => { e.stopPropagation(); setOpen(open ? null : e.currentTarget.getBoundingClientRect()) }}>
        {label} {ok === true ? '✓' : ok === false ? '?' : ''}
      </button>
      {open && createPortal(<CitePopover info={info} body={body} anchor={open} onClose={() => setOpen(null)} />, document.body)}
    </>
  )
}

const STATUS: Record<string, string> = {
  not_found: '? not found', mismatch: '? its identifier names another paper', unchecked: '? not checked yet',
  unknown: '? no such paper in this project',
}

function CitePopover({ info, body, anchor, onClose }: { info: CiteInfo; body: string; anchor: DOMRect; onClose: () => void }) {
  const ctx = useContext(CiteContext)
  const box = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const out = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) onClose() }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    const t = window.setTimeout(() => document.addEventListener('mousedown', out), 0)
    document.addEventListener('keydown', esc)
    window.addEventListener('scroll', onClose, true)
    return () => { window.clearTimeout(t); document.removeEventListener('mousedown', out); document.removeEventListener('keydown', esc); window.removeEventListener('scroll', onClose, true) }
  }, [onClose])
  if (!ctx) return null
  const real = info.resolution === 'replaced' ? info.replaced_with : info.status === 'verified' ? info : undefined
  const ident = real ? (real.doi ? `DOI ${real.doi}` : real.arxiv_id ? `arXiv:${real.arxiv_id}` : real.key ?? '') : ''
  const pdf = real ? (real.pdf_file ? `/api/campaigns/${ctx.campaignId}/files/raw?path=${encodeURIComponent(real.pdf_file)}` : real.pdf_url || '') : ''
  const width = Math.min(440, window.innerWidth - 32)
  const left = Math.max(16, Math.min(anchor.left, window.innerWidth - width - 16))
  const below = anchor.bottom + 8 + 320 < window.innerHeight
  const style: React.CSSProperties = below ? { top: anchor.bottom + 8, left, width } : { bottom: window.innerHeight - anchor.top + 8, left, width }
  const inLiterature = () => { window.dispatchEvent(new CustomEvent('aurora:open-project-tab', { detail: { campaignId: ctx.campaignId, tab: 'literature' } })); onClose() }
  const replace = async () => {
    if (!info.ref_id) return
    setBusy(true)
    setError(null)
    try { await api.literature.decide(ctx.campaignId, info.ref_id, 'replace'); ctx.refresh(); onClose() }
    catch (e) { setError(describeError(e)) } finally { setBusy(false) }
  }
  const words = (real?.abstract ?? '').split(' ')
  return (
    <div ref={box} className="cite-pop" role="dialog" aria-label="Reference" style={style}>
      <div className="cite-pop-head">
        <span className={`cite ${real ? 'v' : 'u'}`} style={{ cursor: 'default' }}>{real ? `✓ ${ident}` : STATUS[info.status] ?? '?'}</span>
        <button className="link-btn" aria-label="Close" onClick={onClose}>×</button>
      </div>
      {real ? (
        <>
          <div className="cite-pop-title">{real.title}</div>
          <div className="lit-sub">{real.byline}{real.key ? ` · ${real.key}` : ''}</div>
          {real.abstract && <div className="cite-pop-abs">{words.slice(0, 70).join(' ')}{words.length > 70 ? '…' : ''}</div>}
          <div className="lit-sub">
            {info.resolution === 'replaced' ? `Replaced the reference “${info.label ?? body}”, which could not be found. ` : ''}
            {real.used_in?.length ? `Used in ${[...new Set(real.used_in.map((u) => u.split(' (')[0]))].join(', ')}. ` : ''}
            {real.in_project ? 'In this project’s literature.' : 'Found in a search; not in the project’s literature yet.'}
          </div>
        </>
      ) : (
        <>
          <div className="cite-pop-title">{info.label ?? info.key ?? body}</div>
          <div className="cite-pop-abs">
            {info.status === 'not_found' ? 'No paper like this exists in Crossref, arXiv, OpenAlex or Semantic Scholar. The agent probably made it up; what it supports is treated as unsupported.'
              : info.status === 'mismatch' ? 'The DOI or arXiv ID it gives belongs to a different paper.'
              : info.status === 'unknown' ? 'This key names no paper this project found or holds: the agent may have invented it. What it supports is treated as unsupported.'
              : 'The indexes could not be reached when it was cited; it is checked again later.'}
            {info.reason && info.status !== 'not_found' ? ` (${info.reason})` : ''}
          </div>
          {info.closest && <div className="lit-sub">Closest real paper: {info.closest.title} — {info.closest.byline}</div>}
        </>
      )}
      {error && <div className="modal-error">{error}</div>}
      <div className="cite-pop-actions">
        {real && pdf && <a className="btn-sm lit-btn" href={pdf} target="_blank" rel="noreferrer">Open PDF</a>}
        {real && !pdf && (real.url || real.doi) && <a className="btn-sm lit-btn" href={real.doi ? `https://doi.org/${real.doi}` : real.url} target="_blank" rel="noreferrer">Open</a>}
        {!real && info.closest && info.ref_id && ctx.canEdit && !info.resolution && (
          <button className="btn-sm btn-approve" disabled={busy} onClick={replace}>Use the real one instead</button>
        )}
        <button className="btn-sm lit-btn" onClick={inLiterature}>In Literature</button>
      </div>
    </div>
  )
}

/** References in a text that could not be found and nobody has decided about yet. */
export function useFlagged(text: string): string[] {
  const { keys, refs } = useCitations(text)
  return [
    ...refs.filter(([, i]) => i && (i.status === 'not_found' || i.status === 'mismatch') && !i.resolution).map(([b, i]) => i?.label || refLabel(b)),
    ...keys.filter(([, i]) => i?.status === 'unknown').map(([k]) => k),
  ]
}

/** The popover of a [Key] citation shown outside CiteText (in server-rendered HTML). */
export function KeyPopover({ k, anchor, onClose }: { k: string; anchor: DOMRect; onClose: () => void }) {
  const ctx = useContext(CiteContext)
  useEffect(() => { ctx?.want([k], []) }, [ctx, k])
  const info = ctx?.keys[k]
  return createPortal(<CitePopover info={info ?? { status: 'unchecked', key: k }} body={k} anchor={anchor} onClose={onClose} />, document.body)
}

/** What these keys point to, once looked up: a label (e.g. "Behrens 2012") and whether the paper is real. */
export function useKeyLabels(keys: string[]): Record<string, { label: string; ok: boolean | null }> {
  const ctx = useContext(CiteContext)
  const joined = keys.join(',')
  useEffect(() => { if (joined) ctx?.want(joined.split(','), []) }, [ctx, joined])
  const out: Record<string, { label: string; ok: boolean | null }> = {}
  for (const k of keys) {
    const info = ctx?.keys[k]
    out[k] = { label: surnameYear(info, k), ok: info ? info.status === 'verified' : null }
  }
  return out
}

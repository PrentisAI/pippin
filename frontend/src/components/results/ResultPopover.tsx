import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, describeError } from '../../api/client'
import type { RunResult } from '../../types'

export function showValue(r: Pick<RunResult, 'value' | 'unit' | 'uncertainty'>): string {
  const v = typeof r.value === 'number' ? Number(r.value.toPrecision(6)).toString() : String(r.value)
  return v + (r.uncertainty != null ? ` ± ${Number(r.uncertainty.toPrecision(3))}` : '') + (r.unit ? ` ${r.unit}` : '')
}

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** Where a recorded result came from: the script (as it was when recorded), the command, the files, the job. */
export function ResultPopover({ result, campaignId, runId, anchor, onClose, superseded }: {
  result: RunResult | null; campaignId: string; runId: string; anchor: DOMRect; onClose: () => void; superseded?: RunResult | null
}) {
  const box = useRef<HTMLDivElement>(null)
  const [script, setScript] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    const out = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) onClose() }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    const t = window.setTimeout(() => document.addEventListener('mousedown', out), 0)
    document.addEventListener('keydown', esc)
    return () => { window.clearTimeout(t); document.removeEventListener('mousedown', out); document.removeEventListener('keydown', esc) }
  }, [onClose])
  const width = Math.min(500, window.innerWidth - 32)
  const left = Math.max(16, Math.min(anchor.left, window.innerWidth - width - 16))
  const below = anchor.bottom + 8 + 360 < window.innerHeight
  const style: React.CSSProperties = below ? { top: anchor.bottom + 8, left, width } : { bottom: window.innerHeight - anchor.top + 8, left, width }
  const raw = (path: string) => api.workspace.rawUrl(campaignId, runId, `workspace/${path}`)
  const showScript = async () => {
    if (!result?.script) return
    setError(null)
    try {
      const path = result.script.snapshot ?? `workspace/${result.script.path}`
      const f = await api.workspace.readFile(campaignId, runId, path)
      setScript(f.content ?? '(a binary file)')
    } catch (e) { setError(describeError(e)) }
  }
  const node = (
    <div ref={box} className="cite-pop res-pop" role="dialog" aria-label="Result" style={style}>
      <div className="cite-pop-head">
        <span className={`res-chip ${result ? '' : 'missing'}`} style={{ cursor: 'default' }}>{result ? result.id : '?'}</span>
        <button className="link-btn" aria-label="Close" onClick={onClose}>×</button>
      </div>
      {!result ? (
        <div className="cite-pop-abs">No result was recorded under this ID: the number it marks cannot be traced.</div>
      ) : (
        <>
          <div className="cite-pop-title">{result.name} = {showValue(result)}</div>
          {result.description && <div className="cite-pop-abs">{result.description}</div>}
          {superseded && <div className="res-warn">Recomputed later: the latest value is {showValue(superseded)} ({superseded.id}).</div>}
          <dl className="res-prov">
            {result.script && <><dt>Script</dt><dd>
              {result.script.exists ? <a href={raw(result.script.path)} target="_blank" rel="noreferrer"><code>{result.script.path}</code></a> : <code>{result.script.path}</code>}
              {result.script.snapshot && <> · <button className="link-btn" onClick={showScript}>as it was then</button></>}
              {!result.script.exists && <span className="res-warn"> (not found)</span>}</dd></>}
            {result.command && <><dt>Command</dt><dd><code className="res-cmd">{result.command}</code>{' '}
              <button className="link-btn" onClick={() => { navigator.clipboard?.writeText(result.command).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500) }).catch(() => {}) }}>{copied ? 'copied' : 'copy'}</button></dd></>}
            {result.inputs.length > 0 && <><dt>Read</dt><dd>{result.inputs.map((f) => <FileLink key={f.path} f={f} href={raw(f.path)} />)}</dd></>}
            {result.outputs.length > 0 && <><dt>Wrote</dt><dd>{result.outputs.map((f) => <FileLink key={f.path} f={f} href={raw(f.path)} />)}</dd></>}
            {result.job && <><dt>Job</dt><dd>{result.job}</dd></>}
            <dt>Recorded</dt><dd>{when(result.at)}{result.supersedes ? ` · replaces ${result.supersedes}` : ''}</dd>
          </dl>
          {!result.script && !result.command && <div className="res-warn">No script or command was given: this number cannot be traced further.</div>}
          {script !== null && <pre className="res-script">{script}</pre>}
          {error && <div className="modal-error">{error}</div>}
        </>
      )}
    </div>
  )
  return createPortal(node, document.body)
}

function FileLink({ f, href }: { f: { path: string; exists: boolean; sha256?: string }; href: string }) {
  return (
    <span className="res-file">
      {f.exists ? <a href={href} target="_blank" rel="noreferrer"><code>{f.path}</code></a> : <code>{f.path}</code>}
      {f.exists ? (f.sha256 ? <span className="lit-sub" title={`sha256 ${f.sha256}… when recorded`}> ✓</span> : null) : <span className="res-warn"> (not found)</span>}
    </span>
  )
}

/** A result ID as a chip that opens its provenance. */
export function ResultChip({ id, results, campaignId, runId, label }: {
  id: string; results: RunResult[]; campaignId: string; runId: string; label?: string
}) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const r = results.find((x) => x.id === id) ?? null
  const latest = r ? [...results].reverse().find((x) => x.name.toLowerCase() === r.name.toLowerCase()) : null
  return (
    <>
      <button type="button" className={`res-chip${r ? '' : ' missing'}`} onClick={(e) => setAnchor(anchor ? null : e.currentTarget.getBoundingClientRect())}>
        {label ?? id}
      </button>
      {anchor && <ResultPopover result={r} campaignId={campaignId} runId={runId} anchor={anchor} onClose={() => setAnchor(null)}
        superseded={latest && latest.id !== r?.id ? latest : null} />}
    </>
  )
}

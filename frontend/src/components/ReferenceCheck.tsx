/** Checking a manuscript's references before you submit it (in the Library): whether each reference exists, was
 *  retracted, says what the sentence citing it says, and which key work is missing. Nothing in the manuscript changes. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { MemAudit } from '../types'

/** The checks made, and a new one; a check opens its report here. */
export function ReferenceCheck() {
  const [open, setOpen] = useState<string | null>(null)
  return open ? <AuditReport id={open} onClose={() => setOpen(null)} /> : <Audits onOpen={setOpen} />
}

/** Checking a manuscript's references before you submit it. */
function Audits({ onOpen }: { onOpen: (id: string) => void }) {
  const [list, setList] = useState<MemAudit[]>([])
  const [tex, setTex] = useState('')
  const [bib, setBib] = useState('')
  const [open, setOpen] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const load = useCallback(async () => setList(await api.memory.audits()), [])
  useEffect(() => { load().catch(() => {}) }, [load])
  const readFile = (f: File | undefined, set: (s: string) => void) => { if (f) f.text().then(set) }
  return (
    <section className="mem-card">
      <h4>Check a manuscript’s references</h4>
      <span className="lit-sub">Before you submit: whether each reference exists, was retracted, says what your sentence says it does,
        and which key work is missing. Nothing in the manuscript is changed. From Overleaf, the extension does this in one click.</span>
      {!open ? <div className="cw-row"><button className="btn-sm lit-btn" onClick={() => setOpen(true)}>Check one</button></div> : (
        <>
          <label className="lit-sub">The manuscript as a PDF <input type="file" accept=".pdf,application/pdf" onChange={async (e) => {
            const f = e.target.files?.[0]
            if (!f) return
            setErr(null)
            try {
              const bytes = new Uint8Array(await f.arrayBuffer())
              let bin = ''
              for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
              const a = await api.memory.auditPdf(btoa(bin), f.name.replace(/\.pdf$/i, ''))
              setOpen(false); load(); onOpen(a.audit_id)
            } catch (x) { setErr((x as Error).message) }
          }} /></label>
          <span className="lit-sub">or its sources:</span>
          <label className="lit-sub">The LaTeX (.tex) <input type="file" accept=".tex,.txt" onChange={(e) => readFile(e.target.files?.[0], setTex)} /></label>
          <textarea className="rd-in" rows={3} value={tex} onChange={(e) => setTex(e.target.value)} placeholder="or paste it" aria-label="LaTeX" />
          <label className="lit-sub">The bibliography (.bib) <input type="file" accept=".bib,.txt" onChange={(e) => readFile(e.target.files?.[0], setBib)} /></label>
          <textarea className="rd-in" rows={3} value={bib} onChange={(e) => setBib(e.target.value)} placeholder="or paste it" aria-label="BibTeX" />
          <div className="cw-row"><button className="btn-sm btn-modify" disabled={!tex.trim()} onClick={async () => {
            try { const a = await api.memory.audit(tex, bib); setOpen(false); setTex(''); setBib(''); load(); onOpen(a.audit_id) } catch (e) { setErr((e as Error).message) }
          }}>Check the references</button><button className="btn-sm lit-btn" onClick={() => setOpen(false)}>Cancel</button></div>
          {err && <div className="panel-message error">{err}</div>}
        </>
      )}
      {list.map((a) => <button key={a.audit_id} className="mem-row" onClick={() => onOpen(a.audit_id)}>
        <span>{a.title}</span><span className="lit-sub">{a.state === 'running' ? a.step || 'running…' : a.state} · {new Date(a.created_at * 1000).toLocaleDateString()}</span></button>)}
    </section>
  )
}

function AuditReport({ id, onClose }: { id: string; onClose: () => void }) {
  const [a, setA] = useState<MemAudit | null>(null)
  useEffect(() => {
    let gone = false
    const tick = async () => {
      const x = await api.memory.getAudit(id).catch(() => null)
      if (gone) return
      setA(x)
      if (x && x.state === 'running') setTimeout(tick, 2500)
    }
    tick()
    return () => { gone = true }
  }, [id])
  const r = a?.report ?? {}
  const flagged = (r.entries ?? []).filter((e) => e.status !== 'verified' || e.retracted)
  return (
    <div className="mem-panel">
      <div className="mem-head"><b className="mem-logo">{a?.title || 'References'}</b><button className="link-btn" onClick={onClose}>← Back</button></div>
      {!a ? <div className="panel-empty">Loading…</div> : a.state === 'running' ? <div className="panel-empty">{a.step || 'Checking…'}</div>
        : a.state === 'failed' ? <div className="panel-message error">{a.error}</div> : (
        <div className="mem-body">
          <span className="lit-sub">{r.counts?.entries} references · {r.counts?.verified} found in the indexes · {r.counts?.retracted} retracted ·
            {' '}{r.counts?.miscites} citations that do not fully hold · {r.counts?.missing} key works not cited</span>
          {(r.entries ?? []).some((e) => e.retracted) && <section className="mem-zone"><h4>Retracted</h4>
            {(r.entries ?? []).filter((e) => e.retracted).map((e) => <div key={e.key} className="mem-row static clash"><span><b>[{e.key}]</b> {e.found_title || e.title}</span><span className="lit-sub">{e.retracted}</span></div>)}</section>}
          {flagged.some((e) => e.status !== 'verified') && <section className="mem-zone"><h4>Not found as written</h4>
            {flagged.filter((e) => e.status !== 'verified').map((e) => <div key={e.key} className="mem-row static"><span><b>[{e.key}]</b> {e.title}</span><span className="lit-sub">{e.status}: {e.reason}</span></div>)}</section>}
          {(r.miscites ?? []).length > 0 && <section className="mem-zone"><h4>Citations that do not fully hold</h4>
            {r.miscites!.map((m, i) => <div key={i} className="mem-why">
              <span><b>[{m.key}]</b> line {m.line} · {m.verdict === 'no' ? 'not supported' : 'partly supported'}</span>
              <span className="lit-sub">“{m.sentence}”</span><span>{m.issue}</span>
              {m.quote && <blockquote className="pl-quote">{m.quote}</blockquote>}</div>)}</section>}
          {(r.missing ?? []).length > 0 && <section className="mem-zone"><h4>Key work not cited</h4>
            {r.missing!.map((m) => <div key={m.paper_id} className="mem-why"><span><b>{m.title}</b> <span className="lit-sub">{m.byline}</span></span><span>{m.why}</span>
              <button className="link-btn" style={{ alignSelf: 'flex-start' }} onClick={() => navigator.clipboard?.writeText(m.bibtex).catch(() => {})}>copy BibTeX</button></div>)}</section>}
          {((r.unused ?? []).length > 0 || (r.undefined ?? []).length > 0) && <section className="mem-zone"><h4>Housekeeping</h4>
            {(r.undefined ?? []).length > 0 && <span>Cited but not in the .bib: {r.undefined!.join(', ')}</span>}
            {(r.unused ?? []).length > 0 && <span className="lit-sub">In the .bib but never cited: {r.unused!.join(', ')}</span>}</section>}
        </div>
      )}
    </div>
  )
}

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, describeError } from '../../api/client'
import { formatCost, formatDuration } from '../../format'
import type { RunReport, RunResult } from '../../types'
import { CiteProvider, KeyPopover, useKeyLabels } from '../Cite'
import { ResultChip, ResultPopover, showValue } from './ResultPopover'

const MODEL_ROLES: [string, string][] = [['agent', 'Agents'], ['research', 'Research'], ['analysis', 'Analysis'], ['writing', 'Writing'],
  ['ideation_seats', 'Ideation seats'], ['ideation_chair', 'Ideation chair'], ['auditor', 'Auditor'], ['supervisor', 'Live auditor']]

/** A run's report: what was done, its key results (each traceable), its figures, and taking it all away. */
export function ReportView({ campaignId, runId, onClose }: { campaignId: string; runId: string; onClose: () => void }) {
  return (
    <CiteProvider key={campaignId} campaignId={campaignId}>
      <Report campaignId={campaignId} runId={runId} onClose={onClose} />
    </CiteProvider>
  )
}

function Report({ campaignId, runId, onClose }: { campaignId: string; runId: string; onClose: () => void }) {
  const [rep, setRep] = useState<RunReport | null>(null)
  const [results, setResults] = useState<RunResult[]>([])
  const [version, setVersion] = useState<number | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [overleaf, setOverleaf] = useState(false)
  const [pop, setPop] = useState<{ kind: 'result' | 'cite'; id: string; anchor: DOMRect } | null>(null)
  const body = useRef<HTMLDivElement>(null)

  const load = useCallback(() => {
    api.results.report(campaignId, runId, version).then(setRep).catch((e) => setError(describeError(e)))
    api.results.list(campaignId, runId).then((r) => setResults(r.results)).catch(() => {})
  }, [campaignId, runId, version])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape' && !pop) onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose, pop])

  // the server's HTML marks results and papers as buttons: label them, and open their popovers on click
  const keys = useMemo(() => [...new Set([...(rep?.html ?? '').matchAll(/data-cite="([^"]+)"/g)].map((m) => m[1]))], [rep?.html])
  const labels = useKeyLabels(keys)
  useLayoutEffect(() => {
    const root = body.current
    if (!root) return
    root.querySelectorAll<HTMLButtonElement>('button[data-cite]').forEach((b) => {
      const l = labels[b.dataset.cite ?? '']
      if (!l) return
      b.textContent = `${l.label} ${l.ok === true ? '✓' : l.ok === false ? '?' : ''}`.trim()
      b.className = `cite ${l.ok === true ? 'v' : l.ok === false ? 'u' : 'p'}`
    })
    root.querySelectorAll<HTMLButtonElement>('button[data-result]').forEach((b) => {
      const r = results.find((x) => x.id === b.dataset.result)
      b.className = `res-chip${r ? '' : ' missing'}`
      b.title = r ? `${r.name} = ${showValue(r)}: click for where it came from` : 'No recorded result has this ID'
    })
  }, [rep?.html, labels, results])
  const onBodyClick = (e: React.MouseEvent) => {
    const b = (e.target as HTMLElement).closest('button[data-result], button[data-cite]') as HTMLButtonElement | null
    if (!b) return
    const rect = b.getBoundingClientRect()
    setPop(b.dataset.result ? { kind: 'result', id: b.dataset.result, anchor: rect } : { kind: 'cite', id: b.dataset.cite!, anchor: rect })
  }

  if (!rep) return <div className="rep-page"><div className="rep-bar"><button className="link-btn" onClick={onClose}>← Back to the run</button></div><div className="panel-empty">{error ?? 'Loading…'}</div></div>
  const ov = rep.overview
  const popResult = pop?.kind === 'result' ? results.find((x) => x.id === pop.id) ?? null : null
  const latestOf = (r: RunResult | null) => r ? [...results].reverse().find((x) => x.name.toLowerCase() === r.name.toLowerCase()) ?? null : null
  return (
    <div className="rep-page">
      <div className="rep-bar">
        <button className="link-btn" onClick={onClose}>← Back to the run</button>
        <div className="rep-actions">
          {rep.versions.length > 1 && (
            <select aria-label="Version" value={rep.version} onChange={(e) => setVersion(Number(e.target.value))}>
              {rep.versions.map((v) => <option key={v} value={v}>Version {v}</option>)}
            </select>
          )}
          <a className="btn-sm lit-btn" href={api.results.exportUrl(campaignId, runId, 'report')}>Download report (zip)</a>
          <a className="btn-sm lit-btn" href={api.results.exportUrl(campaignId, runId, 'workspace')}>Everything (zip)</a>
          <a className="btn-sm lit-btn" href={api.results.reproduceUrl(campaignId, runId)}>reproduce.sh</a>
          <button className="btn-sm btn-approve" disabled={!rep.html} onClick={() => setOverleaf(true)}>Put into Overleaf…</button>
        </div>
      </div>
      {overleaf && <OverleafDialog campaignId={campaignId} runId={runId} onClose={() => setOverleaf(false)} />}
      <div className="rep-cols">
        <article className="rep-doc" ref={body} onClick={onBodyClick}>
          {rep.html ? <div className="rep-html" dangerouslySetInnerHTML={{ __html: rep.html }} />
            : <div className="panel-empty">This run has no report yet{ov.status === 'running' ? ': the writing phase writes it at the end.' : '.'}</div>}
        </article>
        <aside className="rep-side">
          <section className="fd-box">
            <h3>What was done</h3>
            <div className="fd-small"><b>{ov.name}</b> · {ov.status} · {formatCost(ov.cost_usd)} · {formatDuration(ov.wall_seconds)}</div>
            {ov.forked_from && <div className="lit-sub">Forked from {ov.forked_from.name || ov.forked_from.run_id} after {ov.forked_from.point}.</div>}
            {ov.proposal.title && <div className="fd-small"><span className="lit-sub">Direction: </span>{ov.proposal.title}</div>}
            {ov.proposal.scores && <div className="lit-sub">Screening: novelty {ov.proposal.scores.novelty ?? '–'}, feasibility {ov.proposal.scores.feasibility ?? '–'}, significance {ov.proposal.scores.significance ?? '–'}</div>}
            <details><summary className="lit-sub">The brief</summary><div className="rep-brief">{ov.instruction}</div></details>
            <div className="rep-models">{MODEL_ROLES.filter(([k]) => ov.models[k]).map(([k, l]) => <div key={k}><span className="lit-sub">{l}</span> {ov.models[k]}</div>)}</div>
            {ov.cluster_jobs > 0 && <div className="lit-sub">{ov.cluster_jobs} cluster job{ov.cluster_jobs > 1 ? 's' : ''}</div>}
          </section>
          <section className="fd-box">
            <h3>Key results · {ov.results.length}</h3>
            {ov.results.length ? (
              <table className="rep-results"><tbody>
                {ov.results.map((r) => (
                  <tr key={r.id}><td><ResultChip id={r.id} results={results} campaignId={campaignId} runId={runId} /></td>
                    <td>{r.name}</td><td className="rep-num">{showValue(r)}</td></tr>
                ))}
              </tbody></table>
            ) : <div className="lit-sub">The agents recorded no results with <code>aurora results</code>: the report’s numbers cannot be traced to scripts.</div>}
            {ov.results_all > ov.results.length && <div className="lit-sub">{ov.results_all - ov.results.length} earlier values were recomputed; click a result for its history.</div>}
          </section>
          {ov.claims.length > 0 && (
            <section className="fd-box">
              <h3>Conclusions</h3>
              {ov.claims.map((c, i) => <div key={i} className="fd-small"><span className={`rep-claim ${c.status}`}>{c.status}</span> {c.statement}</div>)}
            </section>
          )}
          {rep.figures.length > 0 && (
            <section className="fd-box">
              <h3>Figures · {rep.figures.length}</h3>
              <div className="rep-figs">{rep.figures.filter((f) => f.url).map((f) => (
                <a key={f.path} href={f.url} target="_blank" rel="noreferrer" title={f.path}><img src={f.url} alt={f.src} /></a>
              ))}</div>
            </section>
          )}
        </aside>
      </div>
      {pop?.kind === 'result' && <ResultPopover result={popResult} campaignId={campaignId} runId={runId} anchor={pop.anchor}
        onClose={() => setPop(null)} superseded={latestOf(popResult)?.id !== popResult?.id ? latestOf(popResult) : null} />}
      {pop?.kind === 'cite' && <KeyPopover k={pop.id} anchor={pop.anchor} onClose={() => setPop(null)} />}
    </div>
  )
}

function OverleafDialog({ campaignId, runId, onClose }: { campaignId: string; runId: string; onClose: () => void }) {
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [hasToken, setHasToken] = useState<{ set: boolean; hint: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  useEffect(() => {
    api.results.overleaf(campaignId, runId).then((s) => { setUrl(s.url); setHasToken(s.token) }).catch(() => {})
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [campaignId, runId, onClose])
  const push = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setMessage(null)
    try {
      const r = await api.results.pushOverleaf(campaignId, runId, { url: url.trim(), token: token.trim() })
      setMessage({ tone: 'ok', text: r.message })
      setToken('')
      setHasToken(r.token)
    } catch (err) { setMessage({ tone: 'error', text: describeError(err) }) } finally { setBusy(false) }
  }
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <form className="modal lit-modal" role="dialog" aria-modal="true" aria-label="Put into Overleaf" onMouseDown={(e) => e.stopPropagation()} onSubmit={push}>
        <div className="modal-title">Put the report into Overleaf</div>
        <div className="modal-body">
          <div className="modal-hint">The LaTeX source (report.tex), its figures, references.bib, the results table and the scripts go into a
            folder <code>aurora/&lt;run&gt;/</code> of the Overleaf project; nothing else there is touched. Uses Overleaf’s git integration.</div>
          <label className="fd-field">The project’s address, or its git URL (Overleaf → Menu → Git)
            <input className="fd-input" placeholder="https://www.overleaf.com/project/6ab67eb…" value={url} onChange={(e) => setUrl(e.target.value)} /></label>
          <label className="fd-field">Overleaf git token {hasToken?.set ? `(saved: ${hasToken.hint}; paste a new one to replace it)` : '(Account settings → Git integration)'}
            <input className="fd-input" type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder={hasToken?.set ? 'saved' : 'olp_…'} /></label>
          <div className="modal-hint">The token is stored encrypted, like your API keys; nobody can read it back.</div>
          {message && <div className={message.tone === 'ok' ? 'panel-message ok' : 'modal-error'}>{message.text}</div>}
        </div>
        <div className="modal-actions">
          <button type="button" className="btn-sm" onClick={onClose}>Close</button>
          <button className="btn-sm btn-approve" disabled={busy || !url.trim() || (!token.trim() && !hasToken?.set)}>{busy ? 'Pushing…' : 'Put into Overleaf'}</button>
        </div>
      </form>
    </div>
  )
}

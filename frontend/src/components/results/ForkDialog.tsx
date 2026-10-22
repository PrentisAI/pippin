import { useEffect, useState } from 'react'
import { api, describeError } from '../../api/client'
import { formatCost } from '../../format'
import type { ForkPoints } from '../../types'

/** Fork a run: a new run starts from a phase or a job of this one, with what it had made and spent until
 *  then, and does something differently from there on. The original run is not changed. */
export function ForkDialog({ campaignId, runId, runName, onClose, onForked }: {
  campaignId: string; runId: string; runName: string; onClose: () => void; onForked: (newRunId: string) => void
}) {
  const [data, setData] = useState<ForkPoints | null>(null)
  const [point, setPoint] = useState('')
  const [direction, setDirection] = useState<string>('keep')     // keep | idea_p02 | own
  const [own, setOwn] = useState('')
  const [guidance, setGuidance] = useState('')
  const [research, setResearch] = useState('')
  const [name, setName] = useState(`${runName} (fork)`)
  const [budget, setBudget] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.results.forkpoints(campaignId, runId).then((d) => {
      setData(d)
      const planning = d.points.find((p) => p.label === 'after planning') ?? d.points[d.points.length - 1]
      if (planning) setPoint(planning.id)
    }).catch((e) => setError(describeError(e)))
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [campaignId, runId, onClose])
  const chosen = data?.points.find((p) => p.id === point)
  const canRedirect = !!chosen && (chosen.label === 'after ideation' || chosen.label === 'after screening')
  const directionText = !canRedirect || direction === 'keep' ? ''
    : direction === 'own' ? own.trim()
    : (() => { const i = data?.ideas.find((x) => x.id === direction); return i ? `Pursue ${i.id} (presented by ${i.seat}): ${i.headline}` : '' })()
  const ready = !!point && (guidance.trim() || directionText || research.trim())
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await api.results.fork(campaignId, runId, {
        point, name: name.trim(), guidance: guidance.trim(), direction: directionText,
        ...(research.trim() ? { models: { research: research.trim() } } : {}),
        ...(budget.trim() ? { max_cost_usd: Number(budget) } : {}),
      })
      onForked(r.experiment_id)
    } catch (err) { setError(describeError(err)) } finally { setBusy(false) }
  }
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <form className="modal lit-modal fork-modal" role="dialog" aria-modal="true" aria-label="Fork this run" onMouseDown={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="modal-title">Fork “{runName}”</div>
        <div className="modal-body">
          <div className="modal-hint">A new run starts from a point of this one, with the files, results and cost it had then, and goes on as you say.
            This run is not changed; both appear side by side under Compare.</div>
          {!data ? <div className="modal-hint">{error ?? 'Loading…'}</div> : !data.points.length ? (
            <div className="modal-error">This run kept no fork points (they are kept after each phase and each research job, by runs started from now on).</div>
          ) : (
            <>
              <div className="share-section-title">From where</div>
              <div className="fork-points">
                {data.points.map((p) => (
                  <label key={p.id} className={`fork-point${point === p.id ? ' picked' : ''}`}>
                    <input type="radio" name="fork-point" checked={point === p.id} onChange={() => setPoint(p.id)} />
                    <span className="fork-point-main"><b>{p.label}</b>
                      <span className="lit-sub">{new Date(p.at * 1000).toLocaleString(undefined, { hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short' })}
                        {' · '}{formatCost(p.cost_usd)} spent · {p.files} files{p.skipped.length ? ` · ${p.skipped.length} large file${p.skipped.length > 1 ? 's' : ''} not kept` : ''}</span></span>
                  </label>
                ))}
              </div>
              {canRedirect && (
                <>
                  <div className="share-section-title">Direction</div>
                  <label className="modal-check"><input type="radio" name="fork-dir" checked={direction === 'keep'} onChange={() => setDirection('keep')} />
                    <span>Keep the chosen one{data.chosen ? `: ${data.chosen}` : ''}</span></label>
                  {data.ideas.map((i) => (
                    <label key={i.id} className="modal-check" title={i.text}><input type="radio" name="fork-dir" checked={direction === i.id} onChange={() => setDirection(i.id)} />
                      <span>{i.headline} <span className="lit-sub">· {i.seat}, {i.model.split('/').pop()}</span></span></label>
                  ))}
                  <label className="modal-check"><input type="radio" name="fork-dir" checked={direction === 'own'} onChange={() => setDirection('own')} />
                    <span>My own direction</span></label>
                  {direction === 'own' && <textarea rows={2} className="fd-input" value={own} onChange={(e) => setOwn(e.target.value)} placeholder="The direction to pursue instead" />}
                </>
              )}
              <label className="fd-field">What should be different from there on?
                <textarea rows={3} className="fd-input" value={guidance} onChange={(e) => setGuidance(e.target.value)}
                  placeholder={chosen?.phase === 'research' ? 'e.g. from the next job on, use a hurdle model instead of zero-inflated Poisson' : 'e.g. plan a smaller study first: three stations, one month'} /></label>
              <div className="bs-row">
                <label className="fd-field" style={{ flex: 2, minWidth: 200 }}>Research model (optional)
                  <input className="fd-input" value={research} onChange={(e) => setResearch(e.target.value)} placeholder="the same; or e.g. anthropic/claude-opus-4.8" /></label>
                <label className="fd-field" style={{ flex: 1, minWidth: 110 }}>Budget, US$
                  <input className="fd-input" type="number" min="1" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="the same" /></label>
              </div>
              <label className="fd-field">Name<input className="fd-input" value={name} onChange={(e) => setName(e.target.value)} /></label>
              {chosen && chosen.skipped.length > 0 && <div className="modal-hint">Not kept (too large), so recomputed if needed: {chosen.skipped.slice(0, 5).join(', ')}{chosen.skipped.length > 5 ? '…' : ''}</div>}
            </>
          )}
          {error && data && <div className="modal-error">{error}</div>}
        </div>
        <div className="modal-actions">
          <button type="button" className="btn-sm" onClick={onClose}>Cancel</button>
          <button className="btn-sm btn-approve" disabled={busy || !ready}>{busy ? 'Starting…' : 'Start the fork'}</button>
        </div>
      </form>
    </div>
  )
}

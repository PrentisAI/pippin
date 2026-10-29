/** A run's page in the notebook, written from the run: the idea, the hypothesis and what would refute it, why it
 *  was worth trying, what it expected set against what it found, and then your notes on it. Clips you made for the
 *  run are listed under it. */
import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '../../api/client'
import type { NbClip, NbRunPage, Verdict } from '../../types'
import { Editor } from './Editor'
import { ClipCard } from './Clips'

const VERDICT: Record<Verdict, string> = { held: 'Held', partly: 'Partly', not: 'Did not hold', untested: 'Not tested' }

export function RunPage({ campaignId, runId, project, onOpenRun, clips }: {
  campaignId: string
  runId: string
  project: string
  onOpenRun: (cid: string, rid: string, report?: boolean) => void
  /** All your clips, to put one into the notes. */
  clips: NbClip[]
}) {
  const [p, setP] = useState<NbRunPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => api.notebook.run(campaignId, runId).then(setP).catch((e) => setError(describeError(e))), [campaignId, runId])
  useEffect(() => { load() }, [load])
  // while the comparison is being judged, look again
  const judging = p?.match?.state === 'judging'
  useEffect(() => {
    if (!judging) return
    const t = window.setInterval(load, 3000)
    return () => window.clearInterval(t)
  }, [judging, load])

  if (error) return <div className="nb-page"><p className="nb-err">{error}</p></div>
  if (!p) return <div className="nb-page"><p className="lit-sub">Loading…</p></div>
  const m = p.match
  const verdictOf = (i: number) => m?.state === 'done' ? m.items?.[i] : undefined
  const sc = p.why.scores
  return (
    <article className="nb-page">
      <p className="nb-eyebrow">{project} · {p.name} · {p.status === 'completed' ? 'finished' : p.status.replace('_', ' ')}</p>
      <h1 className="nb-title">{p.idea.title || p.name}</h1>
      {!p.has_idea && <p className="lit-sub">This run has no proposal of its own (it was started from an instruction), so there is no idea or hypothesis to show.</p>}

      {p.idea.description && p.idea.description !== p.hypothesis && (
        <Field label="Idea"><p className="nb-read">{p.idea.description}</p></Field>
      )}
      {p.hypothesis && (
        <Field label="Hypothesis" extra={m?.state === 'done' && m.hypothesis ? <span className={`nb-v v-${m.hypothesis.verdict}`}>{VERDICT[m.hypothesis.verdict]}</span> : null}>
          <p className="nb-read">{p.hypothesis}</p>
          {p.refuted_if && <p className="nb-small"><span className="nb-k">Refuted if</span> {p.refuted_if}</p>}
          {m?.state === 'done' && m.hypothesis?.why && <p className="nb-small">{m.hypothesis.why}</p>}
        </Field>
      )}
      {(p.why.gap.length > 0 || p.why.rationale || p.you_said || Object.keys(sc).length > 0) && (
        <Field label="Why it was worth trying">
          {p.why.gap.map((g, i) => <p key={i} className="nb-small">{g}</p>)}
          {p.why.rationale && <p className="nb-small"><span className="nb-k">Chosen because</span> {p.why.rationale}</p>}
          {p.you_said && <p className="nb-small"><span className="nb-k">You said</span> “{p.you_said}”</p>}
          {p.why.alternatives && <details className="nb-more"><summary>The alternatives it was weighed against</summary><p className="nb-small">{p.why.alternatives}</p></details>}
          {Object.keys(sc).length > 0 && <p className="nb-scores">{Object.entries(sc).map(([k, v]) => <span key={k}>{k} <b>{v}</b></span>)}</p>}
        </Field>
      )}

      {(p.expected.length > 0 || p.claims.length > 0 || p.results.length > 0) && (
        <Field label="Expected → actual" extra={m?.state === 'judging' ? <span className="lit-sub">comparing…</span>
          : m?.state === 'failed' ? <span className="lit-sub">could not be compared</span> : null}>
          {p.expected.length > 0 ? (
            <div className="nb-ea">
              {p.expected.map((x, i) => {
                const v = verdictOf(i)
                return (
                  <div key={i} className="nb-ea-row">
                    <span className="nb-ea-exp">{x}</span>
                    <span className="nb-ea-act">{v?.actual || (p.claims.length || p.results.length ? (m?.state === 'judging' ? '…' : '—') : /running|starting|awaiting/.test(p.status) ? 'Not found yet' : 'No finding recorded')}</span>
                    <span className={`nb-v${v ? ` v-${v.verdict}` : ''}`}>{v ? VERDICT[v.verdict] : ''}</span>
                  </div>
                )
              })}
            </div>
          ) : <p className="lit-sub">The proposal wrote down no expected outcomes.</p>}
          {(p.claims.length > 0 || p.results.length > 0) && (
            <details className="nb-more"><summary>Everything it found: {p.claims.length} claim{p.claims.length === 1 ? '' : 's'}, {p.results.length} recorded result{p.results.length === 1 ? '' : 's'}</summary>
              <ul className="nb-found">
                {p.claims.map((c, i) => <li key={i}><span className="nb-k">{c.status || 'claim'}</span> {c.statement}</li>)}
                {p.results.map((r, i) => <li key={`r${i}`}><span className="nb-k">result</span> {r.name} = {String(r.value)}{r.unit ? ` ${r.unit}` : ''}{r.uncertainty != null ? ` ± ${r.uncertainty}` : ''}</li>)}
              </ul>
            </details>
          )}
        </Field>
      )}

      <div className="nb-links">
        <button className="nb-btn" onClick={() => onOpenRun(campaignId, runId)}>Open the run</button>
        {p.report && <button className="nb-btn" onClick={() => onOpenRun(campaignId, runId, true)}>Its report</button>}
      </div>

      <Field label="Your notes" extra={<span className="lit-sub">only you see them</span>}>
        <Editor key={`${campaignId}/${runId}`} html={p.note?.html ?? ''} clips={clips}
          placeholder="What you make of it, what to try next. Put in a clip from the toolbar."
          onSave={(html, text) => api.notebook.saveRunNote(campaignId, runId, html, text)} />
      </Field>

      {p.clips.length > 0 && (
        <Field label={`Clips for this run · ${p.clips.length}`}>
          {p.clips.map((c) => <ClipCard key={c.clip_id} c={c} onChanged={load} />)}
        </Field>
      )}
    </article>
  )
}

function Field({ label, extra, children }: { label: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="nb-field">
      <h2 className="nb-label">{label}{extra && <span className="nb-label-x">{extra}</span>}</h2>
      {children}
    </section>
  )
}

import { useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import type { ExampleCard } from '../types'
import { startTour, updateOnboarding, useOnboarding } from './store'

/** The examples that come with Pippin: open one (with the tour), copy its question into a project of
 *  your own, or watch the scripted demo. Admins can take an example out of the console. */
export function ExamplesView({ onOpen, onDuplicated, onDemo }: {
  onOpen: (campaignId: string, runId: string) => void
  onDuplicated: (campaignId: string, brief: string) => void
  onDemo: () => void
}) {
  const ob = useOnboarding()
  const [cards, setCards] = useState<ExampleCard[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = () => api.examples.list().then((r) => setCards(r.examples)).catch((e) => setError(describeError(e)))
  useEffect(() => { load() }, [])
  const run = async (id: string, fn: () => Promise<void>) => {
    setBusy(id)
    setError(null)
    try { await fn() } catch (e) { setError(describeError(e)) } finally { setBusy(null) }
  }
  const field = ob?.field
  const sorted = (cards ?? []).slice().sort((a, b) => Number(b.field === field) - Number(a.field === field))
  return (
    <div className="examples-page">
      <div className="examples-head">
        <h2>Examples</h2>
        <p>Finished runs that come with Pippin. Open one to see what a run produces — the tour shows what to look at — or
          duplicate it to change the question and run it yourself. Opening costs nothing.</p>
      </div>
      {error && <div className="panel-message error">{error}</div>}
      {!cards && !error && <div className="panel-empty">Loading…</div>}
      <div className="examples-grid">
        {sorted.map((c) => (
          <div key={c.id} className={`example-card${c.removed ? ' removed' : ''}`}>
            <div className="example-tags">
              <span className={`example-kind ${c.kind === 'real run' ? 'real' : 'scripted'}`}>{c.kind}</span>
              {c.field && <span className="example-tag">{c.field}</span>}
            </div>
            <b className="example-title">{c.title}</b>
            <p>{c.blurb}</p>
            <div className="example-tags">{c.shows.map((s) => <span key={s} className="example-tag">{s}</span>)}</div>
            <div className="example-facts mono">{c.facts}</div>
            <div className="example-actions">
              {c.kind === 'scripted' ? (
                <button className="btn-sm btn-approve" disabled={busy !== null} onClick={() => run(c.id, async () => onDemo())}>Watch it run</button>
              ) : c.removed || !c.installed ? (
                ob?.admin
                  ? <button className="btn-sm lit-btn" disabled={busy !== null} onClick={() => run(c.id, async () => { await api.examples.install(c.id); await load() })}>
                    {busy === c.id ? 'Adding…' : 'Add it to this console'}</button>
                  : <span className="help-hint">Not in this console.</span>
              ) : (
                <>
                  <button className="btn-sm btn-approve" onClick={() => { onOpen(c.campaign_id!, c.run_id!); setTimeout(() => startTour('run'), 400) }}>Open with the tour</button>
                  <button className="btn-sm lit-btn" onClick={() => onOpen(c.campaign_id!, c.run_id!)}>Open</button>
                  <button className="btn-sm lit-btn" disabled={busy !== null}
                    onClick={() => run(c.id, async () => { const r = await api.examples.duplicate(c.id); onDuplicated(r.campaign_id, r.brief) })}>
                    {busy === c.id ? 'Copying…' : 'Duplicate'}</button>
                  {ob?.admin && <button className="link-btn danger" disabled={busy !== null}
                    onClick={() => run(c.id, async () => { await api.examples.remove(c.id); await load() })}>Remove from this console</button>}
                </>
              )}
            </div>
          </div>
        ))}
        {cards && (
          <div className="example-card placeholder">
            <b className="example-title">More examples</b>
            <p>A run of yours can become an example for your team: duplicate it, or share the project with view rights.</p>
          </div>
        )}
      </div>
      {ob && (
        <div className="examples-foot">
          <button className="link-btn" onClick={() => updateOnboarding({ examples_hidden: !ob.examples_hidden }).catch(() => {})}>
            {ob.examples_hidden ? 'Show the examples among my projects' : 'Hide the examples from my projects'}</button>
        </div>
      )}
    </div>
  )
}

import { useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import type { SetupStatus } from '../types'

/** Research briefs to start from: the parts in [brackets] are yours to fill in. */
export const TEMPLATES: { id: string; title: string; blurb: string; phases: string; text: string }[] = [
  {
    id: 'data', title: 'Analyse a dataset', blurb: 'Answer a question with data you have', phases: 'starts from your question: planning, research, analysis, report',
    text: 'Analyse [the dataset: what it is, and where — e.g. a file you upload with +].\nQuestion: [what you want to learn from it].\n'
      + 'Compare [the groups or conditions that matter].\nReport effect sizes with their uncertainty, and the figures that show them. '
      + 'Say what the data cannot tell.',
  },
  {
    id: 'ablation', title: 'Compare methods', blurb: 'An ablation or a benchmark comparison', phases: 'skips ideation; uses cluster GPUs if configured',
    text: 'On [the dataset or benchmark], compare [method A] with [method B], and the ablations [remove X; vary Y].\n'
      + 'Metric: [e.g. accuracy, F1, RMSE]; [3] seeds each; report the mean ± sd and whether the differences hold up.\n'
      + 'Compute: [e.g. at most 20 GPU hours on the cluster].',
  },
  {
    id: 'reproduce', title: 'Reproduce a paper', blurb: 'Check a published result', phases: 'reads the paper first; no ideation',
    text: 'Reproduce [the result: e.g. Table 2] of [the paper: DOI, arXiv ID or title], using [their code and data if available, '
      + 'else a reimplementation from the paper].\nReport which numbers match, which do not, by how much, and the likely reasons.',
  },
  {
    id: 'open', title: 'Explore an open question', blurb: 'From what is known to a tested idea', phases: 'every phase: the round table proposes, screening checks novelty',
    text: 'Why [the phenomenon or puzzle]?\nFind what is already known, propose testable explanations, and test the most promising one '
      + 'with [the data or simulation you can use]. Investigate whether [a competing explanation] holds instead.',
  },
  {
    id: 'simulation', title: 'Run a simulation study', blurb: 'Compute a quantity, converged', phases: 'planning first; long jobs go to the cluster',
    text: 'Simulate [the system] with [the method or code, e.g. DFT with ASE + GPAW, MD with LAMMPS] to find [the quantity].\n'
      + 'Converge [the numerical parameters] first and show it; report values with their convergence checks and limits.',
  },
]

/** What a run needs, what helps, and how to fix what is missing (shown until everything needed is there). */
export function SetupCard({ onDemo }: { onDemo: () => void }) {
  const [s, setS] = useState<SetupStatus | null>(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => { api.setup.get().then((x) => { setS(x); setOpen(!x.ready) }).catch(() => {}) }, [])
  const run = async (what: string, fn: () => Promise<SetupStatus | void>) => {
    setBusy(what)
    setError(null)
    try { const r = await fn(); if (r) setS(r) } catch (e) { setError(describeError(e)) } finally { setBusy(null) }
  }
  if (!s || (s.ready && s.dismissed)) return null
  const missing = s.checks.filter((c) => c.required && !c.ok)
  return (
    <section className={`setup-card${s.ready ? ' ready' : ''}`} aria-label="Setup">
      <div className="setup-head">
        <div>
          <b>{s.ready ? 'Ready to run' : `Before the first run: ${missing.length} thing${missing.length === 1 ? '' : 's'} to set up`}</b>
          <div className="lit-sub">{s.ready ? 'Everything a run needs is there. Optional extras below.' : 'Meanwhile, the demo shows a whole run without any of it.'}</div>
        </div>
        <div className="bs-row">
          <button className="btn-sm lit-btn" onClick={() => setOpen(!open)}>{open ? 'Hide the list' : 'Show the list'}</button>
          <button className="btn-sm lit-btn" disabled={busy !== null} onClick={() => run('check', () => api.setup.get())}>{busy === 'check' ? 'Checking…' : 'Check again'}</button>
          {s.ready && <button className="link-btn" onClick={() => run('dismiss', async () => { await api.setup.dismiss(); setS({ ...s, dismissed: true }) })}>Don’t show again</button>}
        </div>
      </div>
      {open && (
        <ul className="setup-list">
          {s.checks.map((c) => (
            <li key={c.key} className={c.ok ? 'ok' : c.required ? 'missing' : 'optional'}>
              <span className="setup-mark" aria-hidden="true">{c.ok ? '✓' : c.required ? '!' : '○'}</span>
              <div className="setup-body">
                <div><b>{c.label}</b> <span className="lit-sub">· {c.detail}{!c.required && !c.ok ? ' · optional' : ''}</span></div>
                {!c.ok && c.fix && <div className="lit-sub">{c.fix}</div>}
                {c.action === 'key' && (
                  <form className="bs-row" onSubmit={(e) => { e.preventDefault(); run('key', () => api.setup.setKey(key.trim())).then(() => setKey('')) }}>
                    <input className="fd-input" type="password" placeholder="sk-or-…" aria-label="OpenRouter key" value={key} onChange={(e) => setKey(e.target.value)} />
                    <button className="btn-sm btn-approve" disabled={busy !== null || key.trim().length < 20}>{busy === 'key' ? 'Checking…' : 'Save'}</button>
                  </form>
                )}
                {c.action === 'pull' && <button className="btn-sm lit-btn" disabled={busy !== null} onClick={() => run('pull', () => api.setup.pull())}>{busy === 'pull' ? 'Pulling (a few minutes)…' : 'Pull it now'}</button>}
                {c.action === 'settings' && <button className="link-btn" onClick={() => window.dispatchEvent(new CustomEvent('aurora:open-panel', { detail: 'settings' }))}>Open Settings</button>}
              </div>
            </li>
          ))}
        </ul>
      )}
      {error && <div className="modal-error">{error}</div>}
      <div className="setup-demo">
        <button className="btn-sm lit-btn" onClick={onDemo}>Watch a demo run</button>
        <span className="lit-sub">Scripted models replay a small materials study: every phase, a decision, a report. About a minute; no key, no cost.</span>
      </div>
    </section>
  )
}

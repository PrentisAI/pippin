/** A project, read like a document: its question, where it stands, what waits for you (answered here), what its
 *  runs have found (the claims each run kept, with how they stand), and the runs. The project's chat, its runs'
 *  streams, its literature and the comparison stay one tab away. */
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { CampaignSummary, ExperimentSummary, PendingDecision, RunOverview, TodayNeed } from '../types'
import { Need } from './TodayView'

const STATUS: Record<string, string> = {
  completed: 'Finished', running: 'Running', starting: 'Starting', awaiting_human: 'Waiting for you', failed: 'Failed',
  error: 'Failed', stopped: 'Stopped', cancelled: 'Stopped', paused: 'Paused',
}
const CLAIM: Record<string, string> = {
  supported: 'Holds', confirmed: 'Holds', validated: 'Holds', refuted: 'Did not hold', rejected: 'Did not hold',
  revised: 'Revised', open: 'Open', untested: 'Open', pending: 'Open', inconclusive: 'Inconclusive',
}

function runName(e: ExperimentSummary): string {
  return e.task_name || e.run_id
}

function day(at: number | null | undefined): string {
  return at ? new Date(at * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''
}

/** One or two sentences on where the project stands, from what its runs are doing. */
function standing(exps: ExperimentSummary[], waiting: number): string {
  if (!exps.length) return 'No run yet. Talk to the project below to plan the first one.'
  const count = (s: string[]) => exps.filter((e) => s.includes(e.status)).length
  const running = count(['running', 'starting']), done = count(['completed']), failed = count(['failed', 'error'])
  const parts = [`${exps.length} run${exps.length === 1 ? '' : 's'}`]
  const bits = [done && `${done} finished`, running && `${running} running`, waiting && `${waiting} waiting for you`, failed && `${failed} failed`].filter(Boolean)
  const latest = [...exps].sort((a, b) => (b.started_at || 0) - (a.started_at || 0))[0]
  const now = latest.status === 'running' || latest.status === 'starting' ? `is in its ${latest.phase || 'first'} phase`
    : latest.status === 'awaiting_human' ? 'waits for your decision' : `${(STATUS[latest.status] ?? latest.status).toLowerCase()} on ${day(latest.finished_at || latest.started_at)}`
  return `${parts[0]}${bits.length ? `: ${bits.join(', ')}` : ''}. The latest, “${runName(latest)}”, ${now}.`
}

export function ProjectOverview({ campaignId, campaign, experiments, decisions, onOpenRun, onTalk, onDecided }: {
  campaignId: string
  campaign: CampaignSummary | null
  experiments: ExperimentSummary[]
  decisions: (PendingDecision & { experiment_id: string })[]
  onOpenRun: (runId: string) => void
  /** To the project's chat, its box focused. */
  onTalk: () => void
  onDecided: () => void
}) {
  const [runs, setRuns] = useState<RunOverview[]>([])
  const ids = experiments.map((e) => e.run_id).slice(0, 12).join(',')
  useEffect(() => {
    if (!ids) return
    let live = true
    api.results.compare(campaignId, ids.split(',')).then((r) => { if (live) setRuns(r.runs) }).catch(() => { /* the findings are a bonus */ })
    return () => { live = false }
  }, [campaignId, ids])

  const sorted = [...experiments].sort((a, b) => (b.started_at || 0) - (a.started_at || 0))
  const needs: TodayNeed[] = decisions.map((d) => {
    const e = experiments.find((x) => x.run_id === d.experiment_id)
    return { kind: 'decision', id: d.decision_id, title: d.detail?.title || d.context.split('\n')[0], detail: d.detail?.detail ?? '',
      at: d.created_at, campaign_id: campaignId, run_id: d.experiment_id, project: campaign?.name ?? '', run: e ? runName(e) : d.experiment_id,
      source: d.source, options: d.options }
  })
  // what the runs found, newest run first, each claim once
  const seen = new Set<string>()
  const claims: { statement: string; status: string; run: string; runId: string }[] = []
  for (const r of [...runs].sort((a, b) => (b.started_at || 0) - (a.started_at || 0))) {
    for (const c of r.claims) {
      const k = c.statement.trim().toLowerCase()
      if (!k || seen.has(k)) continue
      seen.add(k)
      claims.push({ statement: c.statement, status: c.status, run: r.name, runId: r.run_id })
    }
  }
  const cost = experiments.reduce((n, e) => n + (e.cost_usd || 0), 0)

  return (
    <div className="po">
      <div className="po-col">
        <p className="po-eyebrow">Project · {experiments.length} run{experiments.length === 1 ? '' : 's'}{cost > 0 ? ` · $${cost.toFixed(2)} so far` : ''}</p>
        <h1 className="po-h1">{campaign?.name || campaignId}</h1>
        {campaign?.objective && campaign.objective !== campaign.name && <p className="po-question">{campaign.objective}</p>}

        <section className="po-sec" aria-labelledby="po-stands">
          <h2 id="po-stands" className="po-h2">Where it stands</h2>
          <p className="po-stands">{standing(experiments, needs.length)}</p>
        </section>

        {needs.length > 0 && (
          <section className="po-sec po-wait" aria-label="Waiting for you">
            {needs.map((n) => <Need key={n.id} need={n} onDone={onDecided} onOpenRun={(_, rid) => onOpenRun(rid)} />)}
          </section>
        )}

        {claims.length > 0 && (
          <section className="po-sec" aria-labelledby="po-found">
            <h2 id="po-found" className="po-h2">What it has found</h2>
            <ol className="po-claims">
              {claims.slice(0, 10).map((c, i) => (
                <li key={i}>
                  <span className="po-n">{i + 1}</span>
                  <span className="po-claim">
                    <span className={`po-claim-text${/refut|reject/.test(c.status) ? ' off' : ''}`}>{c.statement}</span>
                    <span className="po-claim-meta">{CLAIM[c.status] ?? c.status} · <button className="td-link" onClick={() => onOpenRun(c.runId)}>{c.run}</button></span>
                  </span>
                </li>
              ))}
            </ol>
          </section>
        )}

        {experiments.length > 0 && (
          <section className="po-sec" aria-labelledby="po-runs">
            <h2 id="po-runs" className="po-h2">Runs</h2>
            <table className="po-runs">
              <thead><tr><th>Run</th><th>Status</th><th>Started</th><th className="num">Cost</th></tr></thead>
              <tbody>
                {sorted.map((e) => (
                  <tr key={e.run_id} onClick={() => onOpenRun(e.run_id)}>
                    <td><button className="td-link" onClick={(ev) => { ev.stopPropagation(); onOpenRun(e.run_id) }}>{runName(e)}</button></td>
                    <td className={e.status === 'awaiting_human' ? 'wait' : /fail|error/.test(e.status) ? 'bad' : ''}>{STATUS[e.status] ?? e.status}{e.status === 'running' && e.phase ? ` · ${e.phase}` : ''}</td>
                    <td>{day(e.started_at)}</td>
                    <td className="num">{e.cost_usd ? `$${e.cost_usd.toFixed(2)}` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        <button className="po-talk" onClick={onTalk}>Tell the project something, or ask about it<span>Project chat →</span></button>
      </div>
    </div>
  )
}

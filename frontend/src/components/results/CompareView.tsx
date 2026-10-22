import { useEffect, useState } from 'react'
import { api, describeError } from '../../api/client'
import { formatCost, formatDuration } from '../../format'
import type { RunComparison, RunOverview } from '../../types'
import { ResultChip } from './ResultPopover'

const MODEL_ROLES: [string, string][] = [['agent', 'Agents'], ['research', 'Research agent'], ['analysis', 'Analysis'],
  ['writing', 'Writing'], ['ideation_seats', 'Ideation seats'], ['ideation_chair', 'Ideation chair'], ['auditor', 'Auditor'],
  ['supervisor', 'Live auditor'], ['frontdesk', 'Front desk']]

type Row = { label: string; cells: React.ReactNode[]; keys: string[] }

function row(label: string, runs: RunOverview[], get: (r: RunOverview) => string, render?: (r: RunOverview) => React.ReactNode): Row {
  return { label, keys: runs.map(get), cells: runs.map((r) => (render ? render(r) : get(r) || '–')) }
}

/** Runs of a project side by side: what they did differently, what they cost, their results by name and what they concluded. */
export function CompareView({ campaignId, onOpenRun }: { campaignId: string; onOpenRun: (runId: string) => void }) {
  const [picked, setPicked] = useState<string[]>([])
  const [data, setData] = useState<RunComparison | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [onlyDiff, setOnlyDiff] = useState(false)
  useEffect(() => {
    api.results.compare(campaignId, picked).then((d) => {
      setData(d)
      if (!picked.length) setPicked(d.runs.map((r) => r.run_id))
    }).catch((e) => setError(describeError(e)))
  }, [campaignId, picked])
  if (!data) return <div className="cmp-page"><div className="panel-empty">{error ?? 'Loading…'}</div></div>
  const runs = data.runs
  const toggle = (id: string) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id])

  const setup: Row[] = [
    row('Direction', runs, (r) => r.proposal.title ?? ''),
    row('Brief', runs, (r) => r.instruction, (r) => <details><summary>{r.instruction.slice(0, 90)}{r.instruction.length > 90 ? '…' : ''}</summary><div className="rep-brief">{r.instruction}</div></details>),
    ...MODEL_ROLES.filter(([k]) => runs.some((r) => r.models[k])).map(([k, l]) => row(l, runs, (r) => r.models[k] ?? '')),
    row('Ideation', runs, (r) => r.settings.skip_phases.includes('ideation') ? 'skipped' : r.settings.ideation_mode || 'roundtable'),
    row('Phases skipped', runs, (r) => r.settings.skip_phases.join(', ') || 'none'),
    row('Autonomy', runs, (r) => r.settings.autonomy),
    row('Container', runs, (r) => [r.settings.image, r.settings.cpus ? `${r.settings.cpus} CPU` : '', r.settings.memory_mb ? `${Math.round(r.settings.memory_mb / 1024)} GB` : ''].filter(Boolean).join(' · ')),
    row('Budget', runs, (r) => r.settings.budget_usd != null ? formatCost(r.settings.budget_usd) : 'none'),
    row('Forked from', runs, (r) => r.forked_from ? `${r.forked_from.name || r.forked_from.run_id}, after ${r.forked_from.point}` : ''),
  ]
  const outcome: Row[] = [
    row('Status', runs, (r) => r.status),
    row('Cost', runs, (r) => formatCost(r.cost_usd)),
    row('Time', runs, (r) => formatDuration(r.wall_seconds)),
    row('Screening', runs, (r) => r.proposal.scores ? `novelty ${r.proposal.scores.novelty ?? '–'} · overall ${r.proposal.scores.composite ?? '–'}` : ''),
    row('Results recorded', runs, (r) => String(r.results.length)),
    row('Cluster jobs', runs, (r) => String(r.cluster_jobs)),
  ]
  const results: Row[] = data.results.map((res) => ({
    label: res.name, keys: res.values.map((v) => v?.shown ?? ''),
    cells: res.values.map((v, i) => v ? <span key={i} className="cmp-val"><span className="rep-num">{v.shown}</span>
      <ResultChip id={v.id} results={runs[i].results} campaignId={campaignId} runId={runs[i].run_id} /></span>
      : <span key={i} className="lit-sub">not measured</span>),
  }))
  const differs = (r: Row) => new Set(r.keys).size > 1
  const section = (title: string, rows: Row[]) => {
    const shown = onlyDiff ? rows.filter(differs) : rows
    return shown.length ? (
      <>
        <tr className="cmp-section"><th colSpan={runs.length + 1}>{title}</th></tr>
        {shown.map((r) => (
          <tr key={title + r.label} className={differs(r) ? 'cmp-diff' : ''}>
            <th scope="row">{r.label}</th>
            {r.cells.map((c, i) => <td key={i}>{c}</td>)}
          </tr>
        ))}
      </>
    ) : null
  }
  return (
    <div className="cmp-page">
      <div className="cmp-head">
        <h2>Compare runs</h2>
        <label className="modal-check"><input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> Only what differs</label>
      </div>
      <div className="lit-chips" role="group" aria-label="Runs to compare">
        {data.all_runs.map((r) => (
          <button key={r.run_id} className={`lit-chip${picked.includes(r.run_id) ? ' on' : ''}`} aria-pressed={picked.includes(r.run_id)}
            onClick={() => toggle(r.run_id)}>{r.name || r.run_id}</button>
        ))}
      </div>
      {runs.length < 2 && <div className="team-note">Pick two runs or more. Differences are highlighted; results line up by name, as the agents recorded them.</div>}
      {runs.length > 0 && (
        <div className="cmp-wrap">
          <table className="cmp-table">
            <thead><tr><th />{runs.map((r) => (
              <th key={r.run_id} scope="col"><button className="link-btn cmp-run" onClick={() => onOpenRun(r.run_id)}>{r.name || r.run_id}</button>
                <div className="lit-sub">{r.started_at ? new Date(r.started_at * 1000).toLocaleDateString() : ''}{r.report ? ' · has a report' : ''}</div></th>
            ))}</tr></thead>
            <tbody>
              {section('Setup', setup)}
              {section('Outcome', outcome)}
              {section('Results', results)}
              {(!onlyDiff || runs.some((r) => r.claims.length)) && (
                <>
                  <tr className="cmp-section"><th colSpan={runs.length + 1}>Conclusions</th></tr>
                  <tr><th scope="row">Findings</th>{runs.map((r) => (
                    <td key={r.run_id}>{r.claims.length ? <ul className="cmp-claims">{r.claims.map((c, i) => (
                      <li key={i}><span className={`rep-claim ${c.status}`}>{c.status}</span> {c.statement}</li>))}</ul>
                      : <span className="lit-sub">none recorded</span>}</td>
                  ))}</tr>
                </>
              )}
            </tbody>
          </table>
        </div>
      )}
      {!data.results.length && runs.length > 1 && <div className="team-note">None of these runs recorded results with <code>aurora results</code>, so there is nothing to line up by name yet.</div>}
    </div>
  )
}

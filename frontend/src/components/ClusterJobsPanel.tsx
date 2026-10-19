import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import { formatDuration } from '../format'
import type { ClusterJobs } from '../types'

/** The experiment's cluster (SLURM) jobs: state, resources, output; cancel one. */
export function ClusterJobsPanel({ campaignId, expId, tick, onOpenLog }: {
  campaignId: string
  expId: string
  /** Changes when a job event arrives: reload. */
  tick: number
  /** Show a job's output (its path in the workspace). */
  onOpenLog: (path: string) => void
}) {
  const [data, setData] = useState<ClusterJobs | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState<number | null>(null)

  const load = useCallback(() => {
    api.compute.jobs(campaignId, expId).then((d) => { setData(d); setError(null) })
      .catch((err) => setError(describeError(err)))
  }, [campaignId, expId])
  useEffect(() => { load() }, [load, tick])
  useEffect(() => {
    if (!data?.live || !data.jobs.some((j) => j.active)) return
    const timer = setInterval(load, 15000)
    return () => clearInterval(timer)
  }, [data, load])

  const cancel = async (id: number) => {
    setCancelling(id)
    try {
      await api.compute.cancel(campaignId, expId, id)
      load()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setCancelling(null)
    }
  }

  return (
    <div className="jobs-panel">
      <div className="jobs-header">
        <span>Cluster jobs</span>
        {data && <span className="jobs-total">{data.gpu_hours.toFixed(1)} GPU hours</span>}
      </div>
      {error && <div className="panel-message error">{error}</div>}
      {data && data.jobs.length === 0 && (
        <div className="panel-empty">No cluster jobs yet. The research agents submit them for GPU or long work.</div>
      )}
      {data?.jobs.slice().reverse().map((j) => (
        <div key={j.id} className={`job-row${j.active ? ' active' : ''}`}>
          <div className="job-line">
            <span className={`job-state state-${j.state.toLowerCase()}`}>{j.state.toLowerCase()}</span>
            <span className="job-name">{j.id}. {j.name}</span>
            <span className="job-meta">SLURM {j.slurm_id || '—'}</span>
          </div>
          <div className="job-meta">
            {j.gpus} GPU · {j.cpus} CPU · {j.mem_gb} GB · limit {j.time_limit}
            {j.partition ? ` · ${j.partition}` : ''}{j.node ? ` · ${j.node}` : ''}
            {j.elapsed_seconds ? ` · ran ${formatDuration(j.elapsed_seconds)}` : ''}
            {j.exit_code != null ? ` · exit ${j.exit_code}` : ''}
          </div>
          {j.reason && <div className="job-meta">{j.state === 'PENDING' ? 'Waiting: ' : ''}{j.reason}</div>}
          <code className="job-command" title={j.command}>{j.command}</code>
          <div className="job-actions">
            <button className="link-btn" onClick={() => onOpenLog(`workspace/${j.dir}/slurm.out`)}>output</button>
            {j.active && data.live && (
              <button className="link-btn danger" disabled={cancelling === j.id} onClick={() => cancel(j.id)}>
                {cancelling === j.id ? 'cancelling…' : 'cancel'}
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

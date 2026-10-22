import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import { formatDuration } from '../format'
import type { ClusterJobs, GpuUsage } from '../types'
import { FirstTip } from '../onboarding/FirstTip'

/** The experiment's GPU jobs (on a SLURM cluster or a GPU machine over SSH): state, resources,
 *  output, what their GPUs are doing; cancel one. */
export function ClusterJobsPanel({ campaignId, expId, tick, onOpenLog }: {
  campaignId: string
  expId: string
  /** Changes when a job event arrives: reload. */
  tick: number
  /** Show a job's output (its path in the workspace). */
  onOpenLog: (path: string) => void
}) {
  const [data, setData] = useState<ClusterJobs | null>(null)
  const [gpus, setGpus] = useState<GpuUsage | null>(null)
  const [gpuLoading, setGpuLoading] = useState(false)
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

  const ssh = data?.backend === 'SSH' || !!data?.jobs.some((j) => j.backend === 'ssh')
  const running = !!data?.live && !!data.jobs.some((j) => j.state === 'RUNNING' && j.gpus > 0)
  // the GPU table: on a GPU machine always (it shows which GPUs are free), on SLURM while a GPU job runs
  const showGpus = !!data?.live && (data.backend === 'SSH' || running)
  const loadGpus = useCallback(() => api.compute.gpus(campaignId, expId).then(setGpus).catch(() => setGpus(null)),
    [campaignId, expId])
  const refreshGpus = () => { setGpuLoading(true); loadGpus().finally(() => setGpuLoading(false)) }
  useEffect(() => {
    if (!showGpus) return
    void loadGpus()
    const timer = setInterval(() => { void loadGpus() }, 30000)
    return () => clearInterval(timer)
  }, [showGpus, loadGpus])

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
      <FirstTip id="jobs" doc="guide/cluster-jobs.md">
        <b>GPU jobs.</b> The agent sent work to GPUs. Each job’s state, resources and output are here, and what its GPUs are
        doing; while a job runs the agent can wait without model calls.
      </FirstTip>
      <div className="jobs-header">
        <span>{ssh ? 'GPU jobs' : 'Cluster jobs'}</span>
        {data && <span className="jobs-total">{data.gpu_hours.toFixed(1)} GPU hours</span>}
      </div>
      {error && <div className="panel-message error">{error}</div>}
      {data?.slurm_error && (
        <div className="panel-message warning">
          Could not ask {ssh ? 'the GPU machine' : 'SLURM'} ({data.slurm_error}); the states below may be out of date.
        </div>
      )}
      {showGpus && (
        <div className="gpu-usage">
          <div className="gpu-usage-head">
            <span>GPUs{gpus?.host ? ` on ${gpus.host}` : ''}</span>
            <button className="link-btn" onClick={refreshGpus} disabled={gpuLoading}>{gpuLoading ? 'asking…' : 'refresh'}</button>
          </div>
          {gpus?.errors.map((e) => <div key={e} className="job-meta">Could not read: {e}</div>)}
          {gpus && gpus.gpus.length === 0 && gpus.errors.length === 0 && <div className="job-meta">No GPUs reported.</div>}
          {gpus && gpus.gpus.length > 0 && (
            <table className="gpu-table">
              <thead><tr><th>GPU</th><th>use</th><th>memory</th><th>held by</th></tr></thead>
              <tbody>
                {gpus.gpus.map((g) => {
                  const mem = g.mem_used_mb != null && g.mem_total_mb ? g.mem_used_mb / g.mem_total_mb : null
                  return (
                    <tr key={`${g.node}-${g.index}`}>
                      <td title={g.name}>{gpus.backend === 'SLURM' && g.node ? `${g.node}:` : ''}{g.index}</td>
                      <td><Meter value={g.util != null ? g.util / 100 : null} label={g.util != null ? `${Math.round(g.util)}%` : '–'} /></td>
                      <td><Meter value={mem} label={g.mem_used_mb != null && g.mem_total_mb ? `${(g.mem_used_mb / 1024).toFixed(1)}/${Math.round(g.mem_total_mb / 1024)} GB` : '–'} /></td>
                      <td className="gpu-holder">
                        {g.jobs.length > 0 ? g.jobs.map((j) => `${j.id}. ${j.name}`).join(', ')
                          : g.other_experiment ? 'another experiment'
                            : g.busy ? 'in use (not AURORA)' : 'free'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
          {gpus && running && gpus.gpus.length > 0 && gpus.gpus.every((g) => g.jobs.length === 0 || (g.util ?? 0) < 5) && (
            <div className="job-meta">The jobs’ GPUs are nearly idle: they may be loading data, or running on the CPU.</div>
          )}
        </div>
      )}
      {data && data.jobs.length === 0 && (
        <div className="panel-empty">No {ssh ? 'GPU' : 'cluster'} jobs yet. The research agents submit them for GPU or long work.</div>
      )}
      {data?.jobs.slice().reverse().map((j) => (
        <div key={j.id} className={`job-row${j.active ? ' active' : ''}`}>
          <div className="job-line">
            <span className={`job-state state-${j.state.toLowerCase()}`}>{j.state.toLowerCase()}</span>
            <span className="job-name">{j.id}. {j.name}</span>
            <span className="job-meta">
              {j.backend === 'ssh' ? (j.slurm_id ? `pid ${j.slurm_id}` : 'not started') : `SLURM ${j.slurm_id || '—'}`}
            </span>
          </div>
          <div className="job-meta">
            {j.gpus} GPU{j.gpu_ids ? ` (${j.gpu_ids})` : ''} · {j.cpus} CPU · {j.mem_gb} GB · limit {j.time_limit}
            {j.partition ? ` · ${j.partition}` : ''}{j.node ? ` · ${j.node}` : ''}
            {j.elapsed_seconds ? ` · ran ${formatDuration(j.elapsed_seconds)}` : ''}
            {j.exit_code != null ? ` · exit ${j.exit_code}` : ''}
          </div>
          {j.reason && <div className="job-meta">{j.state === 'PENDING' && !j.reason.startsWith('waiting') ? 'Waiting: ' : ''}{j.reason}</div>}
          <code className="job-command" title={j.command}>{j.command}</code>
          <div className="job-actions">
            <button className="link-btn" onClick={() => onOpenLog(`workspace/${j.log.replace(/^\/app\//, '')}`)}>output</button>
            {j.active && data.live && (
              <button className="link-btn danger" disabled={cancelling === j.id} onClick={() => cancel(j.id)}>
                {cancelling === j.id ? 'cancelling…' : 'cancel'}
              </button>
            )}
          </div>
          {j.backend === 'ssh' && j.active && j.slurm_id && (
            <div className="job-meta">Its files, and the output file here, arrive when it ends.</div>
          )}
        </div>
      ))}
    </div>
  )
}

function Meter({ value, label }: { value: number | null; label: string }) {
  return (
    <span className="gpu-meter">
      <span className="gpu-meter-bar"><span style={{ width: `${Math.round(Math.max(0, Math.min(1, value ?? 0)) * 100)}%` }} /></span>
      <span className="gpu-meter-label">{label}</span>
    </span>
  )
}

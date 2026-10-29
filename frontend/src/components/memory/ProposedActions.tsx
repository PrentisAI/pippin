/** What Pippin proposed to do on a run or a project, under its answer: nothing happens until it is confirmed here
 *  (or by replying 确认 / yes in a chat app). Afterwards it says what came of it. */
import { useState } from 'react'
import { api, describeError } from '../../api/client'
import type { AssistantAction } from '../../types'

const STATE: Record<AssistantAction['state'], string> = {
  pending: 'Waiting for you', done: 'Done', declined: 'Not done', failed: 'Did not work', expired: 'Too old to do now',
}

export function ProposedActions({ actions, onDone }: { actions: AssistantAction[]; onDone?: (a: AssistantAction) => void }) {
  const [now, setNow] = useState<Record<string, AssistantAction>>({})
  if (!actions.length) return null
  return (
    <div className="pa">
      {actions.map((a0) => <Proposal key={a0.action_id} a={now[a0.action_id] ?? a0}
        onChange={(a) => { setNow((m) => ({ ...m, [a.action_id]: a })); onDone?.(a) }} />)}
    </div>
  )
}

function Proposal({ a, onChange }: { a: AssistantAction; onChange: (a: AssistantAction) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const answer = async (confirm: boolean) => {
    setBusy(true); setError(null)
    try { onChange(await api.assistant.act(a.action_id, confirm)) } catch (e) { setError(describeError(e)) } finally { setBusy(false) }
  }
  return (
    <div className={`pa-card ${a.state}`}>
      <p className="pa-meta"><span className="pa-dot" aria-hidden="true" />{STATE[a.state]} · {a.project}{a.run ? ` · ${a.run}` : ''}</p>
      <p className="pa-label">{a.label}</p>
      {a.state === 'pending' ? (
        <div className="pa-row">
          <button className="pa-btn primary" disabled={busy} onClick={() => answer(true)}>{busy ? 'Doing it…' : 'Do it'}</button>
          <button className="pa-btn" disabled={busy} onClick={() => answer(false)}>Not now</button>
        </div>
      ) : a.result && <p className="pa-result">{a.result}</p>}
      {error && <p className="pa-result bad">{error}</p>}
    </div>
  )
}

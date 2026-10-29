/** Today: the start page of someone who has work going. One sentence on how things stand, then what waits for
 *  you (every project's decisions, what the notebook flagged, skills to approve), each answerable right here;
 *  what changed since yesterday; the papers most worth reading; and a box to ask Pippin. A new question is a
 *  click away (the start conversation). */
import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import type { AssistantTurn, GateAction, TodayNeed, TodayView as TodayData } from '../types'
import { ProposedActions } from './memory/ProposedActions'

const SOURCE: Record<string, string> = {
  milestone_audit: 'The auditor', supervisor: 'The supervisor', screening: 'Screening', direction: 'Choosing a direction', termination: 'Ending',
}

function ago(at: number): string {
  const m = Math.max(0, Math.round((Date.now() / 1000 - at) / 60))
  if (m < 60) return m <= 1 ? 'just now' : `${m} min`
  const h = Math.round(m / 60)
  return h < 24 ? `${h} h` : `${Math.round(h / 24)} d`
}

function clock(at: number): string {
  const d = new Date(at * 1000)
  const today = new Date()
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Yesterday'
}

function greeting(name: string): string {
  const h = new Date().getHours()
  const part = h < 5 ? 'Good evening' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
  return name ? `${part}, ${name}.` : `${part}.`
}

function headline(d: TodayData): string {
  const n = d.needs.length
  const waits = n === 0 ? 'Nothing waits for you.' : n === 1 ? 'One thing waits for you.' : `${n} things wait for you.`
  const rest = d.running ? `${d.running} run${d.running === 1 ? ' is' : 's are'} going.` : n ? 'Everything else is quiet.' : 'Everything is quiet.'
  return `${waits} ${rest}`
}

export function TodayView({ onOpenRun, onNewQuestion }: {
  onOpenRun: (campaignId: string, runId: string) => void
  onNewQuestion: () => void
}) {
  const [data, setData] = useState<TodayData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [date] = useState(() => new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }))
  const load = useCallback(() => api.today.get().then(setData).catch((e) => setError(describeError(e))), [])
  useEffect(() => {
    load()
    const id = window.setInterval(load, 30000)
    return () => window.clearInterval(id)
  }, [load])

  if (!data) return <div className="td"><div className="td-col">{error ? <p className="td-error">{error}</p> : <p className="td-date">Loading…</p>}</div></div>
  const [first, ...more] = headline(data).split('. ')
  return (
    <div className="td">
      <div className="td-col">
        <div className="td-top">
          <p className="td-date">{date}</p>
          <button className="td-new" onClick={onNewQuestion}>New question</button>
        </div>
        <h1 className="td-h1">{greeting(data.name)} {first}.{more.length > 0 && <span> {more.join('. ')}</span>}</h1>

        {data.needs.length > 0 && (
          <section className="td-sec" aria-label="Waiting for you">
            {data.needs.map((n) => <Need key={`${n.kind}:${n.id}`} need={n} onDone={load} onOpenRun={onOpenRun} />)}
          </section>
        )}

        {data.changed.length > 0 && (
          <section className="td-sec" aria-labelledby="td-since">
            <h2 id="td-since" className="td-h2">Since yesterday</h2>
            <div className="td-since">
              {data.changed.map((c, i) => (
                <div key={i} className="td-since-row">
                  <span className="td-when">{clock(c.at)}</span>
                  <span className={`td-what${c.tone === 'bad' ? ' bad' : ''}`}>
                    {c.run_id ? <button className="td-link" onClick={() => onOpenRun(c.campaign_id!, c.run_id!)}>{c.text}</button> : c.text}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {data.reading.length > 0 && (
          <section className="td-sec" aria-labelledby="td-read">
            <h2 id="td-read" className="td-h2">Worth reading</h2>
            {data.reading.map((r) => (
              <article key={r.item_id} className="td-read">
                <a className="td-read-title" href={r.url} target="_blank" rel="noreferrer">{r.title}</a>
                <span className="td-read-why">{r.project ? `For ${r.project}: ` : ''}{r.why}</span>
              </article>
            ))}
            <button className="td-link td-more" onClick={() => window.dispatchEvent(new CustomEvent('aurora:open-page', { detail: 'library' }))}>The whole feed, in the Library</button>
          </section>
        )}

        {data.needs.length === 0 && data.changed.length === 0 && data.reading.length === 0 && (
          <p className="td-quiet">Start with a question, or ask Pippin below what you would like to do.</p>
        )}

        <Ask />
      </div>
    </div>
  )
}

/** One thing that waits for you, answered here when it can be. */
export function Need({ need, onDone, onOpenRun }: { need: TodayNeed; onDone: () => void; onOpenRun: (cid: string, rid: string) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [replying, setReplying] = useState(false)
  const [text, setText] = useState('')
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null)
    try { await fn(); onDone() } catch (e) { setError(describeError(e)) } finally { setBusy(false) }
  }
  const offers = (a: GateAction) => (need.options ?? []).includes(a)
  const decide = (a: GateAction, message = '') => act(() => api.experiments.decide(need.campaign_id!, need.run_id!, a, message, need.id))

  const meta = need.kind === 'decision' ? `${SOURCE[need.source ?? ''] ?? 'A decision'} · ${need.project} · ${need.run}`
    : need.kind === 'alert' ? (need.alert === 'retraction' ? 'A retraction' : 'A new paper close to your aim')
    : 'A skill to approve'
  return (
    <article className="td-need">
      <p className="td-need-meta"><span className="td-dot" aria-hidden="true" /><span className="td-need-kind">Waiting for you</span> · {meta} · {ago(need.at)}</p>
      <p className="td-need-title">{need.title}</p>
      {need.detail && <p className="td-need-detail">{need.detail.length > 420 ? `${need.detail.slice(0, 420)}…` : need.detail}</p>}

      {need.kind === 'decision' && !replying && (
        <div className="td-actions">
          {offers('continue') && <button className="td-btn primary" disabled={busy} onClick={() => decide('continue')}>Continue</button>}
          {offers('propose') && <button className="td-btn primary" disabled={busy} onClick={() => onOpenRun(need.campaign_id!, need.run_id!)}>Choose the direction</button>}
          {offers('instruct') && <button className="td-btn" disabled={busy} onClick={() => setReplying(true)}>Reply with an instruction</button>}
          {offers('stop') && <button className="td-btn quiet" disabled={busy} onClick={() => decide('stop')}>Stop the run</button>}
          <button className="td-btn quiet" onClick={() => onOpenRun(need.campaign_id!, need.run_id!)}>Open the run</button>
        </div>
      )}
      {need.kind === 'decision' && replying && (
        <form className="td-reply" onSubmit={(e) => { e.preventDefault(); if (text.trim()) decide('instruct', text.trim()) }}>
          <textarea rows={2} value={text} autoFocus placeholder="What should the run do instead?" aria-label="Your instruction"
            onChange={(e) => setText(e.target.value)} />
          <div className="td-actions">
            <button className="td-btn primary" disabled={busy || !text.trim()}>Send</button>
            <button type="button" className="td-btn quiet" onClick={() => setReplying(false)}>Cancel</button>
          </div>
        </form>
      )}
      {need.kind === 'alert' && (
        <div className="td-actions">
          {(need.ref?.url || need.ref?.doi) && <a className="td-btn" href={need.ref.url || `https://doi.org/${need.ref.doi}`} target="_blank" rel="noreferrer">Open the paper</a>}
          <button className="td-btn quiet" disabled={busy} onClick={() => act(() => api.memory.alertSeen(need.id))}>Seen</button>
        </div>
      )}
      {need.kind === 'skill' && (
        <div className="td-actions">
          <button className="td-btn primary" disabled={busy} onClick={() => act(() => api.skills.decide(need.id, true))}>Approve</button>
          <button className="td-btn quiet" disabled={busy} onClick={() => act(() => api.skills.decide(need.id, false))}>Keep it in its project</button>
        </div>
      )}
      {error && <p className="td-error">{error}</p>}
    </article>
  )
}

/** Ask Pippin, the same conversation as the orb's; the answer shows here. */
function Ask() {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<(AssistantTurn & { reply: string; said: string }) | null>(null)
  const [error, setError] = useState<string | null>(null)
  const send = async (e: React.FormEvent) => {
    e.preventDefault()
    const t = text.trim()
    if (!t || busy) return
    setBusy(true); setError(null)
    try { setAnswer(await api.assistant.say(t)); setText('') } catch (err) { setError(describeError(err)) } finally { setBusy(false) }
  }
  return (
    <section className="td-ask" aria-label="Ask Pippin">
      {answer && (
        <div className="td-answer">
          <p className="td-answer-q">{answer.said}</p>
          <p className="td-answer-a">{answer.reply}</p>
          <ProposedActions actions={answer.actions ?? []} />
          {(answer.links ?? []).map((l) => <a key={l.path} className="td-btn" href={l.path}>{l.label}</a>)}
        </div>
      )}
      <form className="td-ask-box" onSubmit={send}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder={busy ? 'Looking…' : 'Ask Pippin about any of this'}
          aria-label="Ask Pippin" disabled={busy} />
        <span className="td-ask-key">⏎</span>
      </form>
      {error && <p className="td-error">{error}</p>}
    </section>
  )
}

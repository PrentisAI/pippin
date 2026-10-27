/** The Mentor's side of a brainstorm: the research question written down from the conversation (to confirm, or
 *  to take one of the AI's candidates), and the research plan the guided conversation builds beside it, from the
 *  researcher's own words. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { FinerKey, FinerScores, MsgWidget, PlanInsight, ResearchPlan } from '../../types'

const FINER: [FinerKey, string][] = [['feasible', 'Feasible'], ['interesting', 'Interesting'], ['novel', 'Novel'], ['ethical', 'Ethical'], ['relevant', 'Relevant']]

function Finer({ scores }: { scores: FinerScores }) {
  const avg = Object.values(scores).reduce((s, v) => s + (v?.score ?? 0), 0) / Math.max(1, Object.keys(scores).length)
  return (
    <span className="pl-finer">{FINER.filter(([k]) => scores[k]).map(([k, name]) => (
      <span key={k} className="pl-fin" title={`${name}: ${scores[k]!.why}`}>{name[0]}{scores[k]!.score}</span>
    ))}<span className="lit-sub">avg {avg.toFixed(1)}</span></span>
  )
}

/** The research question in the conversation: confirm it, or take one of the candidates; or reply to change it. */
export function QuestionCard({ w, busy, onAct, onIdea }: {
  w: Extract<MsgWidget, { type: 'question' }>; busy: boolean; onAct: (choice: string) => void; onIdea?: (id: string) => void
}) {
  const rq = w.rq
  const waiting = w.state === 'waiting'
  if (w.kind === 'candidates') return (
    <div className="pl-cands">
      {(rq?.candidates ?? []).map((c, i) => (
        <div key={i} className="pl-cand">
          <span className="pl-ai">AI-generated</span>
          <b>{c.question}</b>
          <span className="lit-sub">{c.type}</span>
          <Finer scores={c.finer} />
          {waiting && <button className="btn-sm btn-modify" disabled={busy} onClick={() => onAct(`take:${i}`)}>Take this one</button>}
        </div>
      ))}
      {waiting ? <span className="lit-sub">or reply below: change one, or say none of them</span>
        : <span className="lit-sub">{w.state === 'confirmed' ? 'You took one; it is on the board, marked as the AI’s suggestion.' : 'Replaced by the version below.'}</span>}
    </div>
  )
  return (
    <div className="pl-q">
      {rq?.formal && Object.keys(rq.formal).length > 0 && (
        <details className="pl-details"><summary>How a full FINER assessment would score it (the AI’s view, not yours)</summary>
          {FINER.filter(([k]) => rq.formal![k]).map(([k, name]) => (
            <div key={k} className="pl-frow"><span className="pl-fin">{name[0]}{rq.formal![k]!.score}</span><span><b>{name}.</b> {rq.formal![k]!.why}</span></div>
          ))}
          {(rq.quality ?? []).map((q, i) => <div key={i} className="lit-sub">⚠ {q}</div>)}
        </details>
      )}
      {waiting ? (
        <div className="cw-row">
          <button className="btn-sm btn-modify" disabled={busy} onClick={() => onAct('confirm')}>That’s my question</button>
          <span className="lit-sub">or reply below with what to change</span>
        </div>
      ) : w.state === 'confirmed' ? (
        <span className="lit-sub">✓ Confirmed. {w.card_id && onIdea ? <button className="link-btn" onClick={() => onIdea(w.card_id!)}>On the board as your question</button> : 'On the board as your question.'}</span>
      ) : <span className="lit-sub">Replaced by the version below.</span>}
    </div>
  )
}

/** The research plan beside the conversation: the question, then what the researcher worked out, by section. */
export function PlanPane({ chatId, onIdea }: { chatId: string | null; onIdea: (id: string) => void }) {
  const [plan, setPlan] = useState<ResearchPlan | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = useCallback(async () => {
    if (!chatId) return
    try { setPlan(await api.brainstorm.plan(chatId)); setErr(null) } catch (e) { setErr((e as Error).message) }
  }, [chatId])
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t) }, [load])
  if (!chatId) return <div className="panel-empty">Open a brainstorm to see its plan.</div>
  if (err) return <div className="panel-message error">{err}</div>
  if (!plan) return <div className="panel-empty">Loading…</div>
  if (!plan.started && !plan.insights.length) return (
    <div className="cw-empty">
      <b>No research plan yet</b>
      <span>Talk a direction through with the Mentor: say what you are drawn to and that you are not sure yet, or write
        @Mentor. It asks rather than proposes. What you work out yourself is kept here, by section and with your own
        words; when your question is clear, it is written down for you to confirm.</span>
    </div>
  )
  const q = plan.question
  return (
    <div className="pl-pane">
      <span className="lit-sub">{plan.active ? (plan.suggesting ? 'The Mentor is giving suggestions at your request (marked as the AI’s).' : 'The Mentor is guiding this conversation. Say so if you would rather it just answered.')
        : plan.ended ? 'The guided conversation has ended. Ask the Mentor again to go on.' : ''}</span>
      <section className="pl-sec">
        <h4>Research question</h4>
        {q?.state === 'settled' ? (
          <div className="pl-item">
            <b className="pl-question">{q.question}</b>
            <span className="pl-meta"><Origin o={q.origin} />{q.card_id && <button className="link-btn" onClick={() => onIdea(q.card_id!)}>on the board</button>}</span>
          </div>
        ) : q?.state === 'draft' ? (
          <div className="pl-item"><span className="lit-sub">Written down, waiting for your word in the conversation.</span>
            {q.question && <span>{q.question}</span>}</div>
        ) : <span className="lit-sub">Not settled yet.</span>}
      </section>
      {plan.sections.filter((s) => s.id !== 'question').map((s) => {
        const items = plan.insights.filter((i) => i.section === s.id)
        return (
          <section key={s.id} className="pl-sec">
            <h4>{s.name}</h4>
            {items.length ? items.map((i) => <Insight key={i.insight_id} i={i} sections={plan.sections} onChanged={load} />)
              : <span className="lit-sub">Nothing yet.</span>}
          </section>
        )
      })}
      {plan.insights.some((i) => i.section === 'question') && (
        <section className="pl-sec">
          <h4>How you got to the question</h4>
          {plan.insights.filter((i) => i.section === 'question').map((i) => <Insight key={i.insight_id} i={i} sections={plan.sections} onChanged={load} />)}
        </section>
      )}
    </div>
  )
}

function Origin({ o }: { o: 'you' | 'ai' }) {
  return o === 'you' ? <span className="pl-you">Yours</span> : <span className="pl-ai">AI’s suggestion, you took it</span>
}

function Insight({ i, sections, onChanged }: { i: PlanInsight; sections: { id: string; name: string }[]; onChanged: () => void }) {
  const [edit, setEdit] = useState(false)
  const [text, setText] = useState(i.text)
  const [ask, setAsk] = useState(false)
  const [quote, setQuote] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const run = async (f: () => Promise<unknown>) => { try { await f(); setErr(null); onChanged() } catch (e) { setErr((e as Error).message) } }
  return (
    <div className="pl-item">
      {edit ? (
        <>
          <textarea className="rd-in" rows={3} value={text} onChange={(e) => setText(e.target.value)} aria-label="The point" />
          <div className="cw-row">
            <button className="btn-sm btn-modify" disabled={!text.trim()} onClick={() => run(async () => { await api.brainstorm.changeInsight(i.insight_id, { text }); setEdit(false) })}>Save</button>
            <button className="btn-sm lit-btn" onClick={() => { setText(i.text); setEdit(false) }}>Cancel</button>
          </div>
        </>
      ) : <span>{i.text}</span>}
      <span className="pl-meta">
        <Origin o={i.origin} />
        {i.quote && <button className="link-btn" onClick={() => setQuote(!quote)}>{quote ? 'hide your words' : 'your words'}</button>}
        {!edit && <button className="link-btn" onClick={() => setEdit(true)}>edit</button>}
        <select className="pl-move" aria-label="Section" value={i.section} onChange={(e) => run(() => api.brainstorm.changeInsight(i.insight_id, { section: e.target.value }))}>
          {sections.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        {ask ? <><span className="lit-sub">Remove it?</span>
          <button className="link-btn" onClick={() => run(() => api.brainstorm.deleteInsight(i.insight_id))}>Yes</button>
          <button className="link-btn" onClick={() => setAsk(false)}>No</button></>
          : <button className="link-btn" aria-label="Remove" onClick={() => setAsk(true)}>remove</button>}
      </span>
      {quote && <blockquote className="pl-quote">{i.quote}</blockquote>}
      {err && <div className="panel-message error">{err}</div>}
    </div>
  )
}

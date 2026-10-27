/** Zoom in, beside the PDF: twelve questions in order. The AI answers each, but its answer stays covered until you
 *  have answered the question yourself or asked to see it, and only your own answers count as answered. The authors'
 *  path (3) asks for your guess first; quoted passages light up in the paper. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { DeepAnswer, DeepRead } from '../../types'
import { Keyed, Label, Paras } from './bits'

const AGENT_CLS: Record<string, string> = { Skeptic: 'av-o', Methods: 'av-p', Librarian: 'av-b' }
const SUPPORT: Record<string, { text: string; cls: string; title: string }> = {
  stated: { text: 'The authors say so', cls: 'rv-ok', title: 'The introduction states this as their motivation' },
  consistent: { text: 'Fits what they write', cls: 'rv-part', title: 'Not stated, but consistent with the introduction' },
  unsupported: { text: 'Reconstruction only', cls: 'rv-no', title: 'Nothing in the introduction supports it' },
}

export function DeepReadPanel({ paper, space, mapId, onQuote }: {
  paper: string
  space: string
  mapId?: string
  onQuote: (quote: string, page: number) => void
}) {
  const [read, setRead] = useState<DeepRead | null | undefined>(undefined)
  const [q, setQ] = useState(1)
  const [guess, setGuess] = useState('')
  const [ask, setAsk] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await api.review.findRead(paper, space)
      setRead(r)
      if (r && !guess) setGuess(r.guess)
    } catch (e) { setErr((e as Error).message) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paper, space])
  useEffect(() => { load() }, [load])
  const pending = !!read && (read.busy || Object.values(read.answers).some((a) => a.state === 'pending'))
  useEffect(() => {
    if (!pending) return
    const t = setInterval(load, 2000)
    return () => clearInterval(t)
  }, [pending, load])

  const run = async (f: () => Promise<DeepRead>) => {
    setBusy(true); setErr(null)
    try { setRead(await f()) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  if (read === undefined) return <div className="rv-dr"><div className="rd-loading">Loading…</div></div>
  if (read === null) {
    return (
      <div className="rv-dr">
        <b className="rd-h">Deep read</b>
        <p className="rv-p">Twelve questions, in order: the problem and what came before; how the authors may have come to the idea,
          reconstructed only from what they could know (you guess first); then the intuition, the method on an example, its mathematics
          and experiments; and last its weakest assumption, a one-week reproduction, a counterexample and a follow-up idea.</p>
        <p className="rv-p rd-sub">Each question is its own call with the paper's text: about fourteen calls with the brainstorm model.</p>
        {err && <div className="rd-note-warn">{err}</div>}
        <button className="rd-act keep" disabled={busy} onClick={() => run(() => api.review.openRead({ paper, space, map_id: mapId }))}>
          {busy ? 'Starting…' : 'Start the deep read'}</button>
      </div>
    )
  }

  const meta = read.questions.find((x) => x.n === q)!
  const a: DeepAnswer = read.answers[String(q)] ?? { state: 'pending' }
  const answered = new Set(read.answered)
  const ready = new Set(read.ready)
  const locked = q === 3 && !read.revealed
  const visible = a.state === 'done'
  return (
    <div className="rv-dr">
      <div className="rd-row-between"><b className="rd-h">Deep read</b>
        <span className="rd-sub">You answered {read.answered.length} of 12 · AI ready {read.ready.length}</span></div>
      <div className="rv-qs" role="tablist" aria-label="Questions">
        {read.questions.map((x) => {
          const st = read.answers[String(x.n)]?.state
          const cls = answered.has(x.n) ? ' done' : st === 'failed' ? ' fail' : ready.has(x.n) ? ' ready' : st === 'pending' ? ' wait' : ''
          const why = answered.has(x.n) ? 'you answered it' : st === 'failed' ? 'the AI answer failed' : ready.has(x.n) ? 'the AI answer is ready' : 'the AI is answering'
          return (
            <button key={x.n} role="tab" aria-selected={q === x.n} title={`${x.title} (${why})`}
              className={`rv-qn${q === x.n ? ' on' : ''}${cls}${x.key ? ' key' : ''}`}
              onClick={() => setQ(x.n)}>{x.n}</button>
          )
        })}
      </div>
      <div className="rv-legend"><span><i className="mine" />You answered</span><span><i className="ready" />AI answer ready, not opened</span></div>

      <div className="rv-qbox">
        <div className="rv-qtitle">
          <span className="rd-sub mono">Q{q}</span><b>{meta.title}</b>
          {meta.agent && <span className="rv-agent"><span className={`rd-av sm ${AGENT_CLS[meta.agent]}`}>{meta.agent.slice(0, 2)}</span>{meta.agent}</span>}
        </div>

        {locked ? (
          <div className="rv-lock">
            <span className="rv-small-h">Before you see the authors' path</span>
            <p className="rv-p">You have the problem (Q1) and what came before (Q2). With only what was known then, how would you have
              come to an idea? A few sentences. Nobody grades it; it is there to compare with.</p>
            <textarea className="rd-in" rows={5} value={guess} onChange={(e) => setGuess(e.target.value)} aria-label="Your guess"
              placeholder="What did they see fail? What new tool had just appeared? What would you have combined?" />
            <div className="rd-actions">
              <button className="rd-act keep" disabled={guess.trim().length < 20 || busy}
                onClick={() => run(() => api.review.changeRead(read.read_id, { guess: guess.trim(), revealed: true }))}>Show the reconstruction</button>
              <button className="rd-act quiet" onClick={() => run(() => api.review.changeRead(read.read_id, { revealed: true }))}>Skip, show it</button>
            </div>
          </div>
        ) : (
          <>
            {q !== 3 && <Mine key={`${read.read_id}:${q}`} n={q} read={read} a={a} busy={busy} run={run} />}
            {a.state === 'pending' ? <div className="rv-wait">The AI is answering from the paper…</div>
              : a.state === 'failed' ? (
                <div className="rd-note-warn">The AI answer failed: {a.error}
                  <button className="rd-act" onClick={() => run(() => api.review.again(read.read_id, [q]))}>Answer again</button></div>
              ) : visible ? (
                <>
                  {q !== 3 && <span className="rv-small-h">The AI's answer</span>}
                  <Answer n={q} a={a} read={read} onQuote={onQuote} run={run} busy={busy} />
                </>
              ) : null}
          </>
        )}

        {!locked && visible && (
          <>
            {(a.followups ?? []).map((f, i) => (
              <div key={i} className="rv-follow"><b>You: {f.q}</b><Paras items={f.paragraphs} onQuote={onQuote} /></div>
            ))}
            <div className="rv-challenge-in">
              <input className="rv-in" value={ask} onChange={(e) => setAsk(e.target.value)} aria-label="Ask more"
                placeholder="Ask more about this question…"
                onKeyDown={(e) => { if (e.key === 'Enter' && ask.trim()) { run(() => api.review.followUp(read.read_id, q, ask.trim())); setAsk('') } }} />
              <button className="rd-act" disabled={!ask.trim() || busy} onClick={() => { run(() => api.review.followUp(read.read_id, q, ask.trim())); setAsk('') }}>Ask</button>
            </div>
            <button className="link-btn" style={{ alignSelf: 'flex-start' }} disabled={busy} onClick={() => run(() => api.review.again(read.read_id, [q]))}>Answer this question again</button>
          </>
        )}
        {err && <div className="rd-note-warn">{err}</div>}
        <div className="rd-row-between">
          <button className="rd-act quiet" disabled={q === 1} onClick={() => setQ(q - 1)}>← Previous</button>
          <button className="rd-act" disabled={q === 12} onClick={() => setQ(q + 1)}>Next →</button>
        </div>
      </div>
    </div>
  )
}

/** The reader's own answer to a question: written first, then compared with the AI's, which stays covered until
 *  they answer or ask to see it. Only their own answers count as answered. */
function Mine({ n, read, a, busy, run }: {
  n: number; read: DeepRead; a: DeepAnswer; busy: boolean; run: (f: () => Promise<DeepRead>) => void
}) {
  const saved = read.mine[String(n)]?.text ?? ''
  const [text, setText] = useState(saved)
  const [editing, setEditing] = useState(!saved)
  const covered = a.state === 'hidden'
  const save = () => run(async () => { const r = await api.review.changeRead(read.read_id, { question: n, answer: text.trim() }); setEditing(!text.trim()); return r })
  if (saved && !editing) return (
    <div className="rv-mine">
      <div className="rd-row-between"><span className="rv-small-h">Your answer</span>
        <button className="link-btn" onClick={() => { setText(saved); setEditing(true) }}>Edit</button></div>
      <div className="rv-mine-text">{saved}</div>
    </div>
  )
  return (
    <div className="rv-mine">
      <span className="rv-small-h">Your answer</span>
      {covered && <p className="rv-p rd-sub">The AI's answer is ready. Write yours first and compare, or look at it now.</p>}
      <textarea className="rd-in" rows={4} value={text} onChange={(e) => setText(e.target.value)} aria-label="Your answer"
        placeholder="A few sentences in your own words." />
      <div className="rd-actions">
        <button className="rd-act keep" disabled={!text.trim() || text.trim() === saved || busy} onClick={save}>
          {covered ? 'Save and compare' : 'Save my answer'}</button>
        {saved && <button className="rd-act quiet" onClick={() => { setText(saved); setEditing(false) }}>Cancel</button>}
        {covered && <button className="rd-act quiet" disabled={busy}
          onClick={() => run(() => api.review.changeRead(read.read_id, { question: n, show: true }))}>Show the AI's answer</button>}
      </div>
    </div>
  )
}

function Answer({ n, a, read, onQuote, run, busy }: {
  n: number; a: DeepAnswer; read: DeepRead; onQuote: (q: string, page: number) => void
  run: (f: () => Promise<DeepRead>) => void; busy: boolean
}) {
  const [sent, setSent] = useState<string | null>(a.card_id ? 'in' : null)
  if (n === 3) {
    const moves = a.moves ?? []
    const ticks = new Set(read.ticks)
    const missed = moves.filter((_, i) => !ticks.has(i))
    return (
      <div className="rv-ans">
        <div className="rd-sub">Written from the introduction (with {a.context?.intro_paragraphs_dropped ?? 0} paragraph(s) on the paper's own
          contributions removed){a.context?.had_related ? ', the related work' : ''}{a.context?.earlier?.length ? ` and ${a.context.earlier.length} earlier papers` : ''}.
          It did not see the method or the experiments.</div>
        <ol className="rv-moves">{moves.map((m, i) => (
          <li key={i} className="rv-move">
            <span className="rv-move-n">{i + 1}</span>
            <span className="rv-move-b">
              <span>{m.text} <Label l={m.label} /></span>
              <span className="rd-sub">{m.kind}{m.support && <> · <span className={SUPPORT[m.support].cls} title={SUPPORT[m.support].title}>{SUPPORT[m.support].text}</span></>}
                {m.quote && <button className="rv-quote" title={`“${m.quote}”`} onClick={() => onQuote(m.quote!, m.page ?? 0)}>p. {(m.page ?? 0) + 1}</button>}</span>
            </span>
          </li>
        ))}</ol>
        <Paras items={a.summary} />
        {(a.authors_say ?? []).length > 0 && <div className="rv-card soft"><span className="rv-small-h">What the authors say themselves</span><Paras items={a.authors_say} onQuote={onQuote} /></div>}
        {read.guess && <div className="rv-card soft"><span className="rv-small-h">Your guess</span><p className="rv-p" style={{ whiteSpace: 'pre-line' }}>{read.guess}</p></div>}
        <div className="rv-card">
          <span className="rv-small-h">Which of these moves had you made?</span>
          {moves.map((m, i) => (
            <label key={i} className="rv-tick"><input type="checkbox" checked={ticks.has(i)}
              onChange={() => { const t = new Set(ticks); if (t.has(i)) t.delete(i); else t.add(i); run(() => api.review.changeRead(read.read_id, { ticks: [...t].sort() })) }} />
              <span>{m.text}</span></label>
          ))}
          {read.ticks.length > 0 && <div className="rd-note">{missed.length === 0 ? 'All of them: you would have found this path.'
            : `You had ${moves.length - missed.length} of ${moves.length}. The one to study: “${missed[0].text}” Where would you have learned it: a talk, a new tool, an adjacent field?`}</div>}
        </div>
      </div>
    )
  }
  return (
    <div className="rv-ans">
      {n === 5 && a.example && <div className="rv-example"><span className="rv-small-h">Running example</span>{a.example}</div>}
      {n === 5 && (a.steps ?? []).length > 0 && <ol className="rv-moves">{a.steps!.map((s, i) => (
        <li key={i} className="rv-move"><span className="rv-move-n">{i + 1}</span><span className="rv-move-b"><b>{s.what}</b><span>{s.example}</span></span></li>))}</ol>}
      {n === 7 && (a.rows ?? []).length > 0 && (
        <table className="rv-table"><thead><tr><th>The question</th><th>The experiment</th><th>The answer</th></tr></thead>
          <tbody>{a.rows!.map((r, i) => <tr key={i}><td>{r.question}</td><td>{r.experiment}</td><td>{r.answer}</td></tr>)}</tbody></table>
      )}
      {n === 10 && a.claim && <div className="rv-example"><span className="rv-small-h">The claim to test</span>{a.claim}</div>}
      {n === 10 && (a.plan ?? []).length > 0 && <div className="rv-plan">{a.plan!.map((p, i) => <div key={i}><b>{p.day}</b> {p.do}</div>)}</div>}
      {n === 12 && a.idea && <div className="rv-idea-big"><span className="rv-small-h">The idea</span><b className="serif">{a.idea}</b>
        {a.source && <span className="rd-sub">Starts from: {a.source}</span>}</div>}
      <Paras items={a.paragraphs} onQuote={onQuote} />
      {n === 12 && (
        <div className="rv-card">
          <span className="rv-small-h">Closest existing work (found by searching)</span>
          {(a.closest ?? []).length ? a.closest!.map((c, i) => <div key={i}><span className="rv-key static">{c.key}</span> <Keyed text={c.difference} /></div>)
            : <span className="rd-sub">{a.novelty_note || 'Nothing close was found; that is not proof nobody did it.'}</span>}
          {a.novelty && <span className={`rv-novel nv-${a.novelty}`}>{a.novelty === 'yes' ? 'Looks new' : a.novelty === 'partly' ? 'Partly done' : a.novelty === 'no' ? 'Done before' : 'Unknown'}</span>}
          {sent ? <span className="rv-ok">✓ In the brainstorm</span>
            : <button className="rd-act keep" style={{ alignSelf: 'flex-start' }} disabled={busy}
              onClick={async () => { try { await api.review.ideaToBrainstorm(read.read_id); setSent('in') } catch { /* shown by the next load */ } }}>Send to the brainstorm</button>}
        </div>
      )}
    </div>
  )
}

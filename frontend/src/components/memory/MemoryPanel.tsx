/** The memory beside what you are doing: what surfaces now (at most three), what is absorbed, what has sunk; a unit
 *  up close (why it is at that depth, its sources, a conflict, what you did with it); and, instead of a summary, a
 *  question to you. The console's Memory page shows it; the desktop app shows the same panel. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { MemAsk, MemDigest, MemItem, MemQuestion, MemStance, MemSurface, MemUnit } from '../../types'

const STANCE: Record<MemStance, string> = { challenges: 'Challenges', adds: 'Adds', supports: 'Supports', background: 'Background', tool: 'Tool' }
const PLATFORM: Record<string, string> = {
  paper: 'Paper', arxiv: 'arXiv', zhihu: 'Zhihu', x: 'X', reddit: 'Reddit', xiaohongshu: 'Xiaohongshu', blog: 'Blog',
  github: 'GitHub', web: 'Web', note: 'Note', text: 'Text',
}
const LANG: Record<string, string> = { zh: 'Chinese', en: 'English', ja: 'Japanese', ko: 'Korean', de: 'German', fr: 'French' }

export function MemoryPanel({ refreshKey = 0, onReport }: { refreshKey?: number; onReport?: () => void }) {
  const [ctxs, setCtxs] = useState<{ current: { label: string; app: string } | null; questions: MemQuestion[] } | null>(null)
  const [ctx, setCtx] = useState<string>('')
  const [data, setData] = useState<MemSurface | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<'now' | 'ask' | 'today'>('now')
  const [sel, setSel] = useState<string | null>(null)

  useEffect(() => {
    api.memory.contexts().then((c) => {
      setCtxs(c)
      setCtx((prev) => prev || (c.current ? 'current' : c.questions[0] ? `q:${c.questions[0].question_id}` : ''))
    }).catch((e) => setErr((e as Error).message))
  }, [refreshKey])

  const load = useCallback(async () => {
    if (!ctx) return
    setBusy(true); setErr(null)
    try { setData(await api.memory.surface(ctx)) } catch (e) { setErr((e as Error).message); setData(null) } finally { setBusy(false) }
  }, [ctx])
  useEffect(() => { load() }, [load, refreshKey])

  if (!ctxs) return <div className="mem-panel"><div className="panel-empty">Loading…</div></div>
  const chips = [...(ctxs.current ? [{ key: 'current', chip: 'Reading', title: ctxs.current.label }] : []),
    ...ctxs.questions.map((q) => ({ key: `q:${q.question_id}`, chip: q.label, title: q.text }))]
  return (
    <div className="mem-panel">
      <div className="mem-head">
        <b className="mem-logo">Memory</b>
        <div className="mem-tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'now'} className={tab === 'now' ? 'on' : ''} onClick={() => setTab('now')}>Now</button>
          <button role="tab" aria-selected={tab === 'ask'} className={tab === 'ask' ? 'on' : ''} onClick={() => { setTab('ask'); setSel(null) }}>Ask me</button>
          <button role="tab" aria-selected={tab === 'today'} className={tab === 'today' ? 'on' : ''} onClick={() => { setTab('today'); setSel(null) }}>Today</button>
        </div>
        {onReport && <button className="link-btn" onClick={onReport}>This week →</button>}
      </div>
      {chips.length === 0 ? (
        <div className="cw-empty"><b>Nothing to organise by yet</b>
          <span>Add a research question (or settle one with the Mentor), or say what you are reading. The memory then shows
            what bears on it.</span></div>
      ) : (
        <>
          <div className="mem-chips">
            <span className="lit-sub">Context</span>
            {chips.map((c) => <button key={c.key} title={c.title} className={`mem-chip${ctx === c.key ? ' on' : ''}`}
              onClick={() => { setCtx(c.key); setSel(null) }}>{c.chip}</button>)}
          </div>
          {data && <div className="mem-ctx"><span className="lit-sub">{data.context.sub}</span><b>{data.context.label}</b>
            <span className="lit-sub">{data.surface.length} surfaced · {data.conflicts} conflict{data.conflicts === 1 ? '' : 's'} · {data.absorbed.length} absorbed · {data.n_sunk} sunk</span></div>}
        </>
      )}
      {err && <div className="panel-message error">{err}</div>}
      {tab === 'today' ? <Today refreshKey={refreshKey} onPick={(id) => { setTab('now'); setSel(id) }} /> : tab === 'now' ? (
        data ? (
          <div className={`mem-body${busy ? ' busy' : ''}${sel ? ' with-detail' : ''}`}>
            <div className="mem-layers">
            <section className="mem-zone surface">
              <h4>Surface: bears on this now (at most 3)</h4>
              {data.surface.length ? data.surface.map((it) => <Big key={it.unit_id} it={it} on={sel === it.unit_id} onPick={() => setSel(sel === it.unit_id ? null : it.unit_id)} />)
                : <span className="lit-sub">Nothing in your memory bears directly on this.</span>}
            </section>
            <section className="mem-zone">
              <h4>Absorbed: read for you, you need not read it</h4>
              {data.absorbed.map((it) => (
                <button key={it.unit_id} className={`mem-row${sel === it.unit_id ? ' on' : ''}`} onClick={() => setSel(sel === it.unit_id ? null : it.unit_id)}>
                  <span>{it.claim}</span><span className="lit-sub">{it.sources} src</span></button>
              ))}
              {!data.absorbed.length && <span className="lit-sub">Nothing.</span>}
            </section>
            <section className="mem-zone deep">
              <h4>Sunk: unrelated now, forgotten unless it matters again</h4>
              <div className="mem-sunk">{data.sunk.map((it) => (
                <button key={it.unit_id} className={`mem-dot${sel === it.unit_id ? ' on' : ''}`} onClick={() => setSel(sel === it.unit_id ? null : it.unit_id)}>{it.claim}</button>
              ))}</div>
              {data.n_sunk > data.sunk.length && <span className="lit-sub">and {data.n_sunk - data.sunk.length} more; search finds them</span>}
            </section>
            </div>
            {sel && <Detail key={sel} id={sel} item={[...data.surface, ...data.absorbed, ...data.sunk].find((x) => x.unit_id === sel)}
              onPick={setSel} onClose={() => setSel(null)} onChanged={load} />}
          </div>
        ) : busy ? <div className="panel-empty">Ranking your memory for this…</div> : null
      ) : ctx ? <AskMe key={ctx} ctx={ctx} onPick={(id) => { setTab('now'); setSel(id) }} /> : null}
    </div>
  )
}

function Badges({ it }: { it: MemItem }) {
  return (
    <span className="mem-badges">
      <span className={`mem-stance st-${it.stance}`}>{STANCE[it.stance]}</span>
      {it.clash_with && <span className="mem-clash">Conflict</span>}
      {it.moved && <span className="mem-moved">Changed my view</span>}
    </span>
  )
}

function Big({ it, on, onPick }: { it: MemItem; on: boolean; onPick: () => void }) {
  return (
    <button className={`mem-big${on ? ' on' : ''}${it.clash_with ? ' clash' : ''}`} onClick={onPick}>
      <span className="mem-line"><Badges it={it} /><span className="lit-sub">{it.platforms.map((p) => PLATFORM[p] ?? p).join(' · ')}{it.sources > 1 ? ` · ${it.sources} sources` : ''}</span></span>
      <b>{it.claim}</b>
      {it.answers.length > 0 && <span className="mem-earlier">You said before: “{it.answers[it.answers.length - 1].answer}”</span>}
    </button>
  )
}

function Detail({ id, item, onPick, onClose, onChanged }: {
  id: string; item?: MemItem; onPick: (id: string) => void; onClose: () => void; onChanged: () => void
}) {
  const [u, setU] = useState<MemUnit | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [snap, setSnap] = useState<string>('')
  useEffect(() => { api.memory.unit(id).then(setU).catch((e) => setErr((e as Error).message)) }, [id])
  const act = async (f: () => Promise<MemUnit>) => { try { setU(await f()); onChanged() } catch (e) { setErr((e as Error).message) } }
  if (!u) return <div className="mem-detail">{err ? <div className="panel-message error">{err}</div> : 'Loading…'}</div>
  const clash = u.links.find((l) => l.rel === 'contradicts')
  return (
    <div className="mem-detail" role="dialog" aria-label="Idea unit">
      <div className="mem-line">{item && <Badges it={item} />}<span className="lit-sub">{item ? { surface: 'On the surface', absorbed: 'Absorbed', sunk: u.sunk ? 'You let it sink' : 'Deep down' }[item.layer] : ''}</span>
        <button className="link-btn" aria-label="Close" onClick={onClose}>×</button></div>
      <b className="mem-title">{u.claim}</b>
      {u.gist && <p className="rv-p">{u.gist}</p>}
      {item && <div className="mem-why"><span className="rv-small-h">Why at this depth</span><span>{item.why}</span></div>}
      {clash && (
        <div className="mem-clashbox"><span className="rv-small-h">Contradicts another of your saves</span>
          <span>{clash.claim}</span>{clash.note && <span className="lit-sub">{clash.note}</span>}
          <button className="btn-sm lit-btn" onClick={() => onPick(clash.unit_id)}>See that one</button></div>
      )}
      <span className="rv-small-h">{u.evidence.length} source{u.evidence.length > 1 ? 's' : ''}{u.evidence.length > 1 ? ' · merged into one idea unit' : ''}</span>
      {u.evidence.map((e) => (
        <div key={e.evidence_id} className="mem-src">
          <div className="mem-line"><b>{PLATFORM[e.platform] ?? e.platform}</b>{e.lang && <span className="lit-sub">{LANG[e.lang] ?? e.lang}</span>}
            {e.stance !== 'asserts' && <span className="lit-sub">{e.stance}</span>}
            <span className="mem-spacer" />
            {e.url && <a href={e.url} target="_blank" rel="noreferrer" className="link-btn">original</a>}
            <button className="link-btn" onClick={async () => { if (open === e.evidence_id) { setOpen(null); return } setOpen(e.evidence_id); setSnap((await api.memory.snapshot(e.source_id)).text) }}>
              {open === e.evidence_id ? 'hide snapshot' : 'snapshot archived'}</button>
            {u.evidence.length > 1 && <button className="link-btn" title="It was merged by mistake: make it a unit of its own" onClick={() => act(() => api.memory.split(e.evidence_id).then(() => api.memory.unit(id)))}>not the same</button>}
          </div>
          {e.title && <span className="lit-sub">{e.title}</span>}
          <blockquote className="pl-quote">{e.quote}</blockquote>
          {open === e.evidence_id && <div className="mem-snap">{snap.slice(0, 6000)}</div>}
        </div>
      ))}
      {u.links.filter((l) => l.rel === 'refines').map((l) => (
        <div key={l.unit_id} className="mem-line"><span className="lit-sub">{l.direction === 'out' ? 'Qualifies' : 'Qualified by'}</span>
          <button className="link-btn" onClick={() => onPick(l.unit_id)}>{l.claim.slice(0, 90)}</button>
          <button className="link-btn" title="They make the same claim: one unit, all the sources" onClick={() => act(() => api.memory.merge(l.unit_id, id))}>same claim, merge</button></div>
      ))}
      {u.questions.length > 0 && <span className="lit-sub">Bears on {u.questions.map((q) => q.label).join(', ')}</span>}
      {u.answers.length > 0 && <div className="mem-why"><span className="rv-small-h">What you said about it</span>
        {u.answers.map((a) => <span key={a.ask_id}>“{a.answer}”</span>)}</div>}
      <div className="cw-row">
        <button className={`btn-sm ${u.moved ? 'btn-modify' : 'lit-btn'}`} onClick={() => act(() => api.memory.mark(id, u.moved ? 'unmoved' : 'moved'))}>
          {u.moved ? 'Noted: it changed my view' : 'This changed my view'}</button>
        <button className="btn-sm lit-btn" onClick={() => act(() => api.memory.mark(id, u.sunk ? 'raised' : 'sunk'))}>{u.sunk ? 'Raise it back' : 'Let it sink'}</button>
      </div>
      {err && <div className="panel-message error">{err}</div>}
    </div>
  )
}

function AskMe({ ctx, onPick }: { ctx: string; onPick: (id: string) => void }) {
  const [a, setA] = useState<MemAsk | null>(null)
  const [text, setText] = useState('')
  const [state, setState] = useState<'loading' | 'ready' | 'answered' | 'later' | 'none'>('loading')
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    api.memory.ask(ctx).then((x) => { setA(x); setState('ready') })
      .catch((e) => { setErr((e as Error).message); setState('none') })
  }, [ctx])
  if (state === 'loading') return <div className="panel-empty">Thinking of a question from what you saved…</div>
  if (state === 'none' || !a) return <div className="cw-empty"><b>No question yet</b><span>{err}</span></div>
  return (
    <div className="mem-ask">
      <span className="lit-sub">Not a summary: a question from what you saved, for this context.</span>
      <p className="mem-q">{a.question}</p>
      <div className="mem-line"><span className="lit-sub">Based on</span>
        {(a.basis_units ?? []).map((b) => <button key={b.unit_id} className="bs-ichip" onClick={() => onPick(b.unit_id)}>{b.claim.slice(0, 50)}</button>)}</div>
      {state === 'answered' ? (
        <div className="mem-why"><span className="rv-small-h">Kept in your thinking record</span><span>{text.trim() || '(You wrote nothing; only the question was kept.)'}</span>
          <span className="lit-sub">The next time these surface, today’s answer comes with them, so you can see whether your view has moved.</span></div>
      ) : state === 'later' ? <span className="lit-sub">Later, then.</span> : (
        <>
          <textarea className="rd-in" rows={3} value={text} onChange={(e) => setText(e.target.value)} aria-label="Your answer" placeholder="One or two sentences are enough." />
          <div className="cw-row">
            <button className="btn-sm btn-modify" onClick={async () => { try { await api.memory.answer(a.ask_id, text); setState('answered') } catch (e) { setErr((e as Error).message) } }}>Keep my answer</button>
            <button className="btn-sm lit-btn" onClick={() => setState('later')}>Later</button>
          </div>
        </>
      )}
      {err && <div className="panel-message error">{err}</div>}
    </div>
  )
}

const VIA: Record<string, string> = { feed: 'from what you follow', extension: 'saved in the browser', orb: 'dropped on the desktop', manual: 'saved by hand', voice: 'said' }

/** The daily digest: what came in over the last day, by question. */
function Today({ refreshKey, onPick }: { refreshKey: number; onPick: (id: string) => void }) {
  const [d, setD] = useState<MemDigest | null>(null)
  useEffect(() => { api.memory.digest(24).then(setD).catch(() => setD(null)) }, [refreshKey])
  if (!d) return <div className="panel-empty">Loading…</div>
  if (!d.groups.length) return <div className="cw-empty"><b>Nothing new today</b><span>What you save, and what the people and threads you follow post, comes here once a day.</span></div>
  return (
    <div className="mem-body">
      <span className="lit-sub">The last 24 hours: {d.sources} saved, {d.from_feed} of them from what you follow.</span>
      {d.groups.map((g) => (
        <section key={g.question_id || 'other'} className="mem-zone">
          <h4>{g.label ? `${g.label} · ${g.text}` : g.text}</h4>
          {g.conflicts.map((c, i) => <button key={i} className="mem-row clash" onClick={() => onPick(c.a_id)}><span>{c.a} ⟷ {c.b}</span></button>)}
          {g.merged.map((u) => <button key={u.unit_id} className="mem-row" onClick={() => onPick(u.unit_id)}>
            <span>{u.claim}</span><span className="lit-sub">+1 source · {u.sources} now</span></button>)}
          {g.new.map((u) => <button key={u.unit_id} className="mem-row" onClick={() => onPick(u.unit_id)}>
            <span>{u.claim}</span><span className="lit-sub">new · {VIA[u.via] ?? u.via}</span></button>)}
        </section>
      ))}
    </div>
  )
}

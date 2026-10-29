/** The notebook beside what you are doing: what surfaces now (at most three), what is absorbed, what has sunk; a unit
 *  up close (why it is at that depth, its sources, a conflict, what you did with it); and, instead of a summary, a
 *  question to you. The console's Notebook page shows it; the desktop app shows the same panel. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { MemAlert, MemVoiceTurn, MemAsk, MemDigest, MemItem, MemQuestion, MemStance, MemSurface, MemUnit } from '../../types'

const STANCE: Record<MemStance, string> = { challenges: 'Challenges', adds: 'Adds', supports: 'Supports', background: 'Background', tool: 'Tool' }
const PLATFORM: Record<string, string> = {
  paper: 'Paper', arxiv: 'arXiv', zhihu: 'Zhihu', x: 'X', reddit: 'Reddit', xiaohongshu: 'Xiaohongshu', blog: 'Blog',
  github: 'GitHub', web: 'Web', note: 'Your note', text: 'Text',
}
const LANG: Record<string, string> = { zh: 'Chinese', en: 'English', ja: 'Japanese', ko: 'Korean', de: 'German', fr: 'French' }

export function MemoryPanel({ refreshKey = 0, onReport, bare = false }: {
  refreshKey?: number; onReport?: () => void
  /** Inside the Notebook page, which has the title already. */
  bare?: boolean
}) {
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

  if (!ctxs) return (
    <div className="mem-panel">
      {err ? <div className="cw-empty"><b>Cannot reach your notebook here</b>
        <span>{window.location.origin} answered: {err}. This console may be an older Pippin, or another program on this port. Point the
          extension (its options) or the desktop app (right click the orb) at the console you started.</span></div>
        : <div className="panel-empty">Loading…</div>}
    </div>
  )
  const chips = [...(ctxs.current ? [{ key: 'current', chip: 'Reading', title: ctxs.current.label }] : []),
    ...ctxs.questions.map((q) => ({ key: `q:${q.question_id}`, chip: q.label, title: q.text }))]
  return (
    <div className={`mem-panel${bare ? ' bare' : ''}`}>
      <div className="mem-head">
        {!bare && <b className="mem-logo">Notebook</b>}
        <div className="mem-tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'now'} className={tab === 'now' ? 'on' : ''} onClick={() => setTab('now')}>Now</button>
          <button role="tab" aria-selected={tab === 'ask'} className={tab === 'ask' ? 'on' : ''} onClick={() => { setTab('ask'); setSel(null) }}>Ask me</button>
          <button role="tab" aria-selected={tab === 'today'} className={tab === 'today' ? 'on' : ''} onClick={() => { setTab('today'); setSel(null) }}>Today</button>
        </div>
        {onReport && <button className="link-btn" onClick={onReport}>This week →</button>}
      </div>
      {chips.length === 0 ? (
        <div className="cw-empty"><b>Nothing to organise by yet</b>
          <span>Add a question you are working on (or settle one with the Mentor in a brainstorm). What you saved is then
            sorted by it: what bears on it now, what was read for you, what is unrelated for now. While you read in the browser
            or on the desktop, what you are reading is a context here too.</span></div>
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
      <Alerts refreshKey={refreshKey} />
      <Heard onPick={(id) => { setTab('now'); setSel(id) }} />
      {tab === 'today' ? <Today refreshKey={refreshKey} onPick={(id) => { setTab('now'); setSel(id) }} /> : tab === 'now' ? (
        data ? (
          <div className={`mem-body${busy ? ' busy' : ''}${sel ? ' with-detail' : ''}`}>
            <div className="mem-layers">
            <section className="mem-zone surface">
              <h4>Surface: bears on this now (at most 3)</h4>
              {data.surface.length ? data.surface.map((it) => <Big key={it.unit_id} it={it} on={sel === it.unit_id} onPick={() => setSel(sel === it.unit_id ? null : it.unit_id)} />)
                : <span className="lit-sub">Nothing in your notebook bears directly on this.</span>}
            </section>
            <section className="mem-zone">
              <h4>Absorbed: read for you, you need not read it</h4>
              {data.context.kind === 'question' && <Synthesis key={data.context.key} qid={data.context.key.slice(2)} onPick={setSel} />}
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
        ) : busy ? <div className="panel-empty">Ranking your notebook for this…</div> : null
      ) : ctx ? <AskMe key={ctx} ctx={ctx} onPick={(id) => { setTab('now'); setSel(id) }} /> : null}
    </div>
  )
}

function Badges({ it }: { it: MemItem }) {
  return (
    <span className="mem-badges">
      <span className={`mem-stance st-${it.stance}`}>{STANCE[it.stance]}</span>
      {it.clash_with && <span className="mem-clash">{it.clash_with.yours ? 'Conflicts with your note' : it.yours ? 'Your note is challenged' : 'Conflict'}</span>}
      {it.yours && !it.clash_with && <span className="mem-moved">Your note</span>}
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
  const [copied, setCopied] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ claim: '', gist: '' })
  useEffect(() => { api.memory.unit(id).then(setU).catch((e) => setErr((e as Error).message)) }, [id])
  const act = async (f: () => Promise<MemUnit>) => { try { setU(await f()); onChanged() } catch (e) { setErr((e as Error).message) } }
  if (!u) return <div className="mem-detail">{err ? <div className="panel-message error">{err}</div> : 'Loading…'}</div>
  const clash = u.links.find((l) => l.rel === 'contradicts')
  return (
    <div className="mem-detail" role="dialog" aria-label="Idea unit">
      <div className="mem-line">{item && <Badges it={item} />}<span className="lit-sub">{item ? { surface: 'On the surface', absorbed: 'Absorbed', sunk: u.sunk ? 'You let it sink' : 'Deep down' }[item.layer] : ''}</span>
        <button className="link-btn" aria-label="Close" onClick={onClose}>×</button></div>
      {editing ? (
        <div className="mem-edit">
          <textarea className="rd-in" rows={2} value={draft.claim} onChange={(e) => setDraft({ ...draft, claim: e.target.value })} aria-label="Claim" />
          <textarea className="rd-in" rows={3} value={draft.gist} onChange={(e) => setDraft({ ...draft, gist: e.target.value })} aria-label="Gist" />
          <div className="cw-row"><button className="btn-sm btn-modify" disabled={!draft.claim.trim()}
            onClick={() => act(() => api.memory.editUnit(id, draft)).then(() => setEditing(false))}>Save</button>
            <button className="link-btn" onClick={() => setEditing(false)}>cancel</button></div>
        </div>
      ) : (
        <>
          <b className="mem-title">{u.claim}</b>
          {u.gist && <p className="rv-p">{u.gist}</p>}
          <button className="link-btn" title="Say it in your own words" onClick={() => { setDraft({ claim: u.claim, gist: u.gist }); setEditing(true) }}>edit</button>
        </>
      )}
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
        <button className="btn-sm lit-btn" title="The claim, its sources with their words, and what it conflicts with, for a note (Obsidian, Notion…)"
          onClick={async () => {
            try { await navigator.clipboard.writeText((await api.memory.unitMarkdown(id)).markdown); setCopied(true); setTimeout(() => setCopied(false), 2000) }
            catch (e) { setErr((e as Error).message) }
          }}>{copied ? 'Copied' : 'Copy as Markdown'}</button>
        <ToBrainstorm unitId={id} />
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
  const [sunk, setSunk] = useState<Set<string>>(new Set())
  const [cmpA, setCmpA] = useState<{ id: string; claim: string } | null>(null)
  const [cmp, setCmp] = useState<{ state: 'busy' | 'done' | 'error'; a: string; b: string; text: string } | null>(null)
  useEffect(() => { api.memory.digest(24).then(setD).catch(() => setD(null)) }, [refreshKey])
  if (!d) return <div className="panel-empty">Loading…</div>
  const compareWith = async (id: string, claim: string) => {
    if (!cmpA) { setCmpA({ id, claim }); return }
    if (cmpA.id === id) { setCmpA(null); return }
    setCmp({ state: 'busy', a: cmpA.claim, b: claim, text: '' })
    setCmpA(null)
    try { const r = await api.memory.compare({ unit_id: cmpA.id }, { unit_id: id }); setCmp({ state: 'done', a: r.a, b: r.b, text: r.text }) }
    catch (e) { setCmp({ state: 'error', a: '', b: '', text: (e as Error).message }) }
  }
  const row = (u: { unit_id: string; claim: string }, sub: string) => (
    <div key={u.unit_id} className={`mem-row-wrap${sunk.has(u.unit_id) ? ' gone' : ''}`}>
      <button className="mem-row" onClick={() => onPick(u.unit_id)}><span>{u.claim}</span><span className="lit-sub">{sub}</span></button>
      <span className="mem-acts">
        <button className="link-btn" title="It need not come up again" onClick={async () => { await api.memory.mark(u.unit_id, 'sunk'); setSunk(new Set(sunk).add(u.unit_id)) }}>
          {sunk.has(u.unit_id) ? 'sunk' : 'let it sink'}</button>
        <button className="link-btn" onClick={() => compareWith(u.unit_id, u.claim)}>
          {cmpA?.id === u.unit_id ? 'pick another to compare…' : cmpA ? 'compare with this' : 'how does it differ from…'}</button>
      </span>
    </div>
  )
  return (
    <div className="mem-body">
      <Worth />
      {cmp && <div className="mem-why">
        <span className="rv-small-h">How they differ</span>
        {cmp.state === 'busy' ? <span className="lit-sub">Reading both…</span> : cmp.state === 'error' ? <span className="lit-sub">{cmp.text}</span> : <>
          <span className="lit-sub">“{cmp.a}” and “{cmp.b}”</span><p className="rv-p mem-heard-text">{cmp.text}</p></>}
        <button className="link-btn" onClick={() => setCmp(null)}>close</button></div>}
      {!d.groups.length ? <div className="cw-empty"><b>Nothing new today</b><span>What you save, and what the people and threads you follow post, comes here once a day.</span></div> : <>
        <span className="lit-sub">The last 24 hours: {d.sources} saved, {d.from_feed} of them from what you follow.</span>
        {d.groups.map((g) => (
          <section key={g.question_id || 'other'} className="mem-zone">
            <h4>{g.label ? `${g.label} · ${g.text}` : g.text}</h4>
            {g.conflicts.map((c, i) => <button key={i} className="mem-row clash" onClick={() => onPick(c.a_id)}><span>{c.a} ⟷ {c.b}</span></button>)}
            {g.merged.map((u) => row(u, `+1 source · ${u.sources} now`))}
            {g.new.map((u) => row(u, `new · ${VIA[u.via] ?? u.via}`))}
          </section>
        ))}
      </>}
    </div>
  )
}

/** One or two papers of the day worth reading closely: start a deep read, or put it on your reading list. */
function Worth() {
  const [list, setList] = useState<Awaited<ReturnType<typeof api.memory.worth>>>([])
  const [state, setState] = useState<Record<string, string>>({})
  useEffect(() => { api.memory.worth().then(setList).catch(() => {}) }, [])
  if (!list.length) return null
  return (
    <section className="mem-zone">
      <h4>Worth reading closely</h4>
      {list.map((p) => (
        <div key={p.paper_id} className="mem-why">
          <b>{p.title}</b><span className="lit-sub">{p.byline} · from {p.from}</span>
          {p.why && <span>{p.why}</span>}
          <div className="cw-row">
            <button className="btn-sm btn-modify" disabled={!!state[p.paper_id + ':read']} onClick={async () => {
              setState({ ...state, [p.paper_id + ':read']: 'starting' })
              try { await api.review.openRead({ paper: p.paper_id, space: 'global', start: true }); setState((s) => ({ ...s, [p.paper_id + ':read']: 'started' })) }
              catch (e) { setState((s) => ({ ...s, [p.paper_id + ':read']: (e as Error).message })) }
            }}>{state[p.paper_id + ':read'] === 'started' ? 'Deep read started: open the paper in the reader (Deep read tab)'
              : state[p.paper_id + ':read'] === 'starting' ? 'Starting…' : state[p.paper_id + ':read'] || 'Deep read'}</button>
            <button className="btn-sm lit-btn" disabled={p.to_read || !!state[p.paper_id + ':list']} onClick={async () => {
              await api.memory.toRead(p.paper_id); setState((s) => ({ ...s, [p.paper_id + ':list']: 'on' }))
            }}>{p.to_read || state[p.paper_id + ':list'] ? 'On your reading list' : 'To read'}</button>
          </div>
        </div>
      ))}
    </section>
  )
}

/** What was worth interrupting for, until you have seen it; you say whether it was worth it. */
/** What a question's absorbed claims add up to, starting from where the field stands when there is a map of it. */
function Synthesis({ qid, onPick }: { qid: string; onPick: (id: string) => void }) {
  const [s, setS] = useState<Awaited<ReturnType<typeof api.memory.synthesis>> | null>(null)
  const [state, setState] = useState<'idle' | 'busy' | 'error'>('idle')
  if (state === 'idle' && !s) return <button className="link-btn" onClick={async () => {
    setState('busy')
    try { setS(await api.memory.synthesis(qid)); setState('idle') } catch { setState('error') }
  }}>What it adds up to</button>
  if (state === 'busy') return <span className="lit-sub">Putting it together…</span>
  if (state === 'error' || !s) return <span className="lit-sub">Could not put it together.</span>
  if (!s.text) return <span className="lit-sub">Nothing in your notebook bears on this question yet.</span>
  const refs = Object.fromEntries(s.units.map((u) => [u.ref, u.unit_id]))
  return (
    <div className="mem-why">
      <span className="rv-small-h">What it adds up to{s.map ? ` · with the map “${s.map.topic}”` : ''}</span>
      <p className="rv-p">{s.text.split(/(\[U\d+\])/).map((part, i) => {
        const m = part.match(/^\[(U\d+)\]$/)
        return m && refs[m[1]] ? <button key={i} className="link-btn" onClick={() => onPick(refs[m[1]])}>{part}</button> : part
      })}</p>
    </div>
  )
}

/** Bring a claim onto a brainstorm board, as material (private to you) to file under an idea there. */
function ToBrainstorm({ unitId }: { unitId: string }) {
  const [spaces, setSpaces] = useState<{ space: string; name: string; can_write: boolean }[] | null>(null)
  const [done, setDone] = useState('')
  const [err, setErr] = useState('')
  if (done) return <span className="lit-sub">On the board of {done}</span>
  if (!spaces) return <button className="btn-sm lit-btn" onClick={() => api.brainstorm.spaces().then((s) => setSpaces(s.filter((x) => x.can_write))).catch((e) => setErr((e as Error).message))}>
    Bring to a brainstorm</button>
  return (
    <span className="mem-line">
      <select className="rd-in" defaultValue="" aria-label="Brainstorm space" onChange={async (e) => {
        const s = spaces.find((x) => x.space === e.target.value)
        if (!s) return
        try { await api.memory.toBrainstorm(unitId, s.space); setDone(s.name) } catch (x) { setErr((x as Error).message) }
      }}>
        <option value="" disabled>Which brainstorm?</option>
        {spaces.map((s) => <option key={s.space} value={s.space}>{s.name}</option>)}
      </select>
      {err && <span className="lit-sub">{err}</span>}
    </span>
  )
}

/** What you said to Pippin a moment ago: the full answer, with its sources (the spoken one was short). */
function Heard({ onPick }: { onPick: (id: string) => void }) {
  const [t, setT] = useState<MemVoiceTurn | null>(null)
  const [hidden, setHidden] = useState('')
  const [undone, setUndone] = useState(false)
  useEffect(() => {
    const load = () => api.memory.voiceTurns(1).then((x) => setT((old) => {
      const n = x[0] && Date.now() / 1000 - x[0].at < 15 * 60 ? x[0] : null
      if (n && old?.turn_id !== n.turn_id) setUndone(false)
      return n
    })).catch(() => {})
    load()
    const id = setInterval(load, 4000)
    return () => clearInterval(id)
  }, [])
  if (!t || t.turn_id === hidden) return null
  return (
    <div className="mem-heard" role="status">
      <div className="mem-line"><span className="rv-small-h">You said</span><span className="mem-qtext">“{t.said}”</span>
        <span className="mem-spacer" /><button className="link-btn" aria-label="Close" onClick={() => setHidden(t.turn_id)}>×</button></div>
      <b>{t.speech}</b>
      {t.text && <p className="rv-p mem-heard-text">{t.text}</p>}
      {t.items.map((it, i) => (
        <div key={i} className="mem-src">
          {it.unit_id && <button className="link-btn" onClick={() => onPick(it.unit_id!)}>{it.claim}</button>}
          {!it.unit_id && it.text && <span>{it.n ? `${it.n}. ` : ''}{it.text}</span>}
          {(it.sources ?? []).map((s, j) => (
            <blockquote key={j} className="pl-quote">{s.quote}<span className="lit-sub"> — {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a> : s.title}</span></blockquote>
          ))}
        </div>
      ))}
      {t.undo_id && <div className="mem-line">{undone ? <span className="lit-sub">Undone</span>
        : <button className="btn-sm lit-btn" onClick={async () => { await api.memory.voiceUndo(t.undo_id); setUndone(true) }}>Undo</button>}</div>}
      {t.waiting && <span className="lit-sub">Waiting for your yes or no (say it, or ignore it).</span>}
    </div>
  )
}

function Alerts({ refreshKey }: { refreshKey: number }) {
  const [list, setList] = useState<MemAlert[]>([])
  const load = useCallback(() => { api.memory.alerts(true).then((a) => setList(a.filter((x) => x.tier === 'now'))).catch(() => {}) }, [])
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t) }, [load, refreshKey])
  if (!list.length) return null
  return (
    <div className="mem-alerts">{list.map((a) => (
      <div key={a.alert_id} className={`mem-alert ${a.kind}`}>
        <b>{a.title}</b><span>{a.body}</span>
        <div className="cw-row">
          {a.kind === 'decision' && a.ref?.url && <a className="btn-sm btn-modify" href={a.ref.url} target="_blank" rel="noreferrer">Open the experiment</a>}
          <button className="btn-sm lit-btn" onClick={async () => { await api.memory.alertSeen(a.alert_id); load() }}>Seen</button>
          {a.kind === 'overlap' && <button className="link-btn" onClick={async () => { await api.memory.alertVerdict(a.alert_id, false); load() }}>
            Not worth interrupting me for</button>}
        </div>
      </div>
    ))}</div>
  )
}

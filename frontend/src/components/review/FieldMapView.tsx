/** Zoom out: the map of a research direction, read in the order it was reasoned.
 *  Why it is needed → what the field means by it → the design space → where each paper sits → how well each
 *  capability is solved → where the field moves → what it assumes → what is attempted but not solved → what
 *  to read next. Each section leads with its conclusion; the evidence opens below it.
 *  Opened anywhere with the event `aurora:open-map` ({ space, mapId?, from? }). */
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { FieldMap, FieldMapSummary, Framework, MapContent, MapPaper, Placement } from '../../types'
import { readPaper } from '../reader/open'
import { Bar, Keyed, Label, Paras, Section } from './bits'

export interface MapRequest { space: string; mapId?: string; from?: string }

const STATUS: Record<string, { text: string; cls: string }> = {
  solved: { text: 'Solved', cls: 'st-solved' }, partly: { text: 'Partly solved', cls: 'st-partly' },
  early: { text: 'Early attempts', cls: 'st-early' }, open: { text: 'Open', cls: 'st-open' },
}
const CAUSE: Record<string, string> = {
  signal: 'No training signal', data: 'Missing data', representation: 'Wrong representation', evaluation: 'Evaluation',
  compute: 'Compute', assumption: 'A shared assumption', definition: 'How the problem is defined',
}
const PALETTE = ['#3b6fd4', '#0f7a52', '#c2620a', '#7b5bc7', '#b8336a', '#2a8a9a']

function valueLabel(fw: Framework, dim: string, v?: string) {
  return fw.dims.find((d) => d.id === dim)?.values.find((x) => x.id === v)?.label ?? '–'
}

export default function FieldMapView({ req, onClose }: { req: MapRequest; onClose: () => void }) {
  const [maps, setMaps] = useState<FieldMapSummary[]>([])
  const [mapId, setMapId] = useState<string | null>(req.mapId ?? null)
  const [map, setMap] = useState<FieldMap | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [paperKey, setPaperKey] = useState<string | null>(null)
  const [old, setOld] = useState<{ v: number; content: MapContent } | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const loadList = useCallback(async () => {
    try {
      const l = await api.review.maps(req.space)
      setMaps(l)
      setMapId((cur) => cur ?? l[0]?.map_id ?? null)
    } catch (e) { setError((e as Error).message) }
  }, [req.space])
  useEffect(() => { loadList() }, [loadList])

  const load = useCallback(async () => {
    if (!mapId) { setMap(null); return }
    try { setMap(await api.review.map(mapId)); setError(null) } catch (e) { setError((e as Error).message) }
  }, [mapId])
  useEffect(() => { load() }, [load])
  const building = !!map && (map.state === 'building' || map.busy)
  useEffect(() => {
    const t = setInterval(() => { load(); if (building) loadList() }, building ? 2000 : 15000)
    return () => clearInterval(t)
  }, [load, loadList, building])

  const say = (t: string) => { setToast(t); setTimeout(() => setToast((x) => (x === t ? null : x)), 4500) }
  const act = async (f: () => Promise<FieldMap | unknown>, done?: string) => {
    try {
      const r = await f()
      if (r && typeof r === 'object' && 'map_id' in (r as object)) setMap(r as FieldMap)
      if (done) say(done)
      loadList()
    } catch (e) { say(`That did not work: ${(e as Error).message}`) }
  }

  const content = old?.content ?? map?.content
  const papers = useMemo(() => {
    const m = new Map<string, MapPaper>()
    for (const p of map?.papers ?? []) m.set(p.key, p)
    return m
  }, [map])
  const openKey = useCallback((k: string) => setPaperKey(k), [])
  const fw = content?.frameworks?.find((f) => f.id === content.chosen)

  return (
    <div className="rv">
      <header className="rv-head">
        <button className="rd-tb ghost" onClick={onClose}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>
          {req.from ?? 'Back'}
        </button>
        <div className="rv-title">
          <span className="rd-sub">Map of a field</span>
          <b>{map?.topic ?? (maps.length ? '…' : 'No map yet')}</b>
        </div>
        {map && (
          <>
            {map.versions.length > 0 && (
              <select className="rv-select" value={old?.v ?? map.version} aria-label="Version"
                onChange={async (e) => {
                  const v = Number(e.target.value)
                  if (v === map.version) setOld(null)
                  else setOld({ v, content: await api.review.version(map.map_id, v) })
                }}>
                {[...map.versions].reverse().map((v) => <option key={v.version} value={v.version}>v{v.version} · {v.reason.slice(0, 60)}</option>)}
              </select>
            )}
            {map.mine && !map.space.startsWith('user:') && (
              <button className="rd-tb" onClick={() => act(() => api.review.change(map.map_id, { shared: map.vis !== 'project' }),
                map.vis === 'project' ? 'Private again' : 'Shared with the project')}>{map.vis === 'project' ? 'Shared · make private' : 'Share with the project'}</button>
            )}
          </>
        )}
      </header>
      {toast && <div className="rd-toast" role="status">{toast}</div>}

      <div className="rv-body">
        <nav className="rv-list" aria-label="Maps">
          {maps.map((m) => (
            <button key={m.map_id} className="rv-item" aria-current={m.map_id === mapId}
              onClick={() => { setMapId(m.map_id); setOld(null) }}>
              <span>{m.topic}</span>
              <span className="rd-sub">{m.state === 'building' ? 'being built…' : m.state === 'failed' ? 'stopped' : `v${m.version}`}{m.vis === 'project' ? ' · shared' : ''}</span>
            </button>
          ))}
          {!maps.length && <span className="rd-sub" style={{ padding: 8 }}>No maps in this space yet.</span>}
        </nav>

        <main className="rv-main">
          {error && <div className="rd-note-warn">{error}</div>}
          {!map ? (maps.length ? <div className="rd-loading">Loading…</div> : (
            <div className="cw-empty"><b>No map of a field here yet</b>
              <span>Maps are made in a brainstorm: ask, for example, what has been done on memory for robots. The Librarian proposes to
                map it, tells you each step as it goes, and you can dispute any of it right there.</span></div>
          )) : (
            <>
              {old && <div className="rd-note-warn">Viewing version {old.v}. <button className="link-btn" onClick={() => setOld(null)}>Back to the current version</button></div>}
              <BuildBar map={map} onResume={() => act(() => api.review.run(map.map_id), 'Resumed')} />
              {content && <Chain map={map} content={content} fw={fw} papers={papers} readOnly={!!old || !map.mine || building}
                onKey={openKey} act={act} />}
            </>
          )}
        </main>

        {paperKey && map && (
          <PaperDrawer map={map} fw={fw} k={paperKey} paper={papers.get(paperKey)} outside={map.outside.find((o) => o.key === paperKey)}
            readOnly={!!old || !map.mine || building} onClose={() => setPaperKey(null)}
            onReplace={(pid, note) => act(() => api.review.replace(map.map_id, pid, note), 'Placing it again with your note…')}
            onRead={(key) => readPaper({ paper: key, space: req.space, from: 'Map', tab: 'deep', mapId: map.map_id })} />
        )}
      </div>
    </div>
  )
}

function BuildBar({ map, onResume }: { map: FieldMap; onResume: () => void }) {
  if (map.state === 'ready' && !map.busy) return null
  const active = map.state === 'building' || map.busy
  return (
    <div className={`rv-build${map.state === 'failed' ? ' failed' : ''}`}>
      <div className="rv-steps">
        {map.steps.map((s, i) => (
          <span key={s.id} className={`rv-step${s.done ? ' done' : ''}${map.step === s.id && active ? ' now' : ''}`}>
            <span className="rv-step-n">{s.done ? '✓' : i}</span>{s.name}
          </span>
        ))}
      </div>
      {active && <div className="rd-sub">{map.step_name ? `${map.step_name}…` : 'Working…'} {map.log.slice(-1)[0]?.text}</div>}
      {map.state === 'waiting' && !map.busy && <div className="rd-sub">Waiting for your word in the conversation on the {map.step === 'scope' ? 'scope and criteria' : 'papers chosen'}.</div>}
      {map.state === 'failed' && (
        <div className="rd-actions"><span className="rd-note-warn" style={{ flex: 1 }}>Stopped: {map.error}</span>
          {map.mine && <button className="rd-act keep" onClick={onResume}>Resume from here</button>}</div>
      )}
      {active && <details className="rv-log"><summary>What it is doing</summary>
        {map.log.slice(-14).map((l, i) => <div key={i} className="rd-sub">{new Date(l.at * 1000).toLocaleTimeString()} · {l.text}</div>)}</details>}
    </div>
  )
}

function Chain({ map, content: c, fw, papers, readOnly, onKey, act }: {
  map: FieldMap; content: MapContent; fw?: Framework; papers: Map<string, MapPaper>; readOnly: boolean
  onKey: (k: string) => void; act: (f: () => Promise<unknown>, done?: string) => void
}) {
  const caps = c.need?.capabilities ?? []
  const capName = (id: string) => caps.find((x) => x.id === id)?.name ?? id
  const nav = [['need', 'Why'], ['definition', 'What it means'], ['space', 'Design space'], ['placement', 'The papers'],
    ['progress', 'How far'], ['trends', 'Where it moves'], ['assumptions', 'Assumptions'], ['problems', 'Unsolved problems'], ['read', 'Read next']]
  return (
    <>
      <nav className="rv-nav" aria-label="Sections">
        {nav.map(([id, label], i) => <a key={id} href={`#rv-${id}`} className="rv-navlink"><span className="rv-num sm">{i}</span>{label}</a>)}
      </nav>

      {c.scope && <Found map={map} c={c} onKey={onKey} />}

      {c.need && (
        <Section n={0} id="rv-need" title={`Why ${c.need.term} is needed`}
          headline={<>The map measures the field by {caps.length} capabilities, derived from the tasks that fail without {c.need.term}.</>}
          details={<>
            <Paras items={c.need.why} onKey={onKey} />
            {c.need.examples.length > 0 && <table className="rv-table"><thead><tr><th>Task</th><th>What it needs that is not in view</th></tr></thead>
              <tbody>{c.need.examples.map((e, i) => <tr key={i}><td>{e.task}</td><td>{e.needs}</td></tr>)}</tbody></table>}
            <Paras items={c.need.formal} onKey={onKey} />
          </>} open>
          <div className="rv-caps">{caps.map((x) => (
            <div key={x.id} className="rv-cap"><span className="rv-num sm">{x.id}</span><b>{x.name}</b>
              <span className="rd-sub">{x.info} · {x.timescale}</span><span className="rv-ex">e.g. {x.example}</span></div>
          ))}</div>
        </Section>
      )}

      {c.definition && (
        <Section n={1} id="rv-definition" title={`What the field means by “${c.need?.term ?? 'it'}”`}
          headline={<Definition c={c} />}
          details={<>
            <table className="rv-table"><thead><tr><th>Community</th><th>What it means</th><th>For example</th></tr></thead>
              <tbody>{c.definition.communities.map((x, i) => <tr key={i}><td>{x.community} <Label l={x.label} /></td><td>{x.meaning}</td>
                <td>{x.examples.map((k) => <button key={k} className="rv-key" onClick={() => onKey(k)}>{k}</button>)}</td></tr>)}</tbody></table>
            {c.definition.terms.length > 0 && <div className="rv-terms">{c.definition.terms.map((t, i) => (
              <div key={i}><b>{t.term}</b>: {t.meanings.join(' · ')}</div>))}</div>}
            {c.definition.conflicts.map((x, i) => (
              <div key={i} className="rv-card"><b>{x.question}</b>{x.positions.map((p, j) => (
                <div key={j} className="rd-sub">{p.view} {p.who.map((k) => <button key={k} className="rv-key" onClick={() => onKey(k)}>{k}</button>)}</div>))}</div>
            ))}
            <b className="rv-small-h">Boundaries the working definition draws</b>
            {c.definition.working.boundaries.map((x, i) => (
              <div key={i} className="rv-card"><b>{x.question}</b><span>{x.decision}</span><span className="rd-sub">Why: {x.why} · Puts in or out: {x.consequence}</span></div>
            ))}
            {(c.decided ?? []).length > 0 && <div className="rd-note-warn">Decided by you: {c.decided!.join('; ')}</div>}
          </>}>
          <Challenge map={map} c={c} readOnly={readOnly} onKey={onKey} act={act} />
        </Section>
      )}

      {c.frameworks && fw && (
        <Section n={2} id="rv-space" title="The design space every paper chooses in"
          headline={<>{fw.dims.length} dimensions, each a part of the definition: {fw.dims.map((d, i) => <Fragment key={d.id}>{i ? ', ' : ''}<b>{d.name}</b></Fragment>)}.</>}
          details={<div className="rv-fws">{c.frameworks.map((f) => (
            <div key={f.id} className={`rv-card${f.id === fw.id ? ' on' : ''}`}>
              <div className="rd-row-between"><b>{f.name}</b><span className="rd-sub mono">{f.by}</span></div>
              <span className="rd-sub">{f.dims.map((d) => d.name).join(' · ')}</span>
              <Bar label={`Papers fit ${f.scores.n_fit ?? ''}`} value={f.scores.fit} kind="computed" />
              <Bar label={`Held-out fit ${f.scores.n_held ?? ''}`} value={f.scores.held} kind="computed" />
              <Bar label="Dimensions independent" value={f.scores.orth} kind="computed" />
              <Bar label="Explains success and cost" value={f.scores.disc} kind="judged" />
              {f.scores.disc_reason && <span className="rd-sub">{f.scores.disc_reason}</span>}
              {f.id !== fw.id && !readOnly && <button className="rd-act" onClick={() => act(() => api.review.choose(map.map_id, f.id), 'Using this framework; the rest of the map follows')}>Use this framework</button>}
            </div>
          ))}</div>} open>
          <div className="rv-dims">{fw.dims.map((d, i) => (
            <div key={d.id} className="rv-dim">
              <span className="rd-sub">From the definition: {c.definition?.working.components.find((k) => k.id === d.component)?.name ?? d.component}</span>
              <b>{d.name}</b>
              <div className="rv-vals">{d.values.map((v) => <span key={v.id} className="rv-val" style={{ borderColor: PALETTE[i % PALETTE.length] }} title={v.meaning}>{v.label}</span>)}</div>
              <span className="rd-sub">{d.explains}</span>
            </div>
          ))}</div>
        </Section>
      )}

      {fw && <Placements map={map} c={c} fw={fw} papers={papers} readOnly={readOnly} onKey={onKey} act={act} />}

      {c.progress && (
        <Section n={4} id="rv-progress" title="How far the field has come"
          headline={<>{(['solved', 'partly', 'early', 'open'] as const).map((s) => {
            const n = c.progress!.filter((p) => p.status === s).length
            return n ? <span key={s} className={`rv-st ${STATUS[s].cls}`}>{n} {STATUS[s].text.toLowerCase()}</span> : null
          })}</>}>
          <div className="rv-progress">{c.progress.map((p) => <ProgressRow key={p.capability} p={p} name={capName(p.capability)} onKey={onKey} />)}</div>
        </Section>
      )}

      {(c.timeline || c.trends) && (
        <Section n={5} id="rv-trends" title="Where the field moves"
          headline={c.trends?.[0] ? <><b>{c.trends[0].title}.</b> <Keyed text={c.trends[0].text} onKey={onKey} /> <Label l={c.trends[0].label} /></> : null}
          details={<>
            {(c.trends ?? []).slice(1).map((t, i) => (
              <div key={i} className="rv-card"><div className="rd-row-between"><b>{t.title}</b><span className="rd-sub">evidence: {t.strength}</span></div>
                <span><Keyed text={t.text} onKey={onKey} /> <Label l={t.label} /></span><span className="rd-sub">Based on: <Keyed text={t.based} onKey={onKey} /></span></div>
            ))}
            {fw && <Distribution c={c} fw={fw} />}
          </>} open>
          <div className="rv-timeline">{(c.timeline ?? []).map((t, i) => (
            <div key={i} className="rv-phase"><span className="rd-sub mono">{t.years}</span><b>{t.name}</b>
              <span className="rv-driver">{t.driver}</span><span className="rd-sub"><Keyed text={t.text} onKey={onKey} /></span>
              <span>{t.turning.map((k) => <button key={k} className="rv-key" onClick={() => onKey(k.replace(/[[\]]/g, ''))}>{k.replace(/[[\]]/g, '')}</button>)}</span></div>
          ))}</div>
        </Section>
      )}

      {c.assumptions && (
        <Section n={6} id="rv-assumptions" title="What the field assumes without saying so"
          headline={<>{c.assumptions.map((a, i) => <span key={i} className="rv-assume">“{a.text}” <span className="rd-sub">({a.keys.length} papers)</span></span>)}</>}
          details={c.assumptions.map((a, i) => (
            <div key={i} className="rv-card"><b>{a.text}</b> <Label l={a.label} />
              <dl className="rv-kv"><dt>Why accepted</dt><dd>{a.why}</dd><dt>What would overturn it</dt><dd>{a.overturn}</dd>
                <dt>Relied on by</dt><dd>{a.keys.map((k) => <button key={k} className="rv-key" onClick={() => onKey(k)}>{k}</button>)}</dd>
                <dt>Challenged by</dt><dd>{a.challengers.length ? a.challengers.map((k) => <button key={k} className="rv-key" onClick={() => onKey(k)}>{k}</button>) : 'nobody in the map'}</dd></dl></div>
          ))} />
      )}

      {c.problems && (
        <Section n={7} id="rv-problems" title="Attempted, but not solved well"
          headline={<>{c.problems.length} problems, each important for a capability, admitted or measured as unsolved, and diagnosed. The closest work for each was found by searching.</>}
          details={<>
            {(c.adjacent ?? []).length > 0 && <><b className="rv-small-h">Adjacent fields</b><div className="rv-fws">{c.adjacent!.map((a, i) => (
              <div key={i} className="rv-card"><b>{a.field}</b><span>Same structure: {a.same}</span><span>Borrow: {a.borrow}</span>
                {a.checks.map((x, j) => <span key={j} className="rd-sub"><span className={x.ok === true ? 'rv-ok' : x.ok === false ? 'rv-no' : 'rv-part'}>{x.ok === true ? '✓' : x.ok === false ? '✗' : '~'}</span> {x.text}</span>)}</div>
            ))}</div></>}
            {(c.sparse ?? []).length > 0 && <><b className="rv-small-h">Regions of the design space few papers tried (possible approaches, not problems)</b>
              <div className="rv-sparse">{c.sparse!.slice(0, 18).map((s, i) => <span key={i} className="rv-chip">{s.a} × {s.b} · {s.papers.length}</span>)}</div></>}
          </>} open>
          <div className="rv-problems">{c.problems.map((p) => (
            <div key={p.id} className="rv-problem">
              <div className="rd-row-between"><b className="rv-ptitle">{p.title}</b><span className={`rv-imp imp-${p.importance}`}>{p.importance}</span></div>
              <span className="rd-sub">For: {capName(p.capability)}</span>
              <div className="rv-kv2"><span>Why unsolved</span><span><Keyed text={p.gap} onKey={onKey} /> {p.evidence.map((k) => <button key={k} className="rv-key" onClick={() => onKey(k)}>{k}</button>)}</span>
                <span>Root cause</span><span><span className="rv-cause">{CAUSE[p.diagnosis.cause] ?? p.diagnosis.cause}</span> {p.diagnosis.text}</span>
                <span>Why now</span><span>{p.why_now}</span>
                <span>Approaches</span><span>{p.approaches.map((a, i) => <span key={i} className="rv-approach">{a.text} <span className="rd-sub">({a.from})</span> <Label l={a.label} /></span>)}</span>
                <span>First experiment</span><span>{p.first_experiment}</span>
                <span>Likely failure</span><span>{p.failure}</span>
                <span>Closest work</span><span>{p.closest.length ? p.closest.map((x, i) => <span key={i} className="rv-approach"><button className="rv-key" onClick={() => onKey(x.key)}>{x.key}</button> {x.difference}</span>) : <span className="rd-sub">{p.novelty_note || 'Not found by the search.'}</span>}
                  {p.novelty && <span className={`rv-novel nv-${p.novelty}`}>{p.novelty === 'yes' ? 'Looks new' : p.novelty === 'partly' ? 'Partly done' : p.novelty === 'no' ? 'Done before' : 'Unknown'}</span>}</span></div>
              {p.card_id ? <span className="rv-ok">✓ In the brainstorm</span> : (
                <button className="rd-act keep" style={{ alignSelf: 'flex-start' }}
                  onClick={() => act(async () => { const r = await api.review.toBrainstorm(map.map_id, p.id); return r }, 'Sent to the brainstorm as an idea, with its evidence')}>Send to the brainstorm</button>
              )}
            </div>
          ))}</div>
        </Section>
      )}

      {c.read_next && (
        <Section n={8} id="rv-read" title="Read these closely"
          headline={<>The papers the map's judgements rest on most. Deep-reading one opens it with the twelve questions.</>}>
          <div className="rv-fws">{c.read_next.map((r) => {
            const p = papers.get(r.key) ?? map.outside.find((o) => o.key === r.key)
            return (
              <div key={r.key} className="rv-card">
                <span className="rd-sub mono">[{r.key}] {p?.year ?? ''}</span><b className="serif">{p?.title ?? r.key}</b>
                <span>{r.why}</span><span className="rd-sub">Focus on questions {r.focus.join(', ')}</span>
                <button className="rd-act keep" style={{ alignSelf: 'flex-start' }}
                  onClick={() => readPaper({ paper: r.key, space: map.space, from: 'Map', tab: 'deep', mapId: map.map_id })}>Deep-read</button>
              </div>
            )
          })}</div>
        </Section>
      )}
    </>
  )
}

export function Found({ map, c, onKey }: { map: FieldMap; c: MapContent; onKey: (k: string) => void }) {
  const [show, setShow] = useState<'included' | 'excluded'>('included')
  const sc = c.scope!
  const pr = c.prisma ?? {}
  const ident = pr.identified ?? {}
  const codes = Object.fromEntries((sc.criteria?.exclude ?? []).map((e) => [e.code, e.text]))
  const rows = Object.entries(c.screening ?? {}).map(([pid, d]) => ({ pid, d, p: map.papers.find((x) => x.paper_id === pid) }))
  const shown = rows.filter((r) => show === 'included' ? r.d.d !== 'exclude' : r.d.d === 'exclude')
  const subName = (id: string) => sc.subquestions.find((x) => x.id === id)?.text ?? id
  const stages: [string, number | undefined][] = [
    ['Identified', Object.values(ident).reduce((a, b) => a + b, 0)], ['Distinct, checked', pr.after_dedupe],
    ['Screened', pr.screened], ['Read in full text', pr.full_text_assessed], ['Included', pr.included],
  ]
  const max = Math.max(1, ...stages.map(([, n]) => n ?? 0))
  return (
    <Section n="·" id="rv-found" title="The scope, and how the papers were chosen"
      headline={<>{sc.question} <span className="rd-sub">({sc.subquestions.length} sub-questions; criteria fixed before searching)</span></>}
      details={<>
        <dl className="rv-kv">
          <dt>In scope</dt><dd>{sc.in_scope.join('; ')}</dd>
          <dt>Out of scope</dt><dd>{sc.out_of_scope.join('; ')}</dd>
          <dt>Sub-questions</dt><dd>{sc.subquestions.map((x) => <div key={x.id}><b>{x.id}</b> {x.text}</div>)}</dd>
          <dt>A paper belongs if</dt><dd>{sc.criteria.include.join('; ')}</dd>
          <dt>Left out if (in order)</dt><dd>{sc.criteria.exclude.map((e) => <div key={e.code}><b>{e.code}</b> {e.text}</div>)}</dd>
          {(sc.amended ?? []).length > 0 && <><dt>You amended</dt><dd>{sc.amended!.join(' · ')}</dd></>}
        </dl>
        <div className="rv-tabs2">
          {(['included', 'excluded'] as const).map((t) => <button key={t} className={`rd-chip${show === t ? ' on' : ''}`} onClick={() => setShow(t)}>
            {t === 'included' ? 'Included' : 'Excluded'} <span className="rd-n">{rows.filter((r) => t === 'included' ? r.d.d !== 'exclude' : r.d.d === 'exclude').length}</span></button>)}
        </div>
        <div className="rv-grid-wrap"><table className="rv-table"><thead><tr><th>Paper</th><th>Decision</th><th>Why</th><th>Decided by</th><th>Sub-questions</th></tr></thead>
          <tbody>{shown.map(({ pid, d, p }) => (
            <tr key={pid}><td>{p ? <button className="rv-key" onClick={() => onKey(p.key)}>{p.key}</button> : null} {p?.title ?? ''}</td>
              <td>{d.d === 'exclude' ? `${d.code}${codes[d.code] ? `: ${codes[d.code]}` : ''}` : d.d}{d.kind && d.d !== 'exclude' ? ` · ${d.kind}` : ''}</td>
              <td>{d.why}</td><td className="rd-sub">{d.by}</td><td className="rd-sub">{(d.subs ?? []).map(subName).join('; ')}</td></tr>
          ))}</tbody></table></div>
      </>}>
      <div className="rv-prisma">{stages.map(([label, n]) => (
        <div key={label} className="rv-prisma-row"><span>{label}</span><span className="rv-bar"><span style={{ width: `${((n ?? 0) / max) * 100}%` }} /></span><b className="mono">{n ?? '–'}</b></div>
      ))}</div>
      <span className="rd-sub">From: {Object.entries(ident).map(([k, v]) => `${k} ${v}`).join(' · ')}{pr.not_found ? ` · ${pr.not_found} named but not found in the indexes, dropped` : ''}
        {pr.agreement != null ? ` · the two screenings agreed on ${Math.round(pr.agreement * 100)}%, ${pr.conflicts ?? 0} adjudicated` : ''}</span>
      {(c.coverage?.skew ?? []).map((a, i) => <div key={i} className="rd-note-warn">Skew: {a.n} of {a.known} papers are {a.value} ({a.dimension}).</div>)}
    </Section>
  )
}

export function Definition({ c }: { c: MapContent }) {
  const w = c.definition!.working
  // the sentence, with each component's words marked in its colour
  let parts: { text: string; k?: number }[] = [{ text: w.text }]
  w.components.forEach((comp, i) => {
    const next: typeof parts = []
    for (const p of parts) {
      const at = p.k == null && comp.phrase ? p.text.toLowerCase().indexOf(comp.phrase.toLowerCase()) : -1
      if (at < 0) { next.push(p); continue }
      next.push({ text: p.text.slice(0, at) }, { text: p.text.slice(at, at + comp.phrase.length), k: i }, { text: p.text.slice(at + comp.phrase.length) })
    }
    parts = next
  })
  return (
    <div className="rv-def">
      <span className="rd-sub">Working definition</span>
      <p className="rv-def-text serif">{parts.map((p, i) => p.k == null ? <Fragment key={i}>{p.text}</Fragment>
        : <mark key={i} style={{ background: `${PALETTE[p.k % PALETTE.length]}22`, borderBottom: `2px solid ${PALETTE[p.k % PALETTE.length]}` }}
          title={w.components[p.k].name}>{p.text}</mark>)}</p>
      <div className="rv-vals">{w.components.map((k, i) => <span key={k.id} className="rv-val" style={{ borderColor: PALETTE[i % PALETTE.length] }}>{k.name}</span>)}</div>
    </div>
  )
}

function Challenge({ map, c, readOnly, onKey, act }: {
  map: FieldMap; c: MapContent; readOnly: boolean; onKey: (k: string) => void; act: (f: () => Promise<unknown>, done?: string) => void
}) {
  const [text, setText] = useState('')
  const list = c.challenges ?? []
  return (
    <div className="rv-challenge">
      {list.map((ch) => (
        <div key={ch.id} className="rv-card">
          <b>You: “{ch.text}”</b>
          <Paras items={ch.support} onKey={onKey} />
          <div className="rv-fws">{ch.options.map((o) => (
            <div key={o.id} className={`rv-option${ch.chosen === o.id ? ' on' : ''}`}>
              <b>{o.boundary}</b>
              <span className="rd-sub">Dimensions: {o.dimensions}</span>
              <span className="rd-sub">Papers: {o.papers}{o.joins.length ? ` (a search found ${o.joins.length})` : ''}</span>
              <span className="rd-sub">Progress: {o.progress}</span>
              {ch.state === 'pending' && !readOnly && <button className="rd-act keep" onClick={() => act(() => api.review.apply(map.map_id, ch.id, o.id),
                'Redrawing the boundary; the map is rebuilt from the definition')}>Apply: rebuild from here</button>}
              {ch.chosen === o.id && <span className="rv-ok">✓ Applied</span>}
            </div>
          ))}</div>
        </div>
      ))}
      {!readOnly && (
        <div className="rv-challenge-in">
          <input className="rv-in" value={text} onChange={(e) => setText(e.target.value)} aria-label="Challenge the definition"
            placeholder="Challenge the definition: aren't the model's weights memory too?" />
          <button className="rd-act" disabled={!text.trim()} onClick={() => { act(() => api.review.challenge(map.map_id, text.trim()), 'Working out what that would change…'); setText('') }}>Challenge</button>
        </div>
      )}
      <span className="rd-sub">A challenge first shows what supports it and what each answer would change. Nothing changes until you apply one.</span>
    </div>
  )
}

function Placements({ map, c, fw, papers, readOnly, onKey, act }: {
  map: FieldMap; c: MapContent; fw: Framework; papers: Map<string, MapPaper>; readOnly: boolean
  onKey: (k: string) => void; act: (f: () => Promise<unknown>, done?: string) => void
}) {
  const [x, setX] = useState(fw.dims[0]?.id ?? '')
  const [y, setY] = useState(fw.dims[1]?.id ?? fw.dims[0]?.id ?? '')
  const dx = fw.dims.find((d) => d.id === x) ?? fw.dims[0]
  const dy = fw.dims.find((d) => d.id === y) ?? fw.dims[1] ?? fw.dims[0]
  const byId = new Map([...papers.values()].map((p) => [p.paper_id, p]))
  const all: [string, Placement, string][] = [
    ...Object.entries(fw.placements).map(([k, v]) => [k, v, 'core'] as [string, Placement, string]),
    ...Object.entries(fw.held).map(([k, v]) => [k, v, 'held_out'] as [string, Placement, string]),
    ...Object.entries(fw.joined ?? {}).filter(([k]) => !(k in fw.placements)).map(([k, v]) => [k, v, 'joined'] as [string, Placement, string]),
  ]
  const years = all.map(([pid]) => byId.get(pid)?.year ?? 0).filter(Boolean)
  const lo = Math.min(...years), hi = Math.max(...years)
  const shade = (yr: number) => (hi > lo ? (yr - lo) / (hi - lo) : 1)
  const pending = (c.proposals ?? []).filter((p) => p.state === 'pending' && p.framework === fw.id)
  const strained = all.filter(([, p]) => p.strain).length
  return (
    <Section n={3} id="rv-placement" title="Where each paper sits"
      headline={<>{all.length - strained} of {all.length} papers fit; held-out papers: {fw.scores.n_held}. {pending.length ? `${pending.length} misfit${pending.length > 1 ? 's' : ''} propose${pending.length > 1 ? '' : 's'} a change to the dimensions.` : 'No misfit waits.'}</>}
      tools={map.new_papers > 0 && !readOnly ? <button className="rd-act" onClick={() => act(() => api.review.checkNew(map.map_id), 'Placing the papers that joined…')}>{map.new_papers} new paper{map.new_papers > 1 ? 's' : ''} in the literature: place them</button> : null}
      open>
      {pending.map((p) => (
        <div key={p.id} className="rv-proposal">
          <span><b>{byId.get(p.paper_id)?.key ?? 'A paper'} does not fit.</b> {p.text}</span>
          {!readOnly && <span className="rd-actions">
            <button className="rd-act keep" onClick={() => act(() => api.review.accept(map.map_id, p.id), 'Revising the dimensions; every paper is placed again')}>Revise the dimensions</button>
            <button className="rd-act quiet" onClick={() => act(() => api.review.dismiss(map.map_id, p.id))}>Leave it</button></span>}
        </div>
      ))}
      <div className="rv-axes">
        <label>Across <select className="rv-select" value={dx.id} onChange={(e) => setX(e.target.value)}>{fw.dims.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
        <label>Down <select className="rv-select" value={dy.id} onChange={(e) => setY(e.target.value)}>{fw.dims.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
        <span className="rd-sub">Darker: more recent. Outlined: held out. Dashed: joined later. Amber dot: fits only with a strain.</span>
      </div>
      <div className="rv-grid-wrap">
        <div className="rv-grid" style={{ gridTemplateColumns: `140px repeat(${dx.values.length}, minmax(120px, 1fr))` }}>
          <span />
          {dx.values.map((v) => <span key={v.id} className="rv-colh" title={v.meaning}>{v.label}</span>)}
          {dy.values.map((vy) => (
            <Fragment key={vy.id}>
              <span className="rv-rowh" title={vy.meaning}>{vy.label}</span>
              {dx.values.map((vx) => {
                const here = all.filter(([, p]) => p.values[dx.id] === vx.id && p.values[dy.id] === vy.id)
                return (
                  <div key={vx.id} className={`rv-cell${here.length ? '' : ' empty'}`}>
                    {here.map(([pid, p, role]) => {
                      const pp = byId.get(pid)
                      const yr = pp?.year ?? lo
                      return (
                        <button key={pid} className={`rv-pchip ${role}`} onClick={() => pp && onKey(pp.key)}
                          style={{ background: `rgba(59,111,212,${0.12 + 0.5 * shade(yr)})`, color: shade(yr) > 0.6 ? '#fff' : undefined }}
                          title={`${pp?.title ?? ''}\n${fw.dims.map((d) => `${d.name}: ${valueLabel(fw, d.id, p.values[d.id])}`).join('\n')}${p.strain ? `\nStrain: ${p.note}` : ''}`}>
                          {pp?.key ?? pid}{p.strain && <span className="rv-strain" />}
                        </button>
                      )
                    })}
                  </div>
                )
              })}
            </Fragment>
          ))}
        </div>
      </div>
      {all.some(([, p]) => !p.values[dx.id] || !p.values[dy.id]) && (
        <div className="rd-sub">Not in the grid (no value on these two dimensions): {all.filter(([, p]) => !p.values[dx.id] || !p.values[dy.id])
          .map(([pid]) => byId.get(pid)?.key).filter(Boolean).map((k) => <button key={k} className="rv-key" onClick={() => onKey(k!)}>{k}</button>)}</div>
      )}
    </Section>
  )
}

function ProgressRow({ p, name, onKey }: { p: NonNullable<MapContent['progress']>[number]; name: string; onKey: (k: string) => void }) {
  const [open, setOpen] = useState(false)
  const max = Math.max(1, ...p.limitations.map((l) => l.keys?.length ?? 0))
  return (
    <div className={`rv-prow${open ? ' open' : ''}`}>
      <button className="rv-prow-h" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={`rv-st ${STATUS[p.status].cls}`}>{STATUS[p.status].text}</span>
        <b>{name}</b>
        <span className="rv-prow-t"><Keyed text={p.headline} /></span>
        <span className="rd-sub">{p.papers.length} papers</span>
      </button>
      {open && (
        <div className="rv-prow-b">
          {p.results.length > 0 && <div><b className="rv-small-h">Best results</b>{p.results.map((r, i) => (
            <div key={i}><button className="rv-key" onClick={() => onKey(r.key)}>{r.key}</button> {r.claim} <span className="rd-sub">{r.benchmark}</span></div>))}</div>}
          {p.conditions.length > 0 && <div><b className="rv-small-h">Holds only when</b><Paras items={p.conditions} onKey={onKey} /></div>}
          {p.limitations.length > 0 && <div><b className="rv-small-h">Limitations that recur</b>{p.limitations.map((l, i) => (
            <div key={i} className="rv-lim"><span className="rv-lim-bar" style={{ width: `${((l.keys?.length ?? 0) / max) * 100}%` }} />
              <span>{l.text}</span><span>{(l.keys ?? []).map((k) => <button key={k} className="rv-key" onClick={() => onKey(k)}>{k}</button>)}</span></div>))}</div>}
          {p.conflicts.length > 0 && <div><b className="rv-small-h">Results that disagree</b><Paras items={p.conflicts} onKey={onKey} /></div>}
          {p.cost.length > 0 && <div><b className="rv-small-h">What it costs</b><Paras items={p.cost} onKey={onKey} /></div>}
          {p.eval_gap.length > 0 && <div><b className="rv-small-h">Where evaluation and need part</b><Paras items={p.eval_gap} onKey={onKey} /></div>}
        </div>
      )}
    </div>
  )
}

function Distribution({ c, fw }: { c: MapContent; fw: Framework }) {
  const periods = c.distribution?.periods ?? []
  if (!periods.length) return null
  return (
    <div className="rv-dist">
      <b className="rv-small-h">How the papers move through the design space</b>
      {fw.dims.map((d, di) => (
        <div key={d.id} className="rv-dist-dim">
          <span className="rd-sub">{d.name}</span>
          {periods.map((p) => {
            const counts = d.values.map((v) => p.dims[d.id]?.[v.id]?.length ?? 0)
            const total = counts.reduce((a, b) => a + b, 0) || 1
            return (
              <div key={p.from} className="rv-dist-row">
                <span className="mono rd-sub">{p.from === p.to ? p.from : `${p.from}–${p.to}`}</span>
                <span className="rv-stack">{d.values.map((v, i) => counts[i] ? (
                  <span key={v.id} style={{ width: `${(counts[i] / total) * 100}%`, background: PALETTE[(di + i) % PALETTE.length] }}
                    title={`${v.label}: ${p.dims[d.id][v.id].join(', ')}`}>{counts[i] / total > 0.14 ? v.label : ''}</span>) : null)}</span>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function PaperDrawer({ map, fw, k, paper, outside, readOnly, onClose, onReplace, onRead }: {
  map: FieldMap; fw?: Framework; k: string; paper?: MapPaper; outside?: { paper_id: string; key: string; title: string; year: number | null }
  readOnly: boolean; onClose: () => void; onReplace: (paperId: string, note: string) => void; onRead: (key: string) => void
}) {
  const [note, setNote] = useState('')
  const [fixing, setFixing] = useState(false)
  const pid = paper?.paper_id ?? outside?.paper_id
  const pl = pid && fw ? (fw.placements[pid] ?? fw.held[pid] ?? fw.joined?.[pid]) : undefined
  const card = paper?.card
  return (
    <aside className="rv-drawer" aria-label={`Paper ${k}`}>
      <div className="rd-row-between"><span className="rd-sub mono">[{k}] {paper?.year ?? outside?.year ?? ''} {paper?.venue}</span>
        <button className="rd-act quiet" aria-label="Close" onClick={onClose}>✕</button></div>
      <b className="serif rv-drawer-t">{paper?.title ?? outside?.title ?? 'Not a paper of this map'}</b>
      {paper && <span className="rd-sub">{paper.role === 'held_out' ? 'Held out: used to test the map' : paper.role === 'joined' ? 'Joined after the map was built' : paper.role === 'adjacent' ? 'From an adjacent field' : paper.role === 'benchmark' ? 'A benchmark: evidence of what the field measures, not placed'
        : paper.role === 'survey' ? 'A survey: for calibration, not placed' : paper.role === 'foundation' ? 'A building block the field uses: not placed' : 'In the map'}
        · card from the {card?.source === 'pdf' ? 'PDF' : 'abstract'}</span>}
      {card && <dl className="rv-kv">
        {card.sub_problem && <><dt>Problem</dt><dd>{card.sub_problem}</dd></>}
        {card.choice && <><dt>Core choice</dt><dd>{card.choice}</dd></>}
        {(card.assumptions ?? []).length > 0 && <><dt>Relies on</dt><dd>{card.assumptions!.join('; ')}</dd></>}
        {(card.limitations ?? []).length > 0 && <><dt>Limitations</dt><dd>{card.limitations!.map((l, i) => <div key={i}>{l.text} <span className="rd-sub">{l.stated ? '(stated)' : '(inferred)'}</span></div>)}</dd></>}
        {(card.results ?? []).length > 0 && <><dt>Results</dt><dd>{card.results!.map((r, i) => <div key={i}>{r.claim} <span className="rd-sub">{r.benchmark}</span></div>)}</dd></>}
        {card.term_use && <><dt>Uses the term</dt><dd>{card.term_use}</dd></>}
      </dl>}
      {(card?.quotes ?? []).map((q, i) => <blockquote key={i} className="rv-bq">“{q.text}”</blockquote>)}
      {pl && fw && (
        <div className="rv-card">
          <b>Its place</b>
          {fw.dims.map((d) => <span key={d.id}>{d.name}: <b>{valueLabel(fw, d.id, pl.values[d.id])}</b></span>)}
          {pl.evidence && <span className="rd-sub">Because: “{pl.evidence}” {pl.verified ? <span className="rv-ok">✓ its own words</span> : <span className="rv-part">(not found in its text)</span>}</span>}
          {pl.strain && <span className="rd-note-warn">Fits only with a strain: {pl.note}</span>}
          {pl.corrected && <span className="rd-sub">You said: {pl.corrected}</span>}
          {!readOnly && map.mine && (fixing ? (
            <div className="rv-challenge-in"><input className="rv-in" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What is wrong with this placement?" />
              <button className="rd-act keep" disabled={!note.trim()} onClick={() => { onReplace(pid!, note.trim()); setFixing(false); setNote('') }}>Place it again</button></div>
          ) : <button className="rd-act quiet" style={{ alignSelf: 'flex-start' }} onClick={() => setFixing(true)}>This placement is wrong</button>)}
        </div>
      )}
      <div className="rd-actions">
        <button className="rd-act keep" onClick={() => onRead(k)}>Deep-read</button>
        <button className="rd-act" onClick={() => readPaper({ paper: k, space: map.space, from: 'Map' })}>Open the paper</button>
      </div>
    </aside>
  )
}

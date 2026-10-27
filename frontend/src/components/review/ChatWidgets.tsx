/** Zoom out and zoom in inside the conversation: what can be acted on in a message (start a proposed map or deep
 *  read, apply an answer to a challenge, skip a guess), each step of a map shown at a glance, and the map beside the
 *  brainstorm. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { BsMessage, FieldMap, MsgWidget } from '../../types'
import { openMap, readPaper } from '../reader/open'
import { Definition } from './FieldMapView'
import { QuestionCard } from '../brainstorm/Plan'

const STATUS: Record<string, [string, string]> = {
  solved: ['Solved', 'st-solved'], partly: ['Partly solved', 'st-partly'], early: ['Early attempts', 'st-early'], open: ['Open', 'st-open'],
}

/** Maps shown in several messages are fetched once and refreshed while they are being built. */
const cache = new Map<string, { at: number; map: FieldMap }>()
function useMap(mapId: string | undefined): FieldMap | null {
  const [m, setM] = useState<FieldMap | null>(mapId ? cache.get(mapId)?.map ?? null : null)
  useEffect(() => {
    if (!mapId) return
    let gone = false
    const load = async () => {
      const hit = cache.get(mapId)
      if (hit && Date.now() - hit.at < 4000) { setM(hit.map); return }
      try {
        const map = await api.review.map(mapId)
        cache.set(mapId, { at: Date.now(), map })
        if (!gone) setM(map)
      } catch { /* the map was deleted: the message keeps its text */ }
    }
    load()
    const t = setInterval(load, 6000)
    return () => { gone = true; clearInterval(t) }
  }, [mapId])
  return m
}

export function ActionWidget({ m, space, onChanged, onIdea }: { m: BsMessage; space: string; onChanged: () => void; onIdea?: (id: string) => void }) {
  const w = m.widget as MsgWidget
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const act = async (choice: string) => {
    setBusy(true); setErr(null)
    try { await api.brainstorm.act(m.msg_id, choice); onChanged() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const body = (() => {
    if (w.type === 'proposal') {
      if (w.state === 'pending') return (
        <div className="cw-row">
          <button className="btn-sm btn-modify" disabled={busy} onClick={() => act('start')}>{w.action === 'map_field' ? 'Map it' : 'Read it closely'}</button>
          <button className="btn-sm lit-btn" disabled={busy} onClick={() => act('decline')}>Not now</button>
          <span className="lit-sub">{w.action === 'map_field' ? `About ${w.calls} model calls` : 'About fourteen model calls'}</span>
        </div>)
      if (w.state === 'declined') return <span className="lit-sub">Not now.</span>
      return w.action === 'map_field'
        ? <span className="lit-sub">Started. Each step's finding comes here. <button className="link-btn" onClick={() => openMap({ space, mapId: w.map_id, from: 'Brainstorm' })}>Watch it on the map</button></span>
        : <span className="lit-sub">Reading. I will ask you something when it is done. <button className="link-btn" onClick={() => readPaper({ paper: w.key, space, from: 'Brainstorm', tab: 'deep' })}>Open it in the reader</button></span>
    }
    if (w.type === 'map_step') return <MapStep mapId={w.map_id} step={w.step} space={space} />
    if (w.type === 'map_failed') return w.state === 'resumed' ? <span className="lit-sub">Resumed.</span>
      : <div className="cw-row"><button className="btn-sm btn-modify" disabled={busy} onClick={() => act('resume')}>Resume from here</button></div>
    if (w.type === 'challenge') return <ChallengeOptions w={w} busy={busy} onApply={(o) => act(o)} />
    if (w.type === 'guess') return w.state === 'waiting'
      ? <div className="cw-row"><span className="lit-sub">Write your guess in the message box below.</span>
          <button className="btn-sm lit-btn" disabled={busy} onClick={() => act('skip')}>Skip, show it</button></div>
      : <span className="lit-sub">Answered.</span>
    if (w.type === 'scope' || w.type === 'corpus') {
      if (w.state === 'waiting') return (
        <div className="cw-row">
          <button className="btn-sm btn-modify" disabled={busy} onClick={() => act('go')}>{w.type === 'scope' ? 'Looks right, search' : 'Go on with these papers'}</button>
          <span className="lit-sub">or reply below with what to change</span>
          {w.type === 'corpus' && <button className="link-btn" onClick={() => openMap({ space, mapId: w.map_id, from: 'Brainstorm' })}>See every decision</button>}
        </div>)
      return <span className="lit-sub">{w.state === 'confirmed' ? 'Confirmed.' : 'Replaced by the version below.'}</span>
    }
    if (w.type === 'question') return <QuestionCard w={w} busy={busy} onAct={act} onIdea={onIdea} />
    if (w.type === 'path') return (
      <div className="cw-row"><button className="btn-sm lit-btn" onClick={() => readPaper({ paper: w.paper_id, space, from: 'Brainstorm', tab: 'deep' })}>
        The twelve questions, in the reader</button></div>)
    return null
  })()
  return <div className="cw">{body}{err && <div className="panel-message error">{err}</div>}</div>
}

function MapStep({ mapId, step, space }: { mapId: string; step: string; space: string }) {
  const map = useMap(mapId)
  const c = map?.content
  const open = <button className="link-btn" onClick={() => openMap({ space, mapId, from: 'Brainstorm' })}>On the map</button>
  if (!c) return <div className="cw-row">{open}</div>
  const caps = c.need?.capabilities ?? []
  if (step === 'need') return (
    <div className="cw-caps">{caps.map((x) => <span key={x.id} className="cw-cap" title={`${x.info} · ${x.timescale}. e.g. ${x.example}`}><b>{x.id}</b> {x.name}</span>)}{open}</div>)
  if (step === 'definition' && c.definition) return <div className="cw-box"><Definition c={c} />{open}</div>
  if (step === 'frameworks') {
    const fw = c.frameworks?.find((f) => f.id === c.chosen)
    const pending = (c.proposals ?? []).filter((p) => p.state === 'pending').length
    return fw ? (
      <div className="cw-box">
        <div className="cw-dims">{fw.dims.map((d) => <span key={d.id} className="cw-dim"><b>{d.name}</b> {d.values.map((v) => v.label).join(' · ')}</span>)}</div>
        <span className="lit-sub">{fw.scores.n_fit} papers fit · held out {fw.scores.n_held}{pending ? ` · ${pending} misfit${pending > 1 ? 's' : ''} to look at` : ''}</span>{open}
      </div>) : open
  }
  if (step === 'progress') return (
    <div className="cw-box">{(c.progress ?? []).map((p) => (
      <div key={p.capability} className="cw-prog"><span className={`rv-st ${STATUS[p.status][1]}`}>{STATUS[p.status][0]}</span>
        <span>{caps.find((x) => x.id === p.capability)?.name ?? p.capability}</span></div>
    ))}{open}</div>)
  if (step === 'problems') return <Problems map={map!} />
  if (step === 'read_next') return (
    <div className="cw-row">{(c.read_next ?? []).map((r) => (
      <button key={r.key} className="bs-ichip" onClick={() => readPaper({ paper: r.key, space, from: 'Brainstorm', tab: 'deep', mapId })}>
        Deep-read {r.key}</button>))}{open}</div>)
  return <div className="cw-row">{open}</div>
}

function Problems({ map }: { map: FieldMap }) {
  const [sent, setSent] = useState<Record<string, boolean>>({})
  return (
    <div className="cw-box">{(map.content.problems ?? []).map((p) => (
      <div key={p.id} className="cw-prob">
        <span><b>{p.title}</b> <span className="lit-sub">{p.novelty === 'yes' ? '· looks new' : p.novelty === 'partly' ? '· partly done' : ''}</span></span>
        {p.card_id || sent[p.id] ? <span className="lit-sub">✓ on the board</span>
          : <button className="btn-sm lit-btn" onClick={async () => { await api.review.toBrainstorm(map.map_id, p.id); setSent({ ...sent, [p.id]: true }) }}>Put it on the board</button>}
      </div>
    ))}<button className="link-btn" style={{ alignSelf: 'flex-start' }} onClick={() => openMap({ space: map.space, mapId: map.map_id, from: 'Brainstorm' })}>Why, and the closest work: on the map</button></div>)
}

function ChallengeOptions({ w, busy, onApply }: { w: Extract<MsgWidget, { type: 'challenge' }>; busy: boolean; onApply: (o: string) => void }) {
  const map = useMap(w.map_id)
  const ch = map?.content.challenges?.find((x) => x.id === w.challenge_id)
  if (!ch) return null
  return (
    <div className="cw-options">{ch.options.map((o) => (
      <div key={o.id} className={`cw-option${w.chosen === o.id ? ' on' : ''}`}>
        <b>{o.boundary}</b>
        <span className="lit-sub">Dimensions: {o.dimensions}</span>
        <span className="lit-sub">Papers: {o.papers}{o.joins.length ? ` (a search found ${o.joins.length})` : ''}</span>
        <span className="lit-sub">Progress: {o.progress}</span>
        {w.state === 'pending' ? <button className="btn-sm btn-modify" disabled={busy} onClick={() => onApply(o.id)}>Rebuild the map this way</button>
          : w.chosen === o.id ? <span className="lit-sub">✓ Applied; the map is rebuilt from the definition</span> : null}
      </div>
    ))}</div>
  )
}

/** The space's map beside the brainstorm: the definition, how far the field has come, its unsolved problems, and
 *  where the ideas of this space sit. */
export function MapPane({ space, cards, onIdea }: { space: string; cards: Map<string, { text: string }>; onIdea: (id: string) => void }) {
  const [map, setMap] = useState<FieldMap | null | undefined>(undefined)
  const load = useCallback(async () => { try { setMap(await api.review.pane(space)) } catch { setMap(null) } }, [space])
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t) }, [load])
  if (map === undefined) return <div className="panel-empty">Loading…</div>
  if (map === null) return (
    <div className="cw-empty">
      <b>No map of the field yet</b>
      <span>Ask in the conversation, for example: what has been done on memory for robots? The Librarian proposes to map it:
        why it is needed, what the field means by it, how far it has come, and what is attempted but not solved. Its steps
        come into the conversation, and the map shows here.</span>
    </div>
  )
  const c = map.content
  const fw = c.frameworks?.find((f) => f.id === c.chosen)
  const caps = c.need?.capabilities ?? []
  const ideas = Object.entries(c.ideas ?? {})
  return (
    <div className="cw-pane">
      <div className="bs-row" style={{ justifyContent: 'space-between' }}>
        <b>{map.topic}</b>
        <button className="link-btn" onClick={() => openMap({ space, mapId: map.map_id, from: 'Brainstorm' })}>The whole map</button>
      </div>
      {map.state !== 'ready' && <span className="lit-sub">{map.state === 'failed' ? `Stopped: ${map.error}` : `Being built: ${map.step_name || '…'}`}</span>}
      {c.definition && <Definition c={c} />}
      {c.progress && <div className="cw-box">{c.progress.map((p) => (
        <div key={p.capability} className="cw-prog"><span className={`rv-st ${STATUS[p.status][1]}`}>{STATUS[p.status][0]}</span>
          <span title={p.headline}>{caps.find((x) => x.id === p.capability)?.name ?? p.capability}</span></div>))}</div>}
      {(c.problems ?? []).length > 0 && <div className="cw-box"><span className="rv-small-h">Attempted, not solved well</span>
        {c.problems!.map((p) => <span key={p.id} className="cw-pline">{p.title}</span>)}</div>}
      {fw && ideas.length > 0 && <div className="cw-box"><span className="rv-small-h">Your ideas on the map</span>
        {ideas.map(([cid, i]) => (
          <div key={cid} className="cw-idea">
            <button className="bs-ichip" onClick={() => onIdea(cid)}>{(cards.get(cid)?.text ?? i.text).slice(0, 60)}</button>
            <span className="lit-sub">{fw.dims.filter((d) => i.values[d.id]).map((d) => d.values.find((v) => v.id === i.values[d.id])?.label).join(' · ')}
              {i.problem ? ` · answers: ${c.problems?.find((p) => p.id === i.problem)?.title ?? i.problem}` : ''}</span>
          </div>))}</div>}
    </div>
  )
}

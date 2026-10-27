/** The moves of ideation in the conversation: drafts to edit, take or take further (an agent's, or the round
 *  table's decision), the researcher's own versions to pick from, and an idea's versions with whose words they are. */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { IdeaVersion, MsgWidget } from '../../types'

/** Whose words a version is, in a few words. */
function whose(origin: 'you' | 'ai' | 'edited', share: number): string {
  if (origin === 'you') return 'yours'
  if (origin === 'ai') return 'the AI’s draft as it was'
  const pct = Math.round(share * 100)
  return share >= 0.5 ? `mostly yours (${pct}% your words)` : `an AI draft you edited (${pct}% your words)`
}

export function Whose({ origin, share }: { origin: 'you' | 'ai' | 'edited'; share: number }) {
  return <span className={origin === 'ai' || (origin === 'edited' && share < 0.5) ? 'pl-ai' : 'pl-you'}>{whose(origin, share)}</span>
}

export function DraftsCard({ w, busy, onAct }: {
  w: Extract<MsgWidget, { type: 'drafts' }>; busy: boolean; onAct: (choice: string, text: string) => void
}) {
  const [texts, setTexts] = useState<string[]>(() => w.drafts.map((d) => d.text))
  const [editing, setEditing] = useState<number | null>(null)
  const waiting = w.state === 'waiting'
  const direction = w.purpose === 'direction'
  const label = (i: number) => {
    const d = w.drafts[i]
    if (d.yours) return <span className="pl-you">Yours</span>
    if (direction) return <span className="pl-ai">{d.chosen ? `The chair’s choice · ${d.seat}` : `Presented by ${d.seat}`}</span>
    return <span className="pl-ai">AI draft</span>
  }
  return (
    <div className="mv-drafts">
      {!direction && w.round && w.round > 1 && <span className="lit-sub">Round {w.round}</span>}
      {w.drafts.map((d, i) => {
        const taken = w.state === 'taken' && w.taken === i
        return (
          <div key={i} className={`mv-draft${taken ? ' on' : ''}${!waiting && !taken ? ' dim' : ''}`}>
            <div className="pl-meta">{label(i)}{texts[i] !== d.text && waiting && <span className="lit-sub">edited</span>}</div>
            {editing === i && waiting ? (
              <textarea className="rd-in" rows={4} value={texts[i]} aria-label={`Draft ${i + 1}`} autoFocus
                onChange={(e) => setTexts(texts.map((t, k) => (k === i ? e.target.value : t)))} />
            ) : <div className="mv-text">{taken && w.text ? w.text : texts[i]}</div>}
            {waiting ? (
              <div className="cw-row">
                <button className="btn-sm btn-modify" disabled={busy || !texts[i].trim()} onClick={() => onAct(`take:${i}`, texts[i])}>
                  {direction ? (d.chosen && texts[i] === d.text ? 'Go on with this' : 'Go with this') : 'Take this'}</button>
                <button className="btn-sm lit-btn" disabled={busy || !texts[i].trim()} onClick={() => onAct(`again:${i}`, texts[i])}>Another round from this</button>
                {editing === i ? <button className="link-btn" onClick={() => setEditing(null)}>done editing</button>
                  : <button className="link-btn" onClick={() => setEditing(i)}>edit</button>}
              </div>
            ) : taken ? (
              <span className="lit-sub">✓ {w.action === 'instruct' ? 'The seats go another round from it.' : w.action === 'continue' ? 'The run goes on with it.'
                : w.action === 'propose' ? 'The run goes on with it, as your direction.' : (w as { again?: boolean }).again ? 'Taken; another round is coming.' : 'Taken.'}
                {w.origin && <> It is <Whose origin={w.origin} share={w.share ?? 1} />.</>}</span>
            ) : null}
          </div>
        )
      })}
      {waiting && <span className="lit-sub">{direction ? 'The run waits for you here (or in the approval bar).' : 'Edit any draft in place before you take it, or reply in words.'}</span>}
      {w.state === 'superseded' && <span className="lit-sub">Replaced by the drafts below.</span>}
      {w.state === 'answered' && <span className="lit-sub">Answered elsewhere ({w.action}).</span>}
    </div>
  )
}

export function ChoiceCard({ w, busy, onAct }: { w: Extract<MsgWidget, { type: 'choice' }>; busy: boolean; onAct: (choice: string) => void }) {
  return (
    <div className="mv-drafts">
      <span className="lit-sub">The versions you wrote, as you wrote them. Which one do you want to go on with?</span>
      {w.versions.map((v, i) => (
        <div key={i} className={`mv-draft${w.chosen === i ? ' on' : ''}${w.state !== 'waiting' && w.chosen !== i ? ' dim' : ''}`}>
          <div className="pl-meta"><span className="pl-you">Yours · version {i + 1}</span></div>
          <div className="mv-text">{v}</div>
          {w.state === 'waiting' && <div className="cw-row"><button className="btn-sm btn-modify" disabled={busy} onClick={() => onAct(`pick:${i}`)}>Go on with this one</button></div>}
          {w.chosen === i && <span className="lit-sub">✓ On the board as your idea.</span>}
        </div>
      ))}
      {w.state === 'superseded' && <span className="lit-sub">Replaced by the list below.</span>}
    </div>
  )
}

/** An idea's versions, newest first. */
export function VersionHistory({ cardId, versions: given }: { cardId: string; versions?: IdeaVersion[] }) {
  const [loaded, setLoaded] = useState<IdeaVersion[] | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (given) return
    api.brainstorm.versions(cardId).then(setLoaded).catch(() => setLoaded([]))
  }, [cardId, given])
  const versions = given ?? loaded
  if (!versions || versions.length < 2) return versions?.length === 1
    ? <div className="pl-meta"><Whose origin={versions[0].origin} share={versions[0].share} /></div> : null
  const last = versions[versions.length - 1]
  return (
    <div className="mv-history">
      <div className="pl-meta"><Whose origin={last.origin} share={last.share} />
        <button className="link-btn" onClick={() => setOpen(!open)}>{open ? 'hide' : `${versions.length} versions`}</button></div>
      {open && [...versions].reverse().map((v) => (
        <div key={v.version_id} className="mv-ver"><span className="lit-sub">v{v.n} · <Whose origin={v.origin} share={v.share} /></span>
          <span>{v.text}</span></div>
      ))}
    </div>
  )
}

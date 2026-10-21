import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '../../api/client'
import type { BsCard, BsLink, BsMessage, BsSpace, Relation } from '../../types'
import { Avatar, BULB, LENS, REL_COLOR, Rich, short } from './common'

const TYPE_LABEL: Record<string, string> = { idea: 'Idea', question: 'Question', material: 'Evidence', note: 'Note' }
const RELS: Relation[] = ['supports', 'challenges', 'builds on', 'asks about', 'related']

/** One card up close: its discussion with people and agents, its links, and what can be done with it. */
export function CardDrawer({ cardId, space, cards, projects, onClose, onOpen, onChanged, onPromote, onPaper, onMoved }: {
  cardId: string; space: BsSpace; cards: Map<string, BsCard>; projects: { space: string; name: string }[]
  onClose: () => void; onOpen: (id: string) => void; onChanged: () => void; onPromote: (c: BsCard) => void
  onPaper: (key: string) => void; onMoved: (campaignId: string) => void
}) {
  const [data, setData] = useState<{ card: BsCard; messages: BsMessage[]; pending: boolean; links: BsLink[] } | null>(null)
  const [text, setText] = useState('')
  const [rel, setRel] = useState<Relation>('supports')
  const [to, setTo] = useState('')
  const [moveOpen, setMoveOpen] = useState(false)
  const [newProject, setNewProject] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const body = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    try { setData(await api.brainstorm.thread(cardId)) } catch (e) { setError(describeError(e)) }
  }, [cardId])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const t = setInterval(load, data?.pending ? 1200 : 6000)
    return () => clearInterval(t)
  }, [data?.pending, load])
  const wasPending = useRef(false)
  useEffect(() => { if (wasPending.current && data && !data.pending) onChanged(); wasPending.current = !!data?.pending }, [data, onChanged])
  // new messages scroll into view; opening a card starts at the card
  const seen = useRef<number | null>(null)
  useEffect(() => {
    const n = data?.messages.length
    if (n === undefined) return
    if (seen.current !== null && n > seen.current) body.current?.scrollTo({ top: body.current.scrollHeight, behavior: 'smooth' })
    seen.current = n
  }, [data?.messages.length])
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])

  const act = async (fn: () => Promise<unknown>) => {
    setError(null)
    try { await fn(); await load(); onChanged() } catch (e) { setError(describeError(e)) }
  }
  if (!data) return <aside className="bs-drawer" role="dialog" aria-label="Card"><div className="panel-empty">{error ?? 'Loading…'}</div></aside>
  const c = data.card
  const others = space.cards.filter((x) => x.card_id !== c.card_id)
  const ask = (agent: string, what: string) => act(() => api.brainstorm.reply(c.card_id, `@${agent} ${what}`))

  return (
    <aside className="bs-drawer" role="dialog" aria-label="Card">
      <div className="bs-drawer-head">
        <div className="bs-row"><span className={`bs-badge k-${c.type}`}>{TYPE_LABEL[c.type]}{c.paper ? ' ✓' : ''}</span>
          <span className="lit-sub">by {c.by_name}{c.live ? ' · live in the round table' : ''}</span></div>
        <button className="link-btn" aria-label="Close" onClick={onClose} style={{ fontSize: 18 }}>×</button>
      </div>
      <div className="bs-drawer-body" ref={body}>
        <div className="bs-drawer-text">{c.paper ? c.paper.title : c.text}</div>
        {c.paper && <div className="lit-sub">{c.paper.byline} · <button className="bs-kchip" onClick={() => onPaper(c.paper!.key)}>{c.paper.key}</button></div>}
        {!c.paper && c.source && <div className="lit-sub">{c.source}{c.url && <> · <a href={c.url} target="_blank" rel="noreferrer">Open it</a></>}</div>}
        {c.answer && <div className="lit-sub"><span className="lit-ok">Answered</span> by {c.answered_by_name}: {c.answer}</div>}
        {space.can_write && (
          <div className="bs-row"><span className="lit-sub">Ask</span>
            <button className="btn-sm lit-btn" onClick={() => ask('Skeptic', 'what is the weakest point?')}>Skeptic: critique</button>
            <button className="btn-sm lit-btn" onClick={() => ask('Librarian', 'what evidence is there?')}>Librarian: find evidence</button>
            <button className="btn-sm lit-btn" onClick={() => ask('Methods', 'how would you take this further?')}>Methods: extend</button></div>
        )}
        {data.links.length > 0 && (
          <div className="bs-linked"><div className="bs-eyebrow">Linked</div>
            {data.links.map((l) => {
              const out = l.from_card === c.card_id
              const other = cards.get(out ? l.to_card : l.from_card)
              if (!other) return null
              return (
                <div key={`${l.from_card}-${l.to_card}-${l.rel}`} className="bs-row" style={{ flexWrap: 'nowrap' }}>
                  <button className="bs-linkrow" onClick={() => onOpen(other.card_id)}>
                    <span style={{ color: REL_COLOR[l.rel], whiteSpace: 'nowrap' }}>{out ? `${l.rel} →` : `← ${l.rel} this`}</span>
                    <span>{short(other.paper ? other.paper.title : other.text, 70)}</span></button>
                  {space.can_write && <button className="link-btn" aria-label="Remove the link" onClick={() => act(() => out ? api.brainstorm.unlink(c.card_id, other.card_id, l.rel) : api.brainstorm.unlink(other.card_id, c.card_id, l.rel))}>×</button>}
                </div>
              )
            })}
          </div>
        )}
        {space.can_write && others.length > 0 && (
          <form className="bs-row" onSubmit={(e) => { e.preventDefault(); const target = to || others[0].card_id; act(() => api.brainstorm.link(c.card_id, target, rel)) }}>
            <span className="lit-sub">Link: this</span>
            <select aria-label="Relation" value={rel} onChange={(e) => setRel(e.target.value as Relation)}>{RELS.map((r) => <option key={r}>{r}</option>)}</select>
            <select aria-label="Other card" value={to || others[0].card_id} onChange={(e) => setTo(e.target.value)} style={{ flex: 1, minWidth: 0 }}>
              {others.map((x) => <option key={x.card_id} value={x.card_id}>{short(x.paper ? x.paper.title : x.text, 45)}</option>)}</select>
            <button className="btn-sm lit-btn">Link</button>
          </form>
        )}
        <div className="bs-row">
          {c.type === 'idea' && space.can_write && <button className="btn-sm btn-modify" onClick={() => onPromote(c)}>Turn into a proposal</button>}
          {!space.project && c.mine && (
            <span className="bs-rel"><button className="btn-sm lit-btn" aria-haspopup="menu" aria-expanded={moveOpen} onClick={() => setMoveOpen(!moveOpen)}>Move to a project ▾</button>
              {moveOpen && (
                <div className="bs-menu" role="menu">
                  {projects.map((p) => <button key={p.space} role="menuitem" onClick={() => act(async () => { const r = await api.brainstorm.move(c.card_id, { campaign_id: p.space }); onMoved(r.campaign_id) })}>{p.name}</button>)}
                  <form className="bs-row" style={{ padding: 6, flexWrap: 'nowrap' }} onSubmit={(e) => { e.preventDefault(); if (newProject.trim()) act(async () => { const r = await api.brainstorm.move(c.card_id, { new_project: newProject.trim() }); onMoved(r.campaign_id) }) }}>
                    <input placeholder="Start a new project from it" aria-label="New project name" value={newProject} onChange={(e) => setNewProject(e.target.value)} />
                    <button className="btn-sm lit-btn" disabled={!newProject.trim()}>Start</button></form>
                </div>
              )}</span>
          )}
          {c.mine && (confirmDelete
            ? <><span className="lit-sub">Delete this card and its links?</span><button className="link-btn danger" onClick={() => act(async () => { await api.brainstorm.deleteCard(c.card_id); onClose() })}>Delete</button><button className="link-btn" onClick={() => setConfirmDelete(false)}>Keep</button></>
            : <button className="link-btn danger" onClick={() => setConfirmDelete(true)}>Delete</button>)}
        </div>
        <div className="bs-eyebrow">Discussion</div>
        {data.messages.map((m) => (
          <div key={m.msg_id} className="bs-msg">
            <Avatar name={m.agent ? m.who : m.who_name === 'You' ? 'Y' : m.who_name} agent={space.agents[m.who]} />
            <div className="bs-msg-main">
              <div className="bs-who"><b>{m.who_name}</b>{m.agent ? <span className="lit-sub"> · agent</span> : null}</div>
              {m.steps.length > 0 && <div className="bs-steps">{m.steps.map((s, i) => <div key={i}>{i === 0 ? BULB : LENS}<span>{s}{m.pending && i === m.steps.length - 1 ? '…' : ''}</span></div>)}</div>}
              {m.text && <div className="bs-text"><Rich text={m.text} cards={cards} onIdea={onOpen} onPaper={onPaper} /></div>}
              {m.effects.map(([what, id], i) => <div key={i} className="bs-effect">↳ {what} {cards.get(id) ? <button className="bs-ichip" onClick={() => onOpen(id)}>{short(cards.get(id)!.text, 40)}</button> : 'a card'}</div>)}
            </div>
          </div>
        ))}
        {!data.messages.length && <div className="lit-sub">No discussion yet. Write something, or ask an agent.</div>}
        {error && <div className="modal-error">{error}</div>}
      </div>
      {space.can_write && (
        <form className="bs-drawer-foot" onSubmit={(e) => { e.preventDefault(); const t = text.trim(); if (t) act(async () => { await api.brainstorm.reply(c.card_id, t); setText('') }) }}>
          <input aria-label="Reply" placeholder="Reply, or @Skeptic @Methods @Librarian" value={text} onChange={(e) => setText(e.target.value)} />
          <button className="btn-sm btn-modify" disabled={!text.trim()}>Send</button>
        </form>
      )}
    </aside>
  )
}

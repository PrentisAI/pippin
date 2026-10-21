import { useEffect, useMemo, useRef, useState } from 'react'
import type { BsCard, BsSpace, BsSuggestion } from '../../types'
import { AGENT_ASK, Avatar, VisPill, analyse, short, type Analysis } from './common'

interface IdeaActions {
  onToggle: (id: string) => void
  onDiscuss: (id: string) => void
  onGo: (id: string) => void
  onAsk: (agent: string, id: string) => void
  onPromote: (card: BsCard) => void
  onShare: (id: string) => void
  onAnswer: (qid: string, answer: string) => void
  onOpen: (id: string) => void            // the card's drawer: its discussion, links, more
}

/** The ideas of a space: what's new, each idea as an argument, and material not filed under an idea yet. */
export function IdeasPane({ space, cards, expanded, focus, flash, filter, setFilter, actions, onNew, onBring, onSuggestion, onDismiss, onAttach, activity }: {
  space: BsSpace; cards: Map<string, BsCard>; expanded: Record<string, boolean>; focus: string; flash: string
  filter: 'all' | 'project' | 'mine'; setFilter: (f: 'all' | 'project' | 'mine') => void; actions: IdeaActions
  onNew: (type: 'idea' | 'question' | 'note') => void; onBring: () => void
  onSuggestion: (s: BsSuggestion) => void; onDismiss: (s: BsSuggestion) => void
  onAttach: (cardId: string, to: string, rel: 'supports' | 'challenges') => void; activity: string
}) {
  // ordered when the space is opened, then kept: answering a question does not make the list jump
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const opened = useMemo(() => space.cards.filter((c) => c.type === 'idea').map((c) => [c.card_id, analyse(c, cards, space.links).rank] as const)
    .sort((a, b) => a[1] - b[1]).map(([id]) => id), [space.key])
  const ids = [...opened, ...space.cards.filter((c) => c.type === 'idea' && !opened.includes(c.card_id)).map((c) => c.card_id)]
  const ideas = ids.map((id) => cards.get(id)).filter((c): c is BsCard => !!c)
    .filter((c) => filter === 'all' || !space.project || (filter === 'project') === (c.vis === 'project'))
  const linkedFrom = new Set(space.links.map((l) => l.from_card))
  const loose = space.cards.filter((c) => (c.type === 'material' || c.type === 'note') && !linkedFrom.has(c.card_id))
  const allIdeas = space.cards.filter((c) => c.type === 'idea')
  const [menu, setMenu] = useState(false)

  return (
    <>
      <section className="bs-news">
        <div className="bs-row" style={{ justifyContent: 'space-between' }}><b>What’s new</b>
          <span className="lit-sub">{activity === 'off' ? 'agents only speak when asked' : 'from the agents, as they work'}</span></div>
        {activity === 'off' && !space.suggestions.length ? <div className="lit-sub">Turn on suggestions with “How active” on the left.</div>
          : space.suggestions.length ? space.suggestions.map((s) => (
            <div key={s.sug_id} className="bs-line"><Avatar name={s.by} agent={space.agents[s.by]} /><div className="bs-line-t">{s.short}</div>
              <div className="bs-row" style={{ flexWrap: 'nowrap' }}><button className="btn-sm lit-btn" onClick={() => onSuggestion(s)}>{s.act}</button>
                <button className="link-btn" aria-label="Dismiss" title="Dismiss: the agents bring fewer things like this" onClick={() => onDismiss(s)}>×</button></div></div>
          )) : <div className="lit-sub">Nothing new. You are up to date.</div>}
      </section>
      <div className="bs-row" style={{ justifyContent: 'space-between' }}>
        {space.project ? (
          <div className="lit-chips">{([['all', 'All I can see'], ['project', 'Shared'], ['mine', 'Only mine']] as const).map(([k, l]) => (
            <button key={k} className={`lit-chip${filter === k ? ' on' : ''}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
          ))}</div>
        ) : <span />}
        {space.can_write && (
          <span className="bs-rel"><button className="btn-sm btn-modify" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>+ Add ▾</button>
            {menu && (
              <div className="bs-menu" role="menu" onMouseLeave={() => setMenu(false)}>
                <button role="menuitem" onClick={() => { setMenu(false); onNew('idea') }}>An idea</button>
                <button role="menuitem" onClick={() => { setMenu(false); onNew('question') }}>A question</button>
                <button role="menuitem" onClick={() => { setMenu(false); onNew('note') }}>A note</button>
                <button role="menuitem" onClick={() => { setMenu(false); onBring() }}>A paper from the Library</button>
              </div>
            )}</span>
        )}
      </div>
      <div className="lit-sub">Ideas waiting on a question first, then ready ones, then early ones (as of when you opened this space).</div>
      {ideas.map((c) => (
        <IdeaCard key={c.card_id} idea={c} a={analyse(c, cards, space.links)} space={space} open={!!expanded[c.card_id]}
          focused={focus === c.card_id} flash={flash === c.card_id} threads={space.threads[c.card_id] ?? 0} actions={actions} />
      ))}
      {!ideas.length && <div className="bs-note">No ideas yet. They appear here as the brainstorm finds them, or add one with “+ Add”.</div>}
      {loose.length > 0 && (
        <section className="bs-news">
          <b>Not attached to an idea</b>
          {loose.map((c) => <Loose key={c.card_id} card={c} ideas={allIdeas} onAttach={onAttach} onOpen={actions.onOpen} />)}
        </section>
      )}
    </>
  )
}

function Loose({ card, ideas, onAttach, onOpen }: { card: BsCard; ideas: BsCard[]; onAttach: (c: string, to: string, rel: 'supports' | 'challenges') => void; onOpen: (id: string) => void }) {
  const [to, setTo] = useState(ideas[0]?.card_id ?? '')
  return (
    <div className="bs-ev">
      <button className="bs-link" onClick={() => onOpen(card.card_id)}>{card.paper ? card.paper.title : card.text}</button>
      <div className="lit-sub">{card.paper ? <>Paper ✓ · {card.paper.byline} · <span className="lit-key">{card.paper.key}</span></> : `${card.type} by ${card.by_name}`}</div>
      {ideas.length > 0 && (
        <div className="bs-row">
          <select aria-label="Idea" value={to} onChange={(e) => setTo(e.target.value)} style={{ flex: 1, minWidth: 0 }}>
            {ideas.map((i) => <option key={i.card_id} value={i.card_id}>{short(i.text, 50)}</option>)}</select>
          <button className="btn-sm lit-btn" onClick={() => onAttach(card.card_id, to, 'supports')}>Supports it</button>
          <button className="btn-sm lit-btn" onClick={() => onAttach(card.card_id, to, 'challenges')}>Challenges it</button>
        </div>
      )}
    </div>
  )
}

export function IdeaCard({ idea, a, space, open, focused, flash, threads, actions }: {
  idea: BsCard; a: Analysis; space: BsSpace; open: boolean; focused: boolean; flash: boolean; threads: number; actions: IdeaActions
}) {
  const [answering, setAnswering] = useState<string | null>(null)
  const [answer, setAnswer] = useState('')
  const ref = useRef<HTMLElement>(null)
  useEffect(() => { if (flash) ref.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }) }, [flash])
  const canWrite = space.can_write
  const tally = [`${a.forIt.length} for`, `${a.against.length} against`, `${a.open.length} open question${a.open.length === 1 ? '' : 's'}`,
    a.next.length ? `${a.next.length} next step${a.next.length === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ')
  const src = (c: BsCard) => c.paper ? <>Paper ✓ · {c.paper.byline} · <span className="lit-key">{c.paper.key}</span></>
    : c.run_id ? <>Run {c.run_id}</> : <>{c.type === 'question' ? 'asked' : 'proposed'} by {c.by_name}{c.live ? ' · live round table' : ''}</>
  return (
    <article ref={ref} className={`bs-idea${open ? ' open' : ''}${focused ? ' focused' : ''}${flash ? ' flash' : ''}`} id={`idea-${idea.card_id}`}>
      <div className="bs-row" style={{ justifyContent: 'space-between' }}>
        <div className="bs-row"><span className={`bs-status ${a.status[0]}`}>{a.status[1]}</span>{space.project && <VisPill vis={idea.vis} />}
          {idea.live && <span className="bs-live-tag">live · from the round table</span>}</div>
        <span className="bs-people" aria-label="Who took part">{a.people.slice(0, 5).map((p) => <Avatar key={p} name={p === 'You' ? 'Y' : p} agent={space.agents[p]} />)}</span>
      </div>
      {a.parents.length > 0 && <div className="lit-sub">Builds on: <button className="bs-link" onClick={() => actions.onGo(a.parents[0].card_id)}>{short(a.parents[0].text, 70)}</button></div>}
      <button className="bs-idea-title" aria-expanded={open} onClick={() => actions.onToggle(idea.card_id)}>{idea.text}</button>
      <div className="bs-stand">{a.stand}</div>
      <div className="lit-sub">{tally}</div>
      <div className="bs-row">
        {canWrite && (a.open.length ? <button className="btn-sm btn-modify" onClick={() => { setAnswering(a.open[0].card_id); if (!open) actions.onToggle(idea.card_id) }}>Answer: {short(a.open[0].text, 40)}</button>
          : a.forIt.length ? <button className="btn-sm btn-modify" onClick={() => actions.onPromote(idea)}>Turn into a proposal</button>
            : <button className="btn-sm btn-modify" onClick={() => actions.onAsk('Librarian', idea.card_id)}>Ask the Librarian for evidence</button>)}
        <button className="link-btn" onClick={() => actions.onToggle(idea.card_id)}>{open ? 'Less' : 'Details'}</button>
        <button className="link-btn" onClick={() => actions.onDiscuss(idea.card_id)} aria-pressed={focused}>{focused ? 'In the chat' : 'Discuss'}</button>
        {canWrite && space.project && idea.mine && idea.vis !== 'project' && <button className="link-btn" onClick={() => actions.onShare(idea.card_id)}>Share with project</button>}
      </div>
      {open && (
        <div className="bs-three">
          <section><h4 className="for">Why we think so</h4>
            {a.forIt.map((c) => <div key={c.card_id} className="bs-ev"><div>{c.text}</div><div className="lit-sub">{src(c)}</div></div>)}
            {!a.forIt.length && <div className="lit-sub">Nothing yet. Ask the Librarian, or add a paper from the Library.</div>}</section>
          <section><h4 className="against">What could be wrong</h4>
            {a.against.map((c) => <div key={c.card_id} className="bs-ev"><div>{c.text}</div><div className="lit-sub">{src(c)}</div></div>)}
            {a.questions.map((q) => (
              <div key={q.card_id} className={`bs-ev${q.answer ? ' answered' : ''}`}>
                <div className="bs-q">{q.text}</div><div className="lit-sub">{src(q)}</div>
                {q.answer ? <div className="lit-sub"><span className="lit-ok">Answered</span> by {q.answered_by_name}: {q.answer}</div>
                  : canWrite && (answering === q.card_id ? (
                    <form className="bs-row" style={{ flexWrap: 'nowrap' }} onSubmit={(e) => { e.preventDefault(); if (answer.trim()) { actions.onAnswer(q.card_id, answer.trim()); setAnswering(null); setAnswer('') } }}>
                      <input autoFocus aria-label="Your answer" placeholder="Your answer, or what you will check" value={answer} onChange={(e) => setAnswer(e.target.value)} />
                      <button className="btn-sm btn-modify">Save</button></form>
                  ) : <button className="btn-sm lit-btn" onClick={() => setAnswering(q.card_id)}>Answer</button>)}
              </div>
            ))}
            {!a.against.length && !a.questions.length && <div className="lit-sub">No doubts raised yet. Ask the Skeptic.</div>}</section>
          <section><h4 className="next">Next steps</h4>
            {a.next.map((c) => <div key={c.card_id} className="bs-ev"><button className="bs-link" onClick={() => actions.onGo(c.card_id)}>{c.text}</button><div className="lit-sub">{src(c)}</div></div>)}
            {!a.next.length && <div className="lit-sub">No next step yet. Ask Methods how to test it.</div>}</section>
          {canWrite && (
            <div className="bs-row">
              <span className="lit-sub">Ask</span>
              {AGENT_ASK.map(([agent, label]) => <button key={agent} className="btn-sm lit-btn" onClick={() => actions.onAsk(agent, idea.card_id)}>{agent}: {label.toLowerCase()}</button>)}
              {a.forIt.length > 0 && a.open.length > 0 && <button className="link-btn" onClick={() => actions.onPromote(idea)}>Turn into a proposal anyway</button>}
            </div>
          )}
          <div className="bs-row" style={{ justifyContent: 'space-between' }}>
            <span className="lit-sub">{threads ? `${threads} message${threads === 1 ? '' : 's'} in its discussion` : 'No discussion on it yet'}</span>
            <button className="link-btn" onClick={() => actions.onOpen(idea.card_id)}>{threads ? 'See the discussion and reply' : 'Start a discussion'}</button>
          </div>
        </div>
      )}
    </article>
  )
}


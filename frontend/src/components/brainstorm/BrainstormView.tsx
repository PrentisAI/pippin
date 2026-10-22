import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, describeError } from '../../api/client'
import { useAuth } from '../../auth'
import type { Activity, AddFound, BsAttachment, BsCard, BsChat, BsMessage, BsSpace, BsSuggestion, ProjectLiterature, ZoteroStatus } from '../../types'
import { ShareDialog } from '../ShareDialog'
import { ZoteroDialog } from '../ZoteroDialog'
import { RULE_LABELS } from '../LiteratureView'
import { CardDrawer } from './CardDrawer'
import { IdeasPane } from './Ideas'
import { FILTER_LABELS, PapersPane, inFilter, type PaperFilter } from './Papers'
import { ACTIVITY, AGENT_ASK, Avatar, BULB, CHAT, LENS, LOCK, Rich, VisPill, analyse, ago, short } from './common'
import { NewCard, PickFile, PickPaper, Promote, ShareChat } from './dialogs'
import { FirstTip } from '../../onboarding/FirstTip'

type Dialog = null | { kind: 'share' } | { kind: 'projectShare' } | { kind: 'promote'; card: BsCard } | { kind: 'new'; type: 'idea' | 'question' | 'note' }
  | { kind: 'bring' } | { kind: 'attachPaper' } | { kind: 'attachFile' } | { kind: 'zotero' }

/** The brainstorm space of a project (or your own): conversations with the agents and the ideas they produce. */
export function BrainstormView({ initialSpace, onOpenProject, onOpenLibrary }: {
  initialSpace?: string | null; onOpenProject: (campaignId: string) => void; onOpenLibrary: () => void
}) {
  const auth = useAuth()
  const [spaces, setSpaces] = useState<{ space: string; name: string; project: boolean; can_write: boolean }[]>([])
  const [spaceId, setSpaceId] = useState<string>(initialSpace || 'global')
  const [space, setSpace] = useState<BsSpace | null>(null)
  const [lit, setLit] = useState<ProjectLiterature | null>(null)
  const [zotero, setZotero] = useState<ZoteroStatus | null>(null)
  const [chatId, setChatId] = useState<string | null>(null)
  const [chat, setChat] = useState<{ chat: BsChat; messages: BsMessage[]; pending: boolean; here: { user_id: string; name: string }[] } | null>(null)
  const [focus, setFocus] = useState('')
  const [tab, setTab] = useState<'ideas' | 'papers'>('ideas')
  const [ideaFilter, setIdeaFilter] = useState<'all' | 'project' | 'mine'>('all')
  const [paperFilter, setPaperFilter] = useState<PaperFilter>('all')
  const [searchSel, setSearchSel] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [flash, setFlash] = useState('')
  const [flashKey, setFlashKey] = useState('')
  const [drawer, setDrawer] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<BsAttachment[]>([])
  const [plus, setPlus] = useState(false)
  const [activityMenu, setActivityMenu] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<Dialog>(null)
  const [toast, setToast] = useState<{ text: string; action?: [string, () => void] } | null>(null)
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => { if (initialSpace) setSpaceId(initialSpace) }, [initialSpace])
  useEffect(() => {
    api.brainstorm.spaces().then(setSpaces).catch(() => {})
    api.zotero.get().then(setZotero).catch(() => {})
  }, [])

  const loadSpace = useCallback(async () => {
    try {
      const s = await api.brainstorm.space(spaceId)
      setSpace(s)
      setChatId((cur) => (cur && s.chats.some((c) => c.chat_id === cur) ? cur : s.chats[0]?.chat_id ?? null))
      if (s.project) api.literature.get(s.space).then(setLit).catch(() => {})
      else setLit(null)
    } catch (e) { setError(describeError(e)) }
  }, [spaceId])
  useEffect(() => { setSpace(null); setChat(null); setChatId(null); setFocus(''); setDrawer(null); setSearchSel(null); loadSpace() }, [loadSpace])

  const loadChat = useCallback(async () => {
    if (!chatId) { setChat(null); return undefined }
    try {
      const c = await api.brainstorm.chat(chatId)
      setChat(c)
      if (c.chat.focus && !c.chat.run_id) setFocus((f) => f || c.chat.focus)
      return c
    } catch (e) { setError(describeError(e)); return undefined }
  }, [chatId])
  useEffect(() => { setFocus(''); loadChat() }, [loadChat])
  // while an agent writes (or a live meeting goes on), follow closely; otherwise now and then
  const live = !!chat?.chat.run_id && !['finished', ''].includes(chat.chat.status ?? '')
  useEffect(() => {
    const t = setInterval(async () => { const c = await loadChat(); if (c?.pending || live) loadSpace() }, chat?.pending || live ? 1500 : 6000)
    return () => clearInterval(t)
  }, [chat?.pending, live, loadChat, loadSpace])
  const wasPending = useRef(false)
  useEffect(() => {
    if (wasPending.current && chat && !chat.pending) loadSpace()
    wasPending.current = !!chat?.pending
  }, [chat, loadSpace])
  useEffect(() => { bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: 'smooth' }) }, [chat?.messages.length, chat?.pending])
  useEffect(() => { if (!flash) return; const t = setTimeout(() => setFlash(''), 2500); return () => clearTimeout(t) }, [flash])

  const cards = useMemo(() => new Map((space?.cards ?? []).map((c) => [c.card_id, c])), [space])
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try { await fn() } catch (e) { setError(describeError(e)) } finally { setBusy(false) }
  }
  const ensureChat = async (): Promise<string> => {
    if (chatId) return chatId
    const c = await api.brainstorm.newChat(spaceId)
    setChatId(c.chat_id)
    await loadSpace()
    return c.chat_id
  }
  const showIdea = (id: string) => {
    const c = cards.get(id)
    if (c && c.type !== 'idea') { setDrawer(id); return }
    setTab('ideas'); setExpanded((x) => ({ ...x, [id]: true })); setFlash(id)
  }
  const showPaper = (key: string) => {
    setTab('papers'); setPaperFilter('all'); setSearchSel(null); setFlashKey(key)
    setTimeout(() => document.getElementById(`paper-${key}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 100)
  }
  const askAgent = (agent: string, cardId: string) => act(async () => {
    const id = await ensureChat()
    await api.brainstorm.ask(id, agent, cardId)
    setFocus(cardId)
    setChat({ ...(await api.brainstorm.chat(id)) })
  })
  const send = (e: React.FormEvent) => {
    e.preventDefault()
    const t = text.trim()
    if (!t) return
    act(async () => {
      const id = await ensureChat()
      await api.brainstorm.say(id, t, live ? '' : focus, attachments)
      setText(''); setAttachments([])
      setChat(await api.brainstorm.chat(id))
      loadSpace()
    })
  }
  const deep = () => act(async () => {
    const id = await ensureChat()
    await api.brainstorm.deep(id, text.trim(), focus)
    setText('')
    setChat(await api.brainstorm.chat(id))
  })
  const onSuggestion = (s: BsSuggestion) => act(async () => {
    const r = await api.brainstorm.doSuggestion(s.sug_id)
    await loadSpace()
    if (s.kind === 'open') {
      const q = cards.get(s.cards[0])
      if (q?.type === 'question') {
        const idea = space?.links.find((l) => l.from_card === q.card_id)?.to_card
        if (idea) showIdea(idea); else setDrawer(q.card_id)
      } else if (s.cards[0]) showIdea(s.cards[0])
    } else if (s.kind === 'worked' && s.cards[0]) { showIdea(s.cards[0]); setDrawer(s.cards[0]) }
    else { s.cards.forEach((id) => setExpanded((x) => ({ ...x, [id]: true }))); if (s.cards[0]) setFlash(s.cards[0]) }
    if (r.added) setToast({ text: 'Added to the idea as evidence.' })
  })

  if (!space) return <div className="bs-page"><div className="panel-empty">{error ?? 'Opening the space…'}</div></div>
  const ideas = space.cards.filter((c) => c.type === 'idea')
  const openQuestions = space.cards.filter((c) => c.type === 'question' && !c.answer).length
  const currentChat = chat?.chat
  const shared = currentChat?.vis === 'project'
  let focusCard = focus ? cards.get(focus) : undefined
  if (shared && focusCard && focusCard.vis !== 'project') focusCard = undefined       // never act on a private idea here
  const myChats = space.chats.filter((c) => c.vis !== 'project')
  const sharedChats = space.chats.filter((c) => c.vis === 'project')
  const projects = spaces.filter((s) => s.project && s.can_write)
  const papersCount = lit?.papers.length ?? space.papers
  const ruleNow: AddFound = (lit?.rule as AddFound) ?? 'cited'
  const ideaActions = {
    onToggle: (id: string) => setExpanded((x) => ({ ...x, [id]: !x[id] })),
    onDiscuss: (id: string) => setFocus(id),
    onGo: showIdea,
    onAsk: askAgent,
    onPromote: (c: BsCard) => setDialog({ kind: 'promote', card: c }),
    onShare: (id: string) => act(async () => { await api.brainstorm.changeCard(id, { vis: 'project' }); await loadSpace(); setToast({ text: 'Shared with the project, with its evidence and open questions.' }) }),
    onAnswer: (qid: string, answer: string) => act(async () => { await api.brainstorm.changeCard(qid, { answer }); await loadSpace() }),
    onOpen: (id: string) => setDrawer(id),
  }

  return (
    <div className="bs-page">
      {/* ── left: spaces, conversations, the Library, agents ── */}
      <nav className="bs-left" aria-label="Brainstorm spaces">
        <label className="bs-field">Space
          <select value={spaceId} onChange={(e) => setSpaceId(e.target.value)}>
            {spaces.map((s) => <option key={s.space} value={s.space}>{s.project ? s.name : 'Your own space (no project)'}</option>)}
          </select>
        </label>
        {space.project ? (
          <div className="bs-members">
            <span className="bs-people">{space.members.slice(0, 4).map((m) => <Avatar key={m.user_id} name={m.name} />)}</span>
            <span className="lit-sub" style={{ flex: 1 }}>{auth.accounts ? `${space.members.length} member${space.members.length === 1 ? '' : 's'}` : 'Just you'}</span>
            {auth.accounts && <button className="btn-sm lit-btn" onClick={() => setDialog({ kind: 'projectShare' })}>Share</button>}
          </div>
        ) : <div className="lit-sub" style={{ padding: '6px 8px 0' }}>Your own space is only yours.</div>}

        <div className="bs-h">{LOCK} Only you</div>
        {myChats.map((c) => (
          <button key={c.chat_id} className="bs-item" aria-current={c.chat_id === chatId} onClick={() => setChatId(c.chat_id)}>
            {CHAT}<span className="bs-ell">{c.title}</span></button>
        ))}
        {!myChats.length && <div className="lit-sub" style={{ padding: '2px 8px' }}>No private brainstorms yet: start one to think an idea through before you run anything.</div>}
        {space.can_write && (
          <button className="bs-item" onClick={() => act(async () => { const c = await api.brainstorm.newChat(spaceId); await loadSpace(); setChatId(c.chat_id) })}>
            <span>+ New brainstorm</span><span className="lit-sub">private</span></button>
        )}
        {space.project && (<>
          <div className="bs-h">Shared with the project</div>
          {sharedChats.map((c) => (
            <button key={c.chat_id} className="bs-item" aria-current={c.chat_id === chatId} onClick={() => setChatId(c.chat_id)}>
              {c.run_id ? <span className={`bs-dot${['finished', ''].includes(c.status ?? '') ? '' : ' on'}`} aria-label="live" /> : CHAT}
              <span className="bs-ell">{c.title}</span><span className="lit-sub">{c.mine ? '' : c.owner_name.split(' ')[0]}</span></button>
          ))}
          {!sharedChats.length && <div className="lit-sub" style={{ padding: '2px 8px' }}>Nothing shared yet.</div>}
        </>)}

        {space.project && lit && (<>
          <div className="bs-h">Library</div>
          {(['all', 'star', 'upload', 'feed', 'search', ...(zotero?.connected ? ['zotero'] : [])] as PaperFilter[]).map((k) => (
            <button key={k} className="bs-item" aria-current={tab === 'papers' && !searchSel && paperFilter === k}
              onClick={() => { setTab('papers'); setPaperFilter(k); setSearchSel(null) }}>
              <span>{k === 'all' ? 'All papers' : FILTER_LABELS[k]}</span><span className="lit-sub">{lit.papers.filter((p) => inFilter(p, k)).length}</span></button>
          ))}
          <button className="bs-item" onClick={onOpenLibrary}><span>Open the feed</span><span className="lit-sub">→</span></button>
          {lit.searches.length > 0 && (<>
            <div className="bs-h">Searches by the agents</div>
            <label className="bs-field">Papers they find
              <select value={ruleNow} onChange={(e) => act(async () => { setLit(await api.literature.setRule(space.space, e.target.value as AddFound)); setToast({ text: `${RULE_LABELS[e.target.value as AddFound]}: for this project, from now on.` }) })}>
                {(Object.keys(RULE_LABELS) as AddFound[]).map((k) => <option key={k} value={k}>{RULE_LABELS[k]}</option>)}
              </select></label>
            {lit.searches.slice(0, 12).map((s) => (
              <button key={s.search_id} className="bs-item" aria-current={tab === 'papers' && searchSel === s.search_id} title={`${s.context} · ${s.by}`}
                onClick={() => { setTab('papers'); setSearchSel(s.search_id) }}>
                {LENS}<span className="bs-ell">{s.context.replace(/^Ideation · /, '')} · {s.by}</span><span className="lit-sub">{s.in_library}/{s.found}</span></button>
            ))}
          </>)}
        </>)}
        <button className="bs-item" onClick={() => setDialog({ kind: 'zotero' })}>
          <span className={`bs-z${zotero?.connected ? ' on' : ''}`}>Z</span>
          <span className="bs-ell">{zotero?.connected ? `Zotero · ${zotero.syncing ? 'syncing' : zotero.last_sync ? ago(zotero.last_sync) : 'not synced yet'}` : 'Connect Zotero'}</span></button>

        <div className="bs-h">Agents</div>
        <span className="bs-rel" style={{ padding: '0 8px' }}>
          <button className="btn-sm lit-btn" style={{ width: '100%', justifyContent: 'space-between' }} aria-haspopup="menu" aria-expanded={activityMenu}
            onClick={() => setActivityMenu(!activityMenu)}>{ACTIVITY[space.activity][0]} ▾</button>
          {activityMenu && (
            <div className="bs-menu wide" role="menu" onMouseLeave={() => setActivityMenu(false)}>
              {(Object.keys(ACTIVITY) as Activity[]).map((k) => (
                <button key={k} role="menuitem" className="bs-menu-2" onClick={() => act(async () => { setActivityMenu(false); await api.brainstorm.setActivity(k); await loadSpace(); setToast({ text: `Agents: ${ACTIVITY[k][0].toLowerCase()}.` }) })}>
                  <span>{space.activity === k ? '✓ ' : ''}{ACTIVITY[k][0]}</span><span className="lit-sub">{ACTIVITY[k][1]}</span></button>
              ))}
            </div>
          )}</span>
        <div className="lit-sub" style={{ padding: '4px 8px 0' }}>Their calls use {auth.accounts ? 'your API key' : 'the server’s key'}.</div>
        {space.project && <button className="bs-item" onClick={() => onOpenProject(space.space)}><span>Open the project</span><span className="lit-sub">→</span></button>}
      </nav>

      {/* ── middle: the conversation ── */}
      <section className="bs-center" aria-label="Conversation">
        <FirstTip id="brainstorm" doc="concepts/brainstorm.md">
          <b>Brainstorm.</b> Say what you are thinking about. The Skeptic looks for the weakest assumption, Methods turns the idea
          into a test, the Librarian brings papers. Ideas are kept on the board with the evidence for and against; a good one
          becomes a proposal with one click.
        </FirstTip>
        <div className="bs-head">
          <div className="bs-head-row">
            <h2>{currentChat?.title ?? 'A new brainstorm'}{currentChat?.run_id && currentChat.status && <span className="bs-livestate"> · {live ? `live, ${currentChat.status}` : currentChat.status}</span>}</h2>
            <div className="bs-row">
              {shared && (chat?.here.length ?? 0) > 0 && <span className="bs-people" aria-label="Here now">{chat!.here.map((h) => <Avatar key={h.user_id} name={h.name === 'You' ? 'Y' : h.name} online />)}</span>}
              {currentChat && space.project && <VisPill vis={currentChat.vis} />}
              {currentChat && space.project && currentChat.mine && currentChat.vis !== 'project' && space.can_write &&
                <button className="btn-sm lit-btn" onClick={() => setDialog({ kind: 'share' })}>Share…</button>}
              {currentChat?.mine && !currentChat.run_id && <button className="link-btn danger" onClick={() => act(async () => { await api.brainstorm.deleteChat(currentChat.chat_id); setChatId(null); await loadSpace() })}>Delete</button>}
            </div>
          </div>
          <div className="bs-know" aria-label="What the agents can use">
            <span className="bs-eyebrow">{shared ? 'Knowledge everyone here can see' : 'Brainstorm knowledge'}</span>
            {shared ? <>
              <button onClick={() => { setTab('ideas'); setIdeaFilter('project') }}><b>{ideas.filter((c) => c.vis === 'project').length}</b> shared ideas</button>
              <button onClick={() => { setTab('papers'); setPaperFilter('all'); setSearchSel(null) }}><b>{papersCount}</b> project papers</button>
              <button onClick={() => onOpenProject(space.space)}><b>{space.runs}</b> runs</button>
              <span className="bs-dotted" title="Each person’s private brainstorms, ideas and Zotero highlights are left out here. The agents ask the owner before using them.">private material left out</span>
            </> : <>
              <button onClick={() => setTab('ideas')}><b>{ideas.length}</b> ideas</button>
              <button onClick={() => setTab('ideas')}><b>{openQuestions}</b> open questions</button>
              <button onClick={() => { setTab('papers'); setPaperFilter('all'); setSearchSel(null) }}><b>{papersCount}</b> papers</button>
              {space.project ? <button onClick={() => onOpenProject(space.space)}><b>{space.runs}</b> runs</button> : <span><b>0</b> runs</span>}
              <span><b>{Math.max(0, space.chats.length - (currentChat ? 1 : 0))}</b> other brainstorms</span>
            </>}
          </div>
        </div>
        <div className="bs-body" ref={bodyRef}>
          {!chat?.messages.length && (
            <div className="bs-note">A new brainstorm. Say what you are thinking about; the Skeptic, Methods and the Librarian join when you
              ask them, or use the buttons below{space.activity !== 'off' ? ' (and when what you write calls for one of them)' : ''}.</div>
          )}
          {chat?.messages.map((m) => <Message key={m.msg_id} m={m} space={space} cards={cards} onIdea={showIdea} onPaper={showPaper}
            onConsent={(share) => act(async () => { await api.brainstorm.consent(m.msg_id, share); await loadChat(); loadSpace() })} />)}
        </div>
        {error && <div className="panel-message error" style={{ margin: '0 16px' }}>{error}</div>}
        {space.can_write ? (
          <form className="bs-foot" onSubmit={send}>
            {currentChat?.run_id ? (
              <div className="lit-sub">{live ? 'You are speaking in a live round table. The chair gives you the floor before the next turn.'
                : 'This round table has finished. Its ideas and questions are on the board.'}</div>
            ) : (
              <div className="bs-quick">
                {focusCard && AGENT_ASK.map(([agent, label]) => <button key={agent} type="button" disabled={busy} onClick={() => askAgent(agent, focusCard!.card_id)}>{label}</button>)}
                {focusCard && <button type="button" onClick={() => setDialog({ kind: 'promote', card: focusCard! })}>Turn into a proposal</button>}
                <button type="button" disabled={busy || (!focusCard && !text.trim())} title={!focusCard && !text.trim() ? 'Pick an idea, or type what to search for' : 'The Librarian searches thoroughly, from many angles'} onClick={deep}>Deep search</button>
              </div>
            )}
            <div className="bs-composer">
              {focusCard && !currentChat?.run_id && <div className="bs-about">About: <button type="button" className="bs-ichip" onClick={() => showIdea(focusCard!.card_id)}>{short(focusCard.text, 50)}</button>
                <span className="lit-sub"> · pick another idea on the right</span><button type="button" className="link-btn" onClick={() => setFocus('')}>clear</button></div>}
              {attachments.length > 0 && <div className="bs-row">{attachments.map((a, i) => (
                <span key={i} className="bs-att">{a.kind === 'paper' ? `Paper ${a.label}` : a.label}
                  <button type="button" className="link-btn" aria-label="Remove" onClick={() => setAttachments(attachments.filter((_, k) => k !== i))}>×</button></span>))}</div>}
              <div className="bs-row" style={{ flexWrap: 'nowrap' }}>
                {!currentChat?.run_id && (
                  <span className="bs-rel"><button type="button" className="bs-plus" aria-label="Attach" aria-haspopup="menu" aria-expanded={plus} onClick={() => setPlus(!plus)}>+</button>
                    {plus && (
                      <div className="bs-menu up" role="menu" onMouseLeave={() => setPlus(false)}>
                        <button role="menuitem" type="button" onClick={() => { setPlus(false); setDialog({ kind: 'attachPaper' }) }}>A paper from the Library</button>
                        {space.project && <button role="menuitem" type="button" onClick={() => { setPlus(false); setDialog({ kind: 'attachFile' }) }}>A file from the project</button>}
                      </div>
                    )}</span>
                )}
                <input aria-label="Message" placeholder={currentChat?.run_id ? 'Speak in the round table' : 'Think out loud, or @Skeptic @Methods @Librarian'}
                  value={text} onChange={(e) => setText(e.target.value)} disabled={!!currentChat?.run_id && !live} />
                <button className="btn-sm btn-modify" disabled={busy || !text.trim()}>Send</button>
              </div>
            </div>
          </form>
        ) : <div className="bs-foot lit-sub">You can read this space; writing in it needs comment rights in the project.</div>}
      </section>

      {/* ── right: ideas and papers ── */}
      <aside className="bs-right" aria-label="Ideas and papers">
        <div className="team-tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'ideas'} className={tab === 'ideas' ? 'on' : ''} onClick={() => setTab('ideas')}>Ideas · {ideas.length}</button>
          <button role="tab" aria-selected={tab === 'papers'} className={tab === 'papers' ? 'on' : ''} onClick={() => setTab('papers')}>Papers · {papersCount}</button>
        </div>
        <div className="bs-right-body">
          {tab === 'ideas' ? (
            <IdeasPane space={space} cards={cards} expanded={expanded} focus={focus} flash={flash} filter={ideaFilter} setFilter={setIdeaFilter}
              actions={ideaActions} activity={space.activity}
              onNew={(type) => setDialog({ kind: 'new', type })} onBring={() => setDialog({ kind: 'bring' })}
              onSuggestion={onSuggestion}
              onDismiss={(s) => act(async () => { await api.brainstorm.dismissSuggestion(s.sug_id); await loadSpace(); setToast({ text: 'Dismissed. The agents bring fewer things like this.' }) })}
              onAttach={(cid, to, rel) => act(async () => { await api.brainstorm.link(cid, to, rel); await loadSpace(); showIdea(to); setToast({ text: `Filed under “${short(cards.get(to)?.text ?? '', 40)}”.` }) })} />
          ) : (
            <PapersPane space={space} lit={lit} setLit={setLit} filter={paperFilter} searchId={searchSel} onSearch={setSearchSel}
              flashKey={flashKey} rule={ruleNow} onIdea={showIdea} />
          )}
        </div>
      </aside>

      {drawer && cards.get(drawer) && (
        <CardDrawer cardId={drawer} space={space} cards={cards} projects={projects} onClose={() => setDrawer(null)} onOpen={(id) => setDrawer(id)}
          onChanged={loadSpace} onPromote={(c) => setDialog({ kind: 'promote', card: c })} onPaper={showPaper}
          onMoved={(cid) => { setDrawer(null); loadSpace(); setToast({ text: 'Moved to the project.', action: ['Open it', () => setSpaceId(cid)] }) }} />
      )}
      {dialog?.kind === 'share' && currentChat && (
        <ShareChat chat={currentChat} ideas={ideas.filter((c) => c.mine && c.vis !== 'project')} members={space.members} onClose={() => setDialog(null)}
          onDone={async (msg) => { setDialog(null); await loadSpace(); await loadChat(); setToast({ text: msg }) }} />
      )}
      {dialog?.kind === 'projectShare' && <ShareDialog campaignId={space.space} projectName={space.name} onClose={() => { setDialog(null); loadSpace() }} />}
      {dialog?.kind === 'promote' && (
        <Promote card={dialog.card} a={analyse(dialog.card, cards, space.links)} space={space} projects={projects} onClose={() => setDialog(null)}
          onDone={(cid, liveRun) => { setDialog(null); loadChat(); setToast({ text: liveRun ? 'The proposal is in the project’s chat. Start it there: its round table then meets here, live.' : 'The proposal is in the project’s chat. Check it and start it there.', action: ['Open the project', () => onOpenProject(cid)] }) }} />
      )}
      {dialog?.kind === 'new' && (
        <NewCard type={dialog.type} space={space} activity={space.activity} onClose={() => setDialog(null)}
          onAdd={async (body) => {
            const r = await api.brainstorm.addCard(spaceId, { ...body, chat_id: chatId ?? '' })
            setDialog(null)
            await loadSpace()
            if (body.type === 'idea') setFocus(r.card.card_id); else setDrawer(r.card.card_id)
            if (r.chat_id) { setChatId(r.chat_id); setChat(await api.brainstorm.chat(r.chat_id)) }
          }} />
      )}
      {dialog?.kind === 'bring' && (
        <PickPaper space={space} title="Material from the Library" onClose={() => setDialog(null)}
          onPick={(pid) => act(async () => { setDialog(null); await api.brainstorm.addCard(spaceId, { type: 'material', text: '', paper_id: pid }); await loadSpace(); setToast({ text: 'Added. File it under an idea: see “Not attached to an idea”.' }) })} />
      )}
      {dialog?.kind === 'attachPaper' && <PickPaper space={space} title="Attach a paper" onClose={() => setDialog(null)} onPick={(pid, key) => { setAttachments([...attachments, { kind: 'paper', paper_id: pid, label: key }]); setDialog(null) }} />}
      {dialog?.kind === 'attachFile' && <PickFile campaignId={space.space} onClose={() => setDialog(null)} onPick={(a) => { setAttachments([...attachments, a]); setDialog(null) }} />}
      {dialog?.kind === 'zotero' && <ZoteroDialog onChanged={setZotero} onClose={() => { setDialog(null); loadSpace() }} />}
      {toast && (
        <div className="bs-toast" role="status">{toast.text}
          {toast.action && <button className="link-btn" onClick={() => { toast.action![1](); setToast(null) }}>{toast.action[0]}</button>}
          <button className="link-btn" onClick={() => setToast(null)} aria-label="Dismiss">×</button></div>
      )}
    </div>
  )
}

function Message({ m, space, cards, onIdea, onPaper, onConsent }: {
  m: BsMessage; space: BsSpace; cards: Map<string, BsCard>; onIdea: (id: string) => void; onPaper: (key: string) => void; onConsent: (share: boolean) => void
}) {
  const agent = space.agents[m.who]
  const seat = /^p\d\d$/.test(m.who)
  if (m.kind === 'system') return <div className="bs-system"><Rich text={m.text} cards={cards} onIdea={onIdea} onPaper={onPaper} /></div>
  if (m.kind === 'consent') {
    const c = cards.get(m.consent)
    return (
      <div className="bs-msg"><Avatar name={m.who} agent={agent} />
        <div className="bs-msg-main bs-onlyme"><div className="bs-tag">{LOCK} Only you can see this</div>
          <div>Your private idea {c && <button className="bs-ichip" onClick={() => onIdea(c.card_id)}>{short(c.text, 50)}</button>} and its evidence bear on this question. {m.text}
            {' '}Share the idea here, so everyone in this brainstorm can see it and the agents can use it?</div>
          {m.decided ? <div className="lit-sub">{m.decided}</div> : (
            <div className="bs-row"><button className="btn-sm btn-modify" onClick={() => onConsent(true)}>Share the idea</button>
              <button className="btn-sm lit-btn" onClick={() => onConsent(false)}>Keep it private</button></div>
          )}
        </div>
      </div>
    )
  }
  if (!m.agent && !seat && m.who !== 'Chair') {
    return (
      <div className={`bs-msg${m.mine ? ' you' : ''}`}><Avatar name={m.who_name === 'You' ? 'Y' : m.who_name} />
        <div className="bs-msg-main"><div className="bs-bubble">{m.text}</div>
          {(m.attachments ?? []).length > 0 && <div className="lit-sub">With {m.attachments!.map((a) => a.kind === 'paper' ? `paper ${a.label ?? ''}` : a.label ?? a.path).join(', ')}</div>}
          {!m.mine && <div className="lit-sub">{m.who_name}</div>}</div></div>
    )
  }
  const role = agent ? agent.role : seat ? m.steps[0] : m.who === 'Chair' ? 'runs the round table' : ''
  const steps = seat ? [] : m.steps
  return (
    <div className="bs-msg"><Avatar name={m.who === 'Chair' ? 'CH' : m.who} agent={agent} />
      <div className="bs-msg-main">
        <div className="bs-who"><b>{seat ? `Seat ${m.who}` : m.who}</b>{role && <span className="lit-sub"> · {role}</span>}</div>
        {steps.length > 0 && (
          <div className="bs-steps">{steps.map((s, i) => <div key={i}>{s === 'Thinking' ? BULB : LENS}
            <span><Rich text={s} cards={cards} onIdea={onIdea} onPaper={onPaper} />{m.pending && i === steps.length - 1 ? '…' : ''}</span></div>)}</div>
        )}
        {m.text && <div className="bs-text"><Rich text={m.text} cards={cards} onIdea={onIdea} onPaper={onPaper} />{m.pending && seat ? '…' : ''}</div>}
        {m.effects.map(([what, id], i) => (
          <div key={i} className="bs-effect">↳ {what} {cards.get(id) ? <button className="bs-ichip" onClick={() => onIdea(id)}>{short(cards.get(id)!.text, 40)}</button> : 'an idea'}</div>
        ))}
      </div>
    </div>
  )
}

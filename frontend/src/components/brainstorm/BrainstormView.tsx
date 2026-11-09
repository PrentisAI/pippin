import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, describeError } from '../../api/client'
import { useAuth } from '../../auth'
import type { Activity, AddFound, BsAttachment, BsCard, BsChat, BsChatView, BsMessage, BsSpace, BsSuggestion, ProjectLiterature, ZoteroStatus } from '../../types'
import { ShareDialog } from '../ShareDialog'
import { ZoteroDialog } from '../ZoteroDialog'
import { RULE_LABELS } from '../LiteratureView'
import { CardDrawer } from './CardDrawer'
import { IdeasPane } from './Ideas'
import { FILTER_LABELS, PapersPane, inFilter, type PaperFilter } from './Papers'
import { ACTIVITY, Avatar, BULB, CHAT, LENS, LOCK, Rich, VisPill, analyse, ago, short } from './common'
import { NewCard, PickFile, PickPaper, Promote, ReturnDialog, ShareChat } from './dialogs'
import { FirstTip } from '../../onboarding/FirstTip'
import { seeTip } from '../../onboarding/store'
import { readPaper } from '../reader/open'
import { isEcho, speak, spoken, stopSpeaking, useVoice } from './voice'
import type { ReadRequest } from '../reader/PaperReader'
const PaperReader = lazy(() => import('../reader/PaperReader'))
import { ActionWidget, MapPane } from '../review/ChatWidgets'
import { PlanPane } from './Plan'

type Dialog = null | { kind: 'share' } | { kind: 'projectShare' } | { kind: 'promote'; card: BsCard } | { kind: 'new'; type: 'idea' | 'question' | 'note' }
  | { kind: 'bring' } | { kind: 'attachPaper' } | { kind: 'attachFile' } | { kind: 'zotero' } | { kind: 'return' }

/** The brainstorm space of a project (or your own): conversations with the agents and the ideas they produce. */
// the widths of the side columns, kept in this browser
const BS_COLS = { left: 240, right: 400, reader: 640 }
const BS_COLS_KEY = 'aurora.brainstorm.cols'
const BS_CENTER_MIN = 360
/** Conversations with their tangents right under them, indented (a tangent whose parent is not listed stands alone). */
function withTangents(chats: BsChat[]): { c: BsChat; depth: number }[] {
  const ids = new Set(chats.map((c) => c.chat_id))
  const out: { c: BsChat; depth: number }[] = []
  const add = (c: BsChat, depth: number) => {
    out.push({ c, depth })
    chats.filter((k) => k.parent === c.chat_id).reverse().forEach((k) => add(k, depth + 1))
  }
  chats.filter((c) => !c.parent || !ids.has(c.parent)).forEach((c) => add(c, 0))
  return out
}
function loadCols(): typeof BS_COLS {
  try { return { ...BS_COLS, ...JSON.parse(localStorage.getItem(BS_COLS_KEY) || '{}') } } catch { return BS_COLS }
}

export function BrainstormView({ initialSpace, opening, onOpeningSent, reader, onCloseReader, onOpenProject, onOpenLibrary }: {
  initialSpace?: string | null
  /** A paper open in the reader: it takes the right column, beside the conversation. */
  reader?: ReadRequest | null
  onCloseReader?: () => void
  /** What the person said on the start page: a new brainstorm opens with it, addressed to the Mentor. */
  opening?: string | null
  onOpeningSent?: () => void
  onOpenProject: (campaignId: string) => void; onOpenLibrary: () => void
}) {
  const auth = useAuth()
  const [spaces, setSpaces] = useState<{ space: string; name: string; project: boolean; can_write: boolean }[]>([])
  const [spaceId, setSpaceId] = useState<string>(initialSpace || 'global')
  const [space, setSpace] = useState<BsSpace | null>(null)
  const [lit, setLit] = useState<ProjectLiterature | null>(null)
  const [zotero, setZotero] = useState<ZoteroStatus | null>(null)
  const [chatId, setChatId] = useState<string | null>(null)
  const [chat, setChat] = useState<BsChatView | null>(null)
  const [focus, setFocus] = useState('')
  const [tab, setTab] = useState<'ideas' | 'papers' | 'map' | 'plan'>('ideas')
  const [ideaFilter, setIdeaFilter] = useState<'all' | 'project' | 'mine'>('all')
  const [paperFilter, setPaperFilter] = useState<PaperFilter>('all')
  const [searchSel, setSearchSel] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [flash, setFlash] = useState('')
  const [flashKey, setFlashKey] = useState('')
  const [openKey, setOpenKey] = useState<string | null>(null)
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
  // arriving from the start page: carry on the conversation there with the Mentor, without the tip
  const openingSent = useRef(false)
  useEffect(() => {
    if (!opening || !space || space.space !== spaceId || openingSent.current) return
    openingSent.current = true
    seeTip('brainstorm')
    act(async () => {
      const c = await api.brainstorm.newChat(spaceId)
      await api.brainstorm.say(c.chat_id, `@Mentor ${opening}`)
      setChatId(c.chat_id)
      await loadSpace()
      onOpeningSent?.()
    })
  }, [opening, space, spaceId]) // eslint-disable-line react-hooks/exhaustive-deps
  const pageRef = useRef<HTMLDivElement>(null)
  const [cols, setCols] = useState(loadCols)
  // the reader in the right column, and whether it is full screen for now
  const [full, setFull] = useState(false)
  useEffect(() => { setFull(false) }, [reader])
  const rightKey: 'reader' | 'right' = reader ? 'reader' : 'right'
  useEffect(() => { try { localStorage.setItem(BS_COLS_KEY, JSON.stringify(cols)) } catch { /* this visit only */ } }, [cols])
  // drag a border between columns; the conversation in the middle keeps room to be read
  const dragCol = (side: 'left' | 'right') => (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const start = cols
    const handle = e.currentTarget
    document.body.classList.add('resizing')
    handle.classList.add('dragging')
    const onMove = (ev: MouseEvent) => {
      const room = (pageRef.current?.clientWidth ?? 1400) - 12 - BS_CENTER_MIN
      const d = ev.clientX - startX
      setCols(side === 'left'
        ? { ...start, left: Math.round(Math.max(180, Math.min(420, room - start.right, start.left + d))) }
        : { ...start, [rightKey]: Math.round(Math.max(280, Math.min(rightKey === 'reader' ? 1400 : 720, room - start.left, start[rightKey] - d))) })
    }
    const onUp = () => {
      document.body.classList.remove('resizing')
      handle.classList.remove('dragging')
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }
  // the message box grows with what is written, up to a few lines
  const msgRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = msgRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [text])
  const showIdea = (id: string) => {
    const c = cards.get(id)
    if (c && c.type !== 'idea') { setDrawer(id); return }
    setTab('ideas'); setExpanded((x) => ({ ...x, [id]: true })); setFlash(id)
  }
  const showPaper = (key: string) => {
    setTab('papers'); setPaperFilter('all'); setSearchSel(null); setFlashKey(key); setOpenKey(key)
  }
  const askAbout = (q: string) => {
    setText(q)
    setTimeout(() => (document.querySelector('.bs-composer [aria-label="Message"]') as HTMLTextAreaElement | null)?.focus(), 50)
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
  // speaking instead of typing: the words go into the message box; hands-free, what is said is sent, the
  // agents' answers are read aloud, and it listens again
  const [handsFree, setHandsFree] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const handsFreeNow = useRef(false)
  useEffect(() => { handsFreeNow.current = handsFree }, [handsFree])
  const sendText = (t: string) => act(async () => {
    const id = await ensureChat()
    await api.brainstorm.say(id, t, live ? '' : focus, attachments)
    setAttachments([])
    setChat(await api.brainstorm.chat(id))
    loadSpace()
  })
  // what was in the box when speaking began: the words so far are shown after it, live
  const typedBefore = useRef('')
  const withTyped = (words: string) => (typedBefore.current.trim() ? `${typedBefore.current.trim()} ${words}` : words)
  // typing takes over from speaking at any moment: what is typed stays, the voice stops
  const typing = useRef(false)
  // what was read aloud last, and when: words heard that only repeat it are its echo, not the researcher
  const lastSpoken = useRef<{ text: string; at: number }>({ text: '', at: 0 })
  const echo = (words: string) => Date.now() - lastSpoken.current.at < 120_000 && isEcho(words, lastSpoken.current.text)
  const voice = useVoice((words, auto) => {
    if (typing.current || echo(words)) return
    if (auto && handsFreeNow.current) { setText(typedBefore.current); sendText(withTyped(words)) }
    else setText(withTyped(words))
  }, (soFar) => { if (!typing.current && !echo(soFar)) setText(withTyped(soFar)) })
  const startVoice = (auto: boolean) => { typing.current = false; typedBefore.current = text; voice.start(auto) }
  // which answer is being read aloud by its speaker button (null: none, or hands-free reading)
  const [readingId, setReadingId] = useState<string | null>(null)
  // settle: how long it still counts as speaking after playback ends (the speakers lag, Bluetooth most), so
  // hands-free does not start listening into the tail of it
  const readAloud = (said: string, then?: () => void, id: string | null = null, settle = 0) => {
    setSpeaking(true)
    setReadingId(id)
    lastSpoken.current = { text: spoken(said), at: Date.now() }
    speak(said, voice.cloudVoice, () => {
      lastSpoken.current.at = Date.now()
      setReadingId(null)
      window.setTimeout(() => { setSpeaking(false); then?.() }, settle)
    })
  }
  /** Stop all voice: reading aloud, listening, hands-free. */
  const quiet = () => {
    setHandsFree(false)
    voice.cancel()
    stopSpeaking()
    setSpeaking(false)
    setReadingId(null)
  }
  const voiceOn = handsFree || speaking || voice.state !== 'idle'
  useEffect(() => {
    if (!voiceOn) return
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') quiet() }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [voiceOn]) // eslint-disable-line react-hooks/exhaustive-deps
  const lastRead = useRef<string | null>(null)
  useEffect(() => {
    if (!handsFree) return
    if (voice.error) { setHandsFree(false); return }
    if (!chat || chat.pending || busy || speaking || voice.state !== 'idle') return
    // the answers since you last spoke, read aloud; then it listens for yours
    const msgs = chat.messages
    let mine = -1
    msgs.forEach((m, i) => { if (m.mine) mine = i })
    const answers = msgs.slice(mine + 1).filter((m) => m.agent && m.text && !m.pending)
    const last = answers.length ? answers[answers.length - 1].msg_id : null
    if (last && lastRead.current !== last) {
      lastRead.current = last
      // then it listens again (below), once the speakers have gone quiet
      readAloud(answers.map((m) => m.text).join('\n'), undefined, null, 700)
      return
    }
    startVoice(true)
  }, [handsFree, chat, busy, speaking, voice.state, voice.error]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!handsFree) { stopSpeaking(); setSpeaking(false); voice.cancel() } }, [handsFree]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => stopSpeaking(), [])
  // who leads this conversation: you, or Pippin (the agents propose the next step with drafts); switch any time
  const lead = chat?.chat.lead ?? 'me'
  const setLead = (to: 'me' | 'pippin') => act(async () => {
    const id = await ensureChat()
    await api.brainstorm.setLead(id, to)
    setChat(await api.brainstorm.chat(id))
  })
  const branchAt = (m: BsMessage) => act(async () => {
    const b = await api.brainstorm.branch(m.chat_id, m.msg_id)
    await loadSpace()
    setChatId(b.chat_id)
    setToast({ text: 'A tangent of its own: the agents here also see the conversation up to that message. “Back” returns, bringing what you found if you like.' })
  })
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
  // the moves: any of them at any time, on the idea in focus or, without one, on the conversation so far (with
  // what is typed, if anything); none waits for another to be done first
  const guided = !!chat?.chat.guided
  const typed = text.trim()
  const ask = (to: string, about: string) => { sendText(`@${to} ${about}${typed ? `: ${typed}` : ''}`); setText('') }
  const onIdea = (agent: string, fallback: string) => () => (focusCard && !typed ? askAgent(agent, focusCard.card_id) : ask(agent, fallback))
  const moves: { key: string; label: string; title: string; run: () => void; on?: boolean; off?: string }[] = [
    { key: 'think', label: guided ? 'Mentor guiding · pause' : 'Think it through', on: guided,
      title: guided ? 'Pause the Mentor: your messages go to whoever fits them; resume any time, it keeps what it learned'
        : 'The Mentor helps you think it through by asking',
      run: () => (guided
        ? act(async () => { const id = await ensureChat(); await api.brainstorm.guide(id, false); await loadChat() })
        : ask('Mentor', 'Help me think this through')) },
    { key: 'challenge', label: 'Challenge it', title: 'The Skeptic looks for the weakest point', run: onIdea('Skeptic', 'What is the weakest point of what we have so far?') },
    { key: 'test', label: 'Make it testable', title: 'Methods turns it into something you could test', run: onIdea('Methods', 'How could what we have so far be tested?') },
    { key: 'known', label: "What's known", title: 'The Librarian searches thoroughly, from many angles', run: deep },
    { key: 'map', label: 'Map the field', title: 'Zoom out: the Librarian maps the whole direction with you', run: () => ask('Librarian', 'Map the field around what we are discussing') },
    { key: 'versions', label: 'Write versions', title: 'Two or three drafts of your idea, to edit or take', run: () => ask('Methods', 'Write two or three versions of my idea') },
    { key: 'write', label: 'Write it down', title: 'Your question as it stands, written down for you to change', run: () => ask('Mentor', 'Write my research question down as it stands, as a first version') },
    { key: 'table', label: 'Round table', off: focusCard ? '' : 'Needs an idea on the board: Write it down first, or pick one on the right',
      title: 'Several models develop your idea into its strongest version, or meet live with you', run: () => focusCard && setDialog({ kind: 'promote', card: focusCard }) },
  ]
  // one or two moves suggested from where things are; a suggestion, never a gate
  const suggested = ((): string[] => {
    const mineSaid = (chat?.messages ?? []).filter((m) => m.mine && m.kind === 'text').length
    if (!focusCard) return ideas.length === 0 && mineSaid >= 6 ? ['write'] : mineSaid === 0 ? ['think'] : []
    const near = new Set(space.links.filter((l) => l.to_card === focusCard!.card_id || l.from_card === focusCard!.card_id)
      .flatMap((l) => [l.from_card, l.to_card]))
    const linked = space.cards.filter((c) => near.has(c.card_id) && c.card_id !== focusCard!.card_id)
    const out: string[] = []
    if (!linked.some((c) => c.type === 'material')) out.push('known')
    if (!(chat?.messages ?? []).some((m) => m.who === 'Skeptic')) out.push('challenge')
    if (linked.some((c) => c.type === 'question' && !c.answer)) out.push('think')
    if (!out.length) out.push('table')
    return out.slice(0, 2)
  })()
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
    <div className="bs-page" ref={pageRef} style={{ '--bs-left': `${cols.left}px`, '--bs-right': `${cols[rightKey]}px` } as React.CSSProperties}>
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
        {withTangents(myChats).map(({ c, depth }) => (
          <button key={c.chat_id} className="bs-item" aria-current={c.chat_id === chatId} onClick={() => setChatId(c.chat_id)}
            style={depth ? { paddingLeft: 8 + depth * 14 } : undefined} title={depth ? 'A tangent' : undefined}>
            {depth ? <span className="bs-tangent-mark" aria-hidden="true">↳</span> : CHAT}<span className="bs-ell">{depth ? c.title.replace(/^↳\s*/, '') : c.title}</span></button>
        ))}
        {!myChats.length && <div className="lit-sub" style={{ padding: '2px 8px' }}>No private brainstorms yet: start one to think an idea through before you run anything.</div>}
        {space.can_write && (
          <button className="bs-item" disabled={busy} onClick={() => {
            // an empty new brainstorm of yours is reused, not made again
            const blank = myChats.find((c) => c.title === 'New brainstorm')
            if (blank) { setChatId(blank.chat_id); return }
            act(async () => { const c = await api.brainstorm.newChat(spaceId); await loadSpace(); setChatId(c.chat_id) })
          }}>
            <span>+ New brainstorm</span><span className="lit-sub">private</span></button>
        )}
        {space.project && (<>
          <div className="bs-h">Shared with the project</div>
          {withTangents(sharedChats).map(({ c, depth }) => (
            <button key={c.chat_id} className="bs-item" aria-current={c.chat_id === chatId} onClick={() => setChatId(c.chat_id)}
              style={depth ? { paddingLeft: 8 + depth * 14 } : undefined}>
              {c.run_id ? <span className={`bs-dot${['finished', ''].includes(c.status ?? '') ? '' : ' on'}`} aria-label="live" /> : depth ? <span className="bs-tangent-mark" aria-hidden="true">↳</span> : CHAT}
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
      <div className="resize-handle" role="separator" aria-orientation="vertical" title="Drag to resize; double-click to reset"
        onMouseDown={dragCol('left')} onDoubleClick={() => setCols(BS_COLS)} />

      {/* ── middle: the conversation ── */}
      <section className="bs-center" aria-label="Conversation">
        <FirstTip id="brainstorm" doc="concepts/brainstorm.md">
          <b>Brainstorm.</b> Say what you are thinking about. The Skeptic looks for the weakest assumption, Methods turns the idea
          into a test, the Librarian brings papers, and the Mentor helps you think a direction through by asking, so your
          question stays yours. Ideas are kept on the board with the evidence for and against; a good one becomes a proposal
          with one click.
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
          {chat?.parent && (
            <div className="bs-crumb">
              <span>↰ A tangent from <button className="link-btn" onClick={() => setChatId(chat.parent!.chat_id)}>{short(chat.parent.title, 50)}</button>
                {chat.parent.at && <>, at <span className="bs-crumb-at">{chat.parent.at_who ? `${chat.parent.at_who}: ` : ''}“{short(chat.parent.at, 90)}”</span></>}</span>
              <span className="lit-sub">The agents here see that conversation up to there.</span>
              {space.can_write && <button className="btn-sm lit-btn" onClick={() => setDialog({ kind: 'return' })}>Back…</button>}
            </div>
          )}
          {!chat?.messages.length && (
            <div className="bs-note">A new brainstorm. Say what you are thinking about; the Skeptic, Methods, the Librarian and the Mentor
              join when you ask them, or use the buttons below{space.activity !== 'off' ? ' (and when what you write calls for one of them)' : ''}.
              Not sure yet what you want to ask? Say so: the Mentor will help you find your question.</div>
          )}
          {chat?.messages.map((m) => <Message key={m.msg_id} m={m} space={space} cards={cards} onIdea={showIdea} onPaper={showPaper}
            spaceId={spaceId} onChanged={() => { loadChat(); loadSpace() }} reading={readingId === m.msg_id}
            onBranch={space.can_write && !currentChat?.run_id && m.kind !== 'system' && !m.pending ? () => branchAt(m) : undefined}
            branches={(chat.branches ?? []).filter((b) => b.branch_at === m.msg_id)} onOpenChat={setChatId}
            onSpeak={(t) => { if (readingId === m.msg_id) { stopSpeaking(); setSpeaking(false); setReadingId(null) } else readAloud(t, undefined, m.msg_id) }}
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
                {(!currentChat || currentChat.mine) && (
                  <span className="bs-lead" role="group" aria-label="Who leads">
                    <span className="bs-lead-label">Who leads</span>
                    <button type="button" className={lead === 'me' ? 'on' : ''} aria-pressed={lead === 'me'} disabled={busy}
                      title="You drive; the agents answer, and question you when you seem very sure" onClick={() => lead !== 'me' && setLead('me')}>You</button>
                    <button type="button" className={lead === 'pippin' ? 'on' : ''} aria-pressed={lead === 'pippin'} disabled={busy}
                      title="Pippin drives: after each message, where things stand, the next step with drafts, and a choice for you" onClick={() => lead !== 'pippin' && setLead('pippin')}>Pippin</button>
                  </span>
                )}
                <button type="button" className={handsFree ? 'on' : ''} aria-pressed={handsFree}
                  title={handsFree ? 'Back to typing (Esc, or just start typing)' : 'Talk instead of typing: what you say is sent after a pause, and the answers are read aloud'}
                  onClick={() => setHandsFree(!handsFree)}>
                  {handsFree ? (voice.state === 'listening' ? '● Listening… (hands-free on)' : voice.state === 'transcribing' ? 'Writing down what you said…'
                    : speaking ? 'Speaking… (hands-free on)' : chat?.pending || busy ? 'Waiting for the answer… (hands-free on)' : 'Hands-free on') : 'Hands-free'}
                  {handsFree && <span className="bs-stop-hint"> · Esc to stop</span>}
                </button>
                {moves.map((mv) => (
                  <button key={mv.key} type="button" disabled={busy || !!mv.off} title={mv.off || mv.title}
                    className={`${mv.on ? 'on' : ''}${suggested.includes(mv.key) ? ' suggested' : ''}`} onClick={mv.run}>
                    {suggested.includes(mv.key) && <span className="bs-sug-dot" aria-label="Suggested next" />}{mv.label}
                  </button>
                ))}
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
                {!currentChat?.run_id || live ? (
                  <button type="button" className={`bs-mic${voice.state === 'listening' ? ' on' : ''}`} disabled={voice.state === 'transcribing'}
                    aria-label={voice.state === 'listening' ? 'Stop and write down what you said' : 'Speak instead of typing'}
                    title={voice.state === 'listening' ? 'Stop: what you said goes into the message box' : 'Speak instead of typing'}
                    onClick={() => { stopSpeaking(); setSpeaking(false); if (voice.state === 'listening') voice.stop(); else startVoice(false) }}>
                    {voice.state === 'transcribing' ? <span className="spinner" /> : voice.state === 'listening'
                      ? <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" /></svg>
                      : <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>}
                  </button>
                ) : null}
                <textarea ref={msgRef} rows={1} className={voice.state !== 'idle' ? 'bs-hearing' : undefined} aria-label="Message" placeholder={currentChat?.run_id ? 'Speak in the round table' : lead === 'pippin' ? 'Steer: reply, or say “I’ll lead” to take it back' : chat?.chat.guided ? 'Answer the Mentor, or name another agent' : 'Think out loud, or @Skeptic @Methods @Librarian @Mentor'}
                  value={text} onChange={(e) => { if (voiceOn) { typing.current = true; quiet() } setText(e.target.value) }}
                  disabled={!!currentChat?.run_id && !live}
                  onKeyDown={(e) => {
                    // Enter sends, Shift+Enter is a new line; Enter that picks a word in an input method is neither
                    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit() }
                  }} />
                <button className="btn-sm btn-modify" disabled={busy || !text.trim()}>Send</button>
              </div>
              {voice.error && <div className="bs-voice-err">{voice.error} <button type="button" className="link-btn" onClick={() => voice.setError(null)}>dismiss</button></div>}
            </div>
          </form>
        ) : <div className="bs-foot lit-sub">You can read this space; writing in it needs comment rights in the project.</div>}
      </section>
      <div className="resize-handle" role="separator" aria-orientation="vertical" title="Drag to resize; double-click to reset"
        onMouseDown={dragCol('right')} onDoubleClick={() => setCols(BS_COLS)} />

      {/* ── right: ideas and papers ── */}
      <aside className="bs-right" aria-label={reader ? 'The paper' : 'Ideas and papers'}>
        {reader && (
          <Suspense fallback={<div className="rd docked"><div className="rd-loading">Opening the paper…</div></div>}>
            <PaperReader key={`${reader.paper}|${reader.space}|${reader.cardId ?? ''}`} req={reader} onClose={() => onCloseReader?.()}
              docked full={full} onToggleFull={() => setFull((f) => !f)} />
          </Suspense>
        )}
        {!reader && <>
        <div className="team-tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'ideas'} className={tab === 'ideas' ? 'on' : ''} onClick={() => setTab('ideas')}>Ideas · {ideas.length}</button>
          <button role="tab" aria-selected={tab === 'papers'} className={tab === 'papers' ? 'on' : ''} onClick={() => setTab('papers')}>Papers · {papersCount}</button>
          <button role="tab" aria-selected={tab === 'map'} className={tab === 'map' ? 'on' : ''} onClick={() => setTab('map')}>Map</button>
          {!currentChat?.run_id && <button role="tab" aria-selected={tab === 'plan'} className={tab === 'plan' ? 'on' : ''} onClick={() => setTab('plan')}
            title="The research plan your conversation with the Mentor builds">Plan{chat?.chat.guided ? ' ●' : ''}</button>}
        </div>
        <div className="bs-right-body">
          {tab === 'ideas' ? (
            <IdeasPane space={space} cards={cards} expanded={expanded} focus={focus} flash={flash} filter={ideaFilter} setFilter={setIdeaFilter}
              actions={ideaActions} activity={space.activity}
              onNew={(type) => setDialog({ kind: 'new', type })} onBring={() => setDialog({ kind: 'bring' })}
              onSuggestion={onSuggestion}
              onDismiss={(s) => act(async () => { await api.brainstorm.dismissSuggestion(s.sug_id); await loadSpace(); setToast({ text: 'Dismissed. The agents bring fewer things like this.' }) })}
              onAttach={(cid, to, rel) => act(async () => { await api.brainstorm.link(cid, to, rel); await loadSpace(); showIdea(to); setToast({ text: `Filed under “${short(cards.get(to)?.text ?? '', 40)}”.` }) })} />
          ) : tab === 'map' ? (
            <MapPane space={spaceId} cards={cards} onIdea={showIdea} />
          ) : tab === 'plan' ? (
            <PlanPane key={currentChat?.chat_id ?? "none"} chatId={currentChat?.run_id ? null : currentChat?.chat_id ?? null} onIdea={showIdea} />
          ) : (
            <PapersPane space={space} lit={lit} setLit={setLit} filter={paperFilter} searchId={searchSel} onSearch={setSearchSel}
              flashKey={flashKey} rule={ruleNow} onIdea={showIdea} openKey={openKey} onOpen={setOpenKey} onAsk={askAbout}
              onRead={(key, cardId) => readPaper({ paper: key, space: spaceId, cardId: focusCard?.card_id ?? cardId,
                chatId: chatId ?? undefined, from: 'Brainstorm' })} />
          )}
        </div>
        </>}
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
      {dialog?.kind === 'return' && chat?.parent && (
        <ReturnDialog chatId={chat.chat.chat_id} parentTitle={chat.parent.title} onClose={() => setDialog(null)}
          onDone={(parent, posted) => { setDialog(null); setChatId(parent); loadSpace(); if (posted) setToast({ text: 'Brought back: it is your message there now.' }) }} />
      )}
      {dialog?.kind === 'zotero' && <ZoteroDialog onChanged={setZotero} onClose={() => { setDialog(null); loadSpace() }} />}
      {toast && (
        <div className="bs-toast" role="status">{toast.text}
          {toast.action && <button className="link-btn" onClick={() => { toast.action![1](); setToast(null) }}>{toast.action[0]}</button>}
          <button className="link-btn" onClick={() => setToast(null)} aria-label="Dismiss">×</button></div>
      )}
    </div>
  )
}

function Message(props: Parameters<typeof MessageBody>[0] & {
  /** Go off on a tangent from here. */
  onBranch?: () => void
  branches?: { chat_id: string; title: string }[]
  onOpenChat?: (chatId: string) => void
}) {
  const { onBranch, branches = [], onOpenChat, ...rest } = props
  if (!onBranch && !branches.length) return <MessageBody {...rest} />
  return (
    <div className="bs-msg-wrap">
      <MessageBody {...rest} />
      {onBranch && <button type="button" className="bs-branch-btn" title="Go off on a tangent from here, and come back later" onClick={onBranch}>Branch off here</button>}
      {branches.length > 0 && (
        <div className="bs-branches">{branches.map((b) => (
          <button key={b.chat_id} type="button" className="link-btn" onClick={() => onOpenChat?.(b.chat_id)}>↳ {b.title.replace(/^↳\s*/, '')}</button>
        ))}</div>
      )}
    </div>
  )
}

function MessageBody({ m, space, cards, onIdea, onPaper, onConsent, spaceId, onChanged, onSpeak, reading }: {
  m: BsMessage; space: BsSpace; cards: Map<string, BsCard>; onIdea: (id: string) => void; onPaper: (key: string) => void; onConsent: (share: boolean) => void
  spaceId: string; onChanged: () => void
  /** Read an answer aloud, or stop reading it. */
  onSpeak?: (text: string) => void
  /** It is being read aloud now. */
  reading?: boolean
}) {
  const agent = space.agents[m.who]
  const seat = /^p\d\d$/.test(m.who)
  if (m.kind === 'system') return <div className="bs-system"><Rich text={m.text} cards={cards} onIdea={onIdea} onPaper={onPaper} space={space.space} /></div>
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
        <div className="bs-msg-main">{m.widget?.type === 'from_branch' && <div className="bs-from-branch">↩ Brought back from the tangent “{m.widget.title.replace(/^↳\s*/, '')}”</div>}
          <div className="bs-bubble">{m.text}</div>
          {(m.attachments ?? []).length > 0 && <div className="lit-sub">With {m.attachments!.map((a) => a.kind === 'paper' ? `paper ${a.label ?? ''}` : a.label ?? a.path).join(', ')}</div>}
          {!m.mine && <div className="lit-sub">{m.who_name}</div>}</div></div>
    )
  }
  const role = agent ? agent.role : seat ? m.steps[0] : m.who === 'Chair' ? 'runs the round table' : ''
  const steps = seat ? [] : m.steps
  return (
    <div className="bs-msg"><Avatar name={m.who === 'Chair' ? 'CH' : m.who} agent={agent} />
      <div className="bs-msg-main">
        <div className="bs-who"><b>{seat ? `Seat ${m.who}` : m.who}</b>{role && <span className="lit-sub"> · {role}</span>}
          {onSpeak && m.text && !m.pending && <button type="button" className={`bs-speak${reading ? ' on' : ''}`}
            title={reading ? 'Stop reading (Esc)' : 'Read it aloud'} aria-label={reading ? 'Stop reading' : 'Read it aloud'} onClick={() => onSpeak(m.text)}>
            {reading
              ? <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" /></svg>
              : <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M11 5L6 9H3v6h3l5 4V5zM15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" /></svg>}</button>}</div>
        {steps.length > 0 && (
          <div className="bs-steps">{steps.map((s, i) => <div key={i}>{s === 'Thinking' ? BULB : LENS}
            <span><Rich text={s} cards={cards} onIdea={onIdea} onPaper={onPaper} space={space.space} />{m.pending && i === steps.length - 1 ? '…' : ''}</span></div>)}</div>
        )}
        {m.text && <div className="bs-text"><Rich text={m.text} cards={cards} onIdea={onIdea} onPaper={onPaper} space={space.space} />{m.pending && seat ? '…' : ''}</div>}
        {m.kind === 'action' && m.widget && <ActionWidget m={m} space={spaceId} onChanged={onChanged} onIdea={onIdea} />}
        {m.widget?.type === 'friction' && m.widget.state === 'open' && !m.pending && (
          <div className="bs-friction"><span className="lit-sub">Answer it, or</span>
            <button type="button" className="link-btn" title="No questions like this for a while"
              onClick={async () => { try { await api.brainstorm.act(m.msg_id, 'later'); onChanged() } catch { /* already decided */ } }}>not now</button></div>
        )}
        {m.widget?.type === 'friction' && m.widget.state === 'waved' && <div className="lit-sub">Waved off: fewer questions like this for a while.</div>}
        {m.effects.map(([what, id], i) => (
          <div key={i} className="bs-effect">↳ {what} {!id ? null : cards.get(id) ? <button className="bs-ichip" onClick={() => onIdea(id)}>{short(cards.get(id)!.text, 40)}</button> : 'an idea'}</div>
        ))}
      </div>
    </div>
  )
}

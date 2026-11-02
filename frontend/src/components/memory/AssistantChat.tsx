/** Pippin at your side: the conversation of the desktop orb and the browser's side panel. Typed here or spoken to the
 *  orb (hold ⌥⇧Space), one thread about everything the console knows: your notebook, what is in front of you, your
 *  projects. Buttons open the right page of the console when something needs it. In the browser's side panel a
 *  question goes with the page open there (and the words selected on it): Pippin reads it, an answer can be kept as
 *  a note on the page, and the page opens in Pippin's reader at the same place. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { AssistantTurn } from '../../types'
import { type BrowserPage, currentPage, inBrowser, onPageChange } from './browserPage'
import { ProposedActions } from './ProposedActions'

const STARTERS = [
  'What is running in my projects?',
  'Does anything wait for my decision?',
  'What does the page in front of me say?',
  'Read me today’s digest',
]
const PAGE_STARTERS = [
  'What is the main claim here, and what supports it?',
  'How does this relate to what I saved?',
  'What would I check before trusting it?',
]

const readerLink = (paperId: string, quote = '') =>
  `/?read=${encodeURIComponent(paperId)}${quote ? `&quote=${encodeURIComponent(quote.slice(0, 300))}` : ''}`

export function AssistantChat({ refreshKey = 0, onPick }: { refreshKey?: number; onPick?: (unitId: string) => void }) {
  const [turns, setTurns] = useState<AssistantTurn[] | null>(null)
  const [draft, setDraft] = useState('')
  const [asking, setAsking] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const end = useRef<HTMLDivElement | null>(null)
  const box = useRef<HTMLTextAreaElement | null>(null)
  const browser = inBrowser()
  const [page, setPage] = useState<BrowserPage | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    if (!browser) return
    currentPage().then(setPage)
    return onPageChange((p, ask) => { setPage(p); if (ask) box.current?.focus() })
  }, [browser])

  const load = useCallback(() => api.assistant.history().then(setTurns).catch((e) => setErr((e as Error).message)), [])
  useEffect(() => { load() }, [load, refreshKey])
  useEffect(() => {
    const again = () => { if (document.visibilityState === 'visible') load() }
    document.addEventListener('visibilitychange', again)
    return () => document.removeEventListener('visibilitychange', again)
  }, [load])
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [turns, asking])

  const send = async (text: string) => {
    const t = text.trim()
    if (!t || asking) return
    setAsking(t); setDraft(''); setErr(null)
    try {
      const pg = browser ? await currentPage() : null
      if (pg) setPage(pg)
      await api.assistant.say(t, '', pg?.url ? pg : null)
      await load()
    } catch (e) {
      setErr((e as Error).message)
      setDraft(t)
    } finally {
      setAsking(null)
    }
  }
  const fresh = async () => { await api.assistant.clear(); setTurns([]) }

  // the page in Pippin's reader, kept in the Library, at the words selected on it
  const openHere = async () => {
    const pg = (await currentPage()) ?? page
    if (!pg?.url) return
    setBusy('open'); setErr(null)
    try {
      const k = await api.assistant.openPage(pg)
      window.open(readerLink(k.paper_id, pg.selection), '_blank')
    } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }
  // an answer about a page, kept as a note on the words it was about
  const keep = async (t: AssistantTurn) => {
    if (!t.page) return
    const now = await currentPage()
    const pg = now?.url.split('#')[0] === t.page.url ? { ...now, selection: t.page.selection }
      : { url: t.page.url, title: t.page.title, text: '', selection: t.page.selection }
    setBusy(t.turn_id); setErr(null)
    try { await api.assistant.keepAnswer(t.turn_id, pg); await load() } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }

  return (
    <div className="as-chat">
      <div className="as-thread" aria-live="polite">
        {turns && turns.length === 0 && !asking && (
          <div className="as-empty">
            <b>{browser ? 'Ask Pippin about this page' : 'Ask Pippin anything, without opening the console'}</b>
            <span className="lit-sub">{browser
              ? 'Pippin reads the page you have open (a paper from its PDF). Select words on it to ask about them. Your projects and notebook are here too.'
              : 'About your projects, your notebook, or what is in front of you. Type here, or hold ⌥⇧Space and speak to the orb.'}</span>
            <div className="as-starters">{(browser && page?.url ? PAGE_STARTERS : STARTERS).map((s) => <button key={s} className="mem-chip" onClick={() => send(s)}>{s}</button>)}</div>
          </div>
        )}
        {turns?.map((t) => t.role === 'user' ? (
          <div key={t.turn_id} className="as-turn me"><div className="as-bubble">
            {t.page?.selection && <blockquote className="as-said-on" title={t.page.title}>{t.page.selection.slice(0, 200)}{t.page.selection.length > 200 ? '…' : ''}</blockquote>}
            {t.via === 'voice' && <span className="as-via" title="Spoken to the orb">🎙</span>}{t.text}</div></div>
        ) : (
          <div key={t.turn_id} className="as-turn">
            <div className="as-bubble">{t.text}</div>
            {(t.items ?? []).length > 0 && (
              <div className="as-sources">
                {t.items!.map((it, i) => (
                  <div key={i} className="mem-src">
                    {it.unit_id && onPick ? <button className="link-btn" onClick={() => onPick(it.unit_id!)}>{it.claim}</button>
                      : <span>{it.n ? `${it.n}. ` : ''}{it.claim || it.text}</span>}
                    {(it.sources ?? []).slice(0, 2).map((s, j) => (
                      <blockquote key={j} className="pl-quote">{s.quote}<span className="lit-sub"> — {s.url
                        ? <a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a> : s.title}</span></blockquote>
                    ))}
                  </div>
                ))}
              </div>
            )}
            <ProposedActions actions={t.actions ?? []} />
            {t.page && (
              <div className="as-links">{t.kept
                ? <a className="btn-sm" href={readerLink(t.kept.paper_id, t.kept.quote)} target="_blank" rel="noreferrer">Kept on “{t.kept.title}” · open ↗</a>
                : <button className="btn-sm" disabled={busy === t.turn_id} onClick={() => keep(t)}
                    title="Your question and this answer, as a note on the words it is about; the page goes into your Library">
                    {busy === t.turn_id ? 'Keeping…' : 'Keep as a note on the page'}</button>}
              </div>
            )}
            {(t.links ?? []).length > 0 && (
              <div className="as-links">{t.links!.map((l) => (
                <a key={l.path} className="btn-sm" href={l.path} target="_blank" rel="noreferrer">{l.label} ↗</a>
              ))}</div>
            )}
          </div>
        ))}
        {asking && <>
          <div className="as-turn me"><div className="as-bubble">{asking}</div></div>
          <div className="as-turn"><div className="as-bubble as-thinking">Looking…</div></div>
        </>}
        <div ref={end} />
      </div>
      {err && <div className="panel-message error">{err}</div>}
      {browser && page?.url && (
        <div className="as-onpage">
          <div className="as-onpage-head">
            <span className="lit-sub">On this page</span>
            <b title={page.url}>{page.title || page.url}</b>
            <button className="btn-sm" disabled={busy === 'open'} onClick={openHere}
              title="Keep it in your Library and read it in Pippin, with notes, gaps and figures">{busy === 'open' ? 'Opening…' : 'Open in Pippin ↗'}</button>
          </div>
          {page.selection && <blockquote className="as-said-on">{page.selection.slice(0, 240)}{page.selection.length > 240 ? '…' : ''}</blockquote>}
        </div>
      )}
      <form className="as-input" onSubmit={(e) => { e.preventDefault(); send(draft) }}>
        <textarea ref={box} rows={2} value={draft} placeholder={browser && page?.url ? (page.selection ? 'Ask about the selected words…' : 'Ask about this page…') : 'Ask, or tell Pippin something…'} aria-label="Message to Pippin"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(draft) } }} />
        <div className="as-input-row">
          {turns && turns.length > 0 && <button type="button" className="link-btn" onClick={fresh}>New conversation</button>}
          <span className="lit-sub" style={{ marginLeft: 'auto' }}>Enter to send</span>
          <button className="btn-sm btn-approve" disabled={!draft.trim() || !!asking}>Send</button>
        </div>
      </form>
    </div>
  )
}

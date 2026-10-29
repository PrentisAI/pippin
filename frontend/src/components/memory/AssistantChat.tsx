/** AURORA at your side: the conversation of the desktop orb and the browser's side panel. Typed here or spoken to the
 *  orb (hold ⌥⇧Space), one thread about everything the console knows: your memory, what is in front of you, your
 *  projects. Buttons open the right page of the console when something needs it. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { AssistantTurn } from '../../types'

const STARTERS = [
  'What is running in my projects?',
  'Does anything wait for my decision?',
  'What does the page in front of me say?',
  'Read me today’s digest',
]

export function AssistantChat({ refreshKey = 0, onPick }: { refreshKey?: number; onPick?: (unitId: string) => void }) {
  const [turns, setTurns] = useState<AssistantTurn[] | null>(null)
  const [draft, setDraft] = useState('')
  const [asking, setAsking] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const end = useRef<HTMLDivElement | null>(null)

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
      await api.assistant.say(t)
      await load()
    } catch (e) {
      setErr((e as Error).message)
      setDraft(t)
    } finally {
      setAsking(null)
    }
  }
  const fresh = async () => { await api.assistant.clear(); setTurns([]) }

  return (
    <div className="as-chat">
      <div className="as-thread" aria-live="polite">
        {turns && turns.length === 0 && !asking && (
          <div className="as-empty">
            <b>Ask AURORA anything, without opening the console</b>
            <span className="lit-sub">About your projects, your memory, or what is in front of you. Type here, or hold ⌥⇧Space and speak to the orb.</span>
            <div className="as-starters">{STARTERS.map((s) => <button key={s} className="mem-chip" onClick={() => send(s)}>{s}</button>)}</div>
          </div>
        )}
        {turns?.map((t) => t.role === 'user' ? (
          <div key={t.turn_id} className="as-turn me"><div className="as-bubble">{t.via === 'voice' && <span className="as-via" title="Spoken to the orb">🎙</span>}{t.text}</div></div>
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
      <form className="as-input" onSubmit={(e) => { e.preventDefault(); send(draft) }}>
        <textarea rows={2} value={draft} placeholder="Ask, or tell AURORA something…" aria-label="Message to AURORA"
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

import { useEffect, useState } from 'react'
import { api, describeError } from '../../api/client'
import type { KbStatus } from '../../types'

const NAME = { obsidian: 'Obsidian', notion: 'Notion' } as const

/** In the reader's notes popover: what you kept from this paper, as one note in Obsidian or Notion, or to copy. */
export function KbSection({ paper, onToast }: { paper: string; onToast: (text: string) => void }) {
  const [st, setSt] = useState<KbStatus | null>(null)
  const [sent, setSent] = useState<Partial<Record<'obsidian' | 'notion', { at: number; ref: string }>>>({})
  const [busy, setBusy] = useState('')
  const [connect, setConnect] = useState(false)
  const [token, setToken] = useState('')
  const [page, setPage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    api.reader.kb().then(setSt).catch(() => {})
    api.reader.kbNote(paper).then((n) => setSent(n.sent)).catch(() => {})
  }, [paper])
  if (!st) return null

  const send = async (t: 'obsidian' | 'notion') => {
    setBusy(t); setError('')
    try {
      const r = await api.reader.kbSend(paper, t)
      setSent((s) => ({ ...s, [t]: { at: Date.now() / 1000, ref: r.where } }))
      onToast(`${r.count} note${r.count === 1 ? '' : 's'} sent to ${NAME[t]}.`)
    } catch (e) { setError(describeError(e)) } finally { setBusy('') }
  }
  const copy = async () => {
    const n = await api.reader.kbNote(paper)
    await navigator.clipboard.writeText(n.markdown)
    onToast('Copied as Markdown.')
  }
  const saveNotion = async () => {
    setBusy('connect'); setError('')
    try { setSt(await api.reader.kbNotion({ token, page })); setConnect(false); setToken('') }
    catch (e) { setError(describeError(e)) } finally { setBusy('') }
  }

  return (
    <div className="rd-kb">
      <b>Your notes elsewhere</b>
      <span className="rd-text-2">What you kept here (your notes, and Pippin’s you kept), as one note per paper.</span>
      {(['obsidian', 'notion'] as const).map((t) => {
        const s = st[t]
        const off = s.level === 'off'
        return (
          <div key={t} className="rd-kb-row">
            <span><b>{NAME[t]}</b>{' '}
              <span className="rd-text-2">
                {!s.ready ? s.why
                  : off ? 'writing is off (account button → What Pippin may write)'
                  : sent[t] ? `sent${s.level === 'auto' ? ', kept up to date' : ''}`
                  : s.level === 'auto' ? 'goes there as you keep notes' : t === 'obsidian' ? 'into the Pippin folder of your vault' : `under “${s.where}”`}
              </span>
            </span>
            {t === 'notion' && !s.ready
              ? <button className="rd-act" onClick={() => setConnect(!connect)}>Connect</button>
              : <button className="rd-act" disabled={!s.ready || off || !!busy} onClick={() => send(t)}>
                  {busy === t ? 'Sending…' : sent[t] ? 'Send again' : 'Send'}</button>}
          </div>
        )
      })}
      {connect && (
        <div className="rd-kb-connect">
          <span className="rd-text-2">At notion.so/my-integrations make an internal integration and copy its secret. Then in
            Notion open the page your notes should go under → ⋯ → Connections → add the integration, and copy the page’s link.</span>
          <input type="password" placeholder={st.notion_settings.token ? `Token kept (${st.notion_settings.hint})` : 'Integration secret'}
            value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
          <input placeholder="https://www.notion.so/Reading-notes-…" value={page} onChange={(e) => setPage(e.target.value)} />
          <div className="rd-actions"><button className="rd-act keep" disabled={!page || busy === 'connect'} onClick={saveNotion}>
            {busy === 'connect' ? 'Checking…' : 'Connect Notion'}</button></div>
        </div>
      )}
      {error && <span className="rd-note-warn">{error}</span>}
      <div className="rd-actions"><button className="rd-act quiet" onClick={copy}>Copy as Markdown</button></div>
    </div>
  )
}

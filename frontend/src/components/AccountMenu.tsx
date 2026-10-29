import { useEffect, useRef, useState } from 'react'
import { api, describeError } from '../api/client'
import { useAuth } from '../auth'
import type { KeyStatus, WriteLevel, WriteSettings } from '../types'

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).map((x) => x[0]).join('').slice(0, 2).toUpperCase() || 'Y'
}

/** The account button at the bottom of the activity bar: who you are, your keys, sign out. */
export function AccountButton({ onOpenTeam }: { onOpenTeam: () => void }) {
  const auth = useAuth()
  const [open, setOpen] = useState(false)
  const [keysOpen, setKeysOpen] = useState(false)
  const [writesOpen, setWritesOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open])

  return (
    <div className="account" ref={ref}>
      <button className="ab-btn account-btn" title={auth.accounts ? auth.user.name : 'Account'} aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen(!open)}>{auth.accounts ? initials(auth.user.name) : 'Y'}</button>
      {open && (
        <div className="account-menu" role="menu">
          {auth.accounts ? (
            <div className="account-who"><b>{auth.user.name}</b><span>{auth.user.email}</span><span>{auth.team} · {auth.user.team_role}</span></div>
          ) : (
            <div className="account-who"><b>Single-user console</b><span>No accounts: nothing asks for a sign-in.</span></div>
          )}
          {auth.accounts && <button role="menuitem" onClick={() => { setOpen(false); setKeysOpen(true) }}>Your API keys</button>}
          <button role="menuitem" onClick={() => { setOpen(false); setWritesOpen(true) }}>What Pippin may write</button>
          <button role="menuitem" onClick={() => { setOpen(false); onOpenTeam() }}>{auth.accounts ? `Team: ${auth.team}` : 'Working with others'}</button>
          {auth.accounts && <button role="menuitem" onClick={() => auth.signOut()}>Sign out</button>}
        </div>
      )}
      {keysOpen && <KeysDialog onClose={() => setKeysOpen(false)} />}
      {writesOpen && <WritesDialog onClose={() => setWritesOpen(false)} />}
    </div>
  )
}

/** Your own API key: what your runs and conversations are charged to. */
function KeysDialog({ onClose }: { onClose: () => void }) {
  const [info, setInfo] = useState<{ keys: KeyStatus[]; uses: string; team_key_allowed?: boolean } | null>(null)
  const [value, setValue] = useState('')
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const load = () => api.me.keys().then(setInfo).catch((err) => setMessage({ tone: 'error', text: describeError(err) }))
  useEffect(() => {
    load()
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  const key = info?.keys[0]
  const uses = { own: 'your own key', team: 'the team’s key', none: 'no key: add one to start runs and talk to the front desk', server: 'the server’s key' }[info?.uses ?? 'none']

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <form className="modal" role="dialog" aria-modal="true" aria-label="Your API keys" onMouseDown={(e) => e.stopPropagation()}
        onSubmit={async (e) => {
          e.preventDefault()
          try { await api.me.setKey('openrouter', value); setValue(''); setMessage({ tone: 'ok', text: 'Saved. Only you can use it; nobody can read it back.' }); load() }
          catch (err) { setMessage({ tone: 'error', text: describeError(err) }) }
        }}>
        <div className="modal-title">Your API keys</div>
        <div className="modal-body">
          <div>OpenRouter: {key?.set ? <code>{key.hint}</code> : 'not set'}</div>
          <div>Your runs, conversations and feed now use <b>{uses}</b>.</div>
          <input type="password" placeholder={key?.set ? 'Paste a new key to replace it' : 'Paste your OpenRouter key'} aria-label="OpenRouter key"
            value={value} onChange={(e) => setValue(e.target.value)} />
          <div className="modal-hint">Used for the runs you start and your own conversations. Nobody else sees it, admins included; they only see whether you have one.
            {info?.team_key_allowed ? ' Without one, the team’s key is used.' : ''}</div>
          {message && <div className={message.tone === 'error' ? 'modal-error' : 'modal-hint'}>{message.text}</div>}
        </div>
        <div className="modal-actions">
          {key?.set && <button type="button" className="btn-sm" onClick={async () => { await api.me.deleteKey('openrouter'); load() }}>Remove</button>}
          <button type="button" className="btn-sm" onClick={onClose}>Close</button>
          <button className="btn-sm btn-approve" disabled={value.length < 8}>Save</button>
        </div>
      </form>
    </div>
  )
}

const LEVEL_LABEL: Record<WriteLevel, string> = { off: 'Off', ask: 'When I do it', auto: 'Also on its own' }

/** What Pippin may write into your own tools: each kind of write is off until you allow it. */
function WritesDialog({ onClose }: { onClose: () => void }) {
  const [w, setW] = useState<WriteSettings | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    api.me.writes().then(setW).catch((e) => setErr(describeError(e)))
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  const set = async (key: string, lv: WriteLevel) => {
    try { setW(await api.me.setWrites({ [key]: lv })) } catch (e) { setErr(describeError(e)) }
  }
  const targets = w ? [...new Set(Object.values(w.writes).map((x) => x.target))] : []
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal modal-wide" role="dialog" aria-modal="true" aria-label="What Pippin may write" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">What Pippin may write</div>
        <div className="modal-body">
          <div className="modal-hint">Pippin reads your pages, Zotero, notes and manuscripts freely. It writes into them only as far as you allow here,
            one kind at a time: <b>Off</b> (it offers what it would write, for you to copy), <b>When I do it</b> (only when you click or say it),
            or <b>Also on its own</b> (a sync may write too). Every write is logged and can be undone. It never changes your manuscript’s text.</div>
          {targets.map((t) => (
            <div key={t} className="wr-group"><b>{t}</b>
              {Object.entries(w!.writes).filter(([, x]) => x.target === t).map(([k, x]) => (
                <div key={k} className="wr-row"><span>{x.label}</span>
                  <span className="wr-seg" role="radiogroup" aria-label={x.label}>
                    {w!.options.map((lv) => <button key={lv} type="button" role="radio" aria-checked={w!.levels[k] === lv}
                      className={w!.levels[k] === lv ? 'on' : ''} onClick={() => set(k, lv)}>{LEVEL_LABEL[lv]}</button>)}
                  </span></div>
              ))}
            </div>
          ))}
          {w && w.log.length > 0 && <div className="wr-group"><b>Recent writes</b>
            {w.log.slice(0, 8).map((l) => <div key={l.log_id} className="wr-row"><span>{w.writes[l.write]?.label ?? l.write}</span>
              <span className="lit-sub">{new Date(l.created_at * 1000).toLocaleString()}{l.undone_at ? ' · undone' : ''}
                {!l.undone_at && <button className="link-btn" onClick={async () => {
                  try { const r = await api.me.undoWrite(l.log_id); if (!r.undone) setErr(r.how ?? 'Undo it where it was made.'); setW(await api.me.writes()) } catch (e) { setErr(describeError(e)) }
                }}> undo</button>}</span></div>)}</div>}
          {err && <div className="modal-error">{err}</div>}
        </div>
        <div className="modal-actions"><button type="button" className="btn-sm" onClick={onClose}>Close</button></div>
      </div>
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { api, describeError } from '../api/client'
import { useAuth } from '../auth'
import type { KeyStatus } from '../types'

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).map((x) => x[0]).join('').slice(0, 2).toUpperCase() || 'Y'
}

/** The account button at the bottom of the activity bar: who you are, your keys, sign out. */
export function AccountButton({ onOpenTeam }: { onOpenTeam: () => void }) {
  const auth = useAuth()
  const [open, setOpen] = useState(false)
  const [keysOpen, setKeysOpen] = useState(false)
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
          <button role="menuitem" onClick={() => { setOpen(false); onOpenTeam() }}>{auth.accounts ? `Team: ${auth.team}` : 'Working with others'}</button>
          {auth.accounts && <button role="menuitem" onClick={() => auth.signOut()}>Sign out</button>}
        </div>
      )}
      {keysOpen && <KeysDialog onClose={() => setKeysOpen(false)} />}
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

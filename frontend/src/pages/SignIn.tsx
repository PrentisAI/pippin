import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, describeError } from '../api/client'
import type { InviteInfo } from '../types'

/** Sign in with email and password. */
export function LoginPage({ team, onSignedIn }: { team: string | null; onSignedIn: () => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.auth.login(email.trim(), password)
      onSignedIn()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="signin-page">
      <form className="signin-card" onSubmit={submit}>
        <div className="signin-brand"><span className="signin-logo">A</span>AURORA</div>
        <h1>Sign in{team ? ` to ${team}` : ''}</h1>
        <label className="signin-field" htmlFor="si-email">Email
          <input id="si-email" type="email" autoComplete="username" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="signin-field" htmlFor="si-password">Password
          <input id="si-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="modal-error">{error}</div>}
        <button className="signin-submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <div className="signin-hint">No account? Ask a team admin for an invitation link. Forgot your password? An admin can set a new one with <code>aurora users passwd</code>.</div>
      </form>
    </div>
  )
}

/** Accept an invitation: set a name and password (or confirm the existing account's password). */
export function InvitePage({ token, onDone }: { token: string; onDone: () => void }) {
  const navigate = useNavigate()
  const [info, setInfo] = useState<InviteInfo | null>(null)
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.auth.invite(token).then(setInfo).catch((err) => setError(describeError(err)))
  }, [token])

  const accept = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await api.auth.accept(token, name.trim(), password)
      navigate(r.campaign_id ? `/campaigns/${r.campaign_id}` : '/', { replace: true })
      onDone()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="signin-page">
      <form className="signin-card" onSubmit={accept}>
        <div className="signin-brand"><span className="signin-logo">A</span>AURORA</div>
        {!info && !error && <div className="signin-hint">Opening the invitation…</div>}
        {!info && error && <><h1>Invitation</h1><div className="modal-error">{error}</div></>}
        {info && (
          <>
            <h1>{info.project ? `Join “${info.project}”` : `Join ${info.team}`}</h1>
            <div className="signin-lead">
              {info.invited_by ? `${info.invited_by} invited ${info.email}` : `Invitation for ${info.email}`}
              {info.project ? ` as ${info.project_role} of a project of ${info.team}` : ` to ${info.team}`}.
              {info.team_role === 'guest' && ' You join as a guest: you see only the projects you are invited to.'}
            </div>
            {!info.has_account && (
              <label className="signin-field" htmlFor="iv-name">Your name
                <input id="iv-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} required />
              </label>
            )}
            <label className="signin-field" htmlFor="iv-password">{info.has_account ? 'Your password' : 'Choose a password (10 characters or more)'}
              <input id="iv-password" type="password" autoComplete={info.has_account ? 'current-password' : 'new-password'}
                value={password} onChange={(e) => setPassword(e.target.value)} required minLength={info.has_account ? undefined : 10} />
            </label>
            {error && <div className="modal-error">{error}</div>}
            <button className="signin-submit" disabled={busy}>{busy ? 'Joining…' : info.has_account ? 'Accept and sign in' : 'Create my account'}</button>
          </>
        )}
      </form>
    </div>
  )
}

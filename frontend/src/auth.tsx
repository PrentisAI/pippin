import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from './api/client'
import type { AuthStatus, AuthUser } from './types'
import { InvitePage, LoginPage } from './pages/SignIn'

interface Auth {
  /** The console has accounts (without them there is one local user and no sign-in). */
  accounts: boolean
  user: AuthUser
  team: string | null
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<Auth | null>(null)

export function useAuth(): Auth {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth outside AuthGate')
  return auth
}

/** Shows the sign-in page until someone is signed in (or the console has no accounts). */
export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const location = useLocation()

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.auth.status())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    const out = () => { refresh() }
    window.addEventListener('aurora:signed-out', out)
    return () => window.removeEventListener('aurora:signed-out', out)
  }, [refresh])

  const invite = location.pathname.match(/^\/invite\/([^/]+)/)
  if (invite) return <InvitePage token={invite[1]} onDone={refresh} />
  if (error) return <div className="signin-page"><div className="signin-card"><div className="modal-error">The console does not answer: {error}</div></div></div>
  if (!status) return null
  if (status.accounts && !status.user) return <LoginPage team={status.team} onSignedIn={refresh} />
  const auth: Auth = {
    accounts: status.accounts,
    user: status.user!,
    team: status.team,
    refresh,
    signOut: async () => { await api.auth.logout().catch(() => {}); await refresh() },
  }
  return <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>
}

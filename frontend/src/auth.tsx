import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, ApiError, type Me } from './api'
import { applyTheme } from './theme'

type AuthState = {
  me: Me | null
  loading: boolean
  /** Reloads the signed-in user (after signing in, or after a change to the account). */
  refresh: () => Promise<Me | null>
  setMe: (me: Me) => void
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMeState] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  const setMe = useCallback((next: Me) => {
    setMeState(next)
    applyTheme(next.settings.display.theme)
  }, [])

  const refresh = useCallback(async () => {
    try {
      const next = await api<Me>('GET', '/api/me')
      setMe(next)
      return next
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMeState(null)
      return null
    } finally {
      setLoading(false)
    }
  }, [setMe])

  const signOut = useCallback(async () => {
    await api('POST', '/api/auth/logout').catch(() => undefined)
    setMeState(null)
  }, [])

  useEffect(() => {
    api<Me>('GET', '/api/me')
      .then(setMe)
      .catch(() => undefined)
      .finally(() => setLoading(false))
  }, [setMe])

  return <AuthContext.Provider value={{ me, loading, refresh, setMe, signOut }}>{children}</AuthContext.Provider>
}

// oxlint-disable-next-line react/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth outside AuthProvider')
  return ctx
}

/** The signed-in user. Only for screens inside the signed-in area. */
// oxlint-disable-next-line react/only-export-components
export function useMe(): Me {
  const { me } = useAuth()
  if (!me) throw new Error('useMe without a signed-in user')
  return me
}

import { useState } from 'react'
import { api, ApiError, type Me, type Settings } from '../api'
import { useAuth } from '../auth'

type DeepPartial<T> = { [K in keyof T]?: T[K] extends object ? (T[K] extends unknown[] ? T[K] : DeepPartial<T[K]>) : T[K] }

/** Saves a change to the user's settings and updates the screen with what the server kept. */
export function useSaveSettings() {
  const { me, setMe } = useAuth()
  return async (changes: DeepPartial<Settings>) => {
    const settings = await api<Settings>('PATCH', '/api/me/settings', changes)
    if (me) setMe({ ...me, settings } as Me)
    return settings
  }
}

export type Status = { kind: 'ok' | 'error'; text: string } | null

/** Runs an action, tracking busy state and a success or error message. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<Status>(null)
  async function run(action: () => Promise<unknown>, okText?: string): Promise<boolean> {
    setBusy(true)
    setStatus(null)
    try {
      await action()
      if (okText) setStatus({ kind: 'ok', text: okText })
      return true
    } catch (e) {
      setStatus({ kind: 'error', text: e instanceof ApiError ? e.message : 'Something went wrong.' })
      return false
    } finally {
      setBusy(false)
    }
  }
  return { busy, status, setStatus, run }
}

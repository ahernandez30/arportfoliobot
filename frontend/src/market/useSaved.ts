import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'

/**
 * A layout loaded from and saved to the server (per user). Saves are delayed a moment so
 * dragging or typing does not send a request per step; a failed save is reported.
 */
export function useSavedLayout<T>(path: string) {
  const [value, setValue] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => {
    api<T>('GET', path)
      .then(setValue)
      .catch((e) => setError(e.message))
  }, [path])

  const update = useCallback(
    (next: T) => {
      setValue(next)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => {
        api<T>('PUT', path, next)
          .then(() => setError(null))
          .catch((e) => setError(`Layout not saved: ${e.message}`))
      }, 600)
    },
    [path],
  )

  const replace = useCallback((next: T) => setValue(next), [])

  return { value, update, replace, error }
}

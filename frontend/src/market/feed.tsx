import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { backoffSeconds, wsUrl } from './feedUtil'

/** A live update from the server for one symbol (merged trades plus latest bid/ask). */
export type LiveTick = {
  symbol: string
  last?: number
  hi?: number
  lo?: number
  vol?: number
  t?: number | null
  bid?: number
  ask?: number
  summary?: { open?: number; high?: number; low?: number; prev_close?: number }
}

export type FeedState = 'connecting' | 'live' | 'delayed' | 'reconnecting' | 'no_key' | 'error' | 'offline'
export type FeedStatus = { state: FeedState; detail: string; realtime: boolean }

type Listener = (tick: LiveTick) => void

type FeedApi = {
  status: FeedStatus
  /** Live updates for a symbol until the returned function is called. */
  subscribe: (symbol: string, listener: Listener) => () => void
  /** Increases each time the connection comes back, so charts can reload what they missed. */
  reconnects: number
}

const FeedContext = createContext<FeedApi | null>(null)

export function FeedProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<FeedStatus>({ state: 'connecting', detail: '', realtime: false })
  const [reconnects, setReconnects] = useState(0)
  const listeners = useRef(new Map<string, Set<Listener>>())
  const socket = useRef<WebSocket | null>(null)
  const sendTimer = useRef<number | undefined>(undefined)

  const sendSubscriptions = useCallback(() => {
    window.clearTimeout(sendTimer.current)
    sendTimer.current = window.setTimeout(() => {
      const ws = socket.current
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'subscribe', symbols: [...listeners.current.keys()] }))
      }
    }, 100)
  }, [])

  useEffect(() => {
    let attempt = 0
    let stopped = false
    let retry: number | undefined
    let opened = false

    function connect() {
      const ws = new WebSocket(wsUrl())
      socket.current = ws
      ws.onopen = () => {
        if (opened) setReconnects((n) => n + 1)
        opened = true
        attempt = 0
        sendSubscriptions()
      }
      ws.onmessage = (e) => {
        let msg: { type?: string } & Record<string, unknown>
        try {
          msg = JSON.parse(e.data)
        } catch {
          return
        }
        if (msg.type === 'tick') {
          const tick = msg as unknown as LiveTick
          listeners.current.get(tick.symbol)?.forEach((l) => l(tick))
        } else if (msg.type === 'status') {
          setStatus((prev) => ({
            state: (msg.state as FeedState) ?? prev.state,
            detail: (msg.detail as string) ?? '',
            realtime: typeof msg.realtime === 'boolean' ? msg.realtime : prev.realtime,
          }))
        }
      }
      ws.onclose = (e) => {
        socket.current = null
        if (stopped) return
        // 4401: signed out. Do not keep retrying; the app returns to the sign-in page.
        if (e.code === 4401) {
          setStatus({ state: 'offline', detail: 'Signed out.', realtime: false })
          return
        }
        setStatus((prev) => ({ ...prev, state: 'reconnecting', detail: 'Reconnecting…' }))
        retry = window.setTimeout(connect, backoffSeconds(attempt++) * 1000)
      }
    }

    connect()
    return () => {
      stopped = true
      window.clearTimeout(retry)
      socket.current?.close()
    }
  }, [sendSubscriptions])

  const subscribe = useCallback(
    (symbol: string, listener: Listener) => {
      const key = symbol.toUpperCase()
      let set = listeners.current.get(key)
      if (!set) {
        set = new Set()
        listeners.current.set(key, set)
        sendSubscriptions()
      }
      set.add(listener)
      return () => {
        const s = listeners.current.get(key)
        if (!s) return
        s.delete(listener)
        if (s.size === 0) {
          listeners.current.delete(key)
          sendSubscriptions()
        }
      }
    },
    [sendSubscriptions],
  )

  return <FeedContext.Provider value={{ status, subscribe, reconnects }}>{children}</FeedContext.Provider>
}

// oxlint-disable-next-line react/only-export-components
export function useFeed(): FeedApi {
  const ctx = useContext(FeedContext)
  if (!ctx) throw new Error('useFeed outside FeedProvider')
  return ctx
}

/** Calls `onTick` for each live update of `symbol` while the component is shown. */
// oxlint-disable-next-line react/only-export-components
export function useTicks(symbol: string | null, onTick: Listener) {
  const { subscribe } = useFeed()
  const handler = useRef(onTick)
  useEffect(() => {
    handler.current = onTick
  })
  useEffect(() => {
    if (!symbol) return
    return subscribe(symbol, (t) => handler.current(t))
  }, [symbol, subscribe])
}

const LABELS: Record<FeedState, string> = {
  connecting: 'Connecting',
  live: 'Live',
  delayed: 'Delayed 15 min',
  reconnecting: 'Reconnecting',
  no_key: 'No data key',
  error: 'Feed problem',
  offline: 'Offline',
}

export function FeedBadge() {
  const { status } = useFeed()
  const cls =
    status.state === 'live' ? 'badge-on' : status.state === 'delayed' ? 'badge-paper' : status.state === 'connecting' ? '' : 'badge-off'
  return (
    <span className={`badge feed-badge ${cls}`} title={status.detail || LABELS[status.state]}>
      <span className="feed-dot" aria-hidden="true" />
      {LABELS[status.state]}
    </span>
  )
}

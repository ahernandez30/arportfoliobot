import { useEffect, useState } from 'react'
import { api } from '../api'
import { changeClass, formatPct, formatPrice, pctChange } from './bars'
import { useFeed, useTicks } from './feed'

type QuoteResponse = { last: number | null; prev_close: number | null }

/** Last price and today's change for a symbol, updated live. */
export default function LivePrice({ symbol }: { symbol: string }) {
  const { reconnects } = useFeed()
  const [last, setLast] = useState<number | null>(null)
  const [prev, setPrev] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    api<QuoteResponse>('GET', `/api/market/quote?symbol=${encodeURIComponent(symbol)}`)
      .then((q) => {
        if (cancelled) return
        setLast(q.last)
        setPrev(q.prev_close)
      })
      .catch(() => {
        if (cancelled) return
        setLast(null)
        setPrev(null)
      })
    return () => {
      cancelled = true
    }
  }, [symbol, reconnects])

  useTicks(symbol, (t) => {
    if (t.last !== undefined) setLast(t.last)
    if (t.summary?.prev_close !== undefined) setPrev(t.summary.prev_close)
  })

  const change = pctChange(last, prev)
  return (
    <span className="live-price num" aria-live="off">
      <span>{formatPrice(last)}</span>
      <span className={changeClass(change)}>{formatPct(change)}</span>
    </span>
  )
}

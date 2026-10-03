import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import { useMe } from '../auth'
import { changeClass, formatPct, formatPrice, rowChanges } from './bars'
import { useFeed, useTicks } from './feed'
import './market.css'

export type WatchRow = {
  symbol: string
  description: string
  found: boolean
  last: number | null
  prev_close: number | null
  refs: { '1W': number | null; '1M': number | null; '3M': number | null }
}

const PERIODS = ['1W', '1M', '3M'] as const

function Line({ row, onPick }: { row: WatchRow; onPick?: (s: string) => void }) {
  // Starts from the loaded row; the list remounts lines whenever it reloads.
  const [live, setLive] = useState(row)
  useTicks(row.found ? row.symbol : null, (t) => {
    setLive((prev) => ({
      ...prev,
      last: t.last ?? prev.last,
      prev_close: t.summary?.prev_close ?? prev.prev_close,
    }))
  })
  const ch = rowChanges(live)
  return (
    <tr className={onPick ? 'clickable' : ''} onClick={() => onPick?.(row.symbol)}>
      <td>
        <span className="sym">{row.symbol}</span>
        {!row.found && <span className="muted"> not found</span>}
      </td>
      <td className="num right">{formatPrice(live.last)}</td>
      {(['1D', ...PERIODS] as const).map((p) => (
        <td key={p} className={`num right ${changeClass(ch[p])}`}>
          {formatPct(ch[p])}
        </td>
      ))}
    </tr>
  )
}

/** The user's watchlist with 1 day, 1 week, 1 month and 3 month changes (plan section 6). */
export default function Watchlist({ onPick }: { onPick?: (symbol: string) => void }) {
  const me = useMe()
  const { reconnects } = useFeed()
  const [rows, setRows] = useState<WatchRow[] | null>(null)
  const [version, setVersion] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const symbolsKey = me.settings.watchlist.symbols.join(',')

  useEffect(() => {
    let cancelled = false
    const load = () =>
      api<{ rows: WatchRow[] }>('GET', '/api/market/watchlist')
        .then((r) => {
          if (cancelled) return
          setRows(r.rows)
          setVersion((v) => v + 1)
          setError(null)
        })
        .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : 'Could not load the watchlist.'))
    void load()
    // Reference prices change once a day; refresh now and then anyway.
    const timer = window.setInterval(load, 10 * 60 * 1000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [symbolsKey, reconnects])

  if (error) return <p className="msg msg-warn">{error}</p>
  if (!rows) return <p className="muted">Loading…</p>
  if (!rows.length) return <p className="muted">Your watchlist is empty. Add symbols in Config → Watchlist.</p>
  return (
    <div className="table-wrap">
      <table className="table watchlist">
        <thead>
          <tr>
            <th>Symbol</th>
            <th className="right">Last</th>
            <th className="right">1 day</th>
            <th className="right">1 week</th>
            <th className="right">1 month</th>
            <th className="right">3 months</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <Line key={`${r.symbol}-${version}`} row={r} onPick={onPick} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

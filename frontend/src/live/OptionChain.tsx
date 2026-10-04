import { useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, type Chain, type ChainSide } from '../api'
import { formatPct, formatPrice, changeClass } from '../market/bars'
import { formatDay } from '../money'
import { SYMBOL_RE } from '../forms'
import { inTheMoney, visibleRows, type Pick } from './ticket'

const QUICK = ['TSLA', 'QQQ', 'SPY']
const AROUND = 10

/** Option chain for any symbol: expirations, then calls and puts side by side by strike.
 * Clicking a bid or ask loads that contract and price into the order ticket. */
export default function OptionChain({ symbol, onSymbol, onPick, picked }: {
  symbol: string
  onSymbol: (s: string) => void
  onPick: (p: Pick) => void
  picked: Pick | null
}) {
  const [text, setText] = useState(symbol)
  const [expirations, setExpirations] = useState<string[] | null>(null)
  const [expiration, setExpiration] = useState<string | null>(null)
  const [chain, setChain] = useState<Chain | null>(null)
  const [all, setAll] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // The page remounts this chain for each new symbol, so state starts empty.
    let cancelled = false
    api<{ expirations: string[] }>('GET', `/api/market/expirations?symbol=${encodeURIComponent(symbol)}`)
      .then((r) => {
        if (cancelled) return
        setExpirations(r.expirations)
        setExpiration(r.expirations[0] ?? null)
        if (!r.expirations.length) setError(`No listed options for ${symbol}.`)
      })
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : 'Could not load expirations.'))
    return () => {
      cancelled = true
    }
  }, [symbol])

  useEffect(() => {
    if (!expiration) return
    let cancelled = false
    const load = () => {
      if (document.visibilityState !== 'visible') return
      api<Chain>('GET', `/api/market/chain?symbol=${encodeURIComponent(symbol)}&expiration=${expiration}`)
        .then((c) => { if (!cancelled) { setChain(c); setError(null) } })
        .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : 'Could not load the chain.'))
    }
    load()
    const timer = window.setInterval(load, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [symbol, expiration])

  function submit(e: FormEvent) {
    e.preventDefault()
    const s = text.trim().toUpperCase()
    if (SYMBOL_RE.test(s)) onSymbol(s)
  }

  const last = chain?.underlying?.last ?? null
  const rows = chain ? visibleRows(chain.rows, last, AROUND, all) : []

  function cell(type: 'call' | 'put', strike: number, side: ChainSide | null, which: 'bid' | 'ask') {
    const price = side?.[which] ?? null
    const isPicked = picked && picked.option_type === type && picked.strike === strike && picked.expiration === expiration && picked.symbol === symbol
    const itm = inTheMoney(type, strike, last)
    return (
      <td key={`${type}-${which}`} className={`num right price-cell ${itm ? 'itm' : ''} ${isPicked ? 'picked' : ''}`}>
        {side && price != null ? (
          <button type="button" className="price-btn" aria-label={`${type} ${strike} ${which} ${price}`}
            onClick={() => onPick({ symbol, option_type: type, strike, expiration: expiration!, price })}>
            {formatPrice(price)}
          </button>
        ) : '—'}
      </td>
    )
  }

  return (
    <div className="panel chain">
      <div className="chain-head">
        <form className="actions" onSubmit={submit}>
          <input className="input symbol-input" value={text} onChange={(e) => setText(e.target.value.toUpperCase())} aria-label="Symbol" spellCheck={false} />
          <button className="btn btn-small">Load</button>
          {QUICK.map((s) => (
            <button key={s} type="button" className={`btn btn-small ${s === symbol ? 'btn-primary' : ''}`} onClick={() => { setText(s); onSymbol(s) }}>{s}</button>
          ))}
        </form>
        {chain?.underlying && (
          <p className="chain-under">
            <span className="sym">{symbol}</span> <span className="num">{formatPrice(last)}</span>{' '}
            <span className={`num ${changeClass(chain.underlying.change_pct)}`}>{formatPct(chain.underlying.change_pct)}</span>
          </p>
        )}
      </div>
      {expirations && expirations.length > 0 && (
        <div className="exp-row" role="group" aria-label="Expiration">
          {expirations.map((d) => (
            <button key={d} type="button" className={`btn btn-small ${d === expiration ? 'btn-primary' : ''}`} onClick={() => setExpiration(d)}>
              {formatDay(d)}
            </button>
          ))}
        </div>
      )}
      {error && <p className="msg msg-warn">{error}</p>}
      {chain && (
        <>
          <div className="table-wrap chain-wrap">
            <table className="table chain-table">
              <thead>
                <tr>
                  <th colSpan={2} className="center">Calls</th>
                  <th className="center">Strike</th>
                  <th colSpan={2} className="center">Puts</th>
                </tr>
                <tr>
                  <th className="right">Bid</th><th className="right">Ask</th><th />
                  <th className="right">Bid</th><th className="right">Ask</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.strike}>
                    {cell('call', r.strike, r.call, 'bid')}
                    {cell('call', r.strike, r.call, 'ask')}
                    <td className="num center strike">{formatPrice(r.strike)}</td>
                    {cell('put', r.strike, r.put, 'bid')}
                    {cell('put', r.strike, r.put, 'ask')}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="actions">
            {chain.rows.length > AROUND * 2 + 1 && (
              <button className="btn btn-small" onClick={() => setAll((a) => !a)}>{all ? 'Strikes near the price' : `All ${chain.rows.length} strikes`}</button>
            )}
            <span className="muted">Shaded: in the money. Click a bid or ask to load the ticket.{!chain.realtime && ' Prices are delayed 15 minutes (sandbox key).'}</span>
          </div>
        </>
      )}
      {!chain && !error && <p className="muted">Loading…</p>}
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, ApiError, type PaperEventView, type PaperPositionView, type PaperSummary } from '../api'
import { useMe } from '../auth'
import { Segmented, StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { formatDateTime } from '../forms'
import { changeClass } from '../market/bars'
import { MarketState } from '../market/MarketHeader'
import { formatMoney } from '../money'
import '../capital/capital.css'
import OptionChain from './OptionChain'
import OrderTicket from './OrderTicket'
import { PositionsTable, RecentOrders, WorkingOrders } from './PaperTables'
import { heldPosition, type Pick } from './ticket'
import './live.css'

const POLL_MS = 5000

function Stat({ label, value, cls = '' }: { label: string; value: string; cls?: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value num ${cls}`}>{value}</span>
    </div>
  )
}

function Controls({ s, onChanged }: { s: PaperSummary; onChanged: (s: PaperSummary) => void }) {
  const action = useAction()
  const c = s.controls
  const post = (path: string, body?: unknown) => action.run(async () => onChanged(await api<PaperSummary>('POST', path, body)))
  async function stopAll() {
    if (!window.confirm('Stop all trading? Every working order is cancelled, automatic trading is turned off, and new orders are refused until you resume. Targets and stops on open positions stay active.')) return
    await post('/api/paper/stop-all')
  }
  return (
    <div className={`panel status-strip ${c.halted ? 'halted' : ''}`}>
      <div className="status-items">
        <span>
          Automatic trading:{' '}
          <b>{c.auto_trading === 'off' ? 'Off' : c.auto_paused ? 'Paused' : 'Paper only'}</b>
        </span>
        <span className="muted">Strategy, symbol and timeframe: none yet (automatic trading arrives in Stage 6)</span>
        {c.halted && <span className="badge badge-off">Trading stopped</span>}
      </div>
      <div className="actions">
        <button className="btn btn-small" disabled={action.busy} onClick={() => void post('/api/paper/auto-pause', { paused: !c.auto_paused })}>
          {c.auto_paused ? 'Un-pause automatic trading' : 'Pause automatic trading'}
        </button>
        {c.halted ? (
          <button className="btn btn-primary" disabled={action.busy} onClick={() => void post('/api/paper/resume')}>Resume trading</button>
        ) : (
          <button className="btn btn-stop" disabled={action.busy} onClick={() => void stopAll()}>Stop all trading</button>
        )}
      </div>
      <StatusLine status={action.status} />
    </div>
  )
}

function Activity() {
  const tz = useMe().settings.display.timezone
  const [rows, setRows] = useState<PaperEventView[] | null>(null)
  useEffect(() => {
    api<PaperEventView[]>('GET', '/api/paper/events').then(setRows).catch(() => setRows([]))
  }, [])
  if (!rows) return <p className="muted">Loading…</p>
  return (
    <ul className="activity">
      {rows.map((e) => (
        <li key={e.id}><span className="muted">{formatDateTime(e.at, tz)}</span> {e.detail}</li>
      ))}
    </ul>
  )
}

/** Live Trader: option chain, manual paper ticket, open positions, Stop all trading (plan section 6). */
export default function LiveTraderPage() {
  const me = useMe()
  const [s, setS] = useState<PaperSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [symbol, setSymbol] = useState(me.settings.watchlist.default_ticker)
  const [pick, setPick] = useState<Pick | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [showLog, setShowLog] = useState(false)

  const load = useCallback(() => {
    if (document.visibilityState !== 'visible') return
    api<PaperSummary>('GET', '/api/paper').then((x) => { setS(x); setError(null) })
      .catch((e) => setError(e instanceof ApiError ? e.message : 'Could not load the paper account.'))
  }, [])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, POLL_MS)
    document.addEventListener('visibilitychange', load)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', load)
    }
  }, [load])

  function sell(p: PaperPositionView) {
    setSymbol(p.symbol)
    setPick({ symbol: p.symbol, option_type: p.option_type, strike: p.strike, expiration: p.expiration, price: p.bid })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const a = s?.account
  return (
    <section className="page page-wide live">
      <div className="page-head">
        <h1 className="page-title">Live Trader</h1>
        <div className="actions">
          <Segmented label="Paper or real" value="paper" onChange={() => undefined}
            options={[{ value: 'paper', label: 'Paper' }]} />
          <button className="btn btn-small" disabled title="Real orders arrive in Stage 8, once a broker is chosen.">Real (Stage 8)</button>
          <MarketState />
        </div>
      </div>
      <div className="paper-banner" role="status">PAPER TRADING · no real money · fills use live quotes</div>

      {error && <p className="msg msg-error">{error}</p>}
      {s && !s.prices.available && (
        <p className="msg msg-warn">
          {s.prices.detail ?? 'Live prices are not available right now.'} {s.prices.detail?.includes('Config') && <Link to="/config/keys">Open Keys &amp; connections</Link>}
        </p>
      )}
      {s?.positions.filter((p) => p.expiring_soon).map((p) => (
        <p key={p.id} className="msg msg-warn">
          {p.label} expires {p.days_left === 0 ? 'today' : `in ${p.days_left} day${p.days_left === 1 ? '' : 's'}`}. If still open after 4:00 pm New York time on that day, it is settled at
          its in-the-money value (or nothing).
        </p>
      ))}

      {s && <Controls s={s} onChanged={setS} />}

      {a && (
        <div className="panel">
          <div className="totals">
            <Stat label="Paper account" value={formatMoney(a.total)} />
            <Stat label="Cash" value={formatMoney(a.cash)} />
            <Stat label="Free to trade" value={formatMoney(a.free_cash)} />
            <Stat label="Open profit or loss" value={formatMoney(a.open_pl, true)} cls={changeClass(a.open_pl)} />
            <Stat label="Today's closed result" value={formatMoney(a.realized_today, true)} cls={changeClass(a.realized_today)} />
          </div>
          <p className="muted">
            {s!.fill_rule === 'mid' ? 'Orders fill at the middle of bid and ask.' : 'Buys fill at the ask, sells at the bid.'} Change the fill rule, starting balance or
            reset the account in <Link to="/config/paper">Config → Paper account</Link>.
          </p>
        </div>
      )}

      <div className="trader-grid">
        <OptionChain key={symbol} symbol={symbol} onSymbol={(x) => { setSymbol(x); setPick(null) }} onPick={(p) => { setPick(p); setMessage(null) }} picked={pick} />
        <div className="trader-side">
          {pick ? (
            <OrderTicket key={`${pick.symbol}-${pick.option_type}-${pick.strike}-${pick.expiration}-${pick.price}`} pick={pick}
              held={heldPosition(s?.positions ?? [], pick)} halted={!!s?.controls.halted}
              onPlaced={(x, m) => { setS(x); setMessage(m) }} />
          ) : (
            <div className="panel empty"><p>Click a bid or ask in the option chain to fill in the order ticket.</p></div>
          )}
          {message && <p className="msg msg-ok" role="status">{message}</p>}
        </div>
      </div>

      {s && <WorkingOrders rows={s.orders} onChanged={setS} />}

      <div className="panel">
        <h2>Open positions</h2>
        {s ? <PositionsTable rows={s.positions} onChanged={setS} onSell={sell} /> : <p className="muted">Loading…</p>}
        <p className="muted">Targets and stops are watched every few seconds during market hours, also when this page is closed. Click a position to change them.</p>
      </div>

      <div className="panel">
        <div className="page-head">
          <h2>Recent orders</h2>
          <button className="btn btn-small" onClick={() => setShowLog((v) => !v)}>{showLog ? 'Hide full log' : 'Show full log'}</button>
        </div>
        {s && <RecentOrders rows={s.recent} />}
        {showLog && <Activity />}
      </div>
    </section>
  )
}

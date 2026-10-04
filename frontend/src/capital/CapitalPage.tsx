import { Fragment, useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, ApiError, type CapitalHistory, type CapitalSummary, type ClosedPosition, type OpenPosition, type PositionTrade } from '../api'
import { useAction } from '../config/hooks'
import { StatusLine } from '../config/common'
import { changeClass, formatPct } from '../market/bars'
import { formatDay, formatMoney, formatQty, formatQuoted } from '../money'
import AllocationBar from './AllocationBar'
import CapitalChart from './CapitalChart'
import FlowsPanel from './FlowsPanel'
import { AddPosition, TradeForm } from './PositionForms'
import './capital.css'

const SOURCE_NOTE: Record<OpenPosition['price_source'], string> = {
  mid: 'middle of bid and ask',
  last: 'last trade',
  intrinsic: 'expired: value if settled at today’s stock price',
  none: 'no price: counted at cost',
}

function Stat({ label, value, cls = '', note }: { label: string; value: string; cls?: string; note?: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value num ${cls}`}>{value}</span>
      {note && <span className="muted stat-note">{note}</span>}
    </div>
  )
}

function Trades({ p, onSaved, editable }: { p: { id: number; kind: 'option' | 'stock'; trades: PositionTrade[] }; onSaved: (s: CapitalSummary) => void; editable: boolean }) {
  const [editing, setEditing] = useState<PositionTrade | null>(null)
  const action = useAction()
  async function remove(t: PositionTrade) {
    const last = p.trades.length === 1
    if (!window.confirm(`Delete this ${t.side} of ${formatQty(t.quantity)} on ${formatDay(t.day)}?${last ? ' It is the only one, so the position goes too.' : ''}`)) return
    await action.run(async () => onSaved(await api<CapitalSummary>('DELETE', `/api/capital/trades/${t.id}`)))
  }
  return (
    <>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Date</th><th>Buy or sell</th><th className="right">Quantity</th><th className="right">Price</th><th className="right">Fees</th><th className="right">Amount</th><th>Note</th>{editable && <th />}</tr>
          </thead>
          <tbody>
            {p.trades.map((t) => (
              <tr key={t.id}>
                <td>{formatDay(t.day)}</td>
                <td>{t.side === 'buy' ? 'Buy' : 'Sell'}</td>
                <td className="num right">{formatQty(t.quantity)}</td>
                <td className="num right">{formatQuoted(t.price)}</td>
                <td className="num right">{formatMoney(t.fees)}</td>
                <td className="num right">{t.side === 'buy' ? '−' : '+'}{formatMoney(t.amount)}</td>
                <td className="muted">{t.note}</td>
                {editable && (
                  <td className="right">
                    <div className="actions">
                      <button className="btn btn-small" onClick={() => setEditing(t)}>Edit</button>
                      <button className="btn btn-small btn-danger" onClick={() => void remove(t)}>Delete</button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <StatusLine status={action.status} />
      {editing && (
        <TradeForm key={editing.id} position={p} editing={editing} onCancel={() => setEditing(null)}
          onSaved={(s) => { setEditing(null); onSaved(s) }} />
      )}
    </>
  )
}

function OpenDetail({ p, onSaved }: { p: OpenPosition; onSaved: (s: CapitalSummary) => void }) {
  const [trading, setTrading] = useState(false)
  const action = useAction()
  async function removePosition() {
    if (!window.confirm(`Delete ${p.label} and all ${p.trades.length} of its buys and sells? Use this only for a position entered by mistake. To record selling it, use “Sell or buy more”.`)) return
    await action.run(async () => onSaved(await api<CapitalSummary>('DELETE', `/api/capital/positions/${p.id}`)))
  }
  return (
    <div className="detail">
      {p.expired && (
        <p className="msg msg-warn">
          This option expired on {formatDay(p.expiration)}. Record how it ended with a sale (price 0 if it expired worthless, or what you got for it).
        </p>
      )}
      <p className="muted">
        Price source: {SOURCE_NOTE[p.price_source]}.
        {p.underlying_last != null && p.kind === 'option' && <> {p.symbol} is at <span className="num">{formatQuoted(p.underlying_last)}</span>.</>}
        {p.realized !== 0 && <> Gain already taken from sales: <span className={`num ${changeClass(p.realized)}`}>{formatMoney(p.realized, true)}</span>.</>}
      </p>
      <Trades p={p} onSaved={onSaved} editable />
      {trading ? (
        // An expired option's sale form starts at what it was worth at expiration.
        <TradeForm position={{ ...p, price: p.expired ? (p.price ?? 0) : p.price }} onCancel={() => setTrading(false)}
          onSaved={(s) => { setTrading(false); onSaved(s) }} />
      ) : (
        <div className="actions">
          <button className="btn btn-small btn-primary" onClick={() => setTrading(true)}>
            {p.expired ? 'Record how it ended' : 'Sell or buy more'}
          </button>
          <button className="btn btn-small btn-danger" onClick={() => void removePosition()}>Delete position</button>
        </div>
      )}
      <StatusLine status={action.status} />
    </div>
  )
}

function OpenTable({ rows, onSaved }: { rows: OpenPosition[]; onSaved: (s: CapitalSummary) => void }) {
  const [open, setOpen] = useState<number | null>(null)
  if (!rows.length) return <p className="muted">No open positions. Use “Add position” to enter one.</p>
  return (
    <div className="table-wrap">
      <table className="table positions">
        <thead>
          <tr>
            <th>Position</th><th>Type</th><th className="right">Quantity</th><th className="right">Average cost</th>
            <th className="right">Current price</th><th className="right">Value</th><th className="right">Gain $</th>
            <th className="right">Gain %</th><th>Expiration</th><th className="right">% of capital</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <Fragment key={p.id}>
              <tr className="clickable" onClick={() => setOpen(open === p.id ? null : p.id)} aria-expanded={open === p.id}>
                <td>
                  <span className="disclosure" aria-hidden="true">{open === p.id ? '▾' : '▸'}</span> <span className="sym">{p.label}</span>
                  {p.note && <span className="muted"> · {p.note}</span>}
                </td>
                <td>{p.kind === 'option' ? 'Option' : 'Stock'}</td>
                <td className="num right">{formatQty(p.quantity)}</td>
                <td className="num right">{formatQuoted(p.average_price)}</td>
                <td className="num right">{p.price == null ? <span className="muted">no price</span> : formatQuoted(p.price)}{p.price_source === 'intrinsic' && <span className="muted"> *</span>}</td>
                <td className="num right">{formatMoney(p.value)}</td>
                <td className={`num right ${changeClass(p.gain)}`}>{formatMoney(p.gain, true)}</td>
                <td className={`num right ${changeClass(p.gain_pct)}`}>{formatPct(p.gain_pct)}</td>
                <td>
                  {p.expiration ? formatDay(p.expiration) : '—'}
                  {p.expired && <span className="badge badge-off"> Expired</span>}
                  {p.expiring_soon && <span className="badge badge-warn"> {p.days_left === 0 ? 'Today' : `${p.days_left}d left`}</span>}
                </td>
                <td className="num right">{formatPct(p.pct_of_capital).replace('+', '')}</td>
              </tr>
              {open === p.id && (
                <tr className="detail-row">
                  <td colSpan={10}><OpenDetail p={p} onSaved={onSaved} /></td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ClosedTable({ rows, onSaved }: { rows: ClosedPosition[]; onSaved: (s: CapitalSummary) => void }) {
  const [open, setOpen] = useState<number | null>(null)
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr><th>Position</th><th>Opened</th><th>Closed</th><th className="right">Paid</th><th className="right">Received</th><th className="right">Gain $</th></tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <Fragment key={p.id}>
              <tr className="clickable" onClick={() => setOpen(open === p.id ? null : p.id)} aria-expanded={open === p.id}>
                <td><span className="disclosure" aria-hidden="true">{open === p.id ? '▾' : '▸'}</span> <span className="sym">{p.label}</span></td>
                <td>{formatDay(p.opened)}</td>
                <td>{formatDay(p.closed)}</td>
                <td className="num right">{formatMoney(p.invested)}</td>
                <td className="num right">{formatMoney(p.returned)}</td>
                <td className={`num right ${changeClass(p.realized)}`}>{formatMoney(p.realized, true)}</td>
              </tr>
              {open === p.id && (
                <tr className="detail-row"><td colSpan={6}><div className="detail"><Trades p={p} onSaved={onSaved} editable /></div></td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Capital Tracking: long-term option and stock positions (plan section 6). */
export default function CapitalPage() {
  const [summary, setSummary] = useState<CapitalSummary | null>(null)
  const [history, setHistory] = useState<CapitalHistory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const load = useCallback(() => {
    api<CapitalSummary>('GET', '/api/capital').then((s) => { setSummary(s); setError(null) })
      .catch((e) => setError(e instanceof ApiError ? e.message : 'Could not load your positions.'))
    api<CapitalHistory>('GET', '/api/capital/history').then(setHistory).catch(() => undefined)
  }, [])

  useEffect(() => {
    load()
    // Prices move; refresh every minute while the page is open.
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') api<CapitalSummary>('GET', '/api/capital').then(setSummary).catch(() => undefined)
    }, 60_000)
    return () => window.clearInterval(timer)
  }, [load])

  const saved = (s: CapitalSummary) => {
    setSummary(s)
    api<CapitalHistory>('GET', '/api/capital/history').then(setHistory).catch(() => undefined)
  }

  if (error) return <section className="page"><h1 className="page-title">Capital Tracking</h1><p className="msg msg-error">{error}</p></section>
  if (!summary) return <section className="page"><h1 className="page-title">Capital Tracking</h1><p className="muted">Loading…</p></section>
  const t = summary.totals
  const warnings = summary.open.filter((p) => p.expired || p.expiring_soon)

  return (
    <section className="page page-wide capital">
      <div className="page-head">
        <h1 className="page-title">Capital Tracking</h1>
        {!adding && <button className="btn btn-primary" onClick={() => setAdding(true)}>Add position</button>}
      </div>

      {!summary.prices.available && (
        <p className="msg msg-warn">
          {summary.prices.detail ?? 'Prices are not available right now.'} Positions without a price are counted at what they cost.{' '}
          {summary.prices.detail?.includes('Config') && <Link to="/config/keys">Open Keys &amp; connections</Link>}
        </p>
      )}
      {warnings.map((p) => (
        <p key={p.id} className="msg msg-warn">
          {p.expired ? `${p.label} has expired. Open it in the table below and record how it ended.` : `${p.label} expires ${p.days_left === 0 ? 'today' : `in ${p.days_left} day${p.days_left === 1 ? '' : 's'}`}.`}
        </p>
      ))}

      {adding && <AddPosition onCancel={() => setAdding(false)} onSaved={(s) => { setAdding(false); saved(s) }} />}

      <div className="panel">
        <div className="totals">
          <Stat label="Total capital" value={formatMoney(t.total)} note={t.estimated ? 'Some positions counted at cost' : 'Cash plus positions'} />
          <Stat label="Money put in" value={formatMoney(t.put_in)} note="Deposits minus withdrawals" />
          <Stat label="Total gain" value={formatMoney(t.gain, true)} cls={changeClass(t.gain)} note={t.gain_pct == null ? undefined : `${formatPct(t.gain_pct)} on money put in`} />
          <Stat label="Cash" value={formatMoney(t.cash)} cls={t.cash < 0 ? 'loss' : ''} note={t.cash < 0 ? 'Below zero: record your deposits' : undefined} />
        </div>
        <p className="muted">
          Gains already taken from sales: <span className={`num ${changeClass(t.realized)}`}>{formatMoney(t.realized, true)}</span> · Gains on open positions:{' '}
          <span className={`num ${changeClass(t.unrealized)}`}>{formatMoney(t.unrealized, true)}</span>
        </p>
      </div>

      <div className="capital-grid">
        <div className="panel">
          <h2>Capital over time</h2>
          {history ? <CapitalChart history={history} liveTotal={t.total} /> : <p className="muted">Loading…</p>}
        </div>
        <div className="panel">
          <h2>Allocation</h2>
          <AllocationBar positions={summary.open} cash={t.cash} />
        </div>
      </div>

      <div className="panel">
        <h2>Open positions</h2>
        <OpenTable rows={summary.open} onSaved={saved} />
        <p className="muted">Click a position to see its buys and sells, sell it, or buy more. Option values use the middle of the bid and ask; one contract is 100 shares.</p>
      </div>

      {summary.closed.length > 0 && (
        <div className="panel">
          <h2>Closed positions</h2>
          <ClosedTable rows={summary.closed} onSaved={saved} />
        </div>
      )}

      <FlowsPanel account="long_term" flows={summary.flows} onChanged={load} />
    </section>
  )
}

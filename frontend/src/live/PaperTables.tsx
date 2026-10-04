import { Fragment, useState } from 'react'
import { api, type PaperOrderView, type PaperPositionView, type PaperSummary } from '../api'
import { useMe } from '../auth'
import { Field, StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { formatDateTime, parseNumber } from '../forms'
import { changeClass, formatPct, formatPrice } from '../market/bars'
import { formatMoney } from '../money'

const REASON: Record<string, string> = { take_profit: 'take profit', stop_loss: 'stop loss', manual: 'manual', expired: 'expired', signal: 'signal', time: 'time' }

function placedBy(source: string): string {
  return source === 'manual' ? 'Manual' : source
}

function ExitsEditor({ p, onSaved }: { p: PaperPositionView; onSaved: (s: PaperSummary) => void }) {
  const [tp, setTp] = useState(p.take_profit_price != null ? String(p.take_profit_price) : '')
  const [sl, setSl] = useState(p.stop_loss_price != null ? String(p.stop_loss_price) : '')
  const action = useAction()
  const tz = useMe().settings.display.timezone
  function save() {
    const t = tp.trim() ? parseNumber(tp) : null
    const s = sl.trim() ? parseNumber(sl) : null
    if ((tp.trim() && (t == null || t <= 0)) || (sl.trim() && (s == null || s < 0))) {
      return action.setStatus({ kind: 'error', text: 'Enter prices, or leave a box empty for none.' })
    }
    void action.run(async () => onSaved(await api<PaperSummary>('PATCH', `/api/paper/positions/${p.id}`, { take_profit_price: t, stop_loss_price: s })), 'Target and stop saved.')
  }
  return (
    <div className="detail">
      <p className="muted">
        Opened {formatDateTime(p.opened_at, tz)} · bid <span className="num">{formatPrice(p.bid)}</span> · ask{' '}
        <span className="num">{formatPrice(p.ask)}</span> · cost <span className="num">{formatMoney(p.cost)}</span>
      </p>
      <div className="grid-2">
        <Field label="Target price" hint="Closes when the option can be sold at this or more. Empty for none.">
          <input className="input num" inputMode="decimal" value={tp} onChange={(e) => setTp(e.target.value)} />
        </Field>
        <Field label="Stop price" hint="Closes when the sell price falls to this or less. Empty for none.">
          <input className="input num" inputMode="decimal" value={sl} onChange={(e) => setSl(e.target.value)} />
        </Field>
      </div>
      <div className="actions">
        <button className="btn btn-small btn-primary" disabled={action.busy} onClick={save}>Save target and stop</button>
      </div>
      <StatusLine status={action.status} />
    </div>
  )
}

/** Open paper positions with live value, target, stop and a Close button. */
export function PositionsTable({ rows, onChanged, onSell }: { rows: PaperPositionView[]; onChanged: (s: PaperSummary) => void; onSell: (p: PaperPositionView) => void }) {
  const [open, setOpen] = useState<number | null>(null)
  const action = useAction()
  async function close(p: PaperPositionView) {
    if (!window.confirm(`Close ${p.quantity} ${p.label} at the market (about ${formatPrice(p.price)})?`)) return
    await action.run(async () => onChanged(await api<PaperSummary>('POST', `/api/paper/positions/${p.id}/close`, {})))
  }
  if (!rows.length) return <p className="muted">No open paper positions. Pick a price in the option chain to start.</p>
  return (
    <>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Contract</th><th className="right">Qty</th><th className="right">Entry</th><th className="right">Current</th>
              <th className="right">P/L $</th><th className="right">P/L %</th><th className="right">Target</th><th className="right">Stop</th>
              <th>Placed by</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <Fragment key={p.id}>
                <tr className="clickable" onClick={() => setOpen(open === p.id ? null : p.id)} aria-expanded={open === p.id}>
                  <td>
                    <span className="disclosure" aria-hidden="true">{open === p.id ? '▾' : '▸'}</span> <span className="sym">{p.label}</span>
                    {p.expiring_soon && <span className="badge badge-warn">{p.days_left === 0 ? 'Expires today' : `${p.days_left}d left`}</span>}
                  </td>
                  <td className="num right">{p.quantity}</td>
                  <td className="num right">{formatPrice(p.entry_price)}</td>
                  <td className="num right">{formatPrice(p.price)}</td>
                  <td className={`num right ${changeClass(p.pl)}`}>{formatMoney(p.pl, true)}</td>
                  <td className={`num right ${changeClass(p.pl_pct)}`}>{formatPct(p.pl_pct)}</td>
                  <td className="num right">{formatPrice(p.take_profit_price)}</td>
                  <td className="num right">{formatPrice(p.stop_loss_price)}</td>
                  <td>{placedBy(p.source)}</td>
                  <td className="right" onClick={(e) => e.stopPropagation()}>
                    <div className="actions">
                      <button className="btn btn-small" onClick={() => onSell(p)}>Sell…</button>
                      <button className="btn btn-small btn-danger" disabled={action.busy} onClick={() => void close(p)}>Close</button>
                    </div>
                  </td>
                </tr>
                {open === p.id && (
                  <tr className="detail-row"><td colSpan={10}><ExitsEditor p={p} onSaved={onChanged} /></td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <StatusLine status={action.status} />
    </>
  )
}

/** Orders waiting to fill, with Cancel. */
export function WorkingOrders({ rows, onChanged }: { rows: PaperOrderView[]; onChanged: (s: PaperSummary) => void }) {
  const action = useAction()
  if (!rows.length) return null
  return (
    <div className="panel">
      <h2>Working orders</h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Order</th><th className="right">Qty</th><th className="right">Limit</th><th className="right">Bid</th><th className="right">Ask</th><th>Placed by</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.id}>
                <td>{o.side === 'buy' ? 'Buy' : 'Sell'} <span className="sym">{o.label}</span>{o.intent === 'close' && o.close_reason && <span className="muted"> · {REASON[o.close_reason] ?? o.close_reason}</span>}</td>
                <td className="num right">{o.quantity}</td>
                <td className="num right">{o.limit_price == null ? 'market' : formatPrice(o.limit_price)}</td>
                <td className="num right">{formatPrice(o.bid)}</td>
                <td className="num right">{formatPrice(o.ask)}</td>
                <td>{placedBy(o.source)}</td>
                <td className="right">
                  <button className="btn btn-small btn-danger" disabled={action.busy}
                    onClick={() => void action.run(async () => onChanged(await api<PaperSummary>('DELETE', `/api/paper/orders/${o.id}`)))}>
                    Cancel
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <StatusLine status={action.status} />
    </div>
  )
}

/** Finished orders: filled, cancelled, refused. */
export function RecentOrders({ rows }: { rows: PaperOrderView[] }) {
  const tz = useMe().settings.display.timezone
  if (!rows.length) return <p className="muted">No finished orders yet.</p>
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr><th>When</th><th>Order</th><th className="right">Qty</th><th>Result</th><th className="right">Price</th><th>Placed by</th></tr>
        </thead>
        <tbody>
          {rows.map((o) => (
            <tr key={o.id}>
              <td>{formatDateTime(o.done_at, tz)}</td>
              <td>{o.side === 'buy' ? 'Buy' : 'Sell'} <span className="sym">{o.label}</span>{o.close_reason && <span className="muted"> · {REASON[o.close_reason] ?? o.close_reason}</span>}</td>
              <td className="num right">{o.quantity}</td>
              <td>{o.status === 'filled' ? 'Filled' : o.status === 'cancelled' ? 'Cancelled' : 'Refused'}{o.status_detail && <span className="muted"> · {o.status_detail}</span>}</td>
              <td className="num right">{formatPrice(o.fill_price)}</td>
              <td>{placedBy(o.source)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

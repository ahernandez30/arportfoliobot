import { useState, type FormEvent } from 'react'
import { api, type OrderReview, type PaperPositionView, type PaperSummary } from '../api'
import { useMe } from '../auth'
import { Field, Segmented, StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { parseNumber } from '../forms'
import { formatPrice } from '../market/bars'
import { formatDay, formatMoney } from '../money'
import { exitPrices, orderValue, type Pick } from './ticket'

type Draft = { quantity: string; limit: string; tp: string; sl: string }

/** Manual paper order ticket. Every order passes a review step before it is placed. */
export default function OrderTicket({ pick, held, halted, onPlaced }: {
  pick: Pick
  held: PaperPositionView | null
  halted: boolean
  onPlaced: (s: PaperSummary, message: string) => void
}) {
  const me = useMe()
  const t = me.settings.trading
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [d, setD] = useState<Draft>({
    quantity: String(t.contracts_per_trade),
    limit: pick.price != null ? pick.price.toFixed(2) : '',
    tp: String(t.take_profit_pct),
    sl: String(t.stop_loss_pct),
  })
  // The buy review comes from the server; a sell (closing) needs no server checks.
  const [review, setReview] = useState<OrderReview | 'sell' | null>(null)
  const action = useAction()
  const set = (c: Partial<Draft>) => {
    setD({ ...d, ...c })
    setReview(null)
  }

  const label = `${pick.symbol} ${pick.strike} ${pick.option_type}, ${formatDay(pick.expiration)}`
  const qty = parseNumber(d.quantity)
  const limit = parseNumber(d.limit)
  const tp = d.tp.trim() ? parseNumber(d.tp) : null
  const sl = d.sl.trim() ? parseNumber(d.sl) : null
  const exits = limit != null ? exitPrices(limit, tp, sl) : { tp: null, sl: null }

  function body() {
    return {
      symbol: pick.symbol, option_type: pick.option_type, strike: pick.strike, expiration: pick.expiration,
      quantity: qty, limit_price: limit, take_profit_pct: tp, stop_loss_pct: sl,
    }
  }

  function problem(): string | null {
    if (qty == null || !Number.isInteger(qty) || qty < 1) return 'Quantity: enter a whole number of contracts.'
    if (limit == null || limit <= 0) return 'Limit price: enter a price above 0.'
    if (side === 'buy') {
      if (d.tp.trim() && (tp == null || tp <= 0)) return 'Take profit %: enter a number above 0, or leave it empty.'
      if (d.sl.trim() && (sl == null || sl <= 0 || sl > 100)) return 'Stop loss %: enter a number from 0 to 100, or leave it empty.'
    } else if (held && qty > held.quantity) {
      return `You hold ${held.quantity} contract${held.quantity === 1 ? '' : 's'}.`
    }
    return null
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    const p = problem()
    if (p) return action.setStatus({ kind: 'error', text: p })
    if (side === 'sell') {
      setReview('sell')
      return
    }
    void action.run(async () => setReview(await api<OrderReview>('POST', '/api/paper/orders/review', body())))
  }

  function place() {
    void action.run(async () => {
      if (side === 'sell' && held) {
        const s = await api<PaperSummary>('POST', `/api/paper/positions/${held.id}/close`, { quantity: qty, limit_price: limit })
        onPlaced(s, `Sell order for ${qty} ${label} sent.`)
      } else {
        const s = await api<PaperSummary>('POST', '/api/paper/orders', body())
        const o = s.order
        onPlaced(s, o?.status === 'filled' ? `Bought ${o.quantity} ${o.label} at ${formatPrice(o.fill_price)}.` : `Order placed. It is working and fills when the ask reaches ${formatPrice(limit)}.`)
      }
      setReview(null)
    })
  }

  return (
    <form className="panel form ticket" onSubmit={submit}>
      <div className="ticket-head">
        <h2>Order ticket</h2>
        <span className="badge badge-paper">PAPER</span>
      </div>
      <p className="ticket-contract"><span className="sym">{label}</span></p>
      <Segmented label="Buy or sell" value={side} onChange={(s) => { setSide(s); setReview(null) }}
        options={held ? [{ value: 'buy', label: 'Buy' }, { value: 'sell', label: `Sell (hold ${held.quantity})` }] : [{ value: 'buy', label: 'Buy' }]} />
      <div className="grid-2">
        <Field label="Contracts">
          <input className="input num" inputMode="numeric" value={d.quantity} onChange={(e) => set({ quantity: e.target.value })} />
        </Field>
        <Field label="Limit price" hint={side === 'buy' ? 'Most you will pay, per share.' : 'Least you will accept, per share.'}>
          <input className="input num" inputMode="decimal" value={d.limit} onChange={(e) => set({ limit: e.target.value })} />
        </Field>
        {side === 'buy' && (
          <>
            <Field label="Take profit %" hint="Empty for none.">
              <input className="input num" inputMode="decimal" value={d.tp} onChange={(e) => set({ tp: e.target.value })} />
            </Field>
            <Field label="Stop loss %" hint="Empty for none.">
              <input className="input num" inputMode="decimal" value={d.sl} onChange={(e) => set({ sl: e.target.value })} />
            </Field>
          </>
        )}
      </div>
      {qty != null && limit != null && (
        <dl className="ticket-sums">
          <dt>{side === 'buy' ? 'Cost (at most)' : 'Proceeds (at least)'}</dt><dd className="num">{formatMoney(orderValue(qty, limit))}</dd>
          {side === 'buy' && <><dt>Takes profit at</dt><dd className="num">{exits.tp != null ? `${formatPrice(exits.tp)} (${formatMoney(orderValue(qty, exits.tp - limit), true)})` : 'none'}</dd></>}
          {side === 'buy' && <><dt>Stops out at</dt><dd className="num">{exits.sl != null ? `${formatPrice(exits.sl)} (${formatMoney(orderValue(qty, exits.sl - limit), true)})` : 'none'}</dd></>}
        </dl>
      )}
      {side === 'buy' && <p className="muted">Target and stop are measured from the actual fill price, on the price the option could be sold at.</p>}

      {!review && (
        <div className="actions">
          <button className="btn btn-primary" disabled={action.busy || halted}>Review order</button>
          {halted && <span className="muted">Trading is stopped.</span>}
        </div>
      )}

      {review && (
        <div className="review" role="region" aria-label="Review order">
          <h3>Review</h3>
          <p>
            <b>{side === 'buy' ? 'Buy' : 'Sell'} {qty} {label}</b> with a limit of <span className="num">{formatPrice(limit)}</span>.
          </p>
          {review === 'sell' ? (
            <p className="muted">
              Bid <span className="num">{formatPrice(held?.bid)}</span> · Ask <span className="num">{formatPrice(held?.ask)}</span>. Sells fill when the bid reaches your
              limit, during market hours.
            </p>
          ) : (
            <>
              <p className="muted">
                Bid <span className="num">{formatPrice(review.bid)}</span> · Ask <span className="num">{formatPrice(review.ask)}</span> ·{' '}
                {review.fill_rule === 'mid' ? 'Fills at the middle price' : 'Buys fill at the ask'}
              </p>
              <p>
                {review.fill_now
                  ? <>Fills now at about <b className="num">{formatPrice(review.fill_price)}</b> ({formatMoney(orderValue(qty!, review.fill_price!))}).</>
                  : review.market_open
                    ? <>The ask is above your limit, so the order waits until it comes down to <span className="num">{formatPrice(limit)}</span>.</>
                    : <>The market is closed. The order waits and can fill after the open.</>}
                {review.take_profit_price != null && <> Target <span className="num">{formatPrice(review.take_profit_price)}</span>.</>}
                {review.stop_loss_price != null && <> Stop <span className="num">{formatPrice(review.stop_loss_price)}</span>.</>}
              </p>
              {review.problem && <p className="msg msg-error">{review.problem}</p>}
            </>
          )}
          <div className="actions">
            <button type="button" className="btn btn-primary" disabled={action.busy || (review !== 'sell' && !!review.problem)} onClick={place}>
              Place paper order
            </button>
            <button type="button" className="btn" onClick={() => setReview(null)}>Back</button>
          </div>
        </div>
      )}
      <StatusLine status={action.status} />
    </form>
  )
}

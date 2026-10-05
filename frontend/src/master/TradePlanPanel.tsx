import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, ApiError, type AutoPlan, type AutoPreview, type TradeSettings } from '../api'
import { Field, Segmented, StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { parseNumber } from '../forms'
import { formatPrice } from '../market/bars'
import { formatMoney } from '../money'
import type { Inputs } from './types'

const STRUCTURES: { value: TradeSettings['structure']; label: string }[] = [
  { value: 'directional', label: 'Directional' },
  { value: 'credit_spread', label: 'Credit spread' },
  { value: 'debit_spread', label: 'Debit spread' },
  { value: 'compare', label: 'Compare all' },
]

const EXPLAIN: Record<TradeSettings['structure'], string> = {
  directional: 'BUY buys a call, SELL buys a put.',
  credit_spread: 'BUY sells a put and buys the next lower one (bull put spread); SELL sells a call and buys the next higher one (bear call spread).',
  debit_spread: 'Same strikes and payoff as the credit spread, bought for a debit (no early-assignment risk on real accounts).',
  compare: 'All three on the same signals, each in its own paper sub-account, so results can be compared side by side.',
}

type Props = { strategy: string; symbol: string; timeframe: string; inputs: Inputs; pegged: boolean }

/** “What to trade on a signal” (plan 7.6): structure, strike and expiration rules, size, and the switch
 * that places paper trades from this symbol and timeframe's signals. Saved with the pegged settings. */
export default function TradePlanPanel({ strategy, symbol, timeframe, inputs, pegged }: Props) {
  const [plan, setPlan] = useState<AutoPlan | null>(null)
  const [t, setT] = useState<TradeSettings | null>(null)
  const [auto, setAuto] = useState(false)
  const [text, setText] = useState({ distance: '', margin: '', days: '', risk: '' })
  const [preview, setPreview] = useState<AutoPreview | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const save = useAction()

  // The page mounts a fresh panel per symbol and timeframe, so an old preview never shows.
  useEffect(() => {
    const q = new URLSearchParams({ strategy, symbol, timeframe })
    api<AutoPlan>('GET', `/api/auto/plan?${q}`).then((p) => {
      setPlan(p)
      setT(p.trade)
      setAuto(p.auto)
      setText({ distance: String(p.trade.distance_pct), margin: String(p.trade.margin_pct), days: String(p.trade.expiry_days), risk: String(p.trade.risk_usd) })
    }).catch(() => setPlan(null))
  }, [strategy, symbol, timeframe, pegged])

  if (!plan || !t) return <div className="panel"><h2>What to trade on a signal</h2><p className="muted">Loading…</p></div>

  // The typed numbers, checked; null when one is not a number.
  function settings(): TradeSettings | null {
    const n = { distance: parseNumber(text.distance), margin: parseNumber(text.margin), days: parseNumber(text.days), risk: parseNumber(text.risk) }
    if (n.distance == null || n.margin == null || n.days == null || n.risk == null) return null
    return { ...t!, distance_pct: n.distance, margin_pct: n.margin, expiry_days: Math.round(n.days), risk_usd: n.risk }
  }

  async function submit() {
    const s = settings()
    if (!s) return save.setStatus({ kind: 'error', text: 'Enter numbers in every box.' })
    await save.run(async () => {
      const p = await api<AutoPlan>('PUT', '/api/auto/plan', { strategy, symbol, timeframe, trade: s, auto })
      setPlan(p)
      setT(p.trade)
      setAuto(p.auto)
    }, auto ? `Saved. Paper trades will be placed from ${symbol} ${timeframe} signals.` : 'Saved.')
  }

  async function runPreview() {
    const s = settings()
    if (!s) return setPreviewError('Enter numbers in every box.')
    setPreviewing(true)
    try {
      setPreview(await api<AutoPreview>('POST', '/api/auto/preview', { strategy, symbol, timeframe, inputs, trade: s }))
      setPreviewError(null)
    } catch (e) {
      setPreviewError(e instanceof ApiError ? e.message : 'Could not work out the trade.')
    } finally {
      setPreviewing(false)
    }
  }

  const set = (k: keyof typeof text) => (e: { target: { value: string } }) => setText({ ...text, [k]: e.target.value })
  const savedAuto = plan.auto

  return (
    <div className="panel trade-plan">
      <h2>What to trade on a signal</h2>
      <p className="muted">For {symbol} {timeframe}. The option position closes when the strategy exits on the stock chart, never on the option’s own price.</p>

      <div className="field">
        <span>Structure</span>
        <Segmented label="Structure" value={t.structure} onChange={(v) => setT({ ...t, structure: v })} options={STRUCTURES} />
        <span className="hint">{EXPLAIN[t.structure]}</span>
      </div>

      <div className="field">
        <span>Strike distance from the price</span>
        <Segmented label="Strike distance" value={t.distance_mode} onChange={(v) => setT({ ...t, distance_mode: v })}
          options={[{ value: 'auto', label: 'Average winning move' }, { value: 'fixed', label: 'Fixed %' }]} />
        <span className="hint">
          {t.distance_mode === 'auto'
            ? 'How far the stock moved, on average, in this strategy’s winning trades on this symbol and timeframe. The % below is used only if there are no winners yet.'
            : 'Always this percent from the price.'}
        </span>
      </div>
      <div className="grid-2">
        <Field label={t.distance_mode === 'auto' ? 'Fallback %' : 'Distance %'}>
          <input className="input num" inputMode="decimal" value={text.distance} onChange={set('distance')} />
        </Field>
        <div className="field">
          <span>Direction</span>
          <Segmented label="Direction of the distance" value={t.side} onChange={(v) => setT({ ...t, side: v })}
            options={[{ value: 'toward', label: 'Toward the signal' }, { value: 'away', label: 'Away' }]} />
        </div>
      </div>
      <p className="hint">
        {t.side === 'toward'
          ? 'Toward: beyond the price in the signal’s direction (above it for a BUY). Spreads pay better than 1:1 but need the stock to move for a full win.'
          : 'Away: on the safe side. Spreads pay under 1:1 but win if the stock merely holds.'}
      </p>

      <div className="field">
        <span>Expiration</span>
        <Segmented label="Expiration rule" value={t.expiry_mode} onChange={(v) => setT({ ...t, expiry_mode: v })}
          options={[{ value: 'auto', label: 'Average trade length + margin' }, { value: 'fixed', label: 'Fixed days' }]} />
      </div>
      <div className="grid-2">
        {t.expiry_mode === 'auto' ? (
          <Field label="Safety margin %" hint="Average length plus this, or the average losing trade’s length if longer.">
            <input className="input num" inputMode="decimal" value={text.margin} onChange={set('margin')} />
          </Field>
        ) : null}
        <Field label={t.expiry_mode === 'auto' ? 'Fallback days' : 'At least this many days away'}
          hint={t.expiry_mode === 'auto' ? 'Used only if there are no finished trades yet.' : undefined}>
          <input className="input num" inputMode="numeric" value={text.days} onChange={set('days')} />
        </Field>
      </div>

      <Field label="Risk per trade ($)" hint="The most each trade can lose. Contracts or spreads are bought up to this. Config limits still apply.">
        <input className="input num" inputMode="decimal" value={text.risk} onChange={set('risk')} />
      </Field>

      <label className="check auto-switch">
        <input type="checkbox" checked={auto} disabled={!plan.pegged} onChange={(e) => setAuto(e.target.checked)} />
        <span>Place paper trades from {symbol} {timeframe} signals</span>
      </label>
      {!plan.pegged && <p className="msg msg-warn">Peg settings to {symbol} {timeframe} first: automatic trades always use the pegged settings, not what is on screen.</p>}
      {plan.pegged && auto && plan.auto_trading === 'off' && (
        <p className="msg msg-warn">Automatic trading is off in <Link to="/config/trading">Config → Trading limits</Link>. Set it to “paper” as well, or nothing is placed.</p>
      )}
      {savedAuto && <p className="msg msg-ok">On: new signals on {symbol} {timeframe} candles that close from now on place paper trades{t.structure === 'compare' ? ' in three sub-accounts' : ''}.</p>}

      <div className="actions">
        <button className="btn btn-primary" disabled={save.busy || !plan.pegged} onClick={() => void submit()}>Save</button>
        <button className="btn btn-small" disabled={previewing} onClick={() => void runPreview()}>{previewing ? 'Working it out…' : 'What would a signal trade now?'}</button>
      </div>
      <StatusLine status={save.status} />
      {previewError && <p className="msg msg-error">{previewError}</p>}
      {preview && (
        <div className="plan-preview">
          <p className="muted">
            {symbol} at <span className="num">{formatPrice(preview.stock_price)}</span>. Strike distance {preview.rules.distance_note}. Expiration {preview.rules.hold_note}.
            {' '}{preview.fill_rule === 'mid' ? 'Priced at mid.' : 'Priced at bid/ask.'} From the settings on screen.
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Signal</th><th>Trade</th><th className="right">Qty</th><th className="right">Price</th><th className="right">Can lose</th><th className="right">Can make</th><th className="right">Payout</th></tr></thead>
              <tbody>
                {preview.rows.map((r) => (
                  <tr key={r.structure + r.signal}>
                    <td className={r.signal === 'BUY' ? 'legend-buy' : 'legend-sell'}>{r.signal}</td>
                    {r.problem ? (
                      <td colSpan={6} className="muted">{r.label}: {r.problem}</td>
                    ) : (
                      <>
                        <td><span className="sym">{r.description}</span></td>
                        <td className="num right">{r.quantity}</td>
                        <td className="num right">{formatPrice(r.price)}</td>
                        <td className="num right">{formatMoney(r.max_loss ?? null)}</td>
                        <td className="num right">{r.max_gain == null ? 'open' : formatMoney(r.max_gain)}</td>
                        <td className="num right">{r.payout == null ? '—' : `${r.payout.toFixed(2)} : 1`}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">Payout: what it can make for each dollar it can lose. Prices move; the real trade is worked out again when a signal fires.</p>
        </div>
      )}
    </div>
  )
}

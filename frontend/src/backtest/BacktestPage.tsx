import { Fragment, useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation } from 'react-router'
import { api, ApiError } from '../api'
import { useMe } from '../auth'
import { Field, Segmented, StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { formatDateTime, parseNumber, SYMBOL_RE } from '../forms'
import { changeClass, formatPct, formatPrice, INTRADAY, TIMEFRAMES, type Timeframe } from '../market/bars'
import { ruleText } from '../master/logic'
import { CreditSpreadsPanel, LuckPanel, ResultsPanel } from '../master/ResultsPanel'
import { formatMoney } from '../money'
import '../capital/capital.css'
import '../master/master.css'
import EquityChart from './EquityChart'
import { COLUMN_LABEL, COLUMN_ORDER, optionTotal, reasonWord } from './logic'
import type { BacktestRun, ColumnKey, Money, SentSetup } from './types'
import './backtest.css'

const QUICK = ['TSLA', 'QQQ', 'SPY']
const STRATEGY = 'swing_v98'
type InputsSource = 'pegged' | 'given' | 'defaults'
type OptionsMode = 'off' | 'plan' | 'compare'

function when(t: number | null | undefined, tf: Timeframe, tz: string): string {
  if (t == null) return '—'
  if (INTRADAY.includes(tf)) return formatDateTime(new Date(t * 1000).toISOString(), tz)
  return new Date(t * 1000).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric' })
}

function days(v: number | null | undefined): string {
  if (v == null) return '—'
  return v < 1 ? `${(v * 24).toFixed(1)} h` : `${v.toFixed(1)} d`
}

const METRICS: { label: string; cell: (m: Money) => { text: string; cls?: string } }[] = [
  { label: 'Total return', cell: (m) => ({ text: formatMoney(m.total, true), cls: changeClass(m.total) }) },
  { label: 'Return on starting cash', cell: (m) => ({ text: formatPct(m.total_pct), cls: changeClass(m.total_pct) }) },
  { label: 'Ending value', cell: (m) => ({ text: formatMoney(m.final_value) }) },
  { label: 'Trades', cell: (m) => ({ text: String(m.trades) }) },
  { label: 'Win rate', cell: (m) => ({ text: m.win_rate == null ? '—' : `${m.win_rate.toFixed(1)}%` }) },
  { label: 'Average win', cell: (m) => ({ text: formatMoney(m.average_win, true), cls: changeClass(m.average_win) }) },
  { label: 'Average loss', cell: (m) => ({ text: formatMoney(m.average_loss, true), cls: changeClass(m.average_loss) }) },
  { label: 'Biggest drop', cell: (m) => ({ text: m.max_drop ? `−${formatMoney(m.max_drop)} (−${m.max_drop_pct.toFixed(1)}%)` : '—', cls: m.max_drop ? 'loss' : '' }) },
  { label: 'Average trade length', cell: (m) => ({ text: days(m.avg_days) }) },
  { label: 'Longest winning / losing streak', cell: (m) => ({ text: `${m.max_win_streak} / ${m.max_loss_streak}` }) },
]

function Summary({ run }: { run: BacktestRun }) {
  const r = run.result!
  const cols = COLUMN_ORDER.filter((k) => r.columns[k])
  return (
    <div className="table-wrap">
      <table className="table compare-table">
        <thead>
          <tr>
            <th />
            {cols.map((k) => (
              <th key={k} className="right">{COLUMN_LABEL[k]}{k !== 'stock' && <span className="badge badge-warn">estimate</span>}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {METRICS.map((m) => (
            <tr key={m.label}>
              <td>{m.label}</td>
              {cols.map((k) => {
                const c = m.cell(r.columns[k]!)
                return <td key={k} className={`num right ${c.cls ?? ''}`}>{c.text}</td>
              })}
            </tr>
          ))}
          {cols.some((k) => r.skipped[k]) && (
            <tr>
              <td>Signals with no option trade</td>
              {cols.map((k) => <td key={k} className="num right muted">{k === 'stock' ? '' : r.skipped[k] ?? 0}</td>)}
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function Years({ run }: { run: BacktestRun }) {
  const r = run.result!
  const cols = COLUMN_ORDER.filter((k) => r.columns[k])
  const years = [...new Set(cols.flatMap((k) => r.columns[k]!.years.map((y) => y.year)))].sort()
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>Year</th>{cols.map((k) => <th key={k} className="right">{COLUMN_LABEL[k]}</th>)}</tr></thead>
        <tbody>
          {years.map((y) => (
            <tr key={y}>
              <td>{y}</td>
              {cols.map((k) => {
                const row = r.columns[k]!.years.find((x) => x.year === y)
                return (
                  <td key={k} className={`num right ${changeClass(row?.pnl ?? null)}`}>
                    {row ? <>{formatMoney(row.pnl, true)} <span className="muted">({row.wins}/{row.trades})</span></> : '—'}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">By the year each trade closed. In brackets: winning trades / trades.</p>
    </div>
  )
}

function Trades({ run, tz }: { run: BacktestRun; tz: string }) {
  const r = run.result!
  const tf = run.timeframe
  const optCols = COLUMN_ORDER.filter((k) => k !== 'stock' && r.columns[k])
  const [open, setOpen] = useState<number | null>(null)
  const rows = [...r.trades].reverse()
  if (!rows.length) return <p className="muted">No trades in this range.</p>
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Opened</th><th>Signal</th><th>Type</th><th className="right">Entry</th><th className="right">Exit</th><th className="right">Move</th>
            <th>How it closed</th><th className="right">Length</th><th className="right">Stock $</th>
            {optCols.map((k) => <th key={k} className="right">{COLUMN_LABEL[k]} $</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((t, i) => (
            <Fragment key={`${t.entry_time}-${t.dir}-${i}`}>
              <tr className="clickable" onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i}>
                <td><span className="disclosure" aria-hidden="true">{open === i ? '▾' : '▸'}</span> {when(t.entry_time, tf, tz)}</td>
                <td className={t.dir === 1 ? 'legend-buy' : 'legend-sell'}>{t.dir === 1 ? '▲ BUY' : '▼ SELL'}{t.entries > 1 && ` ×${t.entries}`}</td>
                <td>{t.type}</td>
                <td className="num right">{formatPrice(t.entry_price)}</td>
                <td className="num right">{formatPrice(t.exit_price)}</td>
                <td className={`num right ${changeClass(t.ret_pct)}`}>{formatPct(t.ret_pct)}</td>
                <td>{reasonWord(t.reason)}</td>
                <td className="num right">{days(t.days)}</td>
                <td className={`num right ${changeClass(t.stock_pnl)}`}>{formatMoney(t.stock_pnl, true)}</td>
                {optCols.map((k) => {
                  const v = optionTotal(t.options[k])
                  return <td key={k} className={`num right ${changeClass(v)}`}>{v == null ? <span className="muted">none</span> : formatMoney(v, true)}</td>
                })}
              </tr>
              {open === i && (
                <tr className="detail-row">
                  <td colSpan={9 + optCols.length}>
                    <div className="detail">
                      <p className="muted">Closed {when(t.exit_time, tf, tz)}.{!t.counted && ' The script leaves this one floating and does not count it in its win rate.'}</p>
                      {optCols.map((k) => (t.options[k] ?? []).map((o, j) => (
                        <p key={`${k}${j}`}>
                          <b>{COLUMN_LABEL[k]}:</b>{' '}
                          {o.problem ? <span className="muted">no trade. {o.problem}</span> : (
                            <>
                              <span className="sym">{o.description}</span> ×{o.quantity}, {k === 'credit_spread' ? 'credit' : 'paid'} <span className="num">{formatPrice(o.entry)}</span>,
                              closed at <span className="num">{formatPrice(o.exit)}</span> → <span className={`num ${changeClass(o.pnl ?? null)}`}>{formatMoney(o.pnl, true)}</span>
                              <span className="muted"> · could lose {formatMoney(o.max_loss)}{o.payout != null && ` · payout ${o.payout.toFixed(2)}:1`} · strike {o.distance_pct}% away · ≥{o.hold_days} days{o.note && ` · ${o.note}`}</span>
                            </>
                          )}
                        </p>
                      )))}
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Result({ run, tz }: { run: BacktestRun; tz: string }) {
  const r = run.result!
  const s = run.setup
  const [tab, setTab] = useState<'trades' | 'types' | 'years' | 'luck' | 'spreads'>('trades')
  const optionsShown = r.notes.options_source != null
  return (
    <>
      <div className="panel">
        <h2>{run.symbol} {run.timeframe} · {when(r.range.first, run.timeframe, tz)} to {when(r.range.last, run.timeframe, tz)}</h2>
        <p className="muted">
          {s.inputs_source === 'pegged' ? 'Pegged settings' : s.inputs_source === 'given' ? 'Settings sent from Master Chart' : 'Script defaults'} · {ruleText(s.inputs)} ·
          starting cash {formatMoney(s.starting_cash)} · stock: {formatMoney(s.stock_dollars)} per trade
          {optionsShown && <> · options: risk {formatMoney(s.trade.risk_usd)} per trade, strike {s.trade.distance_mode === 'auto' ? 'at the average winning move so far' : `${s.trade.distance_pct}%`} {s.trade.side === 'toward' ? 'toward' : 'away from'} the signal</>}
        </p>
        <Summary run={run} />
        {optionsShown && (
          <p className="msg msg-warn">
            Option results are an <b>estimate</b>: prices come from a standard pricing model using the stock’s volatility over the previous {r.notes.vol_days} trading days and
            a {r.notes.rate_pct}% interest rate, with weekly Friday expirations and a bid/ask spread. Real past prices can differ a lot (around earnings especially).
            Databento’s real option history can replace the estimate later.
          </p>
        )}
        <EquityChart columns={r.columns} />
        <p className="muted">Account value after each trade closes. Stock price: a fixed {formatMoney(s.stock_dollars)} per trade, no compounding.</p>
      </div>
      <div className="panel">
        <div className="segmented tabs" role="tablist">
          {([['trades', 'Trades'], ['types', 'By candle type'], ['years', 'By year'], ['luck', 'Luck test'], ['spreads', 'Credit spreads']] as const).map(([k, l]) => (
            <button key={k} type="button" aria-pressed={tab === k} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>
        {tab === 'trades' && <Trades run={run} tz={tz} />}
        {tab === 'types' && <ResultsPanel r={r.results} ruleText={ruleText(s.inputs)} />}
        {tab === 'years' && <Years run={run} />}
        {tab === 'luck' && (r.luck ? <LuckPanel luck={r.luck} /> : <p className="muted">No luck test for this run.</p>)}
        {tab === 'spreads' && (r.credit_spreads ? <CreditSpreadsPanel cs={r.credit_spreads} /> : <p className="muted">No credit-spread statistics for this run (saved before v9.35).</p>)}
      </div>
    </>
  )
}

/** Backtest: the same strategy code as Master Chart over a date range (plan section 6). */
export default function BacktestPage() {
  const me = useMe()
  const tz = me.settings.display.timezone
  const sent = (useLocation().state as { sent?: SentSetup } | null)?.sent ?? null
  const [symbol, setSymbol] = useState(sent?.symbol ?? me.settings.watchlist.default_ticker)
  const [symbolText, setSymbolText] = useState(symbol)
  const [timeframe, setTimeframe] = useState<Timeframe>(sent?.timeframe ?? '1D')
  const [source, setSource] = useState<InputsSource>(sent ? 'given' : 'pegged')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [cash, setCash] = useState('100000')
  const [dollars, setDollars] = useState('10000')
  const [optionsMode, setOptionsMode] = useState<OptionsMode>('compare')
  const [runs, setRuns] = useState<BacktestRun[]>([])
  const [shown, setShown] = useState<BacktestRun | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const action = useAction()

  const loadRuns = useCallback(() => {
    api<BacktestRun[]>('GET', '/api/backtest/runs').then(setRuns).catch(() => undefined)
  }, [])
  useEffect(loadRuns, [loadRuns])

  function pickSymbol(s: string) {
    setSymbol(s)
    setSymbolText(s)
  }

  async function run(e: FormEvent) {
    e.preventDefault()
    const s = symbolText.trim().toUpperCase()
    if (!SYMBOL_RE.test(s)) return setError('Enter a valid symbol.')
    setSymbol(s)
    const c = parseNumber(cash)
    const d = parseNumber(dollars)
    if (c == null || c <= 0 || d == null || d <= 0) return setError('Enter amounts above zero for starting cash and dollars per trade.')
    setRunning(true)
    setError(null)
    try {
      const body = {
        strategy: sent?.strategy ?? STRATEGY, symbol: s, timeframe, inputs_source: source,
        inputs: source === 'given' && sent ? sent.inputs : {}, start: start || null, end: end || null,
        starting_cash: c, stock_dollars: d, options: optionsMode,
      }
      const r = await api<BacktestRun>('POST', '/api/backtest/run', body)
      setShown(r)
      loadRuns()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The backtest could not run.')
    } finally {
      setRunning(false)
    }
  }

  async function open(id: number) {
    await action.run(async () => setShown(await api<BacktestRun>('GET', `/api/backtest/runs/${id}`)))
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  async function remove(id: number) {
    await action.run(async () => {
      await api('DELETE', `/api/backtest/runs/${id}`)
      if (shown?.id === id) setShown(null)
      loadRuns()
    })
  }

  const sourceOptions: { value: InputsSource; label: string }[] = [
    { value: 'pegged', label: `Pegged to ${symbol} ${timeframe}` },
    ...(sent ? [{ value: 'given' as const, label: 'From Master Chart' }] : []),
    { value: 'defaults', label: 'Script defaults' },
  ]

  return (
    <section className="page page-wide backtest">
      <div className="page-head">
        <h1 className="page-title">Backtest</h1>
      </div>

      <form className="panel form" onSubmit={(e) => void run(e)}>
        <h2>Setup</h2>
        {sent && (
          <p className="msg msg-ok">Sent from Master Chart: {sent.symbol} {sent.timeframe} with the settings that were on screen.</p>
        )}
        <div className="actions">
          <input className="input symbol-input" aria-label="Symbol" value={symbolText} spellCheck={false} onChange={(e) => setSymbolText(e.target.value.toUpperCase())}
            onBlur={() => SYMBOL_RE.test(symbolText.trim()) && setSymbol(symbolText.trim().toUpperCase())} />
          {QUICK.map((s) => (
            <button key={s} type="button" className={`btn btn-small ${symbol === s ? 'btn-primary' : ''}`} onClick={() => pickSymbol(s)}>{s}</button>
          ))}
          <div className="segmented" role="group" aria-label="Timeframe">
            {TIMEFRAMES.map((t) => <button key={t} type="button" aria-pressed={timeframe === t} onClick={() => setTimeframe(t)}>{t}</button>)}
          </div>
        </div>
        {INTRADAY.includes(timeframe) && <p className="hint">Intraday history only goes back about 40 days with Tradier, so intraday backtests are short.</p>}
        <div className="field">
          <span>Strategy inputs</span>
          <Segmented label="Strategy inputs" value={source} onChange={setSource} options={sourceOptions} />
          <span className="hint">Strategy: Swing, Vela Diaria/Semanal v9.35, the same code as Master Chart and automatic trading.</span>
        </div>
        <div className="grid-2">
          <Field label="From" hint="Empty: from the start of the history (as TradingView).">
            <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </Field>
          <Field label="To" hint="Empty: up to today.">
            <input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </Field>
          <Field label="Starting cash ($)">
            <input className="input num" inputMode="decimal" value={cash} onChange={(e) => setCash(e.target.value)} />
          </Field>
          <Field label="Stock price: dollars per trade" hint="Each trade's stock-price result uses this fixed amount.">
            <input className="input num" inputMode="decimal" value={dollars} onChange={(e) => setDollars(e.target.value)} />
          </Field>
        </div>
        <div className="field">
          <span>Option trades (estimated prices)</span>
          <Segmented label="Option trades" value={optionsMode} onChange={setOptionsMode}
            options={[{ value: 'compare', label: 'Compare all three' }, { value: 'plan', label: 'My structure only' }, { value: 'off', label: 'Stock price only' }]} />
          <span className="hint">
            Strikes, expirations and dollar risk follow {symbol} {timeframe}’s “What to trade on a signal” in <Link to="/master">Master Chart</Link> (or its defaults).
          </span>
        </div>
        <div className="actions">
          <button className="btn btn-primary" disabled={running}>{running ? 'Running…' : 'Run backtest'}</button>
        </div>
        {error && <p className="msg msg-error">{error}</p>}
      </form>

      {shown?.result && <Result key={shown.id} run={shown} tz={tz} />}

      <div className="panel">
        <h2>Past backtests</h2>
        {runs.length === 0 ? <p className="muted">None yet. The last 30 are kept.</p> : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Run</th><th>Chart</th><th>Dates</th><th>Inputs</th>
                  {COLUMN_ORDER.map((k) => <th key={k} className="right">{COLUMN_LABEL[k]}</th>)}
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className={shown?.id === r.id ? 'current-row' : ''}>
                    <td>{formatDateTime(r.created_at, tz)}</td>
                    <td className="sym">{r.symbol} {r.timeframe}</td>
                    <td>{r.setup.start ?? 'start'} → {r.setup.end ?? 'today'}</td>
                    <td>{r.setup.inputs_source === 'pegged' ? 'Pegged' : r.setup.inputs_source === 'given' ? 'Master Chart' : 'Defaults'}</td>
                    {COLUMN_ORDER.map((k: ColumnKey) => {
                      const c = r.summary[k]
                      return <td key={k} className={`num right ${changeClass(c?.total ?? null)}`}>{c ? formatMoney(c.total, true) : '—'}</td>
                    })}
                    <td className="right">
                      <div className="actions">
                        <button className="btn btn-small" disabled={action.busy} onClick={() => void open(r.id)}>Open</button>
                        <button className="btn btn-small btn-danger" disabled={action.busy} onClick={() => void remove(r.id)}>Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <StatusLine status={action.status} />
      </div>
    </section>
  )
}

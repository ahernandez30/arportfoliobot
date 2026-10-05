import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api, ApiError, type AutoTrade } from '../api'
import { useMe } from '../auth'
import { StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { formatDateTime, SYMBOL_RE } from '../forms'
import { formatPrice, INTRADAY, TIMEFRAMES, type Timeframe } from '../market/bars'
import Chart, { type Overlays } from '../market/Chart'
import { Credits, MarketState } from '../market/MarketHeader'
import InputsPanel from './InputsPanel'
import { changedKeys, defaults, markers, presetFor, refreshMs } from './logic'
import ParityPanel from './ParityPanel'
import TradePlanPanel from './TradePlanPanel'
import { LuckPanel, ResultsPanel } from './ResultsPanel'
import type { Inputs, InputValue, Luck, MasterState, Preset, RunResult, StrategyDef } from './types'
import '../market/market.css'
import '../capital/capital.css'
import './master.css'

const QUICK = ['TSLA', 'QQQ', 'SPY']

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function when(t: number, tf: Timeframe, tz: string): string {
  if (INTRADAY.includes(tf)) return formatDateTime(new Date(t * 1000).toISOString(), tz)
  return new Date(t * 1000).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric' })
}

function ruleText(i: Inputs): string {
  if (i.modoSenal) {
    const tp = i.tpPctSS as number
    const sl = i.slPctSS as number
    return `${i.modoCesta ? 'Basket' : 'Signal to signal'}${tp || sl ? ` · take profit ${tp || '—'}% / stop ${sl || '—'}%` : ' (opposite signal only)'}`
  }
  return `Target ${i.objPct}% / stop ${i.stopPct}% · ${i.cierraMercado ? `closes after ${i.maxVelas} candles` : `floats after ${i.maxVelas} candles`}`
}

const TRADE_STATUS: Record<AutoTrade['status'], string> = {
  waiting: 'Waiting for the market', open: 'Open', floating: 'Open (floating)', closed: 'Closed', refused: 'Not placed', missed: 'Missed',
}

/** The paper trade(s) a signal caused: one per structure. */
function SignalTrades({ rows }: { rows: AutoTrade[] }) {
  if (!rows.length) return <span className="muted">—</span>
  return (
    <>
      {rows.map((a) => (
        <div key={a.id} className="signal-trade" title={a.detail || undefined}>
          <span className={`badge ${a.status === 'open' || a.status === 'floating' ? 'badge-on' : a.status === 'waiting' ? 'badge-warn' : 'badge-off'}`}>
            {TRADE_STATUS[a.status]}
          </span>{' '}
          <span className="sym">{a.position_label ?? a.plan.description ?? a.structure_label}</span>
          {a.detail && (a.status === 'refused' || a.status === 'missed') && <span className="muted"> · {a.detail}</span>}
        </div>
      ))}
    </>
  )
}

/** Master Chart: the strategy on one chart, every input editable, pegging per symbol and timeframe,
 * and the parity check against TradingView (plan section 6). */
export default function MasterChartPage() {
  const me = useMe()
  const tz = me.settings.display.timezone
  const [defs, setDefs] = useState<StrategyDef | null>(null)
  const [state, setState] = useState<MasterState | null>(null)
  const [presets, setPresets] = useState<Preset[]>([])
  const [result, setResult] = useState<RunResult | null>(null)
  const [shownFor, setShownFor] = useState<{ symbol: string; timeframe: Timeframe } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  // The luck test is slower, so it runs on request; it belongs to the settings it ran with.
  const [luckRun, setLuckRun] = useState<{ key: string; luck: Luck } | null>(null)
  const [showExits, setShowExits] = useState(true)
  const [showBlocked, setShowBlocked] = useState(false)
  const [symbolText, setSymbolText] = useState('')
  const [tab, setTab] = useState<'results' | 'luck' | 'signals' | 'parity'>('results')
  const pegAction = useAction()
  const runId = useRef(0)
  // Paper trades the worker placed from this chart's signals, by candle time.
  const [autoTrades, setAutoTrades] = useState<AutoTrade[]>([])

  useEffect(() => {
    Promise.all([api<StrategyDef[]>('GET', '/api/strategy/strategies'), api<MasterState>('GET', '/api/strategy/state')])
      .then(([list, st]) => {
        const d = list.find((x) => x.id === st.strategy) ?? list[0]
        setDefs(d)
        setState(st)
        setSymbolText(st.symbol)
        return api<Preset[]>('GET', `/api/strategy/presets?strategy=${d.id}`).then(setPresets)
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : 'Could not load Master Chart.'))
  }, [])

  // Run the strategy whenever something changes (a short pause lets typing finish), and again
  // now and then so new candles and their signals appear.
  const run = useCallback(async (st: MasterState) => {
    const id = ++runId.current
    setRunning(true)
    try {
      const r = await api<RunResult>('POST', '/api/strategy/run', { strategy: st.strategy, symbol: st.symbol, timeframe: st.timeframe, inputs: st.inputs })
      if (id !== runId.current) return
      setResult(r)
      setShownFor({ symbol: st.symbol, timeframe: st.timeframe })
      setError(null)
    } catch (e) {
      if (id === runId.current) setError(e instanceof ApiError ? e.message : 'Could not run the strategy.')
    } finally {
      if (id === runId.current) setRunning(false)
    }
  }, [])

  useEffect(() => {
    if (!state) return
    const t = window.setTimeout(() => {
      void run(state)
      void api('PUT', '/api/strategy/state', state).catch(() => undefined)
    }, 250)
    const timer = window.setInterval(() => document.visibilityState === 'visible' && void run(state), refreshMs(state.timeframe))
    return () => {
      window.clearTimeout(t)
      window.clearInterval(timer)
    }
  }, [state, run])

  const chartKey = state ? `${state.strategy}|${state.symbol}|${state.timeframe}` : ''
  useEffect(() => {
    if (!chartKey || tab !== 'signals') return
    const [strategy, symbol, timeframe] = chartKey.split('|')
    const q = new URLSearchParams({ strategy, symbol, timeframe, limit: '200' })
    const load = () => api<AutoTrade[]>('GET', `/api/auto/trades?${q}`).then(setAutoTrades).catch(() => setAutoTrades([]))
    void load()
    const timer = window.setInterval(() => document.visibilityState === 'visible' && void load(), 15000)
    return () => window.clearInterval(timer)
  }, [chartKey, tab])

  const pegged = state ? presetFor(presets, state.symbol, state.timeframe) : null
  const diff = state && pegged ? changedKeys(pegged.inputs, state.inputs) : []

  // Switching symbol or timeframe loads that pair's pegged settings, if it has them.
  function moveTo(symbol: string, timeframe: Timeframe) {
    if (!state) return
    const p = presetFor(presets, symbol, timeframe)
    setState({ ...state, symbol, timeframe, inputs: p ? { ...p.inputs } : state.inputs })
  }

  function setInput(key: string, value: InputValue) {
    if (state) setState({ ...state, inputs: { ...state.inputs, [key]: value } })
  }

  function submitSymbol(e: FormEvent) {
    e.preventDefault()
    const s = symbolText.trim().toUpperCase()
    if (SYMBOL_RE.test(s) && state) moveTo(s, state.timeframe)
  }

  async function peg() {
    if (!state) return
    await pegAction.run(async () => {
      const p = await api<Preset>('PUT', '/api/strategy/presets', state)
      setPresets((list) => [...list.filter((x) => x.id !== p.id && !(x.symbol === p.symbol && x.timeframe === p.timeframe)), p]
        .sort((a, b) => (a.symbol + a.timeframe).localeCompare(b.symbol + b.timeframe)))
    }, `Settings pegged to ${state.symbol} ${state.timeframe}.`)
  }

  async function unpeg(p: Preset) {
    if (!window.confirm(`Remove the pegged settings for ${p.symbol} ${p.timeframe}?`)) return
    await pegAction.run(async () => {
      await api('DELETE', `/api/strategy/presets/${p.id}`)
      setPresets((list) => list.filter((x) => x.id !== p.id))
    })
  }

  const stateKey = JSON.stringify(state)
  const luck = luckRun?.key === stateKey ? luckRun.luck : null

  async function runLuck() {
    if (!state) return
    setTab('luck')
    const key = JSON.stringify(state)
    try {
      const r = await api<RunResult>('POST', '/api/strategy/run', { ...state, luck: true })
      if (r.luck) setLuckRun({ key, luck: r.luck })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not run the luck test.')
    }
  }

  const theme = me.settings.display.theme
  const overlays = useMemo<Overlays | undefined>(() => {
    void theme // colours follow the theme
    if (!result) return undefined
    const colors = { buy: cssVar('--buy'), sell: cssVar('--sell'), gain: cssVar('--gain'), loss: cssVar('--loss'), muted: cssVar('--text-2') }
    const lines: Overlays['priceLines'] = []
    const p = result.position
    if (p?.target != null) lines.push({ price: p.target, color: colors.gain, title: 'Target' })
    if (p?.stop != null) lines.push({ price: p.stop, color: colors.loss, title: 'Stop' })
    return { markers: markers(result, colors, { exits: showExits, blocked: showBlocked }), line: result.ma, lineColor: cssVar('--paper'), priceLines: lines }
  }, [result, showExits, showBlocked, theme])

  if (error && !state) return <section className="page"><h1 className="page-title">Master Chart</h1><p className="msg msg-error">{error}</p></section>
  if (!state || !defs) return <section className="page"><h1 className="page-title">Master Chart</h1><p className="muted">Loading…</p></section>

  const recent = result ? [...result.signals].reverse().slice(0, 30) : []
  const ladderOn = result?.ladder.some((l) => l.active)

  return (
    <section className="page page-wide master">
      <div className="page-head">
        <h1 className="page-title">Master Chart <span className="muted title-note">{defs.name} {defs.version}</span></h1>
        <MarketState />
      </div>

      <div className="panel chart-controls">
        <form className="actions" onSubmit={submitSymbol}>
          <input className="input symbol-input" aria-label="Symbol" value={symbolText} spellCheck={false} onChange={(e) => setSymbolText(e.target.value.toUpperCase())} />
          <button className="btn btn-small">Load</button>
          {QUICK.map((s) => (
            <button key={s} type="button" className={`btn btn-small ${state.symbol === s ? 'btn-primary' : ''}`} onClick={() => { setSymbolText(s); moveTo(s, state.timeframe) }}>{s}</button>
          ))}
        </form>
        <div className="segmented" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((t) => (
            <button key={t} type="button" aria-pressed={state.timeframe === t} onClick={() => moveTo(state.symbol, t)}>{t}</button>
          ))}
        </div>
      </div>

      {error && <p className="msg msg-error">{error}</p>}

      <div className="master-grid">
        <div className="master-main">
          <div className="panel master-chart">
            <div className="chart-head">
              <span className="sym">{state.symbol} · {state.timeframe}</span>
              {running && <span className="muted">Updating…</span>}
              {result?.preview && (
                <span className="badge badge-warn">{result.preview.dir === 1 ? 'BUY' : 'SELL'} forming on the open candle · not final until it closes</span>
              )}
              {result?.position && (
                <span className="muted">
                  Open {result.position.dir === 1 ? 'long' : 'short'} from <span className="num">{formatPrice(result.position.entry)}</span>
                  {result.position.entries ? ` (${result.position.entries} entries)` : ''}
                </span>
              )}
              <span className="chart-toggles">
                <label className="check"><input type="checkbox" checked={showExits} onChange={(e) => setShowExits(e.target.checked)} /> Exits</label>
                <label className="check"><input type="checkbox" checked={showBlocked} onChange={(e) => setShowBlocked(e.target.checked)} /> Blocked</label>
              </span>
            </div>
            <div className="master-chart-box">
              {result && shownFor ? (
                <Chart symbol={shownFor.symbol} timeframe={shownFor.timeframe} data={result.bars} overlays={overlays} />
              ) : (
                <p className="muted">Loading…</p>
              )}
            </div>
            {result && (
              <p className="muted legend-line">
                <span className="legend-buy">▲ BUY</span> · <span className="legend-sell">▼ SELL</span> · LL = LLENA, FL = FLECO, EN = ENGULFING
                {result.ma.length > 0 && ' · line: moving-average filter'}
                {ladderOn && <> · ladder: {result.ladder.filter((l) => l.active).map((l) => `${l.tf} ${l.state === 1 ? '▲' : l.state === -1 ? '▼' : '='}`).join(' ')}</>}
                {result.intrabar.on && result.intrabar.covered_from && <> · real path ({result.intrabar.tf}) available from {when(result.intrabar.covered_from, state.timeframe, tz)}; older candles use open/high/low/close</>}
              </p>
            )}
          </div>

          <div className="panel">
            <div className="segmented tabs" role="tablist">
              {([['results', 'Results'], ['signals', 'Recent signals'], ['luck', 'Luck test'], ['parity', 'Parity check']] as const).map(([k, l]) => (
                <button key={k} type="button" aria-pressed={tab === k} onClick={() => (k === 'luck' && !luck ? void runLuck() : setTab(k))}>{l}</button>
              ))}
            </div>
            {tab === 'results' && result && <ResultsPanel r={result.results} ruleText={ruleText(state.inputs)} />}
            {tab === 'luck' && (luck ? <LuckPanel luck={luck} /> : (
              <div className="actions">
                <button className="btn btn-small btn-primary" onClick={() => void runLuck()}>Run the luck test with these settings</button>
              </div>
            ))}
            {tab === 'signals' && result && (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Time</th><th>Signal</th><th>Candle type</th><th className="right">Price</th><th className="right">Body %</th><th className="right">Wick %</th><th>Paper trade</th></tr></thead>
                  <tbody>
                    {result.preview && (
                      <tr className="preview-row">
                        <td>{when(result.preview.time, state.timeframe, tz)}</td>
                        <td>{result.preview.dir === 1 ? 'BUY' : 'SELL'} (forming)</td>
                        <td>{result.preview.type}</td>
                        <td className="num right">{formatPrice(result.preview.price)}</td>
                        <td className="num right">{result.preview.body_pct.toFixed(0)}</td>
                        <td className="num right">{result.preview.wick_pct.toFixed(0)}</td>
                        <td className="muted">Not final</td>
                      </tr>
                    )}
                    {recent.map((s) => (
                      <tr key={s.i}>
                        <td>{when(s.time, state.timeframe, tz)}</td>
                        <td className={s.dir === 1 ? 'legend-buy' : 'legend-sell'}>{s.dir === 1 ? '▲ BUY' : '▼ SELL'}</td>
                        <td>{s.type}</td>
                        <td className="num right">{formatPrice(s.price)}</td>
                        <td className="num right">{s.body_pct.toFixed(0)}</td>
                        <td className="num right">{s.wick_pct.toFixed(0)}</td>
                        <td><SignalTrades rows={autoTrades.filter((a) => a.signal_time === s.time)} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {tab === 'parity' && <ParityPanel strategy={state.strategy} />}
          </div>
        </div>

        <aside className="master-side">
          <div className="panel">
            <h2>Pegged settings</h2>
            {pegged ? (
              diff.length === 0 ? (
                <p className="msg msg-ok">Using the settings pegged to {state.symbol} {state.timeframe}.</p>
              ) : (
                <div className="form">
                  <p className="msg msg-warn">
                    {diff.length} setting{diff.length === 1 ? ' differs' : 's differ'} from those pegged to {state.symbol} {state.timeframe}:{' '}
                    {diff.map((k) => defs.inputs.find((d) => d.key === k)?.label ?? k).join(', ')}.
                  </p>
                  <button className="btn btn-small" onClick={() => setState({ ...state, inputs: { ...pegged.inputs } })}>Go back to pegged settings</button>
                </div>
              )
            ) : (
              <p className="muted">Nothing pegged to {state.symbol} {state.timeframe} yet. These are your working settings.</p>
            )}
            <div className="actions">
              <button className="btn btn-primary" disabled={pegAction.busy} onClick={() => void peg()}>Peg these settings to {state.symbol} {state.timeframe}</button>
              <button className="btn btn-small" onClick={() => setState({ ...state, inputs: defaults(defs.inputs) })}>Script defaults</button>
            </div>
            <StatusLine status={pegAction.status} />
            {presets.length > 0 && (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Symbol</th><th>TF</th><th>Pegged</th><th /></tr></thead>
                  <tbody>
                    {presets.map((p) => (
                      <tr key={p.id} className={p.id === pegged?.id ? 'current-row' : ''}>
                        <td className="sym">{p.symbol}</td>
                        <td>{p.timeframe}</td>
                        <td className="muted">{formatDateTime(p.pegged_at, tz)}</td>
                        <td className="right">
                          <div className="actions">
                            <button className="btn btn-small" onClick={() => { setSymbolText(p.symbol); moveTo(p.symbol, p.timeframe) }}>Open</button>
                            <button className="btn btn-small btn-danger" onClick={() => void unpeg(p)}>Remove</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <div className="panel">
            <h2>Strategy inputs</h2>
            <p className="muted">Every input of the script. Changes redraw the signals straight away.{pegged && ' Changed from the pegged set: marked.'}</p>
            <InputsPanel defs={defs.inputs} inputs={state.inputs} pegged={pegged?.inputs ?? null} onChange={setInput} />
          </div>
          <TradePlanPanel key={`${state.symbol}|${state.timeframe}`} strategy={state.strategy} symbol={state.symbol} timeframe={state.timeframe} inputs={state.inputs} pegged={!!pegged} />
          <div className="panel empty">
            <p>“Send to Backtest” comes with the Backtest tab in Stage 7.</p>
          </div>
        </aside>
      </div>
      <Credits />
    </section>
  )
}

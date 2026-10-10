import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import { useMe } from '../auth'
import { Field, StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { formatDateTime, SYMBOL_RE } from '../forms'
import { TIMEFRAMES, type Timeframe } from '../market/bars'
import { guessFromFilename } from './logic'
import type { ParityCheck } from './types'

const DIR = (d: number) => (d === 1 ? 'BUY' : d === -1 ? 'SELL' : 'none')

function ohlc(o: { open: number; high: number; low: number; close: number } | null): string {
  return o ? `${o.open} / ${o.high} / ${o.low} / ${o.close}` : 'missing'
}

function Report({ c, onSigned }: { c: ParityCheck; onSigned: (c: ParityCheck) => void }) {
  const s = c.summary
  const [note, setNote] = useState(c.sign_off_note)
  const action = useAction()
  const tz = useMe().settings.display.timezone
  return (
    <div className="parity-report">
      <h3>{c.symbol} {c.timeframe} · {c.filename || 'TradingView export'}</h3>
      <p className={`msg ${s.logic_ok ? 'msg-ok' : 'msg-error'}`}>
        {s.logic_ok
          ? `Logic: on TradingView's own prices the engine gives exactly TradingView's signals on all ${s.logic_candles.toLocaleString()} candles.`
          : `Logic: ${s.logic_mismatches} of ${s.logic_candles.toLocaleString()} candles differ even on TradingView's own prices. Check that the indicator was at its default settings when you exported.`}
      </p>
      <p>
        Data, {s.range[0]} to {s.range[1]}: TradingView <b className="num">{s.tv_signals}</b> signals, ours <b className="num">{s.our_signals}</b>, matching{' '}
        <b className="num">{s.matched}</b>, different <b className={`num ${s.differences ? 'warn-text' : ''}`}>{s.differences}</b>.
      </p>
      {c.detail && c.detail.logic_mismatches.length > 0 && (
        <details open>
          <summary>Logic differences ({c.detail.logic_mismatches.length})</summary>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Candle</th><th>TradingView</th><th>Engine</th><th>Open / High / Low / Close</th><th className="right">Body %</th><th className="right">Wick %</th></tr></thead>
              <tbody>
                {c.detail.logic_mismatches.map((m) => (
                  <tr key={m.candle}><td className="num">{m.candle}</td><td>{DIR(m.tradingview)}</td><td>{DIR(m.engine)}</td><td className="num">{ohlc(m.ohlc)}</td>
                    <td className="num right">{m.measure?.body_pct ?? '—'}</td><td className="num right">{m.measure?.wick_pct ?? '—'}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {c.detail && c.detail.differences.length > 0 && (
        <details open>
          <summary>Signals that differ, and why ({c.detail.differences.length})</summary>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Candle</th><th>TradingView</th><th>Ours</th><th>Why</th><th>TradingView O/H/L/C</th><th>Ours O/H/L/C</th><th className="right">Body % TV / ours</th></tr></thead>
              <tbody>
                {c.detail.differences.map((d) => (
                  <tr key={d.candle}>
                    <td className="num">{d.candle}</td><td>{DIR(d.tradingview)}</td><td>{DIR(d.ours)}</td><td className="why">{d.why}</td>
                    <td className="num">{ohlc(d.tv_ohlc)}</td><td className="num">{ohlc(d.our_ohlc)}</td>
                    <td className="num right">{d.tv_measure?.body_pct ?? '—'} / {d.our_measure?.body_pct ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {c.signed_off_at ? (
        <p className="msg msg-ok">Signed off {formatDateTime(c.signed_off_at, tz)}{c.sign_off_note && `: “${c.sign_off_note}”`}</p>
      ) : (
        <div className="form">
          <Field label="Sign-off note (optional)" hint="Sign off once every difference is explained. Automatic paper trading already runs (your choice); real-money trading will wait for this.">
            <input className="input" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <div className="actions">
            <button className="btn btn-primary" disabled={action.busy}
              onClick={() => void action.run(async () => onSigned(await api<ParityCheck>('POST', `/api/strategy/parity/${c.id}/sign-off`, { note })))}>
              Sign off parity for {c.symbol} {c.timeframe}
            </button>
          </div>
          <StatusLine status={action.status} />
        </div>
      )}
    </div>
  )
}

/** Parity check (plan 7.5): compare with signal dates exported from TradingView. */
export default function ParityPanel({ strategy }: { strategy: string }) {
  const tz = useMe().settings.display.timezone
  const [file, setFile] = useState<File | null>(null)
  const [symbol, setSymbol] = useState('')
  const [tf, setTf] = useState<Timeframe>('1D')
  const [current, setCurrent] = useState<ParityCheck | null>(null)
  const [history, setHistory] = useState<ParityCheck[]>([])
  const action = useAction()

  const loadHistory = () => api<ParityCheck[]>('GET', '/api/strategy/parity').then(setHistory).catch(() => undefined)
  useEffect(() => {
    void loadHistory()
  }, [])

  function pick(f: File | null) {
    setFile(f)
    if (!f) return
    const g = guessFromFilename(f.name)
    if (g.symbol) setSymbol(g.symbol)
    if (g.timeframe) setTf(g.timeframe)
  }

  function run() {
    const sym = symbol.trim().toUpperCase()
    if (!file) return action.setStatus({ kind: 'error', text: 'Choose the CSV file exported from TradingView.' })
    if (!SYMBOL_RE.test(sym)) return action.setStatus({ kind: 'error', text: 'Enter the symbol.' })
    void action.run(async () => {
      const csv = await file.text()
      const c = await api<ParityCheck>('POST', '/api/strategy/parity', { strategy, symbol: sym, timeframe: tf, filename: file.name, csv })
      setCurrent(c)
      void loadHistory()
    })
  }

  async function open(id: number) {
    try {
      setCurrent(await api<ParityCheck>('GET', `/api/strategy/parity/${id}`))
    } catch (e) {
      action.setStatus({ kind: 'error', text: e instanceof ApiError ? e.message : 'Could not open it.' })
    }
  }

  return (
    <div className="form">
      <ol className="muted steps">
        <li>In TradingView, open the symbol on 1D or 1W with the Swing v9.37 indicator at its <b>default settings</b> and its arrows showing.</li>
        <li>Chart menu → <b>Export chart data…</b> → export, then choose that file here.</li>
        <li>The site checks the logic on TradingView's own prices, then compares the signal dates with ours and explains each difference.</li>
      </ol>
      <div className="grid-2">
        <Field label="TradingView export (CSV)">
          <input className="input file-input" type="file" accept=".csv,text/csv" onChange={(e) => pick(e.target.files?.[0] ?? null)} />
        </Field>
        <Field label="Symbol">
          <input className="input" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="TSLA" />
        </Field>
        <Field label="Timeframe">
          <select className="select" value={tf} onChange={(e) => setTf(e.target.value as Timeframe)}>
            {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
      </div>
      <div className="actions">
        <button className="btn btn-primary" disabled={action.busy} onClick={run}>{action.busy ? 'Comparing…' : 'Run parity check'}</button>
      </div>
      <StatusLine status={action.status} />
      {current && <Report key={current.id} c={current} onSigned={(c) => { setCurrent(c); void loadHistory() }} />}
      {history.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>When</th><th>Symbol</th><th>TF</th><th>Logic</th><th className="right">Matched</th><th className="right">Different</th><th>Signed off</th><th /></tr></thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.id}>
                  <td>{formatDateTime(h.created_at, tz)}</td><td className="sym">{h.symbol}</td><td>{h.timeframe}</td>
                  <td className={h.summary.logic_ok ? 'gain' : 'loss'}>{h.summary.logic_ok ? '✔ same' : `✗ ${h.summary.logic_mismatches}`}</td>
                  <td className="num right">{h.summary.matched} / {h.summary.tv_signals}</td>
                  <td className="num right">{h.summary.differences}</td>
                  <td>{h.signed_off_at ? '✔' : '—'}</td>
                  <td className="right"><button className="btn btn-small" onClick={() => void open(h.id)}>Open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

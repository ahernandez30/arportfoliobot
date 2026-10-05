import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, ApiError, type ClosedTrade, type Flow, type TradeListing } from '../api'
import { useMe } from '../auth'
import FlowsPanel from '../capital/FlowsPanel'
import '../capital/capital.css'
import { Field, Segmented, StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { formatDateTime } from '../forms'
import { changeClass, formatPct } from '../market/bars'
import { formatMoney, formatQty, formatQuoted } from '../money'
import { REASONS } from './reasons'
import TradeEntry from './TradeEntry'

type Mode = 'real' | 'paper'
type Period = 'today' | 'week' | 'month' | 'year' | 'all' | 'custom'

const PERIODS: { value: Period; label: string }[] = [
  { value: 'today', label: 'Today' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'year', label: 'Year' },
  { value: 'all', label: 'All' },
  { value: 'custom', label: 'Dates' },
]
const REASON_LABEL = Object.fromEntries(REASONS.map((r) => [r.value, r.label]))
const PERIOD_LABEL: Record<Period, string> = { today: 'today', week: 'this week', month: 'this month', year: 'this year', all: 'all time', custom: 'chosen dates' }

function Stat({ label, value, cls = '', note }: { label: string; value: string; cls?: string; note?: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value num ${cls}`}>{value}</span>
      {note && <span className="muted stat-note">{note}</span>}
    </div>
  )
}

function TradeDetail({ t, onChanged, onEdit }: { t: ClosedTrade; onChanged: () => void; onEdit: () => void }) {
  const [notes, setNotes] = useState(t.notes)
  const action = useAction()
  async function remove() {
    if (!window.confirm(`Delete this ${t.label} trade?`)) return
    await action.run(async () => {
      await api('DELETE', `/api/trades/${t.id}`)
      onChanged()
    })
  }
  return (
    <div className="detail">
      <p className="muted">
        {t.structure === 'credit_spread' ? 'Spread sold for a credit, then bought back' : t.structure === 'debit_spread' ? 'Spread bought, then sold'
          : t.direction === 'long' ? 'Bought, then sold' : 'Sold, then bought back'}
        {t.risk != null && <> · most it could lose <span className="num">{formatMoney(t.risk)}</span> (the % is of this)</>}
        {t.underlying_entry != null && t.underlying_exit != null && <> · stock <span className="num">{formatQuoted(t.underlying_entry)}</span> → <span className="num">{formatQuoted(t.underlying_exit)}</span></>}
        {' '}· fees <span className="num">{formatMoney(t.fees)}</span> · placed by{' '}
        {t.source === 'manual' ? 'you (typed in)' : t.source}
      </p>
      <Field label="Notes">
        <textarea className="input" value={notes} maxLength={5000} onChange={(e) => setNotes(e.target.value)} />
      </Field>
      <div className="actions">
        <button className="btn btn-small btn-primary" disabled={action.busy || notes === t.notes}
          onClick={() => void action.run(async () => { await api('PATCH', `/api/trades/${t.id}`, { notes }); onChanged() }, 'Notes saved.')}>
          Save notes
        </button>
        {t.editable && (
          <>
            <button className="btn btn-small" onClick={onEdit}>Edit trade</button>
            <button className="btn btn-small btn-danger" onClick={() => void remove()}>Delete trade</button>
          </>
        )}
      </div>
      <StatusLine status={action.status} />
    </div>
  )
}

/** Account Manager: closed short-term trades, stats, filters and export (plan section 6). */
export default function AccountsPage() {
  const me = useMe()
  const tz = me.settings.display.timezone
  const [mode, setMode] = useState<Mode>('real')
  const [period, setPeriod] = useState<Period>('month')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [symbol, setSymbol] = useState('')
  // Paper sub-accounts (one per structure compared) are never added together.
  const [account, setAccount] = useState('main')
  const [data, setData] = useState<TradeListing | null>(null)
  const [flows, setFlows] = useState<Flow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [entry, setEntry] = useState<'new' | ClosedTrade | null>(null)

  const params = new URLSearchParams({ mode, period })
  if (period === 'custom') {
    if (start) params.set('start', start)
    if (end) params.set('end', end)
  }
  if (symbol) params.set('symbol', symbol)
  if (mode === 'paper') params.set('account', account)
  const query = params.toString()

  const load = useCallback(() => {
    api<TradeListing>('GET', `/api/trades?${query}`).then((d) => { setData(d); setError(null) })
      .catch((e) => setError(e instanceof ApiError ? e.message : 'Could not load trades.'))
    api<Flow[]>('GET', '/api/flows?account=short_term').then(setFlows).catch(() => undefined)
  }, [query])

  useEffect(load, [load])

  const s = data?.stats
  // Strategy trades record the stock's own move beside the option's result (plan 7.6).
  const hasMoves = !!data?.trades.some((t) => t.stock_move_pct != null)
  return (
    <section className="page page-wide">
      <div className="page-head">
        <h1 className="page-title">Account Manager</h1>
        <div className="actions">
          {mode === 'paper' && <span className="badge badge-paper">PAPER</span>}
          <Segmented label="Paper or real" value={mode} onChange={(m) => { setMode(m); setSymbol(''); setOpen(null) }}
            options={[{ value: 'real', label: 'Real' }, { value: 'paper', label: 'Paper' }]} />
        </div>
      </div>

      <div className="filters">
        <div className="field">
          <span>Period</span>
          <Segmented label="Period" value={period} onChange={setPeriod} options={PERIODS} />
        </div>
        {period === 'custom' && (
          <>
            <Field label="From"><input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
            <Field label="To"><input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
          </>
        )}
        {mode === 'paper' && data && data.accounts.length > 1 && (
          <Field label="Paper account">
            <select className="select" value={account} onChange={(e) => { setAccount(e.target.value); setOpen(null) }}>
              {data.accounts.map((x) => <option key={x.name} value={x.name}>{x.label}</option>)}
            </select>
          </Field>
        )}
        <Field label="Symbol">
          <select className="select" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            <option value="">All symbols</option>
            {data?.symbols.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </Field>
        <a className="btn" href={`/api/trades.csv?${query}`} download>Export CSV</a>
        {mode === 'real' && !entry && <button className="btn btn-primary" onClick={() => setEntry('new')}>Add trade</button>}
      </div>

      {error && <p className="msg msg-error">{error}</p>}

      {entry && (
        <TradeEntry key={entry === 'new' ? 'new' : entry.id} editing={entry === 'new' ? undefined : entry}
          onCancel={() => setEntry(null)} onSaved={() => { setEntry(null); load() }} />
      )}

      {s && data && (
        <div className="panel">
          <div className="totals">
            <Stat label={mode === 'paper' ? 'Paper account value' : 'Account value'} value={formatMoney(data.account_value)}
              note={mode === 'paper' ? (data.account_value_estimated ? 'Cash plus open positions (some at cost)' : 'Cash plus open positions') : 'Money put in plus all real results'} />
            <Stat label={`Result, ${PERIOD_LABEL[period]}`} value={formatMoney(s.total, true)} cls={changeClass(s.total)} note={`${s.count} trade${s.count === 1 ? '' : 's'}`} />
            <Stat label="Win rate" value={s.win_rate == null ? '—' : `${s.win_rate.toFixed(1)}%`} note={`${s.wins} won · ${s.losses} lost`} />
            <Stat label="Average win" value={formatMoney(s.average_win, true)} cls={changeClass(s.average_win)} />
            <Stat label="Average loss" value={formatMoney(s.average_loss, true)} cls={changeClass(s.average_loss)} />
          </div>
          {s.count > 0 && (
            <p className="muted">
              By how it closed:{' '}
              {Object.entries(s.by_reason).map(([k, v], i) => (
                <span key={k}>{i > 0 && ' · '}{REASON_LABEL[k] ?? k} {v.count} (<span className={`num ${changeClass(v.total)}`}>{formatMoney(v.total, true)}</span>)</span>
              ))}
            </p>
          )}
        </div>
      )}

      <div className="panel">
        <h2>{mode === 'paper' ? 'Paper trades' : 'Real trades'}</h2>
        {!data ? (
          <p className="muted">Loading…</p>
        ) : data.trades.length === 0 ? (
          <p className="muted">
            {mode === 'paper' ? 'No paper trades in this period. Paper trades appear here by themselves when they close in Live Trader.' : 'No real trades in this period. Use “Add trade” to enter one.'}
          </p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Closed</th><th>Contract</th><th className="right">Quantity</th><th className="right">Entry</th><th className="right">Exit</th>
                  <th className="right">Result $</th><th className="right">Result %</th>{hasMoves && <th className="right">Stock move</th>}
                  <th>How it closed</th><th>Placed by</th><th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {data.trades.map((t) => (
                  <Fragment key={t.id}>
                    <tr className="clickable" onClick={() => setOpen(open === t.id ? null : t.id)} aria-expanded={open === t.id}>
                      <td><span className="disclosure" aria-hidden="true">{open === t.id ? '▾' : '▸'}</span> {formatDateTime(t.closed_at, tz)}</td>
                      <td><span className="sym">{t.label}</span>{t.direction === 'short' && t.structure === 'single' && <span className="muted"> · short</span>}</td>
                      <td className="num right">{formatQty(t.quantity)}</td>
                      <td className="num right">{formatQuoted(t.entry_price)}</td>
                      <td className="num right">{formatQuoted(t.exit_price)}</td>
                      <td className={`num right ${changeClass(t.result)}`}>{formatMoney(t.result, true)}</td>
                      <td className={`num right ${changeClass(t.result_pct)}`}>{formatPct(t.result_pct)}</td>
                      {hasMoves && <td className={`num right ${changeClass(t.stock_move_pct)}`}>{formatPct(t.stock_move_pct)}</td>}
                      <td>{REASON_LABEL[t.close_reason]}</td>
                      <td>{t.source === 'manual' ? 'Manual entry' : t.source}</td>
                      <td className="muted notes-cell">{t.notes}</td>
                    </tr>
                    {open === t.id && (
                      <tr className="detail-row">
                        <td colSpan={hasMoves ? 11 : 10}>
                          <TradeDetail t={t} onChanged={load} onEdit={() => { setEntry(t); window.scrollTo({ top: 0, behavior: 'smooth' }) }} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {mode === 'real' && <FlowsPanel account="short_term" flows={flows} onChanged={load} />}
    </section>
  )
}

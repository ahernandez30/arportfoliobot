import { useEffect, useState, type FormEvent } from 'react'
import { api, type PaperSummary, type Settings } from '../api'
import { formatMoney } from '../money'
import { useMe } from '../auth'
import { parseSymbols, readNumbers, SYMBOL_RE, type NumberSpec } from '../forms'
import { Field, Segmented, StatusLine } from './common'
import { useAction, useSaveSettings } from './hooks'

function NumberFields({
  specs,
  draft,
  setDraft,
}: {
  specs: NumberSpec[]
  draft: Record<string, string>
  setDraft: (d: Record<string, string>) => void
}) {
  return (
    <div className="grid-2">
      {specs.map((s) => (
        <Field key={s.key} label={s.label} hint={s.hint}>
          <input
            className="input num"
            inputMode="decimal"
            value={draft[s.key] ?? ''}
            onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })}
          />
        </Field>
      ))}
    </div>
  )
}

function toDraft(values: object, specs: NumberSpec[]): Record<string, string> {
  const v = values as Record<string, number>
  return Object.fromEntries(specs.map((s) => [s.key, String(v[s.key])]))
}

// ---------- trading defaults and limits ----------

const TRADING_SPECS: NumberSpec[] = [
  { key: 'take_profit_pct', label: 'Take profit %', hint: 'Default for manual trades, measured on the option price.', min: 0, max: 1000, minExclusive: true },
  { key: 'stop_loss_pct', label: 'Stop loss %', hint: 'Default for manual trades. Strategy trades exit by their own rule.', min: 0, max: 100, minExclusive: true },
  { key: 'contracts_per_trade', label: 'Contracts per trade', min: 1, max: 1000, whole: true },
  { key: 'max_order_usd', label: 'Largest order allowed ($)', hint: 'Any order costing more is refused.', min: 0, max: 10_000_000, minExclusive: true },
  { key: 'max_daily_loss_usd', label: 'Largest loss per day ($)', hint: 'New orders are refused once the day loses this much.', min: 0, max: 10_000_000, minExclusive: true },
]

export function TradingSection() {
  const me = useMe()
  const save = useSaveSettings()
  const t = me.settings.trading
  const [draft, setDraft] = useState(() => toDraft(t, TRADING_SPECS))
  const [auto, setAuto] = useState<Settings['trading']['auto_trading']>(t.auto_trading)
  const action = useAction()

  function submit(e: FormEvent) {
    e.preventDefault()
    const nums = readNumbers(TRADING_SPECS, draft)
    if (typeof nums === 'string') return action.setStatus({ kind: 'error', text: nums })
    void action.run(() => save({ trading: { ...nums, auto_trading: auto } }), 'Trading settings saved.')
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>Trading defaults and limits</h2>
      <p className="muted">The limits apply to manual and automatic orders from Stage 4 on.</p>
      <NumberFields specs={TRADING_SPECS} draft={draft} setDraft={setDraft} />
      <div className="field">
        <span>Automatic trading</span>
        <Segmented
          label="Automatic trading"
          value={auto}
          options={[
            { value: 'off', label: 'Off' },
            { value: 'paper', label: 'Paper only' },
          ]}
          onChange={setAuto}
        />
        <span className="hint">
          Automatic trading starts in Stage 6. “Paper and real” becomes available in Stage 8, and needs both you and the
          admin to switch it on.
        </span>
      </div>
      <StatusLine status={action.status} />
      <div className="actions">
        <button className="btn btn-primary" disabled={action.busy}>
          Save
        </button>
      </div>
    </form>
  )
}

// ---------- paper account ----------

const PAPER_SPECS: NumberSpec[] = [
  { key: 'starting_balance', label: 'Starting balance ($)', hint: 'Used when the paper account is created or reset. Save it before pressing Reset.', min: 1000, max: 100_000_000 },
]

/** The paper account's current balance and the reset button. */
function PaperBalance() {
  const [s, setS] = useState<PaperSummary | null>(null)
  const action = useAction()
  useEffect(() => {
    api<PaperSummary>('GET', '/api/paper').then(setS).catch(() => undefined)
  }, [])
  async function reset() {
    if (!window.confirm('Reset the paper account to the saved starting balance? Working orders are cancelled and open paper positions are set aside. Finished paper trades stay in Account Manager.')) return
    await action.run(async () => setS(await api<PaperSummary>('POST', '/api/paper/reset')), 'Paper account reset.')
  }
  if (!s) return <p className="muted">Loading the paper account…</p>
  const a = s.account
  return (
    <div className="form">
      <p>
        Balance <b className="num">{formatMoney(a.total)}</b> (cash <span className="num">{formatMoney(a.cash)}</span>, {s.positions.length} open position
        {s.positions.length === 1 ? '' : 's'}) · started at <span className="num">{formatMoney(a.starting_balance)}</span>
        {a.reset_at && <span className="muted"> · last reset {new Date(a.reset_at).toLocaleDateString()}</span>}
      </p>
      <div className="actions">
        <button type="button" className="btn btn-danger" disabled={action.busy} onClick={() => void reset()}>Reset paper account</button>
      </div>
      <StatusLine status={action.status} />
    </div>
  )
}

export function PaperSection() {
  const me = useMe()
  const save = useSaveSettings()
  const p = me.settings.paper
  const [draft, setDraft] = useState(() => toDraft(p, PAPER_SPECS))
  const [fill, setFill] = useState(p.fill_rule)
  const action = useAction()

  function submit(e: FormEvent) {
    e.preventDefault()
    const nums = readNumbers(PAPER_SPECS, draft)
    if (typeof nums === 'string') return action.setStatus({ kind: 'error', text: nums })
    void action.run(() => save({ paper: { ...nums, fill_rule: fill } }), 'Paper account settings saved.')
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>Paper account</h2>
      <PaperBalance />
      <NumberFields specs={PAPER_SPECS} draft={draft} setDraft={setDraft} />
      <div className="field">
        <span>Fill rule</span>
        <Segmented
          label="Fill rule"
          value={fill}
          options={[
            { value: 'bid_ask', label: 'Buy at ask, sell at bid' },
            { value: 'mid', label: 'Middle price' },
          ]}
          onChange={setFill}
        />
        <span className="hint">“Buy at ask, sell at bid” is closer to real fills. “Middle price” is more optimistic.</span>
      </div>
      <StatusLine status={action.status} />
      <div className="actions">
        <button className="btn btn-primary" disabled={action.busy}>
          Save
        </button>
      </div>
    </form>
  )
}

// ---------- watchlist ----------

export function WatchlistSection() {
  const me = useMe()
  const save = useSaveSettings()
  const w = me.settings.watchlist
  const [symbols, setSymbols] = useState(w.symbols.join(', '))
  const [ticker, setTicker] = useState(w.default_ticker)
  const action = useAction()

  function submit(e: FormEvent) {
    e.preventDefault()
    const list = parseSymbols(symbols)
    const bad = list.filter((s) => !SYMBOL_RE.test(s))
    if (bad.length) return action.setStatus({ kind: 'error', text: `Not a valid symbol: ${bad.join(', ')}` })
    if (list.length > 50) return action.setStatus({ kind: 'error', text: 'Use at most 50 symbols.' })
    const t = ticker.trim().toUpperCase()
    if (!SYMBOL_RE.test(t)) return action.setStatus({ kind: 'error', text: 'Default ticker: not a valid symbol.' })
    void action.run(async () => {
      const s = await save({ watchlist: { symbols: list, default_ticker: t } })
      setSymbols(s.watchlist.symbols.join(', '))
      setTicker(s.watchlist.default_ticker)
    }, 'Watchlist saved.')
  }

  return (
    <form className="panel form form-narrow" onSubmit={submit}>
      <h2>Watchlist</h2>
      <Field label="Symbols" hint="Separate with commas or spaces. Shown on the Dashboard from Stage 2.">
        <textarea className="input num" spellCheck={false} value={symbols} onChange={(e) => setSymbols(e.target.value)} />
      </Field>
      <Field label="Default ticker" hint="The symbol charts open with.">
        <input className="input num" spellCheck={false} maxLength={10} value={ticker} onChange={(e) => setTicker(e.target.value)} />
      </Field>
      <StatusLine status={action.status} />
      <div className="actions">
        <button className="btn btn-primary" disabled={action.busy}>
          Save
        </button>
      </div>
    </form>
  )
}

import { useState, type FormEvent } from 'react'
import { api, type ClosedTrade, type CloseReason } from '../api'
import { useMe } from '../auth'
import { emptyContract, readContract, type ContractDraft } from '../capital/contract'
import ContractFields from '../capital/ContractFields'
import { REASONS } from './reasons'
import { Field, Segmented, StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { parseNumber } from '../forms'
import { changeClass, formatPct } from '../market/bars'
import { formatMoney, localInput, tradeResult } from '../money'


type Draft = { direction: 'long' | 'short'; quantity: string; entry: string; exit: string; fees: string; opened: string; closed: string; reason: CloseReason; notes: string }

function fromTrade(t: ClosedTrade, tz: string): [ContractDraft, Draft] {
  return [
    { kind: t.kind, symbol: t.symbol, option_type: t.option_type ?? 'call', strike: t.strike != null ? String(t.strike) : '', expiration: t.expiration ?? '' },
    { direction: t.direction, quantity: String(t.quantity), entry: String(t.entry_price), exit: String(t.exit_price), fees: t.fees ? String(t.fees) : '',
      opened: localInput(t.opened_at, tz), closed: localInput(t.closed_at, tz), reason: t.close_reason, notes: t.notes },
  ]
}

/** Typing in a finished real trade (or correcting one). Times are in the user's own time zone. */
export default function TradeEntry({ editing, onSaved, onCancel }: { editing?: ClosedTrade; onSaved: () => void; onCancel: () => void }) {
  const me = useMe()
  const tz = me.settings.display.timezone
  const [initContract, initDraft] = editing ? fromTrade(editing, tz) : [emptyContract(me.settings.watchlist.default_ticker), null]
  const [contract, setContract] = useState<ContractDraft>(initContract)
  const [d, setD] = useState<Draft>(() => {
    if (initDraft) return initDraft
    const now = localInput(new Date(), tz)
    return { direction: 'long', quantity: String(me.settings.trading.contracts_per_trade), entry: '', exit: '', fees: '', opened: now, closed: now, reason: 'manual', notes: '' }
  })
  const set = (c: Partial<Draft>) => setD({ ...d, ...c })
  const action = useAction()

  const q = parseNumber(d.quantity)
  const entry = parseNumber(d.entry)
  const exit = parseNumber(d.exit)
  const fees = d.fees.trim() ? parseNumber(d.fees) : 0
  const preview = q !== null && entry !== null && exit !== null && fees !== null ? tradeResult(d.direction, contract.kind, q, entry, exit, fees) : null

  function submit(e: FormEvent) {
    e.preventDefault()
    const c = readContract(contract, parseNumber)
    if (typeof c === 'string') return action.setStatus({ kind: 'error', text: c })
    if (q === null || q <= 0) return action.setStatus({ kind: 'error', text: 'Quantity: enter a number above 0.' })
    if (entry === null || entry < 0) return action.setStatus({ kind: 'error', text: 'Entry price: enter a price.' })
    if (exit === null || exit < 0) return action.setStatus({ kind: 'error', text: 'Exit price: enter a price.' })
    if (fees === null || fees < 0) return action.setStatus({ kind: 'error', text: 'Fees: enter an amount, or leave it empty.' })
    if (!d.opened || !d.closed) return action.setStatus({ kind: 'error', text: 'Enter when the trade opened and closed.' })
    const body = { ...c, direction: d.direction, quantity: q, entry_price: entry, exit_price: exit, fees, opened_at: d.opened, closed_at: d.closed, close_reason: d.reason, notes: d.notes }
    void action.run(async () => {
      await api(editing ? 'PUT' : 'POST', editing ? `/api/trades/${editing.id}` : '/api/trades', body)
      onSaved()
    })
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>{editing ? 'Edit trade' : 'Add a real trade'}</h2>
      {!editing && <p className="muted">For trades you made at your broker. Paper trades are added here by the site itself.</p>}
      <ContractFields value={contract} onChange={setContract} />
      <div className="field">
        <span>Direction</span>
        <Segmented label="Direction" value={d.direction} onChange={(direction) => set({ direction })}
          options={[{ value: 'long', label: 'Bought first (long)' }, { value: 'short', label: 'Sold first (short)' }]} />
      </div>
      <div className="grid-2">
        <Field label={contract.kind === 'option' ? 'Contracts' : 'Shares'}>
          <input className="input num" inputMode="decimal" value={d.quantity} onChange={(e) => set({ quantity: e.target.value })} />
        </Field>
        <Field label="Entry price" hint={contract.kind === 'option' ? 'As quoted: 3.00 means $300 a contract.' : undefined}>
          <input className="input num" inputMode="decimal" value={d.entry} onChange={(e) => set({ entry: e.target.value })} />
        </Field>
        <Field label="Exit price">
          <input className="input num" inputMode="decimal" value={d.exit} onChange={(e) => set({ exit: e.target.value })} />
        </Field>
        <Field label="Fees and commissions ($)" hint="Optional. Both sides together.">
          <input className="input num" inputMode="decimal" value={d.fees} onChange={(e) => set({ fees: e.target.value })} placeholder="0" />
        </Field>
        <Field label="Opened">
          <input className="input" type="datetime-local" value={d.opened} onChange={(e) => set({ opened: e.target.value })} />
        </Field>
        <Field label="Closed">
          <input className="input" type="datetime-local" value={d.closed} onChange={(e) => set({ closed: e.target.value })} />
        </Field>
        <Field label="How it closed">
          <select className="select" value={d.reason} onChange={(e) => set({ reason: e.target.value as CloseReason })}>
            {REASONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </Field>
      </div>
      <Field label="Notes (optional)">
        <textarea className="input" value={d.notes} maxLength={5000} onChange={(e) => set({ notes: e.target.value })} />
      </Field>
      {preview && (
        <p className="muted">
          Result: <b className={`num ${changeClass(preview.dollars)}`}>{formatMoney(preview.dollars, true)}</b>{' '}
          <span className={`num ${changeClass(preview.pct)}`}>({formatPct(preview.pct)})</span>
        </p>
      )}
      <div className="actions">
        <button className="btn btn-primary" disabled={action.busy}>{editing ? 'Save changes' : 'Add trade'}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </div>
      <StatusLine status={action.status} />
    </form>
  )
}

import { useState, type FormEvent } from 'react'
import { api, type CapitalSummary, type OpenPosition, type PositionTrade } from '../api'
import { useMe } from '../auth'
import { Field, Segmented, StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { parseNumber } from '../forms'
import { formatMoney, isoDay, purchaseTotal } from '../money'
import { emptyContract, readContract } from './contract'
import ContractFields from './ContractFields'

type Draft = { quantity: string; price: string; fees: string; day: string; note: string }

function useDraft(init?: Partial<Draft>) {
  const me = useMe()
  return useState<Draft>(() => ({ quantity: '', price: '', fees: '', day: isoDay(me.settings.display.timezone), note: '', ...init }))
}

function readDraft(d: Draft): Record<string, unknown> | string {
  const quantity = parseNumber(d.quantity)
  if (quantity === null || quantity <= 0) return 'Quantity: enter a number above 0.'
  const price = parseNumber(d.price)
  if (price === null || price < 0) return 'Price: enter a price (0 or more).'
  const fees = d.fees.trim() ? parseNumber(d.fees) : 0
  if (fees === null || fees < 0) return 'Fees: enter an amount (0 or more), or leave it empty.'
  if (!d.day) return 'Choose the date.'
  return { quantity, price, fees, day: d.day, note: d.note.trim() }
}

function TradeFields({ kind, draft, setDraft, sideLabel }: { kind: 'option' | 'stock'; draft: Draft; setDraft: (d: Draft) => void; sideLabel: string }) {
  const set = (c: Partial<Draft>) => setDraft({ ...draft, ...c })
  const q = parseNumber(draft.quantity)
  const p = parseNumber(draft.price)
  const f = draft.fees.trim() ? parseNumber(draft.fees) : 0
  const total = q !== null && p !== null && f !== null ? purchaseTotal(kind, q, p, 0) : null
  return (
    <>
      <div className="grid-2">
        <Field label={kind === 'option' ? 'Contracts' : 'Shares'}>
          <input className="input num" inputMode="decimal" value={draft.quantity} onChange={(e) => set({ quantity: e.target.value })} />
        </Field>
        <Field label={kind === 'option' ? 'Price per contract, as quoted' : 'Price per share'}
          hint={kind === 'option' ? 'Option prices are quoted per share: 5.20 means $520 for one contract.' : undefined}>
          <input className="input num" inputMode="decimal" value={draft.price} onChange={(e) => set({ price: e.target.value })} placeholder="5.20" />
        </Field>
        <Field label="Fees and commissions ($)" hint="Optional.">
          <input className="input num" inputMode="decimal" value={draft.fees} onChange={(e) => set({ fees: e.target.value })} placeholder="0" />
        </Field>
        <Field label={`Date ${sideLabel}`}>
          <input className="input" type="date" value={draft.day} onChange={(e) => set({ day: e.target.value })} />
        </Field>
        <Field label="Note (optional)">
          <input className="input" value={draft.note} maxLength={200} onChange={(e) => set({ note: e.target.value })} />
        </Field>
      </div>
      {total !== null && (
        <p className="muted">
          {sideLabel === 'sold' ? 'Proceeds' : 'Cost'}: <span className="num">{formatMoney(total)}</span>
          {f ? <> {sideLabel === 'sold' ? '−' : '+'} <span className="num">{formatMoney(f)}</span> fees = <b className="num">{formatMoney(sideLabel === 'sold' ? total - f : total + f)}</b></> : null}
        </p>
      )}
    </>
  )
}

/** The add-position form: what was bought, how many, at what price, and when. */
export function AddPosition({ onSaved, onCancel }: { onSaved: (s: CapitalSummary) => void; onCancel: () => void }) {
  const me = useMe()
  const [contract, setContract] = useState(() => emptyContract(me.settings.watchlist.default_ticker))
  const [draft, setDraft] = useDraft()
  const action = useAction()

  function submit(e: FormEvent) {
    e.preventDefault()
    const c = readContract(contract, parseNumber)
    if (typeof c === 'string') return action.setStatus({ kind: 'error', text: c })
    const t = readDraft(draft)
    if (typeof t === 'string') return action.setStatus({ kind: 'error', text: t })
    void action.run(async () => onSaved(await api<CapitalSummary>('POST', '/api/capital/positions', { ...c, ...t })))
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>Add a position</h2>
      <p className="muted">Buying more of something you already hold adds to it and updates its average cost.</p>
      <ContractFields value={contract} onChange={setContract} />
      <TradeFields kind={contract.kind} draft={draft} setDraft={setDraft} sideLabel="bought" />
      <div className="actions">
        <button className="btn btn-primary" disabled={action.busy}>Add position</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </div>
      <StatusLine status={action.status} />
    </form>
  )
}

/** Buy more or sell some of a held position, or edit one recorded buy or sell. */
export function TradeForm({ position, editing, onSaved, onCancel }: {
  position: OpenPosition | { id: number; kind: 'option' | 'stock'; quantity?: number; price?: number | null }
  editing?: PositionTrade
  onSaved: (s: CapitalSummary) => void
  onCancel: () => void
}) {
  const [side, setSide] = useState<'buy' | 'sell'>(editing?.side ?? 'sell')
  const [draft, setDraft] = useDraft(editing
    ? { quantity: String(editing.quantity), price: String(editing.price), fees: editing.fees ? String(editing.fees) : '', day: editing.day, note: editing.note }
    : { quantity: position.quantity ? String(position.quantity) : '', price: position.price != null ? String(position.price) : '' })
  const action = useAction()

  function submit(e: FormEvent) {
    e.preventDefault()
    const t = readDraft(draft)
    if (typeof t === 'string') return action.setStatus({ kind: 'error', text: t })
    void action.run(async () =>
      onSaved(await api<CapitalSummary>(editing ? 'PUT' : 'POST', editing ? `/api/capital/trades/${editing.id}` : `/api/capital/positions/${position.id}/trades`, { ...t, side })),
    )
  }

  return (
    <form className="form subform" onSubmit={submit}>
      <Segmented label="Buy or sell" value={side} onChange={setSide}
        options={[{ value: 'sell', label: 'Sell' }, { value: 'buy', label: 'Buy more' }]} />
      <TradeFields kind={position.kind} draft={draft} setDraft={setDraft} sideLabel={side === 'sell' ? 'sold' : 'bought'} />
      <div className="actions">
        <button className="btn btn-primary" disabled={action.busy}>{editing ? 'Save changes' : side === 'sell' ? 'Record sale' : 'Record purchase'}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </div>
      <StatusLine status={action.status} />
    </form>
  )
}

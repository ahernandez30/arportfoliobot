import { useState, type FormEvent } from 'react'
import { api, type Flow } from '../api'
import { useMe } from '../auth'
import { Field, Segmented, StatusLine } from '../config/common'
import { useAction } from '../config/hooks'
import { parseNumber } from '../forms'
import { formatDay, formatMoney, isoDay } from '../money'

/** Deposits and withdrawals for one account, recorded apart from gains. */
export default function FlowsPanel({ account, flows, onChanged }: { account: Flow['account']; flows: Flow[]; onChanged: () => void }) {
  const me = useMe()
  const [kind, setKind] = useState<Flow['kind']>('deposit')
  const [amount, setAmount] = useState('')
  const [day, setDay] = useState(() => isoDay(me.settings.display.timezone))
  const [note, setNote] = useState('')
  const action = useAction()

  function submit(e: FormEvent) {
    e.preventDefault()
    const n = parseNumber(amount)
    if (n === null || n <= 0) return action.setStatus({ kind: 'error', text: 'Amount: enter an amount above 0.' })
    void action.run(async () => {
      await api('POST', '/api/flows', { account, kind, amount: n, day, note: note.trim() })
      setAmount('')
      setNote('')
      onChanged()
    }, `${kind === 'deposit' ? 'Deposit' : 'Withdrawal'} recorded.`)
  }

  async function remove(f: Flow) {
    if (!window.confirm(`Delete the ${f.kind} of ${formatMoney(f.amount)} on ${formatDay(f.day)}?`)) return
    await action.run(async () => {
      await api('DELETE', `/api/flows/${f.id}`)
      onChanged()
    })
  }

  return (
    <div className="panel">
      <h2>Deposits and withdrawals</h2>
      <p className="muted">Money you put in or took out. Kept apart so it never counts as a gain or a loss.</p>
      <form className="form" onSubmit={submit}>
        <Segmented label="Deposit or withdrawal" value={kind} onChange={setKind}
          options={[{ value: 'deposit', label: 'Deposit' }, { value: 'withdrawal', label: 'Withdrawal' }]} />
        <div className="grid-2">
          <Field label="Amount ($)">
            <input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="10,000" />
          </Field>
          <Field label="Date">
            <input className="input" type="date" value={day} onChange={(e) => setDay(e.target.value)} />
          </Field>
          <Field label="Note (optional)">
            <input className="input" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
        <div className="actions">
          <button className="btn btn-primary" disabled={action.busy}>Record {kind}</button>
        </div>
        <StatusLine status={action.status} />
      </form>
      {flows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Date</th><th>Type</th><th className="right">Amount</th><th>Note</th><th /></tr>
            </thead>
            <tbody>
              {[...flows].reverse().map((f) => (
                <tr key={f.id}>
                  <td>{formatDay(f.day)}</td>
                  <td>{f.kind === 'deposit' ? 'Deposit' : 'Withdrawal'}</td>
                  <td className="num right">{f.kind === 'deposit' ? '+' : '−'}{formatMoney(f.amount)}</td>
                  <td className="muted">{f.note}</td>
                  <td className="right">
                    <button className="btn btn-small btn-danger" onClick={() => void remove(f)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

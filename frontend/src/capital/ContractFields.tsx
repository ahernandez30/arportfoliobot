import { Field, Segmented } from '../config/common'
import type { ContractDraft } from './contract'


/** Option or stock, symbol, and for an option: call or put, strike and expiration. */
export default function ContractFields({ value, onChange }: { value: ContractDraft; onChange: (v: ContractDraft) => void }) {
  const set = (c: Partial<ContractDraft>) => onChange({ ...value, ...c })
  return (
    <>
      <div className="actions">
        <Segmented label="Type" value={value.kind} onChange={(kind) => set({ kind })}
          options={[{ value: 'option', label: 'Option' }, { value: 'stock', label: 'Stock' }]} />
        {value.kind === 'option' && (
          <Segmented label="Call or put" value={value.option_type} onChange={(option_type) => set({ option_type })}
            options={[{ value: 'call', label: 'Call' }, { value: 'put', label: 'Put' }]} />
        )}
      </div>
      <div className="grid-2">
        <Field label="Symbol">
          <input className="input" value={value.symbol} autoCapitalize="characters" spellCheck={false}
            onChange={(e) => set({ symbol: e.target.value.toUpperCase() })} placeholder="TSLA" />
        </Field>
        {value.kind === 'option' && (
          <>
            <Field label="Strike">
              <input className="input num" inputMode="decimal" value={value.strike} onChange={(e) => set({ strike: e.target.value })} placeholder="450" />
            </Field>
            <Field label="Expiration">
              <input className="input" type="date" value={value.expiration} onChange={(e) => set({ expiration: e.target.value })} />
            </Field>
          </>
        )}
      </div>
    </>
  )
}

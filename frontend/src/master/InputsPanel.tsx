import { useState } from 'react'
import { TIMEFRAMES } from '../market/bars'
import { readInput } from './logic'
import type { InputDef, Inputs, InputValue } from './types'

const CHOICE_LABEL: Record<string, string> = { Ninguna: 'None', mayor: 'above the level', menor: 'below the level' }

function NumberInput({ def, value, onChange }: { def: InputDef; value: number; onChange: (v: number) => void }) {
  // The box keeps what is typed; the strategy only gets valid numbers.
  const [text, setText] = useState(String(value))
  const [shown, setShown] = useState(value)
  if (value !== shown) {
    setShown(value)
    setText(String(value))
  }
  const check = readInput(def, text)
  return (
    <>
      <input className={`input num input-small ${typeof check === 'string' ? 'input-bad' : ''}`} inputMode="decimal" value={text}
        aria-label={def.label}
        onChange={(e) => {
          setText(e.target.value)
          const n = readInput(def, e.target.value)
          if (typeof n === 'number') {
            setShown(n)
            onChange(n)
          }
        }} />
      {typeof check === 'string' && <span className="hint bad">{check}</span>}
    </>
  )
}

function TimeInput({ def, value, onChange }: { def: InputDef; value: string; onChange: (v: string) => void }) {
  const [text, setText] = useState(value)
  const [shown, setShown] = useState(value)
  if (value !== shown) {
    setShown(value)
    setText(value)
  }
  const ok = text === '' || /^([01]\d|2[0-3]):[0-5]\d$/.test(text)
  return (
    <>
      <input className={`input num input-small ${ok ? '' : 'input-bad'}`} value={text} placeholder="none" aria-label={def.label}
        onChange={(e) => {
          setText(e.target.value)
          if (e.target.value === '' || /^([01]\d|2[0-3]):[0-5]\d$/.test(e.target.value)) {
            setShown(e.target.value)
            onChange(e.target.value)
          }
        }} />
      {!ok && <span className="hint bad">HH:MM, e.g. 15:30</span>}
    </>
  )
}

function Field({ def, value, onChange, changed }: { def: InputDef; value: InputValue; onChange: (v: InputValue) => void; changed: boolean }) {
  if (def.kind === 'bool') {
    return (
      <label className={`check input-row ${changed ? 'changed' : ''}`} title={def.help}>
        <input type="checkbox" checked={value as boolean} onChange={(e) => onChange(e.target.checked)} />
        <span>{def.label}</span>
      </label>
    )
  }
  let control
  if (def.kind === 'float' || def.kind === 'int') {
    control = <NumberInput def={def} value={value as number} onChange={onChange} />
  } else if (def.kind === 'time') {
    control = <TimeInput def={def} value={value as string} onChange={onChange} />
  } else {
    const options = def.kind === 'timeframe' ? TIMEFRAMES : def.options
    control = (
      <select className="select select-small" value={value as string} aria-label={def.label} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o} value={o}>{CHOICE_LABEL[o] ?? o}</option>)}
      </select>
    )
  }
  return (
    <label className={`field input-row ${changed ? 'changed' : ''}`}>
      <span>{def.label}</span>
      {control}
      {def.help && <span className="hint">{def.help}</span>}
    </label>
  )
}

/** Every strategy input, grouped like the script's settings. Changes apply straight away. */
export default function InputsPanel({ defs, inputs, pegged, onChange }: {
  defs: InputDef[]
  inputs: Inputs
  pegged: Inputs | null
  onChange: (key: string, value: InputValue) => void
}) {
  const groups = [...new Set(defs.map((d) => d.group))]
  return (
    <div className="inputs-panel">
      {groups.map((g, gi) => (
        <details key={g} open={gi === 0}>
          <summary>{g}</summary>
          <div className="inputs-grid">
            {defs.filter((d) => d.group === g).map((d) => (
              <Field key={d.key} def={d} value={inputs[d.key]} changed={!!pegged && pegged[d.key] !== inputs[d.key]}
                onChange={(v) => onChange(d.key, v)} />
            ))}
          </div>
        </details>
      ))}
    </div>
  )
}

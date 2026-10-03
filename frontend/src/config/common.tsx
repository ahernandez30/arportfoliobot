import type { ReactNode } from 'react'
import type { Status } from './hooks'

export function StatusLine({ status }: { status: Status }) {
  if (!status) return null
  return (
    <p className={`msg ${status.kind === 'ok' ? 'msg-ok' : 'msg-error'}`} role={status.kind === 'error' ? 'alert' : 'status'}>
      {status.text}
    </p>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  )
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

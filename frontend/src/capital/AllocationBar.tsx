import type { OpenPosition } from '../api'
import { allocation } from '../money'
import { formatPct } from '../market/bars'

/** Where the capital sits: each position's share, the rest folded into Other, then cash. */
export default function AllocationBar({ positions, cash }: { positions: OpenPosition[]; cash: number }) {
  const { slices, total } = allocation(
    positions.map((p) => ({ key: String(p.id), label: p.label, value: p.value })),
    cash,
  )
  if (total <= 0) return <p className="muted">Nothing to show yet.</p>
  return (
    <div className="alloc">
      <div className="alloc-bar" role="img" aria-label="Allocation of capital">
        {slices.map((s, i) => (
          <span key={s.key} className={`alloc-seg ${slot(s.key, i)}`} style={{ flexGrow: s.value }} title={`${s.label}: ${formatPct((s.value / total) * 100).replace('+', '')}`} />
        ))}
      </div>
      <ul className="alloc-legend">
        {slices.map((s, i) => (
          <li key={s.key}>
            <span className={`swatch ${slot(s.key, i)}`} />
            <span>{s.label}</span>
            <span className="num muted">{formatPct((s.value / total) * 100).replace('+', '')}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function slot(key: string, i: number): string {
  if (key === 'cash') return 'series-cash'
  if (key === 'other') return 'series-other'
  return `series-${i + 1}`
}

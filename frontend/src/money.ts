/** Money and contract display helpers for Capital Tracking and Account Manager (pure, tested). */

const MINUS = '−'

/** "$1,234.56". With `signed`, gains get "+" and losses "−" (plan section 5: never color alone). */
export function formatMoney(value: number | null | undefined, signed = false): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const rounded = Math.round(value * 100) / 100
  const abs = Math.abs(rounded).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (rounded < 0) return `${MINUS}$${abs}`
  if (signed && rounded > 0) return `+$${abs}`
  return `$${abs}`
}

/** A quantity without needless decimals: 2, 0.5, 1.25. */
export function formatQty(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value.toLocaleString('en-US', { maximumFractionDigits: 4 })
}

/** An option or stock price as quoted, e.g. 5.2065 → "5.2065", 5.2 → "5.20". */
export function formatQuoted(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
}

/** Shares covered by one unit: an option contract is 100 shares. */
export function multiplier(kind: 'option' | 'stock'): number {
  return kind === 'option' ? 100 : 1
}

/** What a purchase costs in dollars, fees included (shown live in the form before saving). */
export function purchaseTotal(kind: 'option' | 'stock', quantity: number, price: number, fees: number): number {
  return quantity * price * multiplier(kind) + fees
}

/** A closed trade's result, as the server works it out (shown live in the form). */
export function tradeResult(
  direction: 'long' | 'short',
  kind: 'option' | 'stock',
  quantity: number,
  entry: number,
  exit: number,
  fees: number,
): { dollars: number; pct: number | null } {
  const m = multiplier(kind)
  const move = direction === 'long' ? exit - entry : entry - exit
  const dollars = move * quantity * m - fees
  const stake = entry * quantity * m
  return { dollars, pct: stake ? (dollars / stake) * 100 : null }
}

/** "YYYY-MM-DD" for a date in a time zone (today by default). */
export function isoDay(timeZone: string, at: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(at)
  } catch {
    return at.toISOString().slice(0, 10)
  }
}

/** "YYYY-MM-DDTHH:MM" in a time zone, for datetime-local inputs. */
export function localInput(iso: string | Date, timeZone: string): string {
  const d = typeof iso === 'string' ? new Date(iso) : iso
  const p: Record<string, string> = {}
  try {
    for (const part of new Intl.DateTimeFormat('en-CA', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(d)) p[part.type] = part.value
  } catch {
    return d.toISOString().slice(0, 16)
  }
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`
}

/** "Dec 18, 2026" from "2026-12-18", without time-zone drift. */
export function formatDay(day: string | null | undefined): string {
  if (!day) return '—'
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric' })
}

export type Slice = { key: string; label: string; value: number }

/**
 * Allocation bar pieces: the biggest `maxNamed` positions by value, the rest folded into
 * "Other", then cash. Negative values are left out (they cannot be drawn as a share).
 */
export function allocation(positions: Slice[], cash: number, maxNamed = 6): { slices: Slice[]; total: number } {
  const held = positions.filter((p) => p.value > 0).sort((a, b) => b.value - a.value)
  const named = held.slice(0, maxNamed)
  const rest = held.slice(maxNamed)
  const slices = [...named]
  if (rest.length) slices.push({ key: 'other', label: `Other (${rest.length})`, value: rest.reduce((s, p) => s + p.value, 0) })
  if (cash > 0) slices.push({ key: 'cash', label: 'Cash', value: cash })
  return { slices, total: slices.reduce((s, p) => s + p.value, 0) }
}

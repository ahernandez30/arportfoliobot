/**
 * Live candle updates: which candle a new trade belongs to.
 *
 * Intraday candles follow New York regular-session time (9:30–16:00). Hourly candles
 * start at 9:30, 10:30 … 15:30, matching the server and TradingView. Daily and weekly
 * candles are stamped at 00:00 UTC of their date, so the date reads the same everywhere.
 */

export type Timeframe = '1m' | '5m' | '15m' | '30m' | '1h' | '1D' | '1W'
export const TIMEFRAMES: Timeframe[] = ['1m', '5m', '15m', '30m', '1h', '1D', '1W']
export const INTRADAY: Timeframe[] = ['1m', '5m', '15m', '30m', '1h']
const MINUTES: Record<string, number> = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60 }

export type Bar = { time: number; open: number; high: number; low: number; close: number; volume: number }

/** A merged live update from the server (see app/feed.py). */
export type Tick = { last?: number; hi?: number; lo?: number; vol?: number; t?: number | null }

const nyFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: 'numeric',
  day: 'numeric',
  hour: 'numeric',
  minute: 'numeric',
  second: 'numeric',
  weekday: 'short',
  hourCycle: 'h23',
})
const WEEKDAYS: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }

export type NyParts = { year: number; month: number; day: number; hour: number; minute: number; second: number; weekday: number }

export function nyParts(epochSec: number): NyParts {
  const p: Record<string, string> = {}
  for (const part of nyFormat.formatToParts(new Date(epochSec * 1000))) p[part.type] = part.value
  return {
    year: Number(p.year),
    month: Number(p.month),
    day: Number(p.day),
    hour: Number(p.hour) % 24,
    minute: Number(p.minute),
    second: Number(p.second),
    weekday: WEEKDAYS[p.weekday] ?? 0,
  }
}

const OPEN_MIN = 9 * 60 + 30
const CLOSE_MIN = 16 * 60

export function inRegularSession(epochSec: number): boolean {
  const p = nyParts(epochSec)
  const m = p.hour * 60 + p.minute
  return p.weekday >= 1 && p.weekday <= 5 && m >= OPEN_MIN && m < CLOSE_MIN
}

function utcMidnight(year: number, month: number, day: number): number {
  return Date.UTC(year, month - 1, day) / 1000
}

/** Monday of the week holding a UTC-midnight date stamp. */
export function weekStart(dateStamp: number): number {
  const weekday = new Date(dateStamp * 1000).getUTCDay() // 0 = Sunday
  return dateStamp - ((weekday + 6) % 7) * 86400
}

/** The start of the candle a trade at `epochSec` belongs to, or null outside the regular session. */
export function bucketStart(epochSec: number, tf: Timeframe): number | null {
  if (!inRegularSession(epochSec)) return null
  const p = nyParts(epochSec)
  if (tf === '1D') return utcMidnight(p.year, p.month, p.day)
  if (tf === '1W') return weekStart(utcMidnight(p.year, p.month, p.day))
  const sinceOpen = p.hour * 60 + p.minute - OPEN_MIN
  const size = MINUTES[tf]
  return epochSec - ((sinceOpen % size) * 60 + p.second)
}

/** The candle period a bar or bucket belongs to (weekly bars may be stamped on any day of their week). */
function periodOf(time: number, tf: Timeframe): number {
  return tf === '1W' ? weekStart(time) : time
}

/**
 * Applies a live update to the newest candle. Returns the candle to draw (updated or new),
 * or null when the update does not belong on this chart (outside the session, or older).
 */
export function applyTick(last: Bar | undefined, tick: Tick, tf: Timeframe, nowSec = Date.now() / 1000): Bar | null {
  if (tick.last === undefined || tick.last === null) return null
  const at = tick.t ? tick.t / 1000 : nowSec
  const start = bucketStart(at, tf)
  if (start === null) return null
  const price = tick.last
  const hi = Math.max(price, tick.hi ?? price)
  const lo = Math.min(price, tick.lo ?? price)
  const vol = tick.vol ?? 0
  if (last) {
    const lastPeriod = periodOf(last.time, tf)
    const period = periodOf(start, tf)
    if (period < lastPeriod) return null
    if (period === lastPeriod) {
      return { ...last, high: Math.max(last.high, hi), low: Math.min(last.low, lo), close: price, volume: last.volume + vol }
    }
  }
  return { time: start, open: price, high: hi, low: lo, close: price, volume: vol }
}

/** Percent change, or null when either price is missing. */
export function pctChange(last: number | null | undefined, ref: number | null | undefined): number | null {
  if (last == null || ref == null || ref === 0) return null
  return ((last - ref) / ref) * 100
}

/** "+1.23%" / "−0.45%": always with a sign, never color alone (plan section 5). */
export function formatPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const rounded = Math.abs(value) < 0.005 ? 0 : value
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : ''
  return `${sign}${Math.abs(rounded).toFixed(2)}%`
}

export function formatPrice(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const digits = Math.abs(value) < 1 ? 4 : 2
  return value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function changeClass(value: number | null | undefined): string {
  if (value == null || Math.abs(value) < 0.005) return 'flat'
  return value > 0 ? 'gain' : 'loss'
}

type RefPrices = { '1W': number | null; '1M': number | null; '3M': number | null }

/** Watchlist changes, recomputed from the live last price against fixed reference closes. */
export function rowChanges(row: { last: number | null; prev_close: number | null; refs: RefPrices }) {
  return {
    '1D': pctChange(row.last, row.prev_close),
    '1W': pctChange(row.last, row.refs['1W']),
    '1M': pctChange(row.last, row.refs['1M']),
    '3M': pctChange(row.last, row.refs['3M']),
  }
}

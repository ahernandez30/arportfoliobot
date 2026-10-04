/** Capital-over-time chart data (pure, tested). */
import type { CapitalHistory } from '../api'

export type Point = { time: string; value: number }

/** Total capital per recorded day, with today's live value as the last point. */
export function totalSeries(history: CapitalHistory, today: string, liveTotal: number | null): Point[] {
  const byDay = new Map(history.snapshots.map((s) => [s.day, s.total]))
  if (liveTotal !== null) byDay.set(today, liveTotal)
  return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([time, value]) => ({ time, value }))
}

/** Money put in (deposits minus withdrawals) as a running total, stepping on each flow
 * and carried to every day the total line has, so the two lines can be compared. */
export function putInSeries(history: CapitalHistory, extraDays: string[]): Point[] {
  const change = new Map<string, number>()
  for (const f of history.flows) change.set(f.day, (change.get(f.day) ?? 0) + (f.kind === 'deposit' ? f.amount : -f.amount))
  if (!change.size) return []
  const first = [...change.keys()].sort()[0]
  const days = [...new Set([...change.keys(), ...extraDays.filter((d) => d >= first)])].sort()
  let running = 0
  return days.map((time) => {
    running += change.get(time) ?? 0
    return { time, value: Math.round(running * 100) / 100 }
  })
}

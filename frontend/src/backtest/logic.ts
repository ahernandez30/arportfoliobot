/** Backtest helpers (pure, tested). */
import type { ColumnKey } from './types'

export const COLUMN_LABEL: Record<ColumnKey, string> = {
  stock: 'Stock price', directional: 'Directional', credit_spread: 'Credit spread', debit_spread: 'Debit spread',
}

export const COLUMN_ORDER: ColumnKey[] = ['stock', 'directional', 'credit_spread', 'debit_spread']

/** One point per time for a chart line: when several trades close on the same candle, the last value wins. */
export function chartPoints(curve: { time: number; value: number }[]): { time: number; value: number }[] {
  const out: { time: number; value: number }[] = []
  for (const p of [...curve].sort((a, b) => a.time - b.time)) {
    if (out.length && out[out.length - 1].time === p.time) out[out.length - 1] = p
    else out.push(p)
  }
  return out
}

/** The script's exit reason names, in words. */
export function reasonWord(reason: string): string {
  return ({ TP: 'Target', SL: 'Stop', corte: 'Candle count', flot: 'Left floating', SIG: 'Opposite signal', HORA: 'Close time' } as Record<string, string>)[reason] ?? reason
}

/** Total of the option results of one structure for one strategy trade (a basket can hold several). */
export function optionTotal(rows: { pnl?: number }[] | undefined): number | null {
  if (!rows || !rows.some((r) => r.pnl != null)) return null
  return rows.reduce((s, r) => s + (r.pnl ?? 0), 0)
}

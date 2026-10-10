/** Backtest helpers (pure, tested). */
import type { ColumnKey, OptionSetup, TradeRow } from './types'

export const COLUMN_LABEL: Record<ColumnKey, string> = {
  stock: 'Stock price', directional: 'Long call/put', credit_spread: 'Credit spread', debit_spread: 'Debit spread',
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

export const DEFAULT_SETUP: OptionSetup = {
  structure: 'directional', strike_by: 'pct', strike_pct: 0, strike_delta: 0.5, width_usd: 10,
  expiry_mode: 'auto', margin_pct: 50, expiry_days: 35, risk_usd: 5000, commission: 0,
}

/** The setup in a few words, e.g. "Credit spread · sold strike 5% out of the money · $10 wide". */
export function setupText(o: OptionSetup): string {
  const placed = o.structure === 'credit_spread' ? 'sold strike' : 'strike'
  const where = o.strike_by === 'delta'
    ? `${placed} at ${o.strike_delta.toFixed(2)} delta`
    : o.strike_pct === 0 ? `${placed} at the money`
      : `${placed} ${Math.abs(o.strike_pct)}% ${o.strike_pct > 0 ? 'out of' : 'in'} the money`
  const parts = [COLUMN_LABEL[o.structure], where]
  if (o.structure !== 'directional') parts.push(`$${o.width_usd} wide`)
  parts.push(o.expiry_mode === 'fixed' ? `expiration ≥${o.expiry_days} days` : `expiration: average trade +${o.margin_pct}%`)
  parts.push(`risk $${o.risk_usd.toLocaleString('en-US')} per trade`)
  if (o.commission) parts.push(`$${o.commission} per contract`)
  return parts.join(' · ')
}

/** Every option transaction of a run, oldest first, one row per leg. */
export type LogRow = {
  position: number; time: number; side: 'Open' | 'Close'; action: string; contract: string; quantity: number
  price: number; bid: number | null; ask: number | null; underlying: number; net: number; fees: number; cash: number; first: boolean
}

export function transactionLog(trades: TradeRow[], structure: ColumnKey): LogRow[] {
  const positions = trades.flatMap((t) => t.options[structure] ?? []).filter((o) => o.transactions?.length)
  const out: LogRow[] = []
  positions.forEach((o, i) => o.transactions!.forEach((x, j) => x.legs.forEach((lg, k) => out.push({
    position: i + 1, time: x.time, side: j === 0 ? 'Open' : 'Close', action: lg.action, contract: lg.contract,
    quantity: lg.quantity, price: lg.price, bid: lg.bid, ask: lg.ask, underlying: x.underlying, net: x.net,
    fees: x.fees, cash: x.cash, first: k === 0,
  }))))
  return out.sort((a, b) => a.time - b.time || a.position - b.position)
}

function csvCell(v: string | number | null): string {
  if (v == null) return ''
  const s = String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** The log as CSV: one line per leg; the position's net price, fees and cash on its first leg's line. */
export function logCsv(rows: LogRow[], timeText: (t: number) => string): string {
  const head = ['Position', 'Time', 'Open/Close', 'Action', 'Contract', 'Quantity', 'Fill price', 'Bid', 'Ask', 'Stock price', 'Net price', 'Fees', 'Cash']
  const lines = rows.map((r) => [r.position, timeText(r.time), r.side, r.action, r.contract, r.quantity, r.price, r.bid, r.ask,
    r.first ? r.underlying : null, r.first ? r.net : null, r.first ? r.fees : null, r.first ? r.cash : null].map(csvCell).join(','))
  return [head.join(','), ...lines].join('\n') + '\n'
}

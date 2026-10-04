/** Order ticket arithmetic, mirroring the server's paper rules (pure, tested). */
import type { ChainRow, PaperPositionView } from '../api'

export type Pick = { symbol: string; option_type: 'call' | 'put'; strike: number; expiration: string; price: number | null }

function cents(x: number): number {
  return Math.round((x + Number.EPSILON) * 100) / 100
}

/** Option prices where the position takes profit and stops out, from the entry and the percents. */
export function exitPrices(entry: number, tpPct: number | null, slPct: number | null): { tp: number | null; sl: number | null } {
  return {
    tp: tpPct ? cents(entry * (1 + tpPct / 100)) : null,
    sl: slPct ? Math.max(0, cents(entry * (1 - slPct / 100))) : null,
  }
}

/** Dollars for a number of contracts at a quoted price (100 shares each). */
export function orderValue(quantity: number, price: number): number {
  return quantity * price * 100
}

/** Strikes to show: the `around` nearest the stock price on each side, or all. */
export function visibleRows(rows: ChainRow[], last: number | null, around: number, all: boolean): ChainRow[] {
  if (all || last == null || rows.length <= around * 2) return rows
  let atm = 0
  rows.forEach((r, i) => {
    if (Math.abs(r.strike - last) < Math.abs(rows[atm].strike - last)) atm = i
  })
  return rows.slice(Math.max(0, atm - around), atm + around + 1)
}

/** True when the option is in the money at the stock price (for shading the chain). */
export function inTheMoney(type: 'call' | 'put', strike: number, last: number | null): boolean {
  if (last == null) return false
  return type === 'call' ? strike < last : strike > last
}

/** The open position holding this exact contract, if any (for the Sell side of the ticket). */
export function heldPosition(positions: PaperPositionView[], pick: Pick | null): PaperPositionView | null {
  if (!pick) return null
  return positions.find((p) => p.symbol === pick.symbol && p.option_type === pick.option_type && p.strike === pick.strike && p.expiration === pick.expiration) ?? null
}

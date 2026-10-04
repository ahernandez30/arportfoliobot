/** The contract part of the position and trade forms (pure). */
import type { Kind, OptionType } from '../api'

export type ContractDraft = { kind: Kind; symbol: string; option_type: OptionType; strike: string; expiration: string }

export const emptyContract = (symbol = ''): ContractDraft => ({ kind: 'option', symbol, option_type: 'call', strike: '', expiration: '' })

/** The contract part of a request body, or a message for the first problem. */
export function readContract(c: ContractDraft, parse: (s: string) => number | null): Record<string, unknown> | string {
  const symbol = c.symbol.trim().toUpperCase()
  if (!symbol) return 'Enter a symbol.'
  if (c.kind === 'stock') return { kind: 'stock', symbol }
  const strike = parse(c.strike)
  if (strike === null || strike <= 0) return 'Strike: enter a price above 0.'
  if (!c.expiration) return 'Choose the expiration date.'
  return { kind: 'option', symbol, option_type: c.option_type, strike, expiration: c.expiration }
}

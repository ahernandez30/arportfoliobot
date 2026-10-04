import { describe, expect, it } from 'vitest'
import type { ChainRow } from '../api'
import { exitPrices, heldPosition, inTheMoney, orderValue, visibleRows } from './ticket'

describe('ticket math', () => {
  it('matches the server rounding for target and stop', () => {
    expect(exitPrices(2.3, 30, 20)).toEqual({ tp: 2.99, sl: 1.84 })
    expect(exitPrices(1, null, 100)).toEqual({ tp: null, sl: 0 })
    expect(exitPrices(1.05, 15, 10)).toEqual({ tp: 1.21, sl: 0.95 })
  })
  it('counts 100 shares per contract', () => {
    expect(orderValue(2, 2.3)).toBeCloseTo(460)
  })
})

describe('chain', () => {
  const rows: ChainRow[] = [590, 595, 600, 605, 610, 615, 620].map((strike) => ({ strike, call: null, put: null }))
  it('centers on the stock price', () => {
    expect(visibleRows(rows, 603, 1, false).map((r) => r.strike)).toEqual([600, 605, 610])
    expect(visibleRows(rows, 589, 1, false).map((r) => r.strike)).toEqual([590, 595])
    expect(visibleRows(rows, 603, 1, true)).toHaveLength(7)
  })
  it('shades in-the-money options', () => {
    expect(inTheMoney('call', 600, 603)).toBe(true)
    expect(inTheMoney('put', 600, 603)).toBe(false)
    expect(inTheMoney('put', 605, 603)).toBe(true)
  })
  it('finds a held contract', () => {
    const pos = { symbol: 'SPY', option_type: 'call', strike: 600, expiration: '2026-11-20' } as never
    expect(heldPosition([pos], { symbol: 'SPY', option_type: 'call', strike: 600, expiration: '2026-11-20', price: 1 })).toBe(pos)
    expect(heldPosition([pos], { symbol: 'SPY', option_type: 'put', strike: 600, expiration: '2026-11-20', price: 1 })).toBeNull()
  })
})

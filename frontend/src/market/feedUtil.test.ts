import { describe, expect, it } from 'vitest'
import { rowChanges } from './bars'
import { backoffSeconds, wsUrl } from './feedUtil'

describe('feed helpers', () => {
  it('uses wss on https', () => {
    expect(wsUrl({ protocol: 'https:', host: 'arportfoliobot.com' } as Location)).toBe('wss://arportfoliobot.com/ws')
    expect(wsUrl({ protocol: 'http:', host: 'localhost:5173' } as Location)).toBe('ws://localhost:5173/ws')
  })
  it('backs off up to 30 seconds', () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(backoffSeconds)).toEqual([1, 2, 4, 8, 16, 30, 30])
  })
})

describe('watchlist changes', () => {
  it('recompute from the live price', () => {
    const ch = rowChanges({ last: 110, prev_close: 100, refs: { '1W': 55, '1M': null, '3M': 220 } })
    expect(ch['1D']).toBeCloseTo(10)
    expect(ch['1W']).toBeCloseTo(100)
    expect(ch['1M']).toBeNull()
    expect(ch['3M']).toBeCloseTo(-50)
  })
})

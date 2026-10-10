import { describe, expect, it } from 'vitest'
import { chartPoints, DEFAULT_SETUP, logCsv, optionTotal, reasonWord, setupText, transactionLog } from './logic'
import type { TradeRow } from './types'

describe('backtest helpers', () => {
  it('keeps one point per time, the last one, in time order', () => {
    expect(chartPoints([{ time: 5, value: 1 }, { time: 3, value: 2 }, { time: 5, value: 9 }])).toEqual([
      { time: 3, value: 2 }, { time: 5, value: 9 },
    ])
  })
  it('names exit reasons', () => {
    expect(reasonWord('TP')).toBe('Target')
    expect(reasonWord('flot')).toBe('Left floating')
  })
  it('adds up the option results of a basket, or none', () => {
    expect(optionTotal([{ pnl: 10 }, { pnl: -4 }, {}])).toBe(6)
    expect(optionTotal([{}])).toBeNull()
    expect(optionTotal(undefined)).toBeNull()
  })
  it('describes a setup', () => {
    expect(setupText(DEFAULT_SETUP)).toBe('Long call/put · strike at the money · expiration: average trade +50% · risk $5,000 per trade')
    expect(setupText({ ...DEFAULT_SETUP, structure: 'credit_spread', strike_pct: 5, expiry_mode: 'fixed', commission: 0.65 }))
      .toBe('Credit spread · sold strike 5% out of the money · $10 wide · expiration ≥35 days · risk $5,000 per trade · $0.65 per contract')
    expect(setupText({ ...DEFAULT_SETUP, strike_by: 'delta', strike_delta: 0.3 })).toContain('strike at 0.30 delta')
    expect(setupText({ ...DEFAULT_SETUP, strike_pct: -3 })).toContain('3% in the money')
  })
  it('lists every leg of every transaction in time order, and writes CSV', () => {
    const leg = (action: string, contract: string, price: number) => ({ action, contract, quantity: 2, price, bid: price, ask: price + 0.1 })
    const trade = (t0: number, t1: number) => ({
      options: { credit_spread: [{ entry_time: t0, dir: 1, transactions: [
        { time: t0, underlying: 100, legs: [leg('Sell to open', 'TSLA Nov 15 2026 95 put', 1.2), leg('Buy to open', 'TSLA Nov 15 2026 85 put', 0.4)], net: 0.8, fees: 0, cash: 160 },
        { time: t1, underlying: 104, legs: [leg('Buy to close', 'TSLA Nov 15 2026 95 put', 0.5), leg('Sell to close', 'TSLA Nov 15 2026 85 put', 0.1)], net: 0.4, fees: 0, cash: -80 },
      ] }, { entry_time: t0, dir: 1, problem: 'none' }] },
    }) as unknown as TradeRow
    const rows = transactionLog([trade(10, 30), trade(20, 40)], 'credit_spread')
    expect(rows.map((r) => [r.position, r.time, r.first])).toEqual([[1, 10, true], [1, 10, false], [2, 20, true], [2, 20, false], [1, 30, true], [1, 30, false], [2, 40, true], [2, 40, false]])
    const csv = logCsv(rows.slice(0, 2), (t) => `t${t}`).split('\n')
    expect(csv[1]).toBe('1,t10,Open,Sell to open,TSLA Nov 15 2026 95 put,2,1.2,1.2,1.3,100,0.8,0,160')
    expect(csv[2]).toBe('1,t10,Open,Buy to open,TSLA Nov 15 2026 85 put,2,0.4,0.4,0.5,,,,')
  })
})

import { describe, expect, it } from 'vitest'
import { chartPoints, optionTotal, reasonWord } from './logic'

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
})

import { describe, expect, it } from 'vitest'
import { changedKeys, defaults, guessFromFilename, markers, presetFor, readInput } from './logic'
import type { InputDef, RunResult } from './types'

const def = (k: Partial<InputDef>): InputDef => ({ key: 'x', label: 'X', kind: 'float', default: 1, group: 'g', min: 0, max: 100, options: [], help: '', ...k })

describe('inputs', () => {
  it('finds changes and defaults', () => {
    expect(changedKeys({ a: 1, b: true }, { a: 2, b: true })).toEqual(['a'])
    expect(defaults([def({ key: 'a', default: 85 }), def({ key: 'b', kind: 'bool', default: false })])).toEqual({ a: 85, b: false })
  })
  it('checks typed numbers', () => {
    expect(readInput(def({}), '85')).toBe(85)
    expect(readInput(def({}), '85,5')).toBe(85.5)
    expect(readInput(def({}), '120')).toBe('At most 100.')
    expect(readInput(def({ kind: 'int' }), '2.5')).toBe('Enter a whole number.')
    expect(readInput(def({}), '')).toBe('Enter a number.')
  })
  it('finds the pegged set for a symbol and timeframe', () => {
    const p = [{ id: 1, strategy: 's', symbol: 'TSLA', timeframe: '1D' as const, inputs: {}, pegged_at: '' }]
    expect(presetFor(p, 'TSLA', '1D')?.id).toBe(1)
    expect(presetFor(p, 'SPY', '1D')).toBeNull()
    expect(presetFor(p, 'TSLA', '1W')).toBeNull()
  })
})

describe('TradingView file names', () => {
  it('reads symbol and timeframe', () => {
    expect(guessFromFilename('NASDAQ_TSLA, 1D_ab12.csv')).toEqual({ symbol: 'TSLA', timeframe: '1D' })
    expect(guessFromFilename('AMEX_SPY, 1W.csv')).toEqual({ symbol: 'SPY', timeframe: '1W' })
    expect(guessFromFilename('NASDAQ_QQQ, 60.csv')).toEqual({ symbol: 'QQQ', timeframe: '1h' })
    expect(guessFromFilename('data.csv')).toEqual({ symbol: null, timeframe: null })
  })
})

describe('markers', () => {
  const r = {
    signals: [{ i: 2, time: 300, dir: -1, type: 'FLECO', price: 1, body_pct: 5, wick_pct: 70 }, { i: 1, time: 200, dir: 1, type: 'LLENA', price: 1, body_pct: 90, wick_pct: 5 }],
    preview: { i: 3, time: 400, dir: 1, type: 'ENGULFING', price: 1, body_pct: 70, wick_pct: 10 },
    trades: [{ exit_time: 300, reason: 'TP', ret_pct: 15 }],
    blocked: [{ time: 250, dir: 1, preview: false }],
  } as unknown as RunResult
  const colors = { buy: '#5B9DFF', sell: '#F5B83D', gain: 'g', loss: 'l', muted: 'm' }
  it('BUY blue below, SELL amber above, in time order', () => {
    const m = markers(r, colors, { exits: false, blocked: false })
    expect(m.map((x) => x.time)).toEqual([200, 300, 400])
    expect(m[0]).toMatchObject({ position: 'belowBar', color: '#5B9DFF', text: 'BUY LL' })
    expect(m[1]).toMatchObject({ position: 'aboveBar', color: '#F5B83D', text: 'SELL FL' })
    expect(m[2].text).toContain('forming')
  })
  it('adds exits and blocked signals when asked', () => {
    expect(markers(r, colors, { exits: true, blocked: true })).toHaveLength(5)
  })
})

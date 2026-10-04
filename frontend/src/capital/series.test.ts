import { describe, expect, it } from 'vitest'
import type { CapitalHistory } from '../api'
import { putInSeries, totalSeries } from './series'

const flow = (day: string, kind: 'deposit' | 'withdrawal', amount: number) =>
  ({ id: 0, account: 'long_term' as const, kind, amount, day, note: '' })

const history: CapitalHistory = {
  snapshots: [
    { day: '2026-10-02', total: 10300, put_in: 10000, estimated: false },
    { day: '2026-10-01', total: 10100, put_in: 10000, estimated: false },
  ],
  flows: [flow('2026-08-01', 'deposit', 10000), flow('2026-10-05', 'withdrawal', 500), flow('2026-10-05', 'deposit', 100)],
}

describe('capital chart series', () => {
  it('orders recorded days and adds today live', () => {
    expect(totalSeries(history, '2026-10-05', 9950).map((p) => p.time)).toEqual(['2026-10-01', '2026-10-02', '2026-10-05'])
    expect(totalSeries(history, '2026-10-02', 10400).at(-1)).toEqual({ time: '2026-10-02', value: 10400 })
  })
  it('steps the money put in and carries it to every chart day', () => {
    const s = putInSeries(history, ['2026-10-01', '2026-10-02', '2026-10-05'])
    expect(s).toEqual([
      { time: '2026-08-01', value: 10000 },
      { time: '2026-10-01', value: 10000 },
      { time: '2026-10-02', value: 10000 },
      { time: '2026-10-05', value: 9600 },
    ])
    expect(putInSeries({ snapshots: [], flows: [] }, ['2026-10-01'])).toEqual([])
  })
})

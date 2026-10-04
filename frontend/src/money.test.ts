import { describe, expect, it } from 'vitest'
import { allocation, formatDay, formatMoney, formatQty, formatQuoted, isoDay, localInput, purchaseTotal, tradeResult } from './money'

describe('formatMoney', () => {
  it('shows sign only when asked, minus always', () => {
    expect(formatMoney(1234.5)).toBe('$1,234.50')
    expect(formatMoney(1234.5, true)).toBe('+$1,234.50')
    expect(formatMoney(-0.65)).toBe('−$0.65')
    expect(formatMoney(-0.001, true)).toBe('$0.00')
    expect(formatMoney(null)).toBe('—')
  })
})

describe('quantities and prices', () => {
  it('trims decimals', () => {
    expect(formatQty(2)).toBe('2')
    expect(formatQty(0.5)).toBe('0.5')
    expect(formatQuoted(5.2)).toBe('5.20')
    expect(formatQuoted(5.2065)).toBe('5.2065')
  })
})

describe('money math matches the server', () => {
  it('purchase total counts 100 shares per contract', () => {
    expect(purchaseTotal('option', 2, 5.2, 1.3)).toBeCloseTo(1041.3)
    expect(purchaseTotal('stock', 10, 550, 0)).toBe(5500)
  })
  it('trade result for long and short', () => {
    const r = tradeResult('long', 'option', 2, 3, 3.45, 1.3)
    expect(r.dollars).toBeCloseTo(88.7)
    expect(r.pct).toBeCloseTo(14.783, 2)
    expect(tradeResult('short', 'stock', 100, 50, 48, 0).dollars).toBe(200)
    expect(tradeResult('long', 'option', 1, 0, 1, 0).pct).toBeNull()
  })
})

describe('dates', () => {
  it('reads days in the right zone', () => {
    const lateNy = new Date('2026-10-07T02:00:00Z') // still Oct 6 in New York
    expect(isoDay('America/New_York', lateNy)).toBe('2026-10-06')
    expect(localInput('2026-10-01T14:05:00Z', 'America/New_York')).toBe('2026-10-01T10:05')
    expect(formatDay('2026-12-18')).toBe('Dec 18, 2026')
  })
})

describe('allocation', () => {
  it('folds small positions into Other and adds cash', () => {
    const pos = Array.from({ length: 8 }, (_, i) => ({ key: `p${i}`, label: `P${i}`, value: (i + 1) * 100 }))
    const { slices, total } = allocation(pos, 500)
    expect(slices.map((s) => s.key)).toEqual(['p7', 'p6', 'p5', 'p4', 'p3', 'p2', 'other', 'cash'])
    expect(slices[6].value).toBe(300)
    expect(total).toBe(3600 + 500)
  })
  it('leaves out negative cash', () => {
    expect(allocation([{ key: 'a', label: 'A', value: 10 }], -50).slices.map((s) => s.key)).toEqual(['a'])
  })
})

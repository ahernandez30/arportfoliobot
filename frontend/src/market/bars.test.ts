import { describe, expect, it } from 'vitest'
import { applyTick, bucketStart, changeClass, formatPct, formatPrice, inRegularSession, nyParts, pctChange, weekStart, type Bar } from './bars'

/** Unix seconds for a New York wall time (EDT is UTC-4, EST is UTC-5). */
const edt = (iso: string) => Date.parse(`${iso}-04:00`) / 1000
const est = (iso: string) => Date.parse(`${iso}-05:00`) / 1000
const utcDate = (iso: string) => Date.parse(`${iso}T00:00:00Z`) / 1000

describe('New York time', () => {
  it('reads wall-clock parts', () => {
    expect(nyParts(edt('2026-10-02T09:30:15'))).toMatchObject({ hour: 9, minute: 30, second: 15, weekday: 5, day: 2 })
    expect(nyParts(est('2026-12-02T15:59:00'))).toMatchObject({ hour: 15, minute: 59 })
  })
  it('knows the regular session', () => {
    expect(inRegularSession(edt('2026-10-02T09:29:59'))).toBe(false)
    expect(inRegularSession(edt('2026-10-02T09:30:00'))).toBe(true)
    expect(inRegularSession(edt('2026-10-02T16:00:00'))).toBe(false)
    expect(inRegularSession(edt('2026-10-03T11:00:00'))).toBe(false) // Saturday
  })
})

describe('bucketStart', () => {
  it.each([
    ['1m', '2026-10-02T09:31:42', '2026-10-02T09:31:00'],
    ['5m', '2026-10-02T09:34:59', '2026-10-02T09:30:00'],
    ['15m', '2026-10-02T10:01:00', '2026-10-02T10:00:00'],
    ['1h', '2026-10-02T10:29:59', '2026-10-02T09:30:00'],
    ['1h', '2026-10-02T15:45:00', '2026-10-02T15:30:00'],
  ] as const)('%s at %s starts %s', (tf, at, start) => {
    expect(bucketStart(edt(at), tf)).toBe(edt(start))
  })
  it('works after clocks fall back', () => {
    expect(bucketStart(est('2026-11-02T10:45:00'), '1h')).toBe(est('2026-11-02T10:30:00'))
  })
  it('daily and weekly use the New York date at 00:00 UTC', () => {
    // 15:00 New York on Friday Oct 2 is still Oct 2.
    expect(bucketStart(edt('2026-10-02T15:00:00'), '1D')).toBe(utcDate('2026-10-02'))
    expect(bucketStart(edt('2026-10-02T15:00:00'), '1W')).toBe(utcDate('2026-09-28'))
  })
  it('ignores trades outside the session', () => {
    expect(bucketStart(edt('2026-10-02T17:00:00'), '1D')).toBeNull()
  })
})

describe('weekStart', () => {
  it('finds Monday', () => {
    expect(weekStart(utcDate('2026-10-02'))).toBe(utcDate('2026-09-28'))
    expect(weekStart(utcDate('2026-09-28'))).toBe(utcDate('2026-09-28'))
    expect(weekStart(utcDate('2026-10-04'))).toBe(utcDate('2026-09-28')) // Sunday belongs to the week before
  })
})

describe('applyTick', () => {
  const bar: Bar = { time: edt('2026-10-02T10:00:00'), open: 100, high: 101, low: 99, close: 100.5, volume: 1000 }

  it('updates the current candle with the range since the last update', () => {
    const out = applyTick(bar, { last: 100.2, hi: 102, lo: 98.5, vol: 50, t: edt('2026-10-02T10:03:00') * 1000 }, '5m')
    expect(out).toEqual({ ...bar, high: 102, low: 98.5, close: 100.2, volume: 1050 })
  })
  it('starts a new candle', () => {
    const out = applyTick(bar, { last: 103, vol: 10, t: edt('2026-10-02T10:05:01') * 1000 }, '5m')
    expect(out).toEqual({ time: edt('2026-10-02T10:05:00'), open: 103, high: 103, low: 103, close: 103, volume: 10 })
  })
  it('ignores older and out-of-session updates', () => {
    expect(applyTick(bar, { last: 1, t: edt('2026-10-02T09:55:00') * 1000 }, '5m')).toBeNull()
    expect(applyTick(bar, { last: 1, t: edt('2026-10-02T18:00:00') * 1000 }, '5m')).toBeNull()
    expect(applyTick(bar, { bid: 1 } as never, '5m')).toBeNull()
  })
  it('updates a weekly candle stamped on another day of the same week', () => {
    const weekly: Bar = { time: utcDate('2026-09-29'), open: 1, high: 2, low: 1, close: 2, volume: 0 } // a Tuesday stamp
    const out = applyTick(weekly, { last: 3, t: edt('2026-10-02T11:00:00') * 1000 }, '1W')
    expect(out?.time).toBe(utcDate('2026-09-29'))
    expect(out?.high).toBe(3)
  })
  it('uses the current time when the update has none', () => {
    const now = edt('2026-10-02T10:01:00')
    expect(applyTick(bar, { last: 100 }, '15m', now)?.time).toBe(bar.time)
  })
})

describe('formatting', () => {
  it('always shows a sign', () => {
    expect(formatPct(1.234)).toBe('+1.23%')
    expect(formatPct(-0.456)).toBe('−0.46%')
    expect(formatPct(0.001)).toBe('0.00%')
    expect(formatPct(null)).toBe('—')
  })
  it('prices', () => {
    expect(formatPrice(1234.5)).toBe('1,234.50')
    expect(formatPrice(0.0512)).toBe('0.0512')
    expect(formatPrice(undefined)).toBe('—')
  })
  it('change math and colour class', () => {
    expect(pctChange(110, 100)).toBeCloseTo(10)
    expect(pctChange(null, 100)).toBeNull()
    expect(changeClass(1)).toBe('gain')
    expect(changeClass(-1)).toBe('loss')
    expect(changeClass(0)).toBe('flat')
  })
})

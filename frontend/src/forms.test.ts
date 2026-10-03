import { describe, expect, it } from 'vitest'
import {
  describeDevice,
  emailProblem,
  formatDateTime,
  newPasswordProblem,
  parseNumber,
  parseSymbols,
  readNumbers,
  type NumberSpec,
} from './forms'

describe('emailProblem', () => {
  it('requires an email', () => expect(emailProblem('  ')).toMatch(/Enter/))
  it('rejects a malformed email', () => expect(emailProblem('rafa')).toMatch(/does not look right/))
  it('accepts a normal email with spaces around it', () => expect(emailProblem(' rafa@example.com ')).toBeNull())
})

describe('newPasswordProblem', () => {
  it('needs 12 characters', () => expect(newPasswordProblem('short', 'short')).toMatch(/12/))
  it('rejects repetitive passwords', () => expect(newPasswordProblem('aaaaaaaaaaaa', 'aaaaaaaaaaaa')).toMatch(/repetitive/))
  it('needs both copies to match', () =>
    expect(newPasswordProblem('four words in a row', 'four words in a rov')).toMatch(/do not match/))
  it('accepts a good password', () => expect(newPasswordProblem('four words in a row', 'four words in a row')).toBeNull())
})

describe('parseNumber', () => {
  it.each([
    ['15', 15],
    [' 12.5 ', 12.5],
    ['$1,000', 1000],
    ['20%', 20],
    ['.5', 0.5],
    ['-3', -3],
  ])('reads %s', (text, n) => expect(parseNumber(text)).toBe(n))
  it.each(['', 'abc', '1.2.3', '1e5', '--1'])('refuses %s', (text) => expect(parseNumber(text)).toBeNull())
})

describe('parseSymbols', () => {
  it('cleans, upper-cases and removes repeats', () =>
    expect(parseSymbols(' tsla, qqq  spy;TSLA\nbrk.b ')).toEqual(['TSLA', 'QQQ', 'SPY', 'BRK.B']))
  it('handles empty text', () => expect(parseSymbols('  ')).toEqual([]))
})

describe('readNumbers', () => {
  const specs: NumberSpec[] = [
    { key: 'sl', label: 'Stop loss %', min: 0, max: 100, minExclusive: true },
    { key: 'n', label: 'Contracts', min: 1, max: 10, whole: true },
  ]
  it('returns numbers when all are in range', () => expect(readNumbers(specs, { sl: '13', n: '2' })).toEqual({ sl: 13, n: 2 }))
  it('explains a missing number', () => expect(readNumbers(specs, { sl: '', n: '2' })).toBe('Stop loss %: enter a number.'))
  it('explains an exclusive minimum', () => expect(readNumbers(specs, { sl: '0', n: '2' })).toBe('Stop loss %: must be more than 0.'))
  it('explains a maximum', () => expect(readNumbers(specs, { sl: '101', n: '2' })).toBe('Stop loss %: must be at most 100.'))
  it('needs whole numbers where required', () => expect(readNumbers(specs, { sl: '5', n: '1.5' })).toBe('Contracts: enter a whole number.'))
  it('explains an inclusive minimum', () => expect(readNumbers(specs, { sl: '5', n: '0' })).toBe('Contracts: must be at least 1.'))
})

describe('formatDateTime', () => {
  it('shows the time in the chosen zone', () => {
    const iso = '2026-10-03T14:30:00+00:00'
    expect(formatDateTime(iso, 'America/New_York')).toContain('10:30')
    expect(formatDateTime(iso, 'UTC')).toContain('2:30')
  })
  it('shows a dash for no date', () => expect(formatDateTime(null, 'UTC')).toBe('—'))
  it('survives an unknown zone', () => expect(formatDateTime('2026-10-03T14:30:00Z', 'Mars/Base')).toMatch(/2026/))
})

describe('describeDevice', () => {
  it('names common browsers', () => {
    expect(describeDevice('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit Version/18.0 Mobile Safari/604.1')).toBe('Safari on iPhone/iPad')
    expect(describeDevice('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36')).toBe('Chrome on Windows')
    expect(describeDevice('')).toBe('Browser on unknown device')
  })
})

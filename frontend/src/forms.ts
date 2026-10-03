/** Checks done in the browser before sending. The server checks again with the same rules. */

export const MIN_PASSWORD = 12

export function emailProblem(email: string): string | null {
  const e = email.trim()
  if (!e) return 'Enter your email.'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)) return 'That email address does not look right.'
  return null
}

export function newPasswordProblem(password: string, repeat: string): string | null {
  if (password.length < MIN_PASSWORD) return `Use at least ${MIN_PASSWORD} characters.`
  if (new Set(password).size < 4) return 'Use a less repetitive password.'
  if (password !== repeat) return 'The two passwords do not match.'
  return null
}

/** A number typed into a form, or null if it is not a number. Accepts "1,000" and "$1,000". */
export function parseNumber(text: string): number | null {
  const cleaned = text.trim().replace(/[$,%\s]/g, '')
  if (!cleaned || !/^-?\d*\.?\d+$/.test(cleaned)) return null
  return Number(cleaned)
}

/** Watchlist text ("tsla, qqq spy") into clean upper-case symbols, without repeats. */
export function parseSymbols(text: string): string[] {
  const out: string[] = []
  for (const raw of text.split(/[\s,;]+/)) {
    const s = raw.trim().toUpperCase()
    if (s && !out.includes(s)) out.push(s)
  }
  return out
}

export const SYMBOL_RE = /^[A-Z][A-Z0-9.-]{0,9}$/

export function formatDateTime(iso: string | null, timeZone: string): string {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone,
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(iso))
  } catch {
    return new Date(iso).toLocaleString()
  }
}

/** A short device description from a browser's user-agent text. */
export function describeDevice(userAgent: string): string {
  const ua = userAgent || ''
  const browser = /Edg\//.test(ua)
    ? 'Edge'
    : /Chrome\//.test(ua)
      ? 'Chrome'
      : /Firefox\//.test(ua)
        ? 'Firefox'
        : /Safari\//.test(ua)
          ? 'Safari'
          : 'Browser'
  const os = /iPhone|iPad/.test(ua)
    ? 'iPhone/iPad'
    : /Android/.test(ua)
      ? 'Android'
      : /Mac OS X/.test(ua)
        ? 'Mac'
        : /Windows/.test(ua)
          ? 'Windows'
          : /Linux/.test(ua)
            ? 'Linux'
            : 'unknown device'
  return `${browser} on ${os}`
}

export type NumberSpec = {
  key: string
  label: string
  hint?: string
  min: number
  max: number
  minExclusive?: boolean
  whole?: boolean
}

/** Checks typed numbers against their ranges. Returns the numbers, or a message for the first problem. */
export function readNumbers(specs: NumberSpec[], draft: Record<string, string>): Record<string, number> | string {
  const out: Record<string, number> = {}
  for (const s of specs) {
    const n = parseNumber(draft[s.key] ?? '')
    if (n === null) return `${s.label}: enter a number.`
    if (s.whole && !Number.isInteger(n)) return `${s.label}: enter a whole number.`
    if (s.minExclusive ? n <= s.min : n < s.min) return `${s.label}: must be ${s.minExclusive ? 'more than' : 'at least'} ${s.min.toLocaleString()}.`
    if (n > s.max) return `${s.label}: must be at most ${s.max.toLocaleString()}.`
    out[s.key] = n
  }
  return out
}

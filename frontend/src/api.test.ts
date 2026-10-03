import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, errorMessage } from './api'

afterEach(() => vi.unstubAllGlobals())

function stubFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('errorMessage', () => {
  it('uses the server message when there is one', () => expect(errorMessage(422, { detail: 'Stop loss %: too big.' })).toBe('Stop loss %: too big.'))
  it('falls back to plain English', () => {
    expect(errorMessage(401, null)).toBe('Please sign in.')
    expect(errorMessage(429, {})).toMatch(/Too many/)
    expect(errorMessage(502, null)).toMatch(/server had a problem/)
    // A list of raw validation errors is not shown as-is.
    expect(errorMessage(422, { detail: [{ msg: 'x' }] })).toBe('Something went wrong. Try again.')
  })
})

describe('api', () => {
  it('sends JSON with the cookie and returns the body', async () => {
    const fetchFn = stubFetch(200, { status: 'ok' })
    await expect(api('POST', '/api/auth/login', { email: 'a@b.co' })).resolves.toEqual({ status: 'ok' })
    const [path, init] = fetchFn.mock.calls[0]
    expect(path).toBe('/api/auth/login')
    expect(init.credentials).toBe('same-origin')
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(JSON.parse(init.body)).toEqual({ email: 'a@b.co' })
  })

  it('throws ApiError with the message on failure', async () => {
    stubFetch(401, { detail: 'Wrong email or password.' })
    const err = (await api('POST', '/api/auth/login', {}).catch((e) => e)) as ApiError
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(401)
    expect(err.message).toBe('Wrong email or password.')
  })

  it('explains a network failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const err = (await api('GET', '/api/me').catch((e) => e)) as ApiError
    expect(err.status).toBe(0)
    expect(err.message).toMatch(/Cannot reach the server/)
  })
})

/** Calls to the backend. Every error comes back as an ApiError with a message fit for the screen. */

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export function errorMessage(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string' && detail) return detail
  if (status === 401) return 'Please sign in.'
  if (status === 403) return 'You are not allowed to do that.'
  if (status === 429) return 'Too many attempts. Wait a few minutes and try again.'
  if (status >= 500) return 'The server had a problem. Try again in a moment.'
  return 'Something went wrong. Try again.'
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export async function api<T = unknown>(method: Method, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection.')
  }
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, errorMessage(res.status, data))
  return data as T
}

// ---------- shapes returned by the backend ----------

export type Theme = 'dark' | 'light'

export type Settings = {
  display: { timezone: string; theme: Theme }
  trading: {
    take_profit_pct: number
    stop_loss_pct: number
    contracts_per_trade: number
    max_order_usd: number
    max_daily_loss_usd: number
    auto_trading: 'off' | 'paper'
  }
  paper: { starting_balance: number; fill_rule: 'bid_ask' | 'mid' }
  watchlist: { symbols: string[]; default_ticker: string }
}

export type Me = {
  id: number
  email: string
  display_name: string
  role: 'admin' | 'user'
  two_step: boolean
  settings: Settings
}

export type LoginResult = { status: 'ok' | 'code_required' }

export type SessionRow = {
  current: boolean
  created_at: string
  last_seen_at: string
  user_agent: string
  ip: string | null
}

export type KeyProvider = {
  id: string
  name: string
  saved: { last4: string; account_id: string; updated_at: string } | null
}
export type KeysListing = { storage_ready: boolean; providers: KeyProvider[] }

export type AdminUser = {
  id: number
  email: string
  display_name: string
  role: 'admin' | 'user'
  is_active: boolean
  two_step: boolean
  created_at: string
  last_seen_at: string | null
}

export type InviteRow = {
  id: number
  email: string | null
  role: 'admin' | 'user'
  note: string
  kind: 'signup' | 'reset'
  status: 'open' | 'used' | 'revoked' | 'expired'
  created_at: string
  expires_at: string
  used_at: string | null
}

export type InviteInfo = {
  kind: 'signup' | 'reset'
  email: string | null
  role: 'admin' | 'user'
  expires_at: string
}

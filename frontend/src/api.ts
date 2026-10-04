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

// ---------- Capital Tracking and Account Manager (Stage 3) ----------

export type Kind = 'option' | 'stock'
export type OptionType = 'call' | 'put'

export type Flow = {
  id: number
  account: 'long_term' | 'short_term'
  kind: 'deposit' | 'withdrawal'
  amount: number
  day: string
  note: string
}

export type PositionTrade = {
  id: number
  side: 'buy' | 'sell'
  quantity: number
  price: number
  fees: number
  day: string
  note: string
  amount: number
}

type PositionBase = {
  id: number
  kind: Kind
  symbol: string
  option_type: OptionType | null
  strike: number | null
  expiration: string | null
  label: string
  note: string
  realized: number
  opened: string | null
  trades: PositionTrade[]
}

export type OpenPosition = PositionBase & {
  quantity: number
  average_price: number
  cost: number
  price: number | null
  price_source: 'mid' | 'last' | 'intrinsic' | 'none'
  underlying_last: number | null
  value: number
  gain: number | null
  gain_pct: number | null
  pct_of_capital: number | null
  days_left: number | null
  expired: boolean
  expiring_soon: boolean
}

export type ClosedPosition = PositionBase & { closed: string | null; invested: number; returned: number }

export type CapitalTotals = {
  total: number
  put_in: number
  cash: number
  positions_value: number
  gain: number
  gain_pct: number | null
  realized: number
  unrealized: number
  estimated: boolean
}

export type CapitalSummary = {
  totals: CapitalTotals
  open: OpenPosition[]
  closed: ClosedPosition[]
  flows: Flow[]
  prices: { available: boolean; detail: string | null }
}

export type CapitalHistory = {
  snapshots: { day: string; total: number; put_in: number; estimated: boolean }[]
  flows: Flow[]
}

export type CloseReason = 'take_profit' | 'stop_loss' | 'signal' | 'manual' | 'time' | 'expired'

export type ClosedTrade = {
  id: number
  mode: 'paper' | 'real'
  source: string
  editable: boolean
  kind: Kind
  symbol: string
  option_type: OptionType | null
  strike: number | null
  expiration: string | null
  label: string
  direction: 'long' | 'short'
  quantity: number
  entry_price: number
  exit_price: number
  fees: number
  opened_at: string
  closed_at: string
  close_reason: CloseReason
  notes: string
  result: number
  result_pct: number | null
}

export type TradeStats = {
  count: number
  wins: number
  losses: number
  total: number
  win_rate: number | null
  average_win: number | null
  average_loss: number | null
  by_reason: Record<string, { count: number; total: number }>
}

export type TradeListing = {
  mode: 'paper' | 'real'
  account_value: number
  account_value_estimated: boolean
  stats: TradeStats
  trades: ClosedTrade[]
  symbols: string[]
}

export type Totals = {
  long_term: CapitalTotals & { has_records: boolean }
  short_term: { value: number; has_records: boolean }
  paper: PaperAccountView & { open_positions: number }
  prices: { available: boolean; detail: string | null }
}

// ---------- paper trading and Live Trader (Stage 4) ----------

export type PaperAccountView = {
  cash: number
  reserved: number
  free_cash: number
  positions_value: number
  total: number
  open_pl: number
  realized_today: number
  starting_balance: number
  reset_at: string | null
  estimated: boolean
}

export type PaperPositionView = {
  id: number
  source: string
  label: string
  symbol: string
  option_type: OptionType
  strike: number
  expiration: string
  quantity: number
  entry_price: number
  bid: number | null
  ask: number | null
  price: number | null
  cost: number
  value: number | null
  pl: number | null
  pl_pct: number | null
  take_profit_price: number | null
  stop_loss_price: number | null
  opened_at: string
  days_left: number
  expiring_soon: boolean
}

export type PaperOrderView = {
  id: number
  source: string
  side: 'buy' | 'sell'
  intent: 'open' | 'close'
  label: string
  occ_symbol: string
  quantity: number
  limit_price: number | null
  take_profit_pct: number | null
  stop_loss_pct: number | null
  close_reason: string | null
  status: 'working' | 'filled' | 'cancelled' | 'rejected'
  status_detail: string
  fill_price: number | null
  created_at: string
  done_at: string | null
  position_id: number | null
  bid?: number | null
  ask?: number | null
}

export type PaperSummary = {
  account: PaperAccountView
  positions: PaperPositionView[]
  orders: PaperOrderView[]
  recent: PaperOrderView[]
  controls: { halted: boolean; auto_paused: boolean; auto_trading: 'off' | 'paper' }
  fill_rule: 'bid_ask' | 'mid'
  prices: { available: boolean; detail: string | null }
  order?: PaperOrderView
}

export type PaperEventView = { id: number; at: string; event: string; source: string; detail: string }

export type ChainSide = { symbol: string; bid: number | null; ask: number | null; last: number | null; volume: number | null; open_interest: number | null }
export type ChainRow = { strike: number; call: ChainSide | null; put: ChainSide | null }
export type Chain = {
  symbol: string
  expiration: string
  realtime: boolean
  underlying: { last: number | null; change_pct: number | null; description: string } | null
  rows: ChainRow[]
}

export type OrderReview = {
  label: string
  occ_symbol: string
  bid: number | null
  ask: number | null
  max_cost: number
  fill_now: boolean
  fill_price: number | null
  market_open: boolean
  take_profit_price: number | null
  stop_loss_price: number | null
  problem: string | null
  fill_rule: 'bid_ask' | 'mid'
}

/** Shapes returned by the backtest endpoints (backend app/routes_backtest). */
import type { TradeSettings } from '../api'
import type { Timeframe } from '../market/bars'
import type { CreditSpreads, Inputs, Luck, Results } from '../master/types'

export type ColumnKey = 'stock' | 'directional' | 'credit_spread' | 'debit_spread'

export type Money = {
  trades: number
  wins: number
  losses: number
  win_rate: number | null
  total: number
  total_pct: number | null
  final_value: number
  average_win: number | null
  average_loss: number | null
  max_drop: number
  max_drop_pct: number
  avg_days: number | null
  max_win_streak: number
  max_loss_streak: number
  years: { year: number; trades: number; wins: number; pnl: number }[]
  curve: { time: number; value: number }[]
}

export type OptionRow = {
  entry_time: number
  dir: 1 | -1
  problem?: string
  description?: string
  quantity?: number
  entry?: number
  exit?: number
  pnl?: number
  max_loss?: number
  payout?: number | null
  distance_pct?: number
  hold_days?: number
  expiration?: string
  exit_time?: number
  note?: string | null
}

export type TradeRow = {
  entry_time: number
  exit_time: number
  dir: 1 | -1
  type: string
  entry_price: number
  exit_price: number
  ret_pct: number
  reason: string
  counted: boolean
  days: number
  entries: number
  stock_pnl: number
  options: Partial<Record<ColumnKey, OptionRow[]>>
}

export type BacktestResult = {
  results: Results
  luck: Luck | null
  credit_spreads: CreditSpreads | null
  columns: Partial<Record<ColumnKey, Money>>
  skipped: Partial<Record<ColumnKey, number>>
  trades: TradeRow[]
  range: { first: number | null; last: number | null; history_from: number | null }
  notes: { options_source: string | null; rate_pct: number; vol_days: number; fill_rule: 'bid_ask' | 'mid' }
}

export type BacktestSetup = {
  strategy: string
  symbol: string
  timeframe: Timeframe
  inputs_source: 'pegged' | 'given' | 'defaults'
  inputs: Inputs
  start: string | null
  end: string | null
  starting_cash: number
  stock_dollars: number
  options: 'off' | 'plan' | 'compare'
  structures: string[]
  trade: TradeSettings
  fill_rule: 'bid_ask' | 'mid'
}

type Summary = Partial<Record<ColumnKey, Pick<Money, 'trades' | 'total' | 'total_pct' | 'win_rate' | 'max_drop_pct'>>>

export type BacktestRun = {
  id: number
  strategy: string
  symbol: string
  timeframe: Timeframe
  setup: BacktestSetup
  summary: Summary
  created_at: string
  result?: BacktestResult
}

/** What Master Chart's “Send to Backtest” carries. */
export type SentSetup = { strategy: string; symbol: string; timeframe: Timeframe; inputs: Inputs }

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

/** What a backtest trades on each signal (backend app/backtest/options.OptionSetup). */
export type Structure = 'directional' | 'debit_spread' | 'credit_spread'
export type OptionSetup = {
  structure: Structure
  strike_by: 'pct' | 'delta'
  /** Percent from the stock price, out of the money (negative: in the money). */
  strike_pct: number
  strike_delta: number
  width_usd: number
  expiry_mode: 'auto' | 'fixed'
  margin_pct: number
  expiry_days: number
  risk_usd: number
  /** Dollars per contract each time one is bought or sold. */
  commission: number
}

/** One leg's order in a transaction. */
export type LegFill = { action: string; contract: string; quantity: number; price: number; bid: number | null; ask: number | null }

/** Opening or closing one option position. `net` per share; `cash` in or out of the account, after fees. */
export type Transaction = { time: number; underlying: number; legs: LegFill[]; net: number; fees: number; cash: number }

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
  over_risk?: boolean
  payout?: number | null
  /** Older runs: the strike distance from “What to trade on a signal”. */
  distance_pct?: number
  strike_pct?: number
  delta?: number | null
  width?: number | null
  fees?: number
  /** Before a split: what the split-adjusted chart price was multiplied by to price the contract. */
  split_factor?: number | null
  transactions?: Transaction[]
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
  /** Positions of one contract that could lose more than the risk per trade (since 2026-10-10). */
  over_risk?: Partial<Record<ColumnKey, number>>
  trades: TradeRow[]
  range: { first: number | null; last: number | null; history_from: number | null }
  notes: {
    options_source: string | null
    rate_pct: number
    vol_days: number
    fill_rule: 'bid_ask' | 'mid'
    /** How far the estimate was from real prices for this symbol (Backtest plan, step 2). */
    estimate_check?: { period: string; overall: { n: number; typical_miss_pct: number; bias_pct: number; middle_80_pct: [number, number] } } | null
  }
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
  /** The option setup (since 2026-10-10); none: stock price only. */
  option?: OptionSetup | null
  /** Older runs compared structures with “What to trade on a signal”. */
  options?: 'off' | 'plan' | 'compare'
  structures?: string[]
  trade?: TradeSettings
  fill_rule: 'bid_ask' | 'mid'
  /** Older runs (before real prices) have none: the estimate. */
  option_prices?: 'estimate' | 'real'
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

/** A download of real option prices from Databento (Backtest plan, step 3). */
export type DataJob = {
  id: number
  status: 'estimating' | 'confirm' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  plan: { chain_days?: number; moments?: number; contract_minutes?: number; chains_usd?: number; prices_usd?: number; positions?: number; prices_known?: boolean }
  estimate_usd: number | null
  limit_usd: number | null
  spent_usd: number
  progress: { round?: number; phase?: string; done?: number; total?: number; left?: unknown }
  error: string | null
  symbol: string | null
  timeframe: Timeframe | null
}

/** Candles and real option prices stored for the user. */
export type StoredHistory = {
  candles: { symbol: string; timeframe: Timeframe; candles: number; from: string; to: string; source: string }[]
  options: { chains: { underlying: string; days: number; from: string; to: string }[]; quote_samples: number; spent_usd: number; available_from: string }
  databento_key: boolean
}

/** What Master Chart's “Send to Backtest” carries. */
export type SentSetup = { strategy: string; symbol: string; timeframe: Timeframe; inputs: Inputs }

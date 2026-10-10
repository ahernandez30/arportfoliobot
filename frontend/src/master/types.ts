/** Shapes returned by the strategy endpoints (backend app/strategy). */
import type { Bar, Timeframe } from '../market/bars'

export type InputValue = number | boolean | string
export type Inputs = Record<string, InputValue>

export type InputDef = {
  key: string
  label: string
  kind: 'float' | 'int' | 'bool' | 'choice' | 'timeframe' | 'time'
  default: InputValue
  group: string
  min: number | null
  max: number | null
  options: string[]
  help: string
  /** Not offered on the site: always at its default. */
  hidden?: boolean
}

export type StrategyDef = { id: string; name: string; version: string; inputs: InputDef[] }

export type Signal = { i: number; time: number; dir: 1 | -1; type: string; price: number; body_pct: number; wick_pct: number }
export type Blocked = { i: number; time: number; dir: 1 | -1; type: string; why: string[]; preview: boolean }
export type Trade = {
  entry_i: number
  entry_time: number
  entry_price: number
  dir: 1 | -1
  type: string
  exit_i: number
  exit_time: number
  exit_price: number
  reason: string
  ret_pct: number
  counted: boolean
  bars: number
  days: number
  entries?: number
}

export type TypeRow = {
  type: string
  wins: number
  losses: number
  win_rate: number | null
  floating: number
  floating_avg: number | null
  gap_avg: number | null
  gap_wins: number
  gap_n: number
  r_avg: number | null
  days_win: number | null
  days_loss: number | null
}

export type SideRow = { side: 'long' | 'short'; wins: number; losses: number; win_rate: number | null; r_avg: number | null }

export type Results = {
  by_type: TypeRow[]
  total: { wins: number; losses: number; win_rate: number | null; floating: number; r_avg: number | null; days_win: number | null; days_loss: number | null }
  /** Wins and losses by the year the trade opened; dollars by the year it closed (the script's table). */
  years: { year: number; wins: number; losses: number; pnl?: number; pnl_pct?: number }[]
  // The rest came with v9.35: backtests saved before it do not have them.
  by_side?: SideRow[]
  capital?: { start: number; pct_per_trade: number; compound: boolean; final: number; gain: number; return_pct: number; trades: number; max_drop_pct: number }
  /** Everything that closes on one candle counts as one hit. */
  hits?: { max_won_in_a_row: number; max_lost_in_a_row: number; worst_run: number; worst_run_pct: number }
  candles_measured: number
  max_win_streak: number
  max_loss_streak: number
  real_path_trades: number
  avg_days: number | null
  avg_bars: number | null
  mode: 'target_stop' | 'signal' | 'basket'
}

export type Luck = {
  rule: { objPct: number; stopPct: number; maxVelas: number; fixed: boolean }
  rows: { label: string; win_rate: number | null; r_avg: number | null; n: number }[]
  verdicts: { label: string; t: number | null; luck_pct: number | null; verdict: string }[]
}

export type SpreadSide = { n: number; needed: number | null; random_payout: number | null; r: number | null }

export type CreditSpreads = {
  width_pct: number
  target_payout: number
  blocks: {
    days: number
    rows: { strike_pct: number; win_all: number | null; win_all_random: number | null; lose_all: number | null; long: SpreadSide; short: SpreadSide; total_r: number | null; closest: boolean }[]
  }[]
  simulation: {
    days: number
    strike_pct: number
    payout: number
    risk_per_spread: number
    account: number
    total: number
    total_pct: number
    spreads: number
    win_rate: number | null
    lose_all_rate: number | null
    max_drop: number
    max_drop_pct: number
    worst_run: number
    max_open: number
    max_at_risk: number
    broke: boolean
    lowest_account: number
    years: { year: number; pnl: number }[]
  }
}

export type Position = { entry: number; dir: 1 | -1; entry_time: number; type: string; target: number | null; stop: number | null; entries?: number }

export type RunResult = {
  bars: Bar[]
  closed: number
  inputs: Inputs
  realtime: boolean
  signals: Signal[]
  preview: Signal | null
  blocked: Blocked[]
  trades: Trade[]
  open_trades: { entry: number; dir: 1 | -1; entry_time: number; type: string }[]
  position: Position | null
  results: Results
  ladder: { tf: Timeframe; active: boolean; state: number }[]
  /** What is stopping new signals right now (the script's “Ahora” row). */
  now: string[]
  open_now: { longs: number; shorts: number }
  intrabar: { on: boolean; tf: Timeframe; covered_from: number | null }
  ma: { time: number; value: number }[]
  luck?: Luck
  credit_spreads?: CreditSpreads
}

export type MasterState = { strategy: string; symbol: string; timeframe: Timeframe; inputs: Inputs }
export type Preset = { id: number; strategy: string; symbol: string; timeframe: Timeframe; inputs: Inputs; pegged_at: string }

type Ohlc = { open: number; high: number; low: number; close: number } | null
type Measure = { body_pct: number; wick_pct: number } | null

export type ParityCheck = {
  id: number
  strategy: string
  symbol: string
  timeframe: Timeframe
  filename: string
  created_at: string
  signed_off_at: string | null
  sign_off_note: string
  summary: {
    candles_in_file: number
    range: [string, string]
    tv_signals: number
    our_signals: number
    matched: number
    differences: number
    logic_candles: number
    logic_mismatches: number
    logic_ok: boolean
  }
  detail?: {
    logic_mismatches: { candle: string; tradingview: number; engine: number; ohlc: Ohlc; measure: Measure }[]
    differences: { candle: string; tradingview: number; ours: number; why: string; tv_ohlc: Ohlc; our_ohlc: Ohlc; tv_measure: Measure; our_measure: Measure }[]
    matched: string[]
  }
}

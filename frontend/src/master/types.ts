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

export type Results = {
  by_type: TypeRow[]
  total: { wins: number; losses: number; win_rate: number | null; floating: number; r_avg: number | null; days_win: number | null; days_loss: number | null }
  years: { year: number; wins: number; losses: number }[]
  candles_measured: number
  max_win_streak: number
  max_loss_streak: number
  real_path_trades: number
  avg_days: number | null
  avg_bars: number | null
  mode: 'target_stop' | 'signal' | 'basket'
}

export type Luck = {
  rule: { objPct: number; stopPct: number; maxVelas: number }
  rows: { label: string; win_rate: number | null; r_avg: number | null; n: number }[]
  verdicts: { label: string; t: number | null; luck_pct: number | null; verdict: string }[]
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
  intrabar: { on: boolean; tf: Timeframe; covered_from: number | null }
  ma: { time: number; value: number }[]
  luck?: Luck
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

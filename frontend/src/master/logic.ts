/** Master Chart helpers (pure, tested). */
import type { SeriesMarker, Time } from 'lightweight-charts'
import type { Timeframe } from '../market/bars'
import type { InputDef, Inputs, Preset, RunResult } from './types'

/** Keys whose values differ between two sets of inputs. */
export function changedKeys(a: Inputs, b: Inputs): string[] {
  return Object.keys({ ...a, ...b }).filter((k) => a[k] !== b[k])
}

export function presetFor(presets: Preset[], symbol: string, tf: Timeframe): Preset | null {
  return presets.find((p) => p.symbol === symbol && p.timeframe === tf) ?? null
}

/** The script's default for every input. */
export function defaults(defs: InputDef[]): Inputs {
  return Object.fromEntries(defs.map((d) => [d.key, d.default]))
}

/** Checks a typed number against an input's range; returns the number or a message. */
export function readInput(def: InputDef, text: string): number | string {
  const n = Number(text.trim().replace(',', '.'))
  if (!text.trim() || !Number.isFinite(n)) return 'Enter a number.'
  if (def.kind === 'int' && !Number.isInteger(n)) return 'Enter a whole number.'
  if (def.min != null && n < def.min) return `At least ${def.min}.`
  if (def.max != null && n > def.max) return `At most ${def.max}.`
  return n
}

/** TradingView names exports like “NASDAQ_TSLA, 1D_ab12.csv”. */
export function guessFromFilename(name: string): { symbol: string | null; timeframe: Timeframe | null } {
  const m = /(?:[A-Z]+_)?([A-Z][A-Z0-9.]{0,9}),\s*(\d+[SDWM]?|[DWM])/.exec(name)
  if (!m) return { symbol: null, timeframe: null }
  const tf: Record<string, Timeframe> = { '1D': '1D', D: '1D', '1W': '1W', W: '1W', '60': '1h', '30': '30m', '15': '15m', '5': '5m', '1': '1m' }
  return { symbol: m[1], timeframe: tf[m[2]] ?? null }
}

const SHORT: Record<string, string> = { LLENA: 'LL', FLECO: 'FL', ENGULFING: 'EN', RACHA: 'RA' }

export type MarkerColors = { buy: string; sell: string; gain: string; loss: string; muted: string }

/** Chart markers: BUY below the candle in blue, SELL above in amber (plan section 5); the
 * forming candle's signal faint; optionally where each trade closed and the blocked signals. */
export function markers(r: RunResult, c: MarkerColors, opts: { exits: boolean; blocked: boolean }): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = []
  for (const s of r.signals) {
    out.push(s.dir === 1
      ? { time: s.time as Time, position: 'belowBar', shape: 'arrowUp', color: c.buy, text: `BUY ${SHORT[s.type] ?? ''}`.trim() }
      : { time: s.time as Time, position: 'aboveBar', shape: 'arrowDown', color: c.sell, text: `SELL ${SHORT[s.type] ?? ''}`.trim() })
  }
  if (r.preview) {
    const s = r.preview
    out.push({ time: s.time as Time, position: s.dir === 1 ? 'belowBar' : 'aboveBar', shape: s.dir === 1 ? 'arrowUp' : 'arrowDown',
      color: `${s.dir === 1 ? c.buy : c.sell}66`, text: `${s.dir === 1 ? 'BUY' : 'SELL'}? (forming)` })
  }
  if (opts.exits) {
    for (const t of r.trades) {
      out.push({ time: t.exit_time as Time, position: 'inBar', shape: 'circle', size: 0.6,
        color: t.reason === 'flot' ? c.muted : t.ret_pct >= 0 ? c.gain : c.loss, text: t.reason })
    }
  }
  if (opts.blocked) {
    for (const b of r.blocked) {
      if (b.preview) continue
      out.push({ time: b.time as Time, position: b.dir === 1 ? 'belowBar' : 'aboveBar', shape: 'square', size: 0.5, color: c.muted, text: '✕' })
    }
  }
  // The chart library wants markers in time order.
  return out.sort((a, b) => (a.time as number) - (b.time as number))
}

/** How often to recompute: often enough to catch each candle's close. */
export function refreshMs(tf: Timeframe): number {
  return { '1m': 15_000, '5m': 30_000, '15m': 60_000, '30m': 60_000, '1h': 60_000, '1D': 300_000, '1W': 600_000 }[tf]
}

/** The trade rule in words, for under the results tables. */
export function ruleText(i: Inputs): string {
  if (i.modoSenal) {
    const tp = i.tpPctSS as number
    const sl = i.slPctSS as number
    return `${i.modoCesta ? 'Basket' : 'Signal to signal'}${tp || sl ? ` · take profit ${tp || '—'}% / stop ${sl || '—'}%` : ' (opposite signal only)'}`
  }
  const cesta = (i.cestaN as number) > 0 ? ` · CESTA at ${i.cestaN}%` : ''
  return `Target ${i.objPct}% / stop ${i.stopPct}% · ${i.cierraMercado ? `closes after ${i.maxVelas} candles` : `floats after ${i.maxVelas} candles`}${cesta}`
}

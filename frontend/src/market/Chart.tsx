import { useEffect, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  TickMarkType,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { api, ApiError } from '../api'
import { useMe } from '../auth'
import { applyTick, INTRADAY, type Bar, type Timeframe } from './bars'
import { useFeed, useTicks } from './feed'
import './market.css'

type CandlesResponse = { symbol: string; timeframe: Timeframe; realtime: boolean; bars: Bar[] }

const RELOAD_MS = 5 * 60 * 1000

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function formatter(timeZone: string, opts: Intl.DateTimeFormatOptions) {
  try {
    return new Intl.DateTimeFormat('en-US', { timeZone, ...opts })
  } catch {
    return new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', ...opts })
  }
}

/** One candlestick chart with volume, kept up to date by live prices. */
export default function Chart({ symbol, timeframe }: { symbol: string; timeframe: Timeframe }) {
  const me = useMe()
  const theme = me.settings.display.theme
  const userZone = me.settings.display.timezone
  const { reconnects } = useFeed()
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const candles = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volume = useRef<ISeriesApi<'Histogram'> | null>(null)
  const bars = useRef<Bar[]>([])
  const [error, setError] = useState<string | null>(null)
  // Which symbol|timeframe the shown data (or error) belongs to.
  const [loadedKey, setLoadedKey] = useState('')

  // Create the chart once.
  useEffect(() => {
    if (!box.current) return
    const c = createChart(box.current, {
      autoSize: true,
      layout: { attributionLogo: true, fontFamily: 'Hanken Grotesk Variable, system-ui, sans-serif', fontSize: 12 },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, rightOffset: 4 },
      crosshair: { mode: 0 },
    })
    candles.current = c.addSeries(CandlestickSeries, { priceLineVisible: true })
    volume.current = c.addSeries(HistogramSeries, { priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false })
    c.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    chart.current = c
    return () => {
      c.remove()
      chart.current = null
      candles.current = null
      volume.current = null
    }
  }, [])

  // Colors follow the theme.
  useEffect(() => {
    const gain = cssVar('--gain')
    const loss = cssVar('--loss')
    chart.current?.applyOptions({
      layout: { background: { type: ColorType.Solid, color: cssVar('--panel') }, textColor: cssVar('--text-2') },
      grid: { vertLines: { color: cssVar('--border') + '66' }, horzLines: { color: cssVar('--border') + '66' } },
    })
    candles.current?.applyOptions({ upColor: gain, downColor: loss, borderUpColor: gain, borderDownColor: loss, wickUpColor: gain, wickDownColor: loss })
    volume.current?.applyOptions({ color: cssVar('--text-2') + '55' })
  }, [theme])

  // Dates: intraday in the user's time zone; daily and weekly by calendar date.
  useEffect(() => {
    const intraday = INTRADAY.includes(timeframe)
    const zone = intraday ? userZone : 'UTC'
    const full = formatter(zone, intraday ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' } : { year: 'numeric', month: 'short', day: 'numeric' })
    const time = formatter(zone, { hour: 'numeric', minute: '2-digit' })
    const day = formatter(zone, { month: 'short', day: 'numeric' })
    const month = formatter(zone, { month: 'short' })
    const year = formatter(zone, { year: 'numeric' })
    const toDate = (t: Time) => new Date((t as number) * 1000)
    chart.current?.applyOptions({
      localization: { timeFormatter: (t: Time) => full.format(toDate(t)) },
      timeScale: {
        timeVisible: intraday,
        tickMarkFormatter: (t: Time, type: TickMarkType) =>
          type === TickMarkType.Year
            ? year.format(toDate(t))
            : type === TickMarkType.Month
              ? month.format(toDate(t))
              : type === TickMarkType.DayOfMonth
                ? day.format(toDate(t))
                : time.format(toDate(t)),
      },
    })
  }, [timeframe, userZone])

  // Load candles when the symbol or timeframe changes, after reconnecting, and every few minutes.
  const shown = useRef('')
  useEffect(() => {
    let cancelled = false
    const key = `${symbol}|${timeframe}`
    async function load() {
      try {
        const r = await api<CandlesResponse>('GET', `/api/market/candles?symbol=${encodeURIComponent(symbol)}&tf=${timeframe}`)
        if (cancelled || !candles.current || !volume.current) return
        bars.current = r.bars
        candles.current.setData(r.bars.map((b) => ({ time: b.time as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close })))
        volume.current.setData(r.bars.map((b) => ({ time: b.time as UTCTimestamp, value: b.volume })))
        if (shown.current !== key) {
          chart.current?.timeScale().fitContent()
          if (r.bars.length > 150) chart.current?.timeScale().setVisibleLogicalRange({ from: r.bars.length - 150, to: r.bars.length + 4 })
          shown.current = key
        }
        setError(r.bars.length ? null : `No ${timeframe} candles for ${symbol}.`)
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : 'Could not load the chart.')
      } finally {
        if (!cancelled) setLoadedKey(key)
      }
    }
    void load()
    const timer = window.setInterval(load, RELOAD_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [symbol, timeframe, reconnects])

  useTicks(symbol, (tick) => {
    const last = bars.current[bars.current.length - 1]
    if (!last) return
    const next = applyTick(last, tick, timeframe)
    if (!next || !candles.current || !volume.current) return
    if (next.time === last?.time) bars.current[bars.current.length - 1] = next
    else bars.current.push(next)
    const time = next.time as UTCTimestamp
    candles.current.update({ time, open: next.open, high: next.high, low: next.low, close: next.close })
    volume.current.update({ time, value: next.volume })
  })

  const loading = loadedKey !== `${symbol}|${timeframe}`
  return (
    <div className="chart-box">
      <div ref={box} className="chart-canvas" />
      {(error || loading) && (
        <div className={`chart-overlay ${error && !loading ? 'chart-error' : ''}`}>{loading ? 'Loading…' : error}</div>
      )}
    </div>
  )
}

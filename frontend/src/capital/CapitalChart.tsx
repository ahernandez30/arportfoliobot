import { useEffect, useMemo, useRef, useState } from 'react'
import { ColorType, createChart, LineSeries, LineType, LineStyle, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'
import type { CapitalHistory } from '../api'
import { useMe } from '../auth'
import { formatDay, formatMoney, isoDay } from '../money'
import { putInSeries, totalSeries } from './series'

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function dayOf(t: Time | undefined): string | null {
  if (t == null) return null
  if (typeof t === 'string') return t
  if (typeof t === 'number') return new Date(t * 1000).toISOString().slice(0, 10)
  return `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`
}

/** Total capital over time, against the money put in (one recorded point per market day). */
export default function CapitalChart({ history, liveTotal }: { history: CapitalHistory; liveTotal: number | null }) {
  const me = useMe()
  const theme = me.settings.display.theme
  const today = isoDay('America/New_York')
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const totalLine = useRef<ISeriesApi<'Line'> | null>(null)
  const putInLine = useRef<ISeriesApi<'Line'> | null>(null)
  const total = useMemo(() => totalSeries(history, today, liveTotal), [history, today, liveTotal])
  const putIn = useMemo(() => putInSeries(history, total.map((p) => p.time)), [history, total])
  const [hoverDay, setHoverDay] = useState<string | null>(null)

  useEffect(() => {
    if (!box.current) return
    const c = createChart(box.current, {
      autoSize: true,
      layout: { attributionLogo: true, fontFamily: 'Hanken Grotesk Variable, system-ui, sans-serif', fontSize: 12 },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      localization: { priceFormatter: (v: number) => formatMoney(v) },
      handleScroll: false,
      handleScale: false,
    })
    putInLine.current = c.addSeries(LineSeries, { lineWidth: 2, lineType: LineType.WithSteps, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    totalLine.current = c.addSeries(LineSeries, { lineWidth: 2, priceLineVisible: false, crosshairMarkerRadius: 4 })
    c.subscribeCrosshairMove((p) => setHoverDay(dayOf(p.time)))
    chart.current = c
    return () => {
      c.remove()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.applyOptions({
      layout: { background: { type: ColorType.Solid, color: cssVar('--panel') }, textColor: cssVar('--text-2') },
      grid: { vertLines: { visible: false }, horzLines: { color: cssVar('--border') + '66' } },
    })
    totalLine.current?.applyOptions({ color: cssVar('--accent') })
    putInLine.current?.applyOptions({ color: cssVar('--text-2') })
  }, [theme])

  useEffect(() => {
    totalLine.current?.setData(total)
    putInLine.current?.setData(putIn)
    chart.current?.timeScale().fitContent()
  }, [total, putIn])

  const shownDay = hoverDay ?? total.at(-1)?.time ?? null
  const totalAt = total.find((p) => p.time === shownDay)?.value ?? null
  const putInAt = [...putIn].reverse().find((p) => shownDay && p.time <= shownDay)?.value ?? null

  return (
    <div className="capital-chart">
      <div className="chart-legend" aria-live="polite">
        <span className="muted">{shownDay === today && !hoverDay ? 'Now' : formatDay(shownDay)}</span>
        <span className="legend-item"><span className="swatch swatch-total" />Total capital <b className="num">{formatMoney(totalAt)}</b></span>
        <span className="legend-item"><span className="swatch swatch-putin" />Money put in <b className="num">{formatMoney(putInAt)}</b></span>
      </div>
      <div ref={box} className="capital-chart-box" role="img" aria-label="Total capital and money put in over time" />
      {history.snapshots.length < 2 && (
        <p className="muted">
          One point is recorded for each market day, after the close (about 4:20 pm New York time), so this line grows from
          here on. The dashed line shows your deposits and withdrawals over time.
        </p>
      )}
    </div>
  )
}

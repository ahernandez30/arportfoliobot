import { useEffect, useRef } from 'react'
import { ColorType, createChart, LineSeries, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts'
import { useMe } from '../auth'
import { formatMoney } from '../money'
import { chartPoints, COLUMN_LABEL, COLUMN_ORDER } from './logic'
import type { ColumnKey, Money } from './types'

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

const COLOR_VAR: Record<ColumnKey, string> = {
  stock: '--bt-stock', directional: '--bt-directional', credit_spread: '--bt-credit', debit_spread: '--bt-debit',
}

/** Account value after each closed trade, one line per result column. */
export default function EquityChart({ columns }: { columns: Partial<Record<ColumnKey, Money>> }) {
  const theme = useMe().settings.display.theme
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const lines = useRef<Partial<Record<ColumnKey, ISeriesApi<'Line'>>>>({})

  useEffect(() => {
    if (!box.current) return
    const c = createChart(box.current, {
      autoSize: true,
      layout: { attributionLogo: true, fontFamily: 'Hanken Grotesk Variable, system-ui, sans-serif', fontSize: 12 },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      localization: { priceFormatter: (v: number) => formatMoney(v) },
    })
    chart.current = c
    return () => {
      c.remove()
      chart.current = null
      lines.current = {}
    }
  }, [])

  useEffect(() => {
    const c = chart.current
    if (!c) return
    for (const key of COLUMN_ORDER) {
      const col = columns[key]
      let s = lines.current[key]
      if (!col) {
        if (s) c.removeSeries(s)
        delete lines.current[key]
        continue
      }
      if (!s) {
        s = c.addSeries(LineSeries, { lineWidth: 2, priceLineVisible: false, title: COLUMN_LABEL[key] })
        lines.current[key] = s
      }
      s.setData(chartPoints(col.curve).map((p) => ({ time: p.time as UTCTimestamp, value: p.value })))
    }
    c.timeScale().fitContent()
  }, [columns])

  useEffect(() => {
    chart.current?.applyOptions({
      layout: { background: { type: ColorType.Solid, color: cssVar('--panel') }, textColor: cssVar('--text-2') },
      grid: { vertLines: { visible: false }, horzLines: { color: cssVar('--border') + '66' } },
    })
    for (const [k, s] of Object.entries(lines.current)) s?.applyOptions({ color: cssVar(COLOR_VAR[k as ColumnKey]) })
  }, [theme, columns])

  return (
    <div className="equity">
      <div className="chart-legend">
        {COLUMN_ORDER.filter((k) => columns[k]).map((k) => (
          <span key={k} className="legend-item"><span className="swatch" style={{ background: `var(${COLOR_VAR[k]})` }} />{COLUMN_LABEL[k]}</span>
        ))}
      </div>
      <div ref={box} className="equity-box" role="img" aria-label="Account value after each trade" />
    </div>
  )
}

import type { Timeframe } from './bars'
import ChartPanel from './ChartPanel'
import { Credits, MarketState } from './MarketHeader'
import { useSavedLayout } from './useSaved'
import './market.css'

type Pane = { symbol: string; timeframe: Timeframe }
type Layout = { panes: Pane[] }

/** Four charts at once, each with its own symbol and timeframe, saved per user. */
export default function ChartsPage() {
  const { value: layout, update, error } = useSavedLayout<Layout>('/api/layouts/charts')

  function change(i: number, symbol: string, timeframe: Timeframe) {
    if (!layout) return
    const panes = layout.panes.map((p, j) => (j === i ? { symbol, timeframe } : p))
    update({ panes })
  }

  return (
    <section className="page page-wide">
      <div className="page-head">
        <h1 className="page-title">Charts</h1>
        <MarketState />
      </div>
      {error && <p className="msg msg-error">{error}</p>}
      {layout && (
        <div className="charts-grid">
          {layout.panes.map((p, i) => (
            <div className="panel" key={i}>
              <ChartPanel symbol={p.symbol} timeframe={p.timeframe} onChange={(s, tf) => change(i, s, tf)} />
            </div>
          ))}
        </div>
      )}
      <Credits />
    </section>
  )
}

import { useState } from 'react'
import ReactGridLayout, { useContainerWidth } from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { api } from '../api'
import { useMe } from '../auth'
import ChartPanel from '../market/ChartPanel'
import { Credits, MarketState } from '../market/MarketHeader'
import { useSavedLayout } from '../market/useSaved'
import Watchlist from '../market/Watchlist'
import { addTile, applyPositions, COLS, removeTile, samePositions, stackOrder, TILE_INFO, updateTile, type Dashboard, type Tile, type TileKind } from './tiles'
import './dashboard.css'

const ROW_HEIGHT = 40
const PHONE_WIDTH = 700

function Totals() {
  const items = [
    { label: 'Long-term capital', note: 'Stage 3' },
    { label: 'Short-term account', note: 'Stage 3' },
    { label: 'Paper account', note: 'Stage 4' },
    { label: 'Open profit or loss', note: 'Stage 4' },
  ]
  return (
    <div className="totals">
      {items.map((i) => (
        <div key={i.label} className="stat">
          <span className="stat-label">{i.label}</span>
          <span className="stat-value num">—</span>
          <span className="muted stat-note">Arrives in {i.note}</span>
        </div>
      ))}
    </div>
  )
}

function TileBody({ tile, onChange, onPickSymbol }: { tile: Tile; onChange: (c: Partial<Tile>) => void; onPickSymbol: (s: string) => void }) {
  switch (tile.kind) {
    case 'totals':
      return <Totals />
    case 'watchlist':
      return <Watchlist onPick={onPickSymbol} />
    case 'chart':
      return (
        <ChartPanel
          symbol={tile.symbol ?? 'SPY'}
          timeframe={tile.timeframe ?? '1D'}
          onChange={(symbol, timeframe) => onChange({ symbol, timeframe })}
        />
      )
    case 'trades':
      return <p className="muted">Open trades appear here from Stage 4 (paper trading).</p>
  }
}

export default function DashboardPage() {
  const me = useMe()
  const ticker = me.settings.watchlist.default_ticker
  const { value, update, replace, error } = useSavedLayout<Dashboard>('/api/layouts/dashboard')
  const [editing, setEditing] = useState(false)
  const [adding, setAdding] = useState<TileKind>('chart')
  const { width, containerRef, mounted } = useContainerWidth()
  const tiles = value?.tiles ?? []
  const phone = mounted && width < PHONE_WIDTH

  function save(next: Tile[]) {
    update({ tiles: next })
  }

  function pickSymbol(symbol: string) {
    const chart = tiles.find((t) => t.kind === 'chart')
    if (chart) save(updateTile(tiles, chart.id, { symbol }))
  }

  async function reset() {
    if (!window.confirm('Put the dashboard back to the starting tiles?')) return
    replace(await api<Dashboard>('DELETE', '/api/layouts/dashboard'))
  }

  const tileView = (t: Tile) => (
    <div key={t.id} className="panel tile">
      <div className={`tile-head ${editing ? 'drag-handle' : ''}`}>
        <h2>{t.kind === 'chart' ? `${TILE_INFO.chart.label}` : TILE_INFO[t.kind].label}</h2>
        {editing && (
          <button className="btn btn-small btn-danger tile-remove" onClick={() => save(removeTile(tiles, t.id))} aria-label={`Remove ${TILE_INFO[t.kind].label}`}>
            Remove
          </button>
        )}
      </div>
      <div className="tile-body">
        <TileBody tile={t} onChange={(c) => save(updateTile(tiles, t.id, c))} onPickSymbol={pickSymbol} />
      </div>
    </div>
  )

  return (
    <section className="page page-wide">
      <div className="page-head">
        <h1 className="page-title">Dashboard</h1>
        <MarketState />
      </div>
      <div className="actions">
        {!phone && (
          <button className={`btn btn-small ${editing ? 'btn-primary' : ''}`} onClick={() => setEditing((e) => !e)}>
            {editing ? 'Done' : 'Edit layout'}
          </button>
        )}
        {editing && (
          <>
            <select className="select select-small" value={adding} onChange={(e) => setAdding(e.target.value as TileKind)} aria-label="Tile to add">
              {(Object.keys(TILE_INFO) as TileKind[]).map((k) => (
                <option key={k} value={k}>
                  {TILE_INFO[k].label}
                </option>
              ))}
            </select>
            <button className="btn btn-small" onClick={() => save(addTile(tiles, adding, ticker))}>
              Add tile
            </button>
            <button className="btn btn-small" onClick={reset}>
              Reset to start
            </button>
            <span className="muted">Drag a tile by its title. Resize from the bottom-right corner.</span>
          </>
        )}
      </div>
      {error && <p className="msg msg-error">{error}</p>}
      <div ref={containerRef} className="dash">
        {value && tiles.length === 0 && <p className="muted">No tiles. Use Edit layout → Add tile.</p>}
        {value && mounted && phone && <div className="dash-stack">{stackOrder(tiles).map(tileView)}</div>}
        {value && mounted && !phone && (
          <ReactGridLayout
            width={width}
            layout={tiles.map((t) => ({ i: t.id, x: t.x, y: t.y, w: t.w, h: t.h, minW: TILE_INFO[t.kind].minW, minH: TILE_INFO[t.kind].minH }))}
            gridConfig={{ cols: COLS, rowHeight: ROW_HEIGHT, margin: [16, 16], containerPadding: [0, 0] }}
            dragConfig={{ enabled: editing, handle: '.drag-handle', bounded: false }}
            resizeConfig={{ enabled: editing, handles: ['se'] }}
            onLayoutChange={(layout) => {
              const next = applyPositions(tiles, layout)
              if (!samePositions(tiles, next)) save(next)
            }}
          >
            {tiles.map(tileView)}
          </ReactGridLayout>
        )}
      </div>
      <Credits />
    </section>
  )
}

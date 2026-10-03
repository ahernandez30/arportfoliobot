/** Dashboard tiles: their shapes and the layout arithmetic (pure, tested). */
import type { Timeframe } from '../market/bars'

export type TileKind = 'totals' | 'watchlist' | 'chart' | 'trades'

export type Tile = {
  id: string
  kind: TileKind
  x: number
  y: number
  w: number
  h: number
  symbol?: string | null
  timeframe?: Timeframe | null
}

export type Dashboard = { tiles: Tile[] }

export const COLS = 12
export const MAX_TILES = 20

export const TILE_INFO: Record<TileKind, { label: string; w: number; h: number; minW: number; minH: number }> = {
  totals: { label: 'Account totals', w: 12, h: 3, minW: 4, minH: 2 },
  watchlist: { label: 'Watchlist', w: 6, h: 10, minW: 4, minH: 4 },
  chart: { label: 'Chart', w: 6, h: 10, minW: 4, minH: 6 },
  trades: { label: 'Open trades', w: 12, h: 5, minW: 4, minH: 3 },
}

type Pos = { i: string; x: number; y: number; w: number; h: number }

/** New positions from the grid, keeping everything else about each tile. */
export function applyPositions(tiles: Tile[], layout: readonly Pos[]): Tile[] {
  const byId = new Map(layout.map((l) => [l.i, l]))
  return tiles.map((t) => {
    const l = byId.get(t.id)
    return l ? { ...t, x: l.x, y: l.y, w: l.w, h: l.h } : t
  })
}

export function samePositions(a: Tile[], b: Tile[]): boolean {
  if (a.length !== b.length) return false
  const byId = new Map(b.map((t) => [t.id, t]))
  return a.every((t) => {
    const o = byId.get(t.id)
    return o && o.x === t.x && o.y === t.y && o.w === t.w && o.h === t.h
  })
}

function newId(kind: TileKind, tiles: Tile[]): string {
  let n = 1
  const ids = new Set(tiles.map((t) => t.id))
  while (ids.has(`${kind}-${n}`)) n++
  return `${kind}-${n}`
}

/** Adds a tile below everything else. */
export function addTile(tiles: Tile[], kind: TileKind, ticker: string): Tile[] {
  if (tiles.length >= MAX_TILES) return tiles
  const info = TILE_INFO[kind]
  const bottom = tiles.reduce((m, t) => Math.max(m, t.y + t.h), 0)
  const tile: Tile = { id: newId(kind, tiles), kind, x: 0, y: bottom, w: info.w, h: info.h }
  if (kind === 'chart') Object.assign(tile, { symbol: ticker, timeframe: '1D' })
  return [...tiles, tile]
}

export function removeTile(tiles: Tile[], id: string): Tile[] {
  return tiles.filter((t) => t.id !== id)
}

export function updateTile(tiles: Tile[], id: string, changes: Partial<Tile>): Tile[] {
  return tiles.map((t) => (t.id === id ? { ...t, ...changes } : t))
}

/** Phone order: top to bottom, then left to right. */
export function stackOrder(tiles: Tile[]): Tile[] {
  return [...tiles].sort((a, b) => a.y - b.y || a.x - b.x)
}

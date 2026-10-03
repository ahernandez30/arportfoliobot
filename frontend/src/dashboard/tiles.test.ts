import { describe, expect, it } from 'vitest'
import { addTile, applyPositions, MAX_TILES, removeTile, samePositions, stackOrder, updateTile, type Tile } from './tiles'

const base: Tile[] = [
  { id: 'totals', kind: 'totals', x: 0, y: 0, w: 12, h: 3 },
  { id: 'chart', kind: 'chart', x: 5, y: 3, w: 7, h: 10, symbol: 'TSLA', timeframe: '1D' },
  { id: 'watchlist', kind: 'watchlist', x: 0, y: 3, w: 5, h: 10 },
]

describe('dashboard tiles', () => {
  it('applies grid positions and keeps tile settings', () => {
    const out = applyPositions(base, [{ i: 'chart', x: 0, y: 13, w: 12, h: 8 }])
    expect(out[1]).toEqual({ ...base[1], x: 0, y: 13, w: 12, h: 8 })
    expect(out[0]).toBe(base[0])
  })
  it('detects real moves only', () => {
    expect(samePositions(base, applyPositions(base, [{ i: 'chart', x: 5, y: 3, w: 7, h: 10 }]))).toBe(true)
    expect(samePositions(base, applyPositions(base, [{ i: 'chart', x: 4, y: 3, w: 7, h: 10 }]))).toBe(false)
    expect(samePositions(base, base.slice(1))).toBe(false)
  })
  it('adds a tile at the bottom with a unique id', () => {
    const out = addTile(addTile(base, 'chart', 'SPY'), 'chart', 'SPY')
    expect(out.slice(-2).map((t) => t.id)).toEqual(['chart-1', 'chart-2'])
    expect(out[3]).toMatchObject({ y: 13, symbol: 'SPY', timeframe: '1D' })
    expect(out[4].y).toBe(23)
  })
  it('stops at the tile limit', () => {
    let tiles: Tile[] = []
    for (let i = 0; i < MAX_TILES + 3; i++) tiles = addTile(tiles, 'trades', 'SPY')
    expect(tiles).toHaveLength(MAX_TILES)
  })
  it('removes and updates tiles', () => {
    expect(removeTile(base, 'chart').map((t) => t.id)).toEqual(['totals', 'watchlist'])
    expect(updateTile(base, 'chart', { symbol: 'QQQ' })[1].symbol).toBe('QQQ')
  })
  it('stacks top to bottom, then left to right, for phones', () => {
    expect(stackOrder(base).map((t) => t.id)).toEqual(['totals', 'watchlist', 'chart'])
  })
})

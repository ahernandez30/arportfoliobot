import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api } from '../api'
import { FeedBadge, useFeed } from './feed'

type Status = { provider: string | null; realtime: boolean; clock: { state: string; description: string } | null; detail: string | null }

const MARKET_LABEL: Record<string, string> = {
  open: 'Market open',
  premarket: 'Pre-market',
  postmarket: 'After hours',
  closed: 'Market closed',
}

/** Feed status, market open/closed, and a hint when there is no data key. */
export function MarketState() {
  const { status: feed } = useFeed()
  const [status, setStatus] = useState<Status | null>(null)
  useEffect(() => {
    const load = () => api<Status>('GET', '/api/market/status').then(setStatus).catch(() => undefined)
    void load()
    const timer = window.setInterval(load, 60_000)
    return () => window.clearInterval(timer)
  }, [])
  return (
    <div className="actions market-state">
      {status?.clock && <span className="badge">{MARKET_LABEL[status.clock.state] ?? status.clock.state}</span>}
      <FeedBadge />
      {(feed.state === 'no_key' || status?.provider === null) && (
        <Link to="/config/keys" className="btn btn-small">
          Add data key
        </Link>
      )}
    </div>
  )
}

/** Required credits: TradingView for the charts (Apache-2.0 NOTICE) and Tradier for the data. */
export function Credits() {
  return (
    <p className="credits">
      Charts: TradingView Lightweight Charts™, Copyright (c) 2025 TradingView, Inc.{' '}
      <a href="https://www.tradingview.com/" target="_blank" rel="noopener">
        tradingview.com
      </a>
      {' · '}Market data powered by{' '}
      <a href="https://tradier.com/" target="_blank" rel="noopener">
        Tradier
      </a>
    </p>
  )
}

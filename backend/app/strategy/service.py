"""Runs a strategy for a chart: gathers the candles it needs with the user's own key, marks the
candle still in progress, and runs the one engine shared by Master Chart, the worker and Backtest."""
import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import history
from app.marketdata import service as md_service
from app.marketdata.bars import NY
from app.marketdata.base import INTRADAY, Bar, MarketData
from app.strategy.base import TIMEFRAME_SECONDS, Strategy, StrategyData

# How far back daily and weekly candles go for strategies: long histories give more signals to
# compare with TradingView. Intraday history is whatever the provider keeps (about 40 days).
HISTORY_START = {"1D": date(2005, 1, 1), "1W": date(1995, 1, 1)}
SESSION_CLOSE_MIN = 16 * 60


def candle_closed(b: Bar, tf: str, now: datetime) -> bool:
    """Whether a candle has finished (plan 7.5: only closed candles may trade)."""
    now_ny = now.astimezone(NY)
    if tf in ("1D", "1W"):
        d = datetime.fromtimestamp(b.time, timezone.utc).date()
        last_day = d + timedelta(days=4) if tf == "1W" else d
        close = datetime(last_day.year, last_day.month, last_day.day, 16, 0, tzinfo=NY)
        return now_ny >= close
    start = datetime.fromtimestamp(b.time, NY)
    end = start + timedelta(seconds=TIMEFRAME_SECONDS[tf])
    session_end = start.replace(hour=16, minute=0, second=0, microsecond=0)
    return now_ny >= min(end, session_end)


def closed_count(bars: list[Bar], tf: str, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    if bars and not candle_closed(bars[-1], tf, now):
        return len(bars) - 1
    return len(bars)


async def load(cache: md_service.TTLCache, user_id: int, md: MarketData, strategy: Strategy, symbol: str,
               timeframe: str, inputs: dict, now: datetime | None = None, db: Session | None = None) -> StrategyData:
    """With `db`, the user's stored intraday history goes in front of the provider's candles
    (Master Chart and Backtest; automatic trading runs on the provider's candles only)."""
    wanted = [timeframe, *sorted(strategy.needs(timeframe, inputs))]
    got = await asyncio.gather(*(md_service.candles(cache, user_id, md, symbol, tf, HISTORY_START.get(tf))
                                 for tf in wanted))
    if db is not None:
        got = [history.merge(history.stored(db, user_id, symbol, tf), bars) if tf in INTRADAY else bars
               for tf, bars in zip(wanted, got)]
    bars = got[0]
    return StrategyData(symbol=symbol, timeframe=timeframe, bars=bars, closed=closed_count(bars, timeframe, now),
                        other=dict(zip(wanted[1:], got[1:])))


async def run(strategy: Strategy, data: StrategyData, inputs: dict, *, luck: bool = False,
              luck_from: int | None = None) -> dict:
    # Thousands of candles: keep the web server responsive while it computes.
    return await asyncio.to_thread(strategy.run, data, inputs, luck=luck, luck_from=luck_from)

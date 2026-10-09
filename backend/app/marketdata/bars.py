"""Candle arithmetic: New York session times, intraday buckets, and building 1-hour candles.

Hourly candles start at 9:30, 10:30 ... 15:30 New York time (the last one is 30 minutes
long), the same way TradingView builds regular-session hourly candles for US stocks.
"""
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.marketdata.base import Bar

NY = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def ny_wall_to_epoch(text: str) -> int:
    """'2026-10-02 09:30:00' (New York wall time) -> Unix seconds."""
    fmt = "%Y-%m-%dT%H:%M:%S" if "T" in text else "%Y-%m-%d %H:%M:%S"
    if len(text) == 16:
        fmt = fmt[:-3]
    return int(datetime.strptime(text, fmt).replace(tzinfo=NY).timestamp())


def date_to_epoch(d: date) -> int:
    """A calendar date as 00:00 UTC, so daily candles show the same date everywhere."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def in_regular_session(epoch: int) -> bool:
    t = datetime.fromtimestamp(epoch, NY)
    return t.weekday() < 5 and SESSION_OPEN <= t.time() < SESSION_CLOSE


def bucket_start(epoch: int, timeframe: str) -> int | None:
    """Start of the intraday candle holding this moment, or None outside the regular session."""
    if not in_regular_session(epoch):
        return None
    t = datetime.fromtimestamp(epoch, NY)
    open_ = t.replace(hour=9, minute=30, second=0, microsecond=0)
    minutes = int((t - open_).total_seconds() // 60)
    size = MINUTES[timeframe]
    return int((open_ + timedelta(minutes=minutes - minutes % size)).timestamp())


def aggregate(bars: Iterable[Bar], timeframe: str) -> list[Bar]:
    """Combine smaller regular-session candles into `timeframe` candles."""
    out: list[Bar] = []
    for b in bars:
        start = bucket_start(b.time, timeframe)
        if start is None:
            continue
        if out and out[-1].time == start:
            last = out[-1]
            out[-1] = Bar(start, last.open, max(last.high, b.high), min(last.low, b.low), b.close, last.volume + b.volume)
        else:
            out.append(Bar(start, b.open, b.high, b.low, b.close, b.volume))
    return out

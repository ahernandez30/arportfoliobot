"""Stored candle history (Backtest plan, step 1).

The live provider keeps only about 40 days of intraday candles. A user can import older candles
bought under their own data licence (for example Databento's 1-minute candles); they are kept per
user, and strategy runs for Master Chart and Backtest put them in front of the live candles.

Candles are built the way the strategy and TradingView see them: regular session only (09:30 to
16:00 New York, 13:00 on early-close days), buckets from 09:30, and prices adjusted for stock
splits so they line up with today's prices (and with the live provider's adjusted daily candles).
"""
import time as _time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.marketdata.bars import NY
from app.marketdata.base import Bar
from app.marketdata.calendar import nyse_closed, session_close_minute
from app.models import CandleHistory

# Splits since 2018 for the symbols imported so far: (first day trading split-adjusted, ratio).
SPLITS: dict[str, list[tuple[date, float]]] = {
    "TSLA": [(date(2020, 8, 31), 5.0), (date(2022, 8, 25), 3.0)],
    "NVDA": [(date(2021, 7, 20), 4.0), (date(2024, 6, 10), 10.0)],
}
BUILT_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}
OPEN_MINUTE = 9 * 60 + 30
CACHE_SECONDS = 600


def split_factor(splits: list[tuple[date, float]], d: date) -> float:
    """What a price on day d is divided by to read in today's terms."""
    f = 1.0
    for day, ratio in splits:
        if d < day:
            f *= ratio
    return f


@dataclass(frozen=True)
class Minute:
    time: int  # Unix seconds, start of the minute
    open: float
    high: float
    low: float
    close: float
    volume: float


def build(minutes: Iterable[Minute], timeframe: str, splits: list[tuple[date, float]]) -> list[Bar]:
    """Regular-session candles of `timeframe` from 1-minute candles (any order, any session)."""
    size = BUILT_MINUTES[timeframe]
    out: dict[int, list[float]] = {}
    for m in minutes:
        t = datetime.fromtimestamp(m.time, NY)
        d = t.date()
        mins = t.hour * 60 + t.minute
        if t.weekday() >= 5 or nyse_closed(d) or not OPEN_MINUTE <= mins < session_close_minute(d):
            continue
        f = split_factor(splits, d)
        k = mins - (mins - OPEN_MINUTE) % size
        start = int(datetime(d.year, d.month, d.day, k // 60, k % 60, tzinfo=NY).timestamp())
        o, h, lo, c, v = m.open / f, m.high / f, m.low / f, m.close / f, m.volume * f
        cur = out.get(start)
        if cur is None:
            out[start] = [m.time, o, h, lo, c, v, m.time]
        else:
            if m.time < cur[0]:
                cur[0], cur[1] = m.time, o
            if m.time >= cur[6]:
                cur[6], cur[4] = m.time, c
            cur[2], cur[3] = max(cur[2], h), min(cur[3], lo)
            cur[5] += v
    return [Bar(s, r[1], r[2], r[3], r[4], r[5]) for s, r in sorted(out.items())]


def store(db: Session, user_id: int, symbol: str, timeframe: str, bars: list[Bar], source: str) -> int:
    """Saves candles (replacing any stored for the same times). Returns how many."""
    rows = [{"user_id": user_id, "symbol": symbol, "timeframe": timeframe, "time": b.time, "open": b.open,
             "high": b.high, "low": b.low, "close": b.close, "volume": b.volume, "source": source} for b in bars]
    for i in range(0, len(rows), 5000):
        chunk = rows[i:i + 5000]
        stmt = insert(CandleHistory).values(chunk)
        db.execute(stmt.on_conflict_do_update(
            index_elements=["user_id", "symbol", "timeframe", "time"],
            set_={k: stmt.excluded[k] for k in ("open", "high", "low", "close", "volume", "source")}))
    _cache.pop((user_id, symbol, timeframe), None)
    return len(rows)


_cache: dict[tuple[int, str, str], tuple[float, list[Bar]]] = {}


def stored(db: Session, user_id: int, symbol: str, timeframe: str) -> list[Bar]:
    """The user's stored candles, oldest first (kept in memory for a few minutes)."""
    key = (user_id, symbol, timeframe)
    hit = _cache.get(key)
    if hit and _time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    rows = db.execute(select(CandleHistory.time, CandleHistory.open, CandleHistory.high, CandleHistory.low,
                             CandleHistory.close, CandleHistory.volume)
                      .where(CandleHistory.user_id == user_id, CandleHistory.symbol == symbol,
                             CandleHistory.timeframe == timeframe).order_by(CandleHistory.time))
    bars = [Bar(*r) for r in rows]
    _cache[key] = (_time.monotonic(), bars)
    return bars


def merge(old: list[Bar], live: list[Bar]) -> list[Bar]:
    """Stored candles older than the live provider's first one, then the live ones (the live
    provider wins where both have a candle)."""
    if not old:
        return live
    if not live:
        return old
    first = live[0].time
    return [b for b in old if b.time < first] + live


def coverage(db: Session, user_id: int) -> list[dict]:
    """What the user has stored, for the screens."""
    from sqlalchemy import func

    q = (select(CandleHistory.symbol, CandleHistory.timeframe, func.count(), func.min(CandleHistory.time),
                func.max(CandleHistory.time), func.min(CandleHistory.source))
         .where(CandleHistory.user_id == user_id).group_by(CandleHistory.symbol, CandleHistory.timeframe)
         .order_by(CandleHistory.symbol, CandleHistory.timeframe))
    return [{"symbol": s, "timeframe": tf, "candles": n,
             "from": datetime.fromtimestamp(a, timezone.utc).isoformat(),
             "to": datetime.fromtimestamp(b, timezone.utc).isoformat(), "source": src}
            for s, tf, n, a, b, src in db.execute(q)]


def read_parquet_minutes(path: str) -> list[Minute]:
    """1-minute candles from a Databento ohlcv-1m Parquet file (ts_event, open, high, low, close,
    volume). Several rows for one minute (one per exchange) are combined."""
    import pyarrow.parquet as pq

    t = pq.read_table(path, columns=["ts_event", "open", "high", "low", "close", "volume"])
    cols = {c: t.column(c).to_pylist() for c in t.column_names}
    by_min: dict[int, Minute] = {}
    for ts, o, h, lo, c, v in zip(cols["ts_event"], cols["open"], cols["high"], cols["low"], cols["close"],
                                  cols["volume"]):
        if None in (ts, o, h, lo, c):
            continue  # a row without prices
        sec = int(ts.timestamp()) if isinstance(ts, datetime) else int(ts) // 1_000_000_000
        sec -= sec % 60
        cur = by_min.get(sec)
        if cur is None:
            by_min[sec] = Minute(sec, float(o), float(h), float(lo), float(c), float(v or 0))
        else:  # same minute from another exchange: widen the range, add the volume
            by_min[sec] = Minute(sec, cur.open, max(cur.high, float(h)), min(cur.low, float(lo)), cur.close,
                                 cur.volume + float(v or 0))
    return list(by_min.values())


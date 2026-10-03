"""Picks each user's market data provider from their own saved key, and the watchlist math.

Tradier data is licensed to the key owner for personal use, so providers and caches are
kept per user: nothing fetched with one user's key is ever served to another user.
"""
import asyncio
import calendar
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crypto
from app.marketdata.bars import NY
from app.marketdata.base import Bar, DailyClose, MarketData, MarketDataError, Quote
from app.marketdata.tradier import TradierMarketData
from app.models import ApiKey

# Key providers that can supply market data, best first.
DATA_PROVIDERS = ("tradier", "tradier_sandbox")

NO_KEY = "Add a Tradier key in Config → Keys & connections to see market data."


@dataclass(frozen=True)
class FeedInfo:
    provider: str | None  # which saved key is used, or None
    realtime: bool


def feed_info(db: Session, user_id: int) -> FeedInfo:
    providers = set(db.scalars(select(ApiKey.provider).where(ApiKey.user_id == user_id)))
    for p in DATA_PROVIDERS:
        if p in providers:
            return FeedInfo(p, realtime=(p == "tradier"))
    return FeedInfo(None, realtime=False)


def build_provider(provider: str, secret: str) -> MarketData:
    if provider == "tradier":
        return TradierMarketData(secret)
    if provider == "tradier_sandbox":
        return TradierMarketData(secret, sandbox=True)
    raise ValueError(provider)


class ProviderCache:
    """One provider object per user and key version, so connections and rate limits are shared
    by that user's requests. A replaced key gets a fresh object."""

    def __init__(self):
        self._items: dict[int, tuple[tuple, MarketData]] = {}

    def get(self, db: Session, user_id: int) -> MarketData | None:
        row = None
        for p in DATA_PROVIDERS:
            row = db.scalar(select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.provider == p))
            if row is not None:
                break
        if row is None:
            self._items.pop(user_id, None)
            return None
        version = (row.provider, row.id, row.updated_at)
        cached = self._items.get(user_id)
        if cached and cached[0] == version:
            return cached[1]
        provider = build_provider(row.provider, crypto.decrypt(row.secret_enc))
        self._items[user_id] = (version, provider)
        return provider

    def drop(self, user_id: int) -> None:
        self._items.pop(user_id, None)


class TTLCache:
    """Small in-memory cache. Keys always start with the user id."""

    def __init__(self, max_items: int = 2000):
        self._data: dict[tuple, tuple[float, object]] = {}
        self.max_items = max_items

    def get(self, key: tuple):
        hit = self._data.get(key)
        if hit is None or hit[0] < time.monotonic():
            return None
        return hit[1]

    def put(self, key: tuple, value: object, ttl: float) -> None:
        if len(self._data) >= self.max_items:
            now = time.monotonic()
            self._data = {k: v for k, v in self._data.items() if v[0] >= now}
            if len(self._data) >= self.max_items:
                self._data.clear()
        self._data[key] = (time.monotonic() + ttl, value)


# ---------- candles ----------

CANDLE_TTL = {"1m": 15, "5m": 30, "15m": 60, "1h": 60, "1D": 300, "1W": 900}


async def candles(cache: TTLCache, user_id: int, md: MarketData, symbol: str, timeframe: str) -> list[Bar]:
    key = (user_id, "candles", symbol, timeframe)
    hit = cache.get(key)
    if hit is not None:
        return hit
    bars = await md.candles(symbol, timeframe)
    cache.put(key, bars, CANDLE_TTL.get(timeframe, 60))
    return bars


# ---------- watchlist ----------

PERIODS = ("1W", "1M", "3M")


def months_back(d: date, months: int) -> date:
    """Same day `months` earlier, clamped to the end of a shorter month (May 31 -> Feb 28/29)."""
    y, m = divmod(d.month - 1 - months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def reference_dates(today: date) -> dict[str, date]:
    """The dates whose closing price each change is measured from."""
    return {"1W": today - timedelta(days=7), "1M": months_back(today, 1), "3M": months_back(today, 3)}


def close_on_or_before(closes: list[DailyClose], day: date) -> float | None:
    """Closing price on `day`, or on the last trading day before it (weekends, holidays)."""
    best = None
    for c in closes:
        if c.day <= day:
            best = c.close
        else:
            break
    return best


def pct_change(last: float | None, ref: float | None) -> float | None:
    if last is None or ref is None or ref == 0:
        return None
    return (last - ref) / ref * 100


async def reference_closes(cache: TTLCache, user_id: int, md: MarketData, symbol: str, today: date) -> dict[str, float | None]:
    """Closing prices 1 week, 1 month and 3 months ago. They only change once a day."""
    key = (user_id, "refs", symbol, today)
    hit = cache.get(key)
    if hit is not None:
        return hit
    refs = reference_dates(today)
    closes = await md.daily_closes(symbol, min(refs.values()) - timedelta(days=10), today)
    result = {p: close_on_or_before(closes, d) for p, d in refs.items()}
    cache.put(key, result, 3600)
    return result


def watchlist_row(symbol: str, quote: Quote | None, refs: dict[str, float | None]) -> dict:
    last = quote.last if quote else None
    prev = quote.prev_close if quote else None
    return {
        "symbol": symbol,
        "description": quote.description if quote else "",
        "found": quote is not None,
        "last": last,
        "prev_close": prev,
        "refs": refs,
        "change": {"1D": pct_change(last, prev), **{p: pct_change(last, refs.get(p)) for p in PERIODS}},
    }


async def watchlist(cache: TTLCache, user_id: int, md: MarketData, symbols: list[str]) -> list[dict]:
    today = datetime.now(NY).date()
    quotes = await md.quotes(symbols)

    async def refs_for(s: str) -> dict:
        if s not in quotes:
            return {p: None for p in PERIODS}
        try:
            return await reference_closes(cache, user_id, md, s, today)
        except MarketDataError:
            return {p: None for p in PERIODS}

    all_refs = await asyncio.gather(*(refs_for(s) for s in symbols))
    return [watchlist_row(s, quotes.get(s), r) for s, r in zip(symbols, all_refs)]

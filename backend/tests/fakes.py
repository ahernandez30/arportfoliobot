"""A stand-in for a market data provider, so tests never call Tradier."""
import asyncio
from datetime import date

from app.marketdata.base import Bar, Clock, DailyClose, MarketData, MarketDataError, Quote, StreamEvent


class FakeMarketData(MarketData):
    def __init__(self, *, realtime: bool = True, label: str = "fake", quotes: dict | None = None,
                 closes: dict | None = None, events: list[StreamEvent] | None = None, bars: dict | None = None,
                 expirations: dict | None = None, chains: dict | None = None, clock_state: str = "open"):
        self.realtime = realtime
        self.label = label
        self._quotes = quotes or {}
        self._closes = closes or {}
        self._events = events or []
        # (symbol, timeframe) -> candles, for strategy tests.
        self._bars = bars or {}
        # symbol -> expiration dates, and (symbol, expiration) -> OptionQuotes, for option tests.
        self.expirations = expirations or {}
        self.chains = chains or {}
        self.clock_state = clock_state
        self.calls: list[tuple] = []

    async def quotes(self, symbols):
        self.calls.append(("quotes", tuple(symbols)))
        return {s: self._quotes[s] for s in symbols if s in self._quotes}

    async def candles(self, symbol, timeframe, start=None):
        self.calls.append(("candles", symbol, timeframe))
        if (symbol, timeframe) in self._bars:
            return self._bars[(symbol, timeframe)]
        if symbol == "ERR":
            raise MarketDataError("Tradier returned an error (500).")
        return [Bar(1_790_000_000, 1.0, 2.0, 0.5, 1.5, 100.0), Bar(1_790_086_400, 1.5, 2.5, 1.0, 2.0, 200.0)]

    async def daily_closes(self, symbol, start: date, end: date):
        self.calls.append(("daily_closes", symbol))
        return [c for c in self._closes.get(symbol, []) if start <= c.day <= end]

    async def option_expirations(self, symbol):
        return list(self.expirations.get(symbol, []))

    async def option_chain(self, symbol, expiration):
        return list(self.chains.get((symbol, expiration), []))

    async def clock(self):
        return Clock(self.clock_state, "Market clock", "16:00", "postmarket")

    async def stream(self, symbols):
        yield StreamEvent("ready", "")
        for ev in self._events:
            if ev.symbol in symbols:
                yield ev
        # Stay connected quietly, like a real stream with no trades.
        await asyncio.sleep(3600)


def closes_for(rows: list[tuple[str, float]]) -> list[DailyClose]:
    return [DailyClose(date.fromisoformat(d), c) for d, c in rows]


def quote(symbol: str, last: float, prev: float) -> Quote:
    return Quote(symbol=symbol, description=f"{symbol} Inc", last=last, prev_close=prev, bid=last - 0.01, ask=last + 0.01)

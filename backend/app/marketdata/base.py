"""The MarketData interface. Everything else in the app talks to this, never to a provider directly,
so a provider can be swapped or added without touching screens, the worker, or strategies."""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date

# Chart timeframes the site offers (plan section 6, Charts).
TIMEFRAMES = ("1m", "5m", "15m", "1h", "1D", "1W")
INTRADAY = ("1m", "5m", "15m", "1h")


class MarketDataError(Exception):
    """A problem talking to the provider, with a message fit for the screen."""

    def __init__(self, message: str, *, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Bar:
    """One candle. `time` is the bar's start in Unix seconds (UTC). Daily and weekly bars
    start at 00:00 UTC of their date, so the date reads the same in every time zone."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Quote:
    symbol: str
    description: str = ""
    last: float | None = None
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    bid: float | None = None
    ask: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    # Milliseconds since 1970 (UTC) of the last trade.
    trade_time: int | None = None


@dataclass(frozen=True)
class DailyClose:
    day: date
    close: float


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    underlying: str
    option_type: str  # "call" or "put"
    strike: float
    expiration: date
    bid: float | None
    ask: float | None
    last: float | None
    volume: float | None
    open_interest: float | None


@dataclass(frozen=True)
class Clock:
    state: str  # "premarket", "open", "postmarket", "closed"
    description: str
    next_change: str
    next_state: str


@dataclass
class StreamEvent:
    """A live update. kind is "trade", "quote" or "summary" (only the fields that event carries are
    set), or "ready" once the stream is connected."""

    kind: str
    symbol: str
    fields: dict = field(default_factory=dict)


class MarketData(ABC):
    """Prices, candles, option chains and a live stream. One instance per user key."""

    #: True when prices are real-time; False when the provider delays them (e.g. 15 minutes).
    realtime: bool = True

    @abstractmethod
    async def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Quotes by symbol. Unknown symbols are left out."""

    @abstractmethod
    async def candles(self, symbol: str, timeframe: str, start: date | None = None) -> list[Bar]:
        """Regular-session candles, oldest first. Daily and weekly candles go back to `start` when
        given (the strategy engine wants long histories); intraday history is what the provider keeps."""

    @abstractmethod
    async def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        """Daily closing prices between two dates, oldest first."""

    @abstractmethod
    async def option_expirations(self, symbol: str) -> list[date]:
        """Listed option expiration dates, soonest first."""

    @abstractmethod
    async def option_chain(self, symbol: str, expiration: date) -> list[OptionQuote]:
        """Calls and puts for one expiration."""

    @abstractmethod
    async def clock(self) -> Clock:
        """Whether the market is open now."""

    @abstractmethod
    def stream(self, symbols: list[str]) -> AsyncIterator[StreamEvent]:
        """Live trades, quotes and session summaries for the symbols, until cancelled."""

    async def aclose(self) -> None:
        """Release connections."""

"""Past option prices for option backtests (plan Stage 7, open decision 5).

Everything else asks an `OptionHistory` for strikes, expirations and bid/ask at a moment in the
past, so real history (Databento, Rafa's choice for later) can replace the estimate without
touching the backtest or the screens.

`ModelOptionHistory` (Rafa's choice for now, 2026-10-05) ESTIMATES prices: the standard
Black-Scholes formula, with the stock's own volatility over the previous 30 trading days, a fixed
interest rate, no dividends, Friday expirations every week, a strike grid like the listed one, and
a bid/ask spread around the model price. Real prices differ (volatility smiles, events, wider
spreads in fast markets), so every result built on it is labelled an estimate.
"""
import bisect
import math
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.marketdata.base import DailyClose

RATE = 0.04  # yearly interest rate used for pricing
VOL_DAYS = 30  # trading days of closes behind the volatility
MIN_VOL, MAX_VOL = 0.05, 3.0
HALF_SPREAD_PCT = 0.015  # half the bid/ask spread, as a share of the price...
MIN_HALF_SPREAD = 0.025  # ...but never under 2.5 cents
TICK = Decimal("0.01")


class OptionHistory(ABC):
    """Past option chains. `at` is a moment in New York time; `underlying` the stock price then."""

    #: Shown beside results: "estimate" for a model, or the data provider's name.
    source: str

    @abstractmethod
    def expirations(self, symbol: str, at: datetime) -> list[date]:
        """Expirations listed at that moment, soonest first."""

    @abstractmethod
    def strikes(self, symbol: str, at: datetime, underlying: float) -> list[Decimal]:
        """Listed strikes around the price, lowest first."""

    @abstractmethod
    def quote(self, symbol: str, option_type: str, strike: Decimal, expiration: date, at: datetime,
              underlying: float) -> tuple[Decimal | None, Decimal | None]:
        """Bid and ask of one option at that moment."""


# ---------- the model ----------


def ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes(option_type: str, spot: float, strike: float, years: float, vol: float, rate: float = RATE) -> float:
    """Price per share of a European option. At or after expiration: what it is in the money."""
    intrinsic = max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    if years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return intrinsic
    sq = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2.0) * years) / sq
    d2 = d1 - sq
    disc = math.exp(-rate * years)
    if option_type == "call":
        return spot * ncdf(d1) - strike * disc * ncdf(d2)
    return strike * disc * ncdf(-d2) - spot * ncdf(-d1)


def historical_vol(closes: list[float]) -> float | None:
    """Yearly volatility from daily closing prices (standard deviation of daily log changes x sqrt 252)."""
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 5:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return min(MAX_VOL, max(MIN_VOL, math.sqrt(var * 252.0)))


def strike_step(price: float) -> float:
    """Distance between listed strikes near the price, roughly as exchanges list them."""
    if price < 25:
        return 0.5
    if price < 100:
        return 1.0
    if price < 250:
        return 2.5
    if price < 1000:
        return 5.0
    return 10.0


def fridays_after(day: date, weeks: int = 104) -> list[date]:
    first = day + timedelta(days=(4 - day.weekday()) % 7)
    return [first + timedelta(weeks=k) for k in range(weeks)]


def _cents(x: float) -> Decimal:
    return Decimal(str(round(max(x, 0.0), 2))).quantize(TICK)


class ModelOptionHistory(OptionHistory):
    source = "estimate"

    def __init__(self, daily: dict[str, list[DailyClose]], rate: float = RATE):
        # Daily closes by symbol, oldest first: the volatility at a moment uses only closes before it.
        self.daily = {s: sorted(rows, key=lambda c: c.day) for s, rows in daily.items()}
        self._days = {s: [c.day for c in rows] for s, rows in self.daily.items()}
        self.rate = rate

    def vol(self, symbol: str, day: date) -> float | None:
        days = self._days.get(symbol, [])
        i = bisect.bisect_right(days, day)
        closes = [c.close for c in self.daily[symbol][max(0, i - VOL_DAYS - 1):i]] if days else []
        return historical_vol(closes)

    def expirations(self, symbol: str, at: datetime) -> list[date]:
        d = at.date()
        # An expiration on the day itself has already finished after 16:00.
        return [f for f in fridays_after(d) if f > d or at.hour < 16]

    def strikes(self, symbol: str, at: datetime, underlying: float) -> list[Decimal]:
        step = strike_step(underlying)
        lo = math.floor(underlying * 0.5 / step) * step
        hi = math.ceil(underlying * 1.5 / step) * step
        n = int(round((hi - lo) / step))
        return [Decimal(str(round(lo + k * step, 2))) for k in range(n + 1) if lo + k * step > 0]

    def quote(self, symbol: str, option_type: str, strike: Decimal, expiration: date, at: datetime,
              underlying: float) -> tuple[Decimal | None, Decimal | None]:
        vol = self.vol(symbol, at.date())
        if vol is None:
            return None, None
        close = datetime(expiration.year, expiration.month, expiration.day, 16, 0, tzinfo=at.tzinfo)
        years = max((close - at).total_seconds(), 0.0) / (365.0 * 86400.0)
        mid = black_scholes(option_type, underlying, float(strike), years, vol, self.rate)
        half = max(MIN_HALF_SPREAD, mid * HALF_SPREAD_PCT)
        return _cents(mid - half), _cents(mid + half)

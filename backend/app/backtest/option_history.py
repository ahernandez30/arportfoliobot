"""Past option prices for option backtests (plan Stage 7, open decision 5).

Everything else asks an `OptionHistory` for strikes, expirations and bid/ask at a moment in the
past, so real history (Databento, Rafa's choice for later) can replace the estimate without
touching the backtest or the screens.

`ModelOptionHistory` (Rafa's choice for now, 2026-10-05) ESTIMATES prices: the standard
Black-Scholes formula, a fixed interest rate, no dividends, Friday expirations every week, a strike
grid like the listed one, and a bid/ask spread around the model price. The volatility (Rafa,
2026-10-17, fitted to real TSLA quotes April 2023 to October 2026) blends the stock's own over the
previous 30 and 252 trading days, plus a smile that rises the further the strike is from the price:
real option prices move much less than a 30-day volatility does, and charge more far from the money.
Using the 30-day one alone priced far out-of-the-money options after a quiet month at a few cents,
which made such backtests look absurdly good. Real prices still differ (events, wider spreads in
fast markets), so every result built on it is labelled an estimate.
"""
import bisect
import math
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import NormalDist

from app.marketdata.base import DailyClose

RATE = 0.04  # yearly interest rate used for pricing
VOL_DAYS = 30  # trading days of closes behind the short volatility...
LONG_VOL_DAYS = 252  # ...and the long one
SHORT_WEIGHT = 0.3  # the short one's share of the blend
# The smile: volatility x exp(LEVEL + UP or DOWN x d + CURVE x d^2), d the strike's distance from the
# price in standard deviations to expiration (|ln(strike / price)| / (vol x sqrt(years))), UP for
# strikes above the price and DOWN below. Fitted to real TSLA quotes (227,733 near or out of the money,
# April 2023 to October 2026): typical miss of the price 10% instead of 23%, over 25% from the money
# 25% instead of 72%.
SMILE_LEVEL, SMILE_UP, SMILE_DOWN, SMILE_CURVE = -0.0754, 0.0231, 0.0337, 0.0309
SMILE_MAX_D = 4.0  # the smile stops rising beyond this distance
MIN_VOL, MAX_VOL = 0.05, 3.0
HALF_SPREAD_PCT = 0.0045  # half the bid/ask spread, as a share of the price (real TSLA: 0.44%, checked 2026-10-09)...
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


def delta(option_type: str, spot: float, strike: float, years: float, vol: float, rate: float = RATE) -> float | None:
    """How much the option's price moves per $1 of the stock (puts negative)."""
    if years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return None
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2.0) * years) / (vol * math.sqrt(years))
    return ncdf(d1) if option_type == "call" else ncdf(d1) - 1.0


def implied_vol(option_type: str, price: float, spot: float, strike: float, years: float, rate: float = RATE) -> float | None:
    """The volatility at which the formula gives this price, or None when no volatility does
    (a price below what the option is worth at expiration, or above the stock or strike)."""
    if years <= 0 or price <= 0 or spot <= 0 or strike <= 0:
        return None
    lo, hi = 0.005, 8.0
    if not black_scholes(option_type, spot, strike, years, lo, rate) <= price <= black_scholes(option_type, spot, strike, years, hi, rate):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if black_scholes(option_type, spot, strike, years, mid, rate) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def strike_for_delta(option_type: str, spot: float, target: float, years: float, vol: float, rate: float = RATE) -> float:
    """The strike whose delta (as a positive number, 0 to 1) is `target`."""
    target = min(max(target, 0.001), 0.999)
    d1 = NormalDist().inv_cdf(target if option_type == "call" else 1.0 - target)
    sq = vol * math.sqrt(years)
    return spot * math.exp((rate + vol * vol / 2.0) * years - d1 * sq)


def historical_vol(closes: list[float]) -> float | None:
    """Yearly volatility from daily closing prices (standard deviation of daily log changes x sqrt 252)."""
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 5:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return min(MAX_VOL, max(MIN_VOL, math.sqrt(var * 252.0)))


def smile_vol(base: float, spot: float, strike: float, years: float) -> float:
    """The volatility for one strike: the stock's blended volatility, adjusted by the smile."""
    if years <= 0 or base <= 0:
        return base
    k = math.log(strike / spot)
    d = min(abs(k) / (base * math.sqrt(years)), SMILE_MAX_D)
    tilt = SMILE_UP if k > 0 else SMILE_DOWN
    return min(MAX_VOL, max(MIN_VOL, base * math.exp(SMILE_LEVEL + tilt * d + SMILE_CURVE * d * d)))


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

    def vol(self, symbol: str, day: date, after_close: bool = True, n: int = VOL_DAYS) -> float | None:
        """Volatility over `n` trading days from the closes known then: the day's own close only once
        it has happened (fewer days early in the history)."""
        days = self._days.get(symbol, [])
        i = bisect.bisect_right(days, day) if after_close else bisect.bisect_left(days, day)
        closes = [c.close for c in self.daily[symbol][max(0, i - n - 1):i]] if days else []
        return historical_vol(closes)

    def blended_vol(self, symbol: str, day: date, after_close: bool = True) -> float | None:
        """The short and long volatility blended, before the smile."""
        short = self.vol(symbol, day, after_close)
        if short is None:
            return None
        long = self.vol(symbol, day, after_close, LONG_VOL_DAYS) or short
        return SHORT_WEIGHT * short + (1 - SHORT_WEIGHT) * long

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
        base = self.blended_vol(symbol, at.date(), after_close=at.hour >= 16)
        if base is None:
            return None, None
        close = datetime(expiration.year, expiration.month, expiration.day, 16, 0, tzinfo=at.tzinfo)
        years = max((close - at).total_seconds(), 0.0) / (365.0 * 86400.0)
        vol = smile_vol(base, underlying, float(strike), years)
        mid = black_scholes(option_type, underlying, float(strike), years, vol, self.rate)
        half = max(MIN_HALF_SPREAD, mid * HALF_SPREAD_PCT)
        return _cents(mid - half), _cents(mid + half)

"""Paper trading rules: pure functions, no database or network (plan section 8).

Prices are as quoted (per share); one option contract is 100 shares.
Default fill rule: buys fill at the ask and sells at the bid. The "mid" rule fills both
at the middle of bid and ask (a setting in Config).
"""
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal

from app.ledger import HUNDRED, ZERO, intrinsic

CENT = Decimal("0.01")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


def d(x: float | Decimal | None) -> Decimal | None:
    if x is None:
        return None
    return x if isinstance(x, Decimal) else Decimal(str(x))


@dataclass(frozen=True)
class Book:
    """The current bid and ask of one option."""

    bid: Decimal | None
    ask: Decimal | None

    @classmethod
    def of(cls, bid: float | None, ask: float | None) -> "Book":
        return cls(d(bid), d(ask))

    @property
    def two_sided(self) -> bool:
        """A usable market: both prices there, ask above zero, not crossed."""
        return self.bid is not None and self.ask is not None and self.ask > 0 and 0 <= self.bid <= self.ask

    @property
    def mid(self) -> Decimal | None:
        return (self.bid + self.ask) / 2 if self.two_sided else None


def market_price(side: str, book: Book, rule: str) -> Decimal | None:
    """What an order on this side would get right now, or None when there is no usable price."""
    if rule == "mid":
        return book.mid
    if side == "buy":
        return book.ask if book.ask is not None and book.ask > 0 else None
    # Selling at a bid of zero is real (a worthless option), but only with an ask showing a market.
    return book.bid if book.bid is not None and book.ask is not None and book.ask > 0 else None


def fill_price(side: str, book: Book, rule: str, limit: Decimal | None) -> Decimal | None:
    """The fill for a working order, or None if it does not fill yet. A limit order fills only
    when the quote reaches its limit, and then at the quote (never worse than the limit)."""
    price = market_price(side, book, rule)
    if price is None:
        return None
    if limit is not None:
        if side == "buy" and price > limit:
            return None
        if side == "sell" and price < limit:
            return None
    return price


def exit_prices(entry: Decimal, take_profit_pct: Decimal | None, stop_loss_pct: Decimal | None
                ) -> tuple[Decimal | None, Decimal | None]:
    """Target and stop option prices from the entry price and the percents, to the cent."""
    tp = (entry * (1 + take_profit_pct / HUNDRED)).quantize(CENT, ROUND_HALF_UP) if take_profit_pct else None
    sl = (entry * (1 - stop_loss_pct / HUNDRED)).quantize(CENT, ROUND_HALF_UP) if stop_loss_pct else None
    if sl is not None and sl < 0:
        sl = ZERO
    return tp, sl


def exit_trigger(book: Book, rule: str, take_profit: Decimal | None, stop_loss: Decimal | None) -> str | None:
    """Whether a held option has reached its target or stop, judged on the price it could be
    sold at now. Needs a proper two-sided quote, so one bad tick cannot stop a position out."""
    if not book.two_sided:
        return None
    price = market_price("sell", book, rule)
    if price is None:
        return None
    if take_profit is not None and price >= take_profit:
        return "take_profit"
    if stop_loss is not None and price <= stop_loss:
        return "stop_loss"
    return None


def order_value(quantity: int, price: Decimal) -> Decimal:
    return Decimal(quantity) * price * HUNDRED


def settlement_price(option_type: str, strike: Decimal, underlying: Decimal) -> Decimal:
    """At expiration an option is worth how far it is in the money, or nothing."""
    return intrinsic(option_type, strike, underlying).quantize(CENT, ROUND_HALF_UP)


def session_open(clock_state: str | None, now_ny: datetime) -> bool:
    """Options trade 9:30 to 16:00 New York time on market days. The provider's clock says
    whether today is a market day (holidays)."""
    if clock_state != "open":
        return False
    return now_ny.weekday() < 5 and SESSION_OPEN <= now_ny.time() < SESSION_CLOSE


def expired(expiration: date, now_ny: datetime) -> bool:
    """An option is done once 16:00 New York time on its expiration day has passed."""
    return now_ny.date() > expiration or (now_ny.date() == expiration and now_ny.time() >= SESSION_CLOSE)


@dataclass(frozen=True)
class Limits:
    max_order_usd: Decimal
    max_daily_loss_usd: Decimal


def opening_order_problem(*, halted: bool, quantity: int, limit: Decimal, available_cash: Decimal,
                          realized_today: Decimal, limits: Limits, expiration: date, today: date,
                          what: str = "cost") -> str | None:
    """Why a new opening order must be refused, in plain words, or None if it may go ahead.
    The limits apply to manual and automatic orders alike (plan section 10). For a credit spread
    `limit` is the most one spread can lose per share and `what` is "risk"."""
    if halted:
        return "Trading is stopped. Press “Resume trading” on the Live Trader first."
    if expiration < today:
        return "That option has already expired."
    cost = order_value(quantity, limit)
    if cost > limits.max_order_usd:
        verb = "could lose" if what == "risk" else "would cost"
        return (f"This order {verb} ${cost:,.2f}, more than your largest order allowed "
                f"(${limits.max_order_usd:,.2f}). Change it in Config → Trading.")
    if realized_today <= -limits.max_daily_loss_usd:
        return (f"Today's paper losses (${-realized_today:,.2f}) have reached your daily limit "
                f"(${limits.max_daily_loss_usd:,.2f}). New orders are refused until tomorrow.")
    if cost > available_cash:
        return f"Not enough paper cash: this order needs ${cost:,.2f} and ${available_cash:,.2f} is free."
    return None

"""Money math for Capital Tracking and Account Manager. Pure functions, no database or network.

All amounts are Decimal. Prices are as quoted: per share for stock, and per share of the
contract for options, so one option contract at 5.20 costs 5.20 x 100 = $520.

Long-term positions use the average-cost method: a sale takes out its share of the
average cost, and the difference (less fees) is the realized gain.
"""
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")


class LedgerError(ValueError):
    """A change that would make the record impossible (for example selling more than is held)."""


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def multiplier(kind: str) -> Decimal:
    """Shares per unit: one option contract covers 100 shares (plan section 6)."""
    return HUNDRED if kind == "option" else Decimal(1)


def occ_symbol(symbol: str, option_type: str, strike: Decimal, expiration: date) -> str:
    """The standard option symbol, e.g. TSLA 2026-12-18 call 450 -> TSLA261218C00450000."""
    thousandths = int((Decimal(strike) * 1000).to_integral_value(rounding=ROUND_HALF_UP))
    return f"{symbol.replace('.', '')}{expiration:%y%m%d}{'C' if option_type == 'call' else 'P'}{thousandths:08d}"


def mid_price(bid: float | None, ask: float | None, last: float | None) -> Decimal | None:
    """An option's value: the middle of bid and ask (plan section 6), else the last trade."""
    if bid is not None and ask is not None and ask > 0 and 0 <= bid <= ask:
        return (Decimal(str(bid)) + Decimal(str(ask))) / 2
    if last is not None:
        return Decimal(str(last))
    return None


def intrinsic(option_type: str, strike: Decimal, underlying: Decimal) -> Decimal:
    """What an option is worth at expiration: how far in the money it is, or nothing."""
    gap = underlying - strike if option_type == "call" else strike - underlying
    return max(gap, ZERO)


# ---------- long-term positions ----------


@dataclass(frozen=True)
class Fill:
    side: str  # "buy" or "sell"
    quantity: Decimal
    price: Decimal
    fees: Decimal
    day: date
    id: int = 0


@dataclass
class Holding:
    quantity: Decimal = ZERO
    cost: Decimal = ZERO  # what the units still held cost, fees included
    realized: Decimal = ZERO  # gain from sales, after all fees
    cash_out: Decimal = ZERO  # paid for buys, fees included
    cash_in: Decimal = ZERO  # received from sales, after fees
    opened: date | None = None
    closed: date | None = None  # the day the quantity last went to zero
    mult: Decimal = Decimal(1)  # shares per unit

    @property
    def average_price(self) -> Decimal | None:
        """Average cost as quoted (per share, so comparable with today's price), fees included."""
        return self.cost / self.quantity / self.mult if self.quantity else None


def replay(fills: Iterable[Fill], kind: str) -> Holding:
    """Works out a position from its buys and sells, oldest first. Raises LedgerError when
    a sale is for more than was held on that day."""
    m = multiplier(kind)
    h = Holding(mult=m)
    for f in sorted(fills, key=lambda f: (f.day, f.side != "buy", f.id)):
        gross = f.quantity * f.price * m
        if f.side == "buy":
            h.quantity += f.quantity
            h.cost += gross + f.fees
            h.cash_out += gross + f.fees
            h.opened = h.opened or f.day
            h.closed = None
        else:
            if f.quantity > h.quantity:
                raise LedgerError(
                    f"That sells {fmt_qty(f.quantity)} on {f.day:%b %d, %Y}, but only {fmt_qty(h.quantity)} "
                    "was held then."
                )
            share_of_cost = h.cost * f.quantity / h.quantity
            h.realized += gross - f.fees - share_of_cost
            h.cost -= share_of_cost
            h.quantity -= f.quantity
            h.cash_in += gross - f.fees
            if h.quantity == 0:
                h.cost = ZERO
                h.closed = f.day
    return h


def fmt_qty(q: Decimal) -> str:
    return f"{q.normalize():f}"


@dataclass(frozen=True)
class Valued:
    """A held position at today's price. `price` is None when no price was available,
    in which case it is counted at its cost (and flagged)."""

    quantity: Decimal
    cost: Decimal
    price: Decimal | None
    kind: str

    @property
    def value(self) -> Decimal:
        if self.price is None:
            return self.cost
        return self.quantity * self.price * multiplier(self.kind)

    @property
    def gain(self) -> Decimal:
        return self.value - self.cost

    @property
    def gain_pct(self) -> Decimal | None:
        return self.gain / self.cost * HUNDRED if self.cost else None


def net_flows(flows: Iterable[tuple[str, Decimal]]) -> Decimal:
    """Deposits minus withdrawals. `flows` holds (kind, amount) pairs."""
    total = ZERO
    for kind, amount in flows:
        total += amount if kind == "deposit" else -amount
    return total


@dataclass
class CapitalTotals:
    put_in: Decimal  # deposits minus withdrawals
    cash: Decimal
    positions_value: Decimal
    total: Decimal  # cash plus positions
    gain: Decimal  # total minus what was put in
    gain_pct: Decimal | None
    realized: Decimal
    unrealized: Decimal
    estimated: bool  # some position had no price and was counted at cost


def capital_totals(put_in: Decimal, holdings: Sequence[Holding], valued: Sequence[Valued]) -> CapitalTotals:
    """Totals for Capital Tracking. Cash is what was put in, less what buys cost, plus what
    sales brought; so total gain = realized + unrealized gains, and deposits never count as gain."""
    cash = put_in - sum((h.cash_out for h in holdings), ZERO) + sum((h.cash_in for h in holdings), ZERO)
    positions_value = sum((v.value for v in valued), ZERO)
    total = cash + positions_value
    gain = total - put_in
    return CapitalTotals(
        put_in=put_in,
        cash=cash,
        positions_value=positions_value,
        total=total,
        gain=gain,
        gain_pct=gain / put_in * HUNDRED if put_in > 0 else None,
        realized=sum((h.realized for h in holdings), ZERO),
        unrealized=sum((v.gain for v in valued), ZERO),
        estimated=any(v.price is None for v in valued),
    )


# ---------- short-term closed trades ----------


def trade_result(direction: str, kind: str, quantity: Decimal, entry: Decimal, exit_: Decimal,
                 fees: Decimal) -> tuple[Decimal, Decimal | None]:
    """A closed trade's result in dollars (after fees) and in percent of what was put at stake
    at entry. A short trade gains when the price falls."""
    m = multiplier(kind)
    move = exit_ - entry if direction == "long" else entry - exit_
    dollars = move * quantity * m - fees
    stake = entry * quantity * m
    return dollars, (dollars / stake * HUNDRED if stake else None)


@dataclass
class TradeStats:
    count: int = 0
    wins: int = 0
    losses: int = 0
    total: Decimal = ZERO
    win_total: Decimal = ZERO
    loss_total: Decimal = ZERO
    by_reason: dict = field(default_factory=dict)

    @property
    def win_rate(self) -> Decimal | None:
        """Winners as a percent of all trades (a trade that broke even is not a win)."""
        return Decimal(self.wins) / self.count * HUNDRED if self.count else None

    @property
    def average_win(self) -> Decimal | None:
        return self.win_total / self.wins if self.wins else None

    @property
    def average_loss(self) -> Decimal | None:
        return self.loss_total / self.losses if self.losses else None


def trade_stats(results: Iterable[tuple[Decimal, str]]) -> TradeStats:
    """Statistics over (result in dollars, close reason) pairs."""
    s = TradeStats()
    for dollars, reason in results:
        s.count += 1
        s.total += dollars
        if dollars > 0:
            s.wins += 1
            s.win_total += dollars
        elif dollars < 0:
            s.losses += 1
            s.loss_total += dollars
        r = s.by_reason.setdefault(reason, {"count": 0, "total": ZERO})
        r["count"] += 1
        r["total"] += dollars
    return s

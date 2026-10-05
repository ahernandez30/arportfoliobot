"""Turning a strategy signal into an option trade (plan 7.6). Pure functions: no database or network.

Rafa's rules (2026-10-05):
- Strike distance: the average move of the signal's WINNING trades on this symbol and timeframe with
  these settings (or a fixed percent the user types). Toward the signal by default, or away from it.
- Expiration: the first one after the average trade length plus a safety margin (by default the
  longer of: the average losing trade's length, or the average length plus 50%), or a fixed number
  of days the user types.
- Size: a fixed dollar amount at risk per trade (the most the position can lose).

Structures, for a BUY signal (a SELL is the mirror image):
- directional: buy a call.
- credit_spread: sell a put, buy the next lower strike put (bull put spread).
- debit_spread: buy a call at the lower strike, sell a call at the higher one (bull call spread), the
  same payoff as the "toward the signal" credit spread without early-assignment risk.
For spreads, the sold leg sits at the strike distance and the bought leg is the next listed strike
on the protective side, so the width is the two closest strikes.
"""
import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import ledger
from app.paper_rules import CENT, Book, market_price

HUNDRED = Decimal(100)
STRUCTURES = ("directional", "credit_spread", "debit_spread")
STRUCTURE_LABELS = {"directional": "Directional", "credit_spread": "Credit spread", "debit_spread": "Debit spread"}


class TradeSettings(BaseModel):
    """“What to trade on a signal”, saved with the pegged settings of one symbol and timeframe."""

    model_config = ConfigDict(extra="forbid")

    # One structure, or "compare": all three at once, each in its own paper sub-account.
    structure: Literal["directional", "credit_spread", "debit_spread", "compare"] = "credit_spread"
    distance_mode: Literal["auto", "fixed"] = "auto"
    distance_pct: float = Field(3.0, ge=0, le=50)
    side: Literal["toward", "away"] = "toward"
    expiry_mode: Literal["auto", "fixed"] = "auto"
    # Auto: hold = the longer of the average losing trade, or the average trade plus this percent.
    margin_pct: float = Field(50.0, ge=0, le=500)
    # Fixed: the expiration must be at least this many days away.
    expiry_days: int = Field(35, ge=1, le=800)
    risk_usd: float = Field(500.0, gt=0, le=10_000_000)

    def structures(self) -> tuple[str, ...]:
        return STRUCTURES if self.structure == "compare" else (self.structure,)

    def account_for(self, structure: str) -> str:
        """Comparing runs each structure in its own paper sub-account; a single one uses the main account."""
        return structure if self.structure == "compare" else "main"


def load_settings(raw: dict | None) -> TradeSettings:
    """Saved settings over the defaults; anything no longer valid falls back to its default."""
    raw = dict(raw or {})
    try:
        return TradeSettings.model_validate(raw)
    except ValidationError:
        out = {}
        for k, v in raw.items():
            try:
                TradeSettings.model_validate({k: v})
                out[k] = v
            except ValidationError:
                pass
        return TradeSettings.model_validate(out)


# ---------- what the strategy's own history says ----------


@dataclass(frozen=True)
class History:
    winners: int
    avg_win_move: float | None  # percent move of the stock on winning trades
    avg_days: float | None  # average trade length, calendar days
    avg_loss_days: float | None


def history_of(run: dict) -> History:
    wins = [abs(t["ret_pct"]) for t in run.get("trades", []) if t.get("counted") and t["ret_pct"] > 0]
    res = run.get("results") or {}
    return History(len(wins), sum(wins) / len(wins) if wins else None, res.get("avg_days"),
                   (res.get("total") or {}).get("days_loss"))


@dataclass(frozen=True)
class Rules:
    distance_pct: float
    distance_note: str
    hold_days: int
    hold_note: str


def rules_for(s: TradeSettings, h: History) -> Rules:
    """The strike distance and the shortest time to expiration, with how each was worked out."""
    if s.distance_mode == "fixed":
        dist, dnote = s.distance_pct, f"{s.distance_pct:g}% (fixed)"
    elif h.avg_win_move is None:
        dist, dnote = s.distance_pct, f"{s.distance_pct:g}% (no winning trades in the history yet, so the fixed value)"
    else:
        dist = round(min(h.avg_win_move, 50.0), 2)
        dnote = f"{dist:g}% (average move of {h.winners} winning trades)"
    if s.expiry_mode == "fixed":
        hold, hnote = s.expiry_days, f"at least {s.expiry_days} days (fixed)"
    elif h.avg_days is None:
        hold, hnote = s.expiry_days, f"at least {s.expiry_days} days (no finished trades in the history yet)"
    else:
        padded = h.avg_days * (1 + s.margin_pct / 100.0)
        hold = math.ceil(max(padded, h.avg_loss_days or 0.0, 1.0))
        parts = [f"average trade {h.avg_days:.1f} days + {s.margin_pct:g}% = {padded:.1f}"]
        if h.avg_loss_days:
            parts.append(f"average losing trade {h.avg_loss_days:.1f}")
        hnote = f"at least {hold} days ({'; '.join(parts)}; the longer one)"
    return Rules(dist, dnote, hold, hnote)


def pick_expiration(expirations: list[date], today: date, hold_days: int) -> date | None:
    """The first listed expiration at least `hold_days` away."""
    earliest = today + timedelta(days=hold_days)
    return next((d for d in sorted(expirations) if d >= earliest), None)


# ---------- choosing the contract ----------


@dataclass(frozen=True)
class Leg:
    strike: Decimal
    bid: Decimal | None
    ask: Decimal | None
    occ: str

    @property
    def book(self) -> Book:
        return Book(self.bid, self.ask)


@dataclass(frozen=True)
class Choice:
    structure: str
    option_type: str
    sold: Leg | None  # spreads only
    bought: Leg  # the option bought (directional) or the spread's protective/long leg
    target_strike: Decimal

    @property
    def width(self) -> Decimal:
        return abs(self.sold.strike - self.bought.strike) if self.sold else Decimal(0)


def option_type_for(structure: str, direction: int) -> str:
    if structure == "credit_spread":
        return "put" if direction == 1 else "call"
    return "call" if direction == 1 else "put"


def target_strike(price: Decimal, direction: int, distance_pct: float, side: str, structure: str) -> Decimal:
    """Where the sold leg (spreads) or the bought option (directional) should sit.

    "Toward the signal" is beyond the price in the signal's direction: above it for a BUY."""
    d = Decimal(str(distance_pct)) / HUNDRED
    up = (direction == 1) == (side == "toward")
    return price * (1 + d) if up else price * (1 - d)


def choose(structure: str, direction: int, price: Decimal, distance_pct: float, side: str,
           legs: list[Leg]) -> Choice | str:
    """The contract(s) to trade, or why there is none. `legs` are the listed strikes of the right
    option type for the chosen expiration."""
    otype = option_type_for(structure, direction)
    usable = sorted((lg for lg in legs if lg.book.two_sided), key=lambda lg: lg.strike)
    if not usable:
        return f"No {otype}s with a two-sided quote for that expiration."
    tgt = target_strike(price, direction, distance_pct, side, structure)
    best = min(usable, key=lambda lg: (abs(lg.strike - tgt), lg.strike))
    if structure == "directional":
        return Choice(structure, otype, None, best, tgt)
    i = usable.index(best)
    j = i - 1 if direction == 1 else i + 1
    if not 0 <= j < len(usable):
        return f"No listed strike {'below' if direction == 1 else 'above'} {best.strike} for the spread's other leg."
    return Choice(structure, otype, best, usable[j], tgt)


# ---------- prices, risk and size ----------


def _clamp(x: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, x))


def spread_price(structure: str, sold: Book, bought: Book, rule: str, opening: bool, width: Decimal) -> Decimal | None:
    """Net price per share of a spread, as a positive number, or None without usable quotes.

    Credit spread: opening sells the pair for a credit, closing buys it back for a debit.
    Debit spread: opening buys the pair for a debit, closing sells it for a credit.
    Each leg fills like a single option under the fill rule (sell at the bid, buy at the ask, or mid)."""
    if not (sold.two_sided and bought.two_sided):
        return None
    # Either kind opens by selling the sold leg and buying the other, and closes the other way round.
    s = market_price("sell" if opening else "buy", sold, rule)
    b = market_price("buy" if opening else "sell", bought, rule)
    if s is None or b is None:
        return None
    net = s - b if structure == "credit_spread" else b - s
    return _clamp(net, Decimal(0), width).quantize(CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class Sizing:
    price: Decimal  # per share: the option's price, or the spread's net credit or debit
    unit_risk: Decimal  # dollars one contract or spread can lose
    unit_reward: Decimal | None  # dollars one can make (None: no fixed ceiling)
    quantity: int

    @property
    def payout(self) -> float | None:
        """Most it can make for each dollar it can lose."""
        if self.unit_reward is None or not self.unit_risk:
            return None
        return float(round(self.unit_reward / self.unit_risk, 2))


def size(choice: Choice, rule: str, risk_usd: Decimal) -> Sizing | str:
    """The opening price, risk and number of contracts or spreads, or why it cannot be traded."""
    if choice.structure == "directional":
        price = market_price("buy", choice.bought.book, rule)
        if price is None or price <= 0:
            return "No price to buy that option at."
        unit_risk, unit_reward = price * HUNDRED, None
    else:
        w = choice.width
        price = spread_price(choice.structure, choice.sold.book, choice.bought.book, rule, True, w)
        if price is None:
            return "No two-sided quotes for both legs of the spread."
        if choice.structure == "credit_spread":
            if price <= 0:
                return "The spread pays no credit at current prices."
            if price >= w:
                return "The spread's credit is not below its width at current prices; quotes look wrong."
            unit_risk, unit_reward = (w - price) * HUNDRED, price * HUNDRED
        else:
            if price <= 0 or price >= w:
                return "The spread's price is not between zero and its width; quotes look wrong."
            unit_risk, unit_reward = price * HUNDRED, (w - price) * HUNDRED
    qty = int(risk_usd // unit_risk)
    if qty < 1:
        what = "contract" if choice.structure == "directional" else "spread"
        return (f"Your risk per trade (${risk_usd:,.2f}) is less than what one {what} can lose "
                f"(${unit_risk:,.2f}). Raise it in “What to trade on a signal”.")
    return Sizing(price, unit_risk, unit_reward, qty)


def describe(choice: Choice, expiration: date, symbol: str) -> str:
    k = ledger.fmt_qty
    exp = f"{expiration:%b %d %Y}"
    if choice.structure == "directional":
        return f"Buy {symbol} {k(choice.bought.strike)} {choice.option_type}, {exp}"
    name = {("credit_spread", "put"): "Bull put spread", ("credit_spread", "call"): "Bear call spread",
            ("debit_spread", "call"): "Bull call spread", ("debit_spread", "put"): "Bear put spread"}[
        (choice.structure, choice.option_type)]
    return (f"{name} {symbol}: sell {k(choice.sold.strike)} {choice.option_type}, "
            f"buy {k(choice.bought.strike)} {choice.option_type}, {exp}")

"""Option backtests: each strategy trade replayed as one option setup the user chooses (Rafa,
2026-10-10): a long call or put, a debit spread or a credit spread.

- Strike: the bought option (long call/put, debit spread) or the sold one (credit spread) sits a
  percent from the stock price (positive: out of the money) or at a delta, chosen per setup.
- Spreads: the other leg is the listed strike nearest to that strike plus the width in dollars,
  further out of the money.
- Expiration: the first at least a fixed number of days away, or (auto) the average trade length
  plus a margin, from only the trades that had already closed when the signal fired.
- Size: as many as the dollar risk allows (the most the position can lose), but at least one so
  every signal is traded (Rafa, 2026-10-10); fees per contract.
- Prices: the candles are split-adjusted, but contracts were listed on the price of the day, so
  each position is priced on the stock's price in that day's terms (strikes, width and size real).
- Exits follow the strategy on the stock chart; a position the strategy left floating, or one
  still open the market day before expiration, is closed that day, as live.
Every position keeps its transactions (each leg's fill) for the transaction log.
"""
import bisect
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app import auto_plan, ledger
from app.history import SPLITS, split_factor
from app.backtest.option_history import OptionHistory, delta, implied_vol, strike_for_delta
from app.backtest.stats import Outcome
from app.inputs import Strict
from app.marketdata.bars import NY
from app.marketdata.base import DailyClose
from app.paper_rules import Book, market_price
from app.strategy.base import TIMEFRAME_SECONDS

HUNDRED = Decimal(100)
STRIKE_WINDOW = 0.35  # strikes looked at: within this share of the price...
NEAREST_STRIKES = 8  # ...and of those, the ones nearest the target
OTHER_LEG_STRIKES = 3  # strikes priced around a spread's other leg
STRUCTURE_LABELS = {"directional": "Long call/put", "debit_spread": "Debit spread", "credit_spread": "Credit spread"}


class OptionSetup(Strict):
    """What a backtest trades on each signal (one setup per run)."""

    structure: Literal["directional", "debit_spread", "credit_spread"] = "directional"
    strike_by: Literal["pct", "delta"] = "pct"
    # Percent from the stock price, out of the money (negative: in the money).
    strike_pct: float = Field(0.0, ge=-50, le=50)
    # Delta as a positive number (0.50: about at the money).
    strike_delta: float = Field(0.50, ge=0.05, le=0.95)
    width_usd: float = Field(10.0, gt=0, le=1000)
    expiry_mode: Literal["auto", "fixed"] = "auto"
    margin_pct: float = Field(50.0, ge=0, le=500)
    expiry_days: int = Field(35, ge=1, le=800)
    risk_usd: float = Field(5000.0, gt=0, le=10_000_000)
    commission: float = Field(0.0, ge=0, le=20)  # dollars per contract, each time one is bought or sold

    def rules_settings(self) -> auto_plan.TradeSettings:
        """The expiration settings in the form app.auto_plan works out (the distance part is unused)."""
        return auto_plan.TradeSettings(expiry_mode=self.expiry_mode, margin_pct=self.margin_pct,
                                       expiry_days=self.expiry_days, distance_mode="fixed", risk_usd=self.risk_usd)


def close_moment(bar_time: int, tf: str) -> datetime:
    """When a candle closes, New York time (daily and weekly candles close at 16:00)."""
    if tf in ("1D", "1W"):
        d = datetime.fromtimestamp(bar_time, timezone.utc).date()
        last = d + timedelta(days=4) if tf == "1W" else d
        return datetime(last.year, last.month, last.day, 16, 0, tzinfo=NY)
    start = datetime.fromtimestamp(bar_time, NY)
    return min(start + timedelta(seconds=TIMEFRAME_SECONDS[tf]), start.replace(hour=16, minute=0, second=0))


def last_day_before(expiration: date) -> date:
    d = expiration - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


class PriorHistory:
    """What the strategy's own record showed at a moment: built only from trades closed before it."""

    def __init__(self, trades: list[dict], tf: str):
        done = sorted((t for t in trades if t["counted"]), key=lambda t: t["exit_time"])
        self.close_times = [close_moment(t["exit_time"], tf) for t in done]
        self.trades = done

    def at(self, moment: datetime) -> auto_plan.History:
        k = bisect.bisect_left(self.close_times, moment)
        past = self.trades[:k]
        wins = [abs(t["ret_pct"]) for t in past if t["ret_pct"] > 0]
        losses = [t["days"] for t in past if t["ret_pct"] < 0]
        return auto_plan.History(len(wins), sum(wins) / len(wins) if wins else None,
                                 sum(t["days"] for t in past) / len(past) if past else None,
                                 sum(losses) / len(losses) if losses else None)


@dataclass
class Leg:
    entry_time: int
    price: float  # the stock at entry


def legs_of(trade: dict, entries: list[dict]) -> list[Leg]:
    """A basket opens one option position per entry; other modes one per trade."""
    times = trade.get("entry_times")
    if not times:
        return [Leg(trade["entry_time"], trade["entry_price"])]
    by_time = {(e["time"], e["dir"]): e["price"] for e in entries}
    return [Leg(t, by_time.get((t, trade["dir"]), trade["entry_price"])) for t in times]


def close_on(daily: list[DailyClose], days: list[date], d: date) -> float | None:
    i = bisect.bisect_right(days, d)
    return daily[i - 1].close if i else None


def years_to(exp: date, at: datetime) -> float:
    close = datetime(exp.year, exp.month, exp.day, 16, 0, tzinfo=NY)
    return max((close - at).total_seconds(), 0.0) / (365.0 * 86400.0)


def leg_delta(otype: str, leg: auto_plan.Leg, price: float, years: float) -> float | None:
    """The delta implied by the leg's mid price, as a positive number."""
    mid = leg.book.mid
    if mid is None:
        return None
    vol = implied_vol(otype, float(mid), price, float(leg.strike), years)
    d = delta(otype, price, float(leg.strike), years, vol) if vol else None
    return abs(d) if d is not None else None


@dataclass
class Placed:
    choice: auto_plan.Choice
    delta: float | None  # of the strike placed (the bought option, or the credit spread's sold one)


def place(setup: OptionSetup, direction: int, price: float, at: datetime, exp: date, history: OptionHistory,
          symbol: str) -> Placed | str:
    """The contract(s) for one signal, or why there are none."""
    otype = auto_plan.option_type_for(setup.structure, direction)
    years = years_to(exp, at)
    lo, hi = price * (1 - STRIKE_WINDOW), price * (1 + STRIKE_WINDOW)
    listed = [k for k in history.strikes(symbol, at, price) if lo <= float(k) <= hi]
    if not listed:
        return "No listed strikes near the price."
    quoted: dict[Decimal, auto_plan.Leg] = {}

    def leg(k: Decimal) -> auto_plan.Leg:
        if k not in quoted:
            bid, ask = history.quote(symbol, otype, k, exp, at, price)
            quoted[k] = auto_plan.Leg(k, bid, ask, f"{otype}{k}")
        return quoted[k]

    def nearest(target: float, pool: list[Decimal], n: int) -> list[Decimal]:
        return sorted(pool, key=lambda k: (abs(float(k) - target), k))[:n]

    sign = 1 if otype == "call" else -1  # out of the money is above the price for calls
    if setup.strike_by == "pct":
        target = price * (1 + sign * setup.strike_pct / 100.0)
        usable = [lg for lg in map(leg, nearest(target, listed, NEAREST_STRIKES)) if lg.book.two_sided]
        if not usable:
            return f"No {otype}s with a two-sided quote near the strike."
        best = min(usable, key=lambda lg: (abs(float(lg.strike) - target), lg.strike))
        best_delta = leg_delta(otype, best, price, years)
    else:
        # A first guess from the nearest-the-money option's volatility, then each nearby strike's own delta.
        atm = leg(nearest(price, listed, 1)[0])
        vol = implied_vol(otype, float(atm.book.mid), price, float(atm.strike), years) if atm.book.mid else None
        target = strike_for_delta(otype, price, setup.strike_delta, years, vol) if vol and years > 0 else price
        scored = [(d, lg) for lg in map(leg, nearest(target, listed, NEAREST_STRIKES)) if lg.book.two_sided
                  for d in [leg_delta(otype, lg, price, years)] if d is not None]
        if not scored:
            return f"No {otype}s with a price that gives a delta near the target."
        best_delta, best = min(scored, key=lambda x: (abs(x[0] - setup.strike_delta), x[1].strike))

    if setup.structure == "directional":
        return Placed(auto_plan.Choice(setup.structure, otype, None, best, best.strike), best_delta)
    other_target = float(best.strike) + sign * setup.width_usd
    beyond = [k for k in listed if (k - best.strike) * sign > 0]
    others = [lg for lg in map(leg, nearest(other_target, beyond, OTHER_LEG_STRIKES)) if lg.book.two_sided]
    if not others:
        return f"No listed {otype} about ${setup.width_usd:g} beyond the {best.strike} strike with a two-sided quote."
    other = min(others, key=lambda lg: (abs(float(lg.strike) - other_target), abs(lg.strike - best.strike)))
    if setup.structure == "debit_spread":
        return Placed(auto_plan.Choice(setup.structure, otype, other, best, best.strike), best_delta)
    return Placed(auto_plan.Choice(setup.structure, otype, best, other, best.strike), best_delta)


def contract_name(symbol: str, exp: date, otype: str, strike: Decimal) -> str:
    return f"{symbol} {exp:%b %d %Y} {ledger.fmt_qty(strike)} {otype}"


def fills(choice: auto_plan.Choice, books: dict[Decimal, Book], rule: str, opening: bool, q: int, exp: date,
          symbol: str) -> list[dict] | None:
    """Each leg's order and fill price, or None when a leg has no price."""
    legs = [(choice.bought, "buy" if opening else "sell")]
    if choice.sold is not None:
        legs.insert(0, (choice.sold, "sell" if opening else "buy"))
    out = []
    for lg, side in legs:
        book = books[lg.strike]
        px = market_price(side, book, rule)
        if px is None:
            return None
        out.append({"action": f"{side.capitalize()} to {'open' if opening else 'close'}",
                    "contract": contract_name(symbol, exp, choice.option_type, lg.strike), "quantity": q,
                    "price": float(px), "bid": float(book.bid) if book.bid is not None else None,
                    "ask": float(book.ask) if book.ask is not None else None})
    return out


def simulate(setup: OptionSetup, trades: list[dict], entries: list[dict], history: OptionHistory, symbol: str,
             tf: str, daily: list[DailyClose], rule: str, prior: PriorHistory) -> tuple[list[Outcome], list[dict]]:
    """Every trade replayed as the option setup: the money outcomes, and one row per position (with
    the reason when no option trade could be made)."""
    days = [c.day for c in daily]
    risk = Decimal(str(setup.risk_usd))
    fee = Decimal(str(setup.commission))
    settings = setup.rules_settings()
    structure = setup.structure
    outcomes, rows = [], []
    for t in trades:
        for leg in legs_of(t, entries):
            row = {"entry_time": leg.entry_time, "dir": t["dir"]}
            rows.append(row)
            at = close_moment(leg.entry_time, tf)
            rules = auto_plan.rules_for(settings, prior.at(at))
            exp = auto_plan.pick_expiration(history.expirations(symbol, at), at.date(), rules.hold_days)
            if exp is None:
                row["problem"] = "No expiration far enough away."
                continue
            safety = last_day_before(exp)
            safety_at = datetime(safety.year, safety.month, safety.day, 16, 0, tzinfo=NY)
            if safety_at <= at:
                row["problem"] = "The expiration is too close: it would be closed before it opened."
                continue
            # The stock in the entry day's terms; the contract keeps those terms to the end.
            f = split_factor(SPLITS.get(symbol, []), at.date())
            price = leg.price * f
            placed = place(setup, t["dir"], price, at, exp, history, symbol)
            if isinstance(placed, str):
                row["problem"] = placed
                continue
            choice = placed.choice
            sizing = auto_plan.size(choice, rule, risk, at_least_one=True)
            if isinstance(sizing, str):
                row["problem"] = sizing
                continue
            q = sizing.quantity
            strikes = [choice.bought.strike] + ([choice.sold.strike] if choice.sold else [])
            opened = fills(choice, {lg.strike: lg.book for lg in (choice.bought, choice.sold) if lg}, rule, True, q, exp, symbol)

            # The exit: when the strategy exits, unless it left the trade floating or the expiration comes first.
            exit_at = close_moment(t["exit_time"], tf)
            under = t["exit_price"] * f
            note = None
            if t["reason"] == "flot" or exit_at > safety_at:
                exit_at = safety_at
                under = (close_on(daily, days, safety) or t["exit_price"]) * f
                note = "closed the day before expiration" if t["reason"] != "flot" else \
                    "left floating by the strategy; closed the day before expiration"
            books = {k: Book(*history.quote(symbol, choice.option_type, k, exp, exit_at, under)) for k in strikes}
            if choice.sold is None:
                exit_price = market_price("sell", books[choice.bought.strike], rule)
            else:
                exit_price = auto_plan.spread_price(structure, books[choice.sold.strike], books[choice.bought.strike],
                                                    rule, False, choice.width)
            closed = fills(choice, books, rule, False, q, exp, symbol)
            if exit_price is None or closed is None or opened is None:
                row["problem"] = "No price to close at."
                continue
            n_legs = len(strikes)
            fees = fee * q * n_legs
            gross = (sizing.price - exit_price if structure == "credit_spread" else exit_price - sizing.price) * HUNDRED * q
            pnl = gross - 2 * fees
            open_cash = (sizing.price if structure == "credit_spread" else -sizing.price) * HUNDRED * q - fees
            close_cash = (-exit_price if structure == "credit_spread" else exit_price) * HUNDRED * q - fees
            held = (exit_at - at).total_seconds() / 86400.0
            outcomes.append(Outcome(leg.entry_time, int(exit_at.timestamp()), float(pnl), held))
            placed_strike = choice.sold.strike if structure == "credit_spread" else choice.bought.strike
            row.update({
                "description": auto_plan.describe(choice, exp, symbol), "quantity": q, "entry": float(sizing.price),
                "exit": float(exit_price), "pnl": round(float(pnl), 2), "fees": round(float(2 * fees), 2),
                "max_loss": float(sizing.unit_risk * q), "over_risk": sizing.unit_risk * q > risk, "payout": sizing.payout,
                "strike_pct": round((float(placed_strike) / price - 1) * 100 * (1 if choice.option_type == "call" else -1), 2),
                "delta": round(placed.delta, 3) if placed.delta is not None else None,
                "width": float(choice.width) if choice.sold else None, "hold_days": rules.hold_days,
                "expiration": exp.isoformat(), "exit_time": int(exit_at.timestamp()), "note": note,
                "split_factor": f if f != 1 else None,
                "transactions": [
                    {"time": int(at.timestamp()), "underlying": round(price, 4), "legs": opened, "net": float(sizing.price),
                     "fees": float(fees), "cash": round(float(open_cash), 2)},
                    {"time": int(exit_at.timestamp()), "underlying": round(under, 4), "legs": closed, "net": float(exit_price),
                     "fees": float(fees), "cash": round(float(close_cash), 2)},
                ],
            })
    return outcomes, rows

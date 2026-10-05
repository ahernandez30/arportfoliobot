"""Option backtests: each strategy trade replayed as the option trade automatic trading would have
placed (plan 7.6 and the Backtest's "Compare structures").

Same rules as live (app.auto_plan): strike distance from the average winning move, expiration
from the average trade length plus a margin, size from the dollar risk. The history behind those
averages is only the trades that had already closed when the signal fired, so nothing from the
future leaks in. Exits follow the strategy on the stock chart; a position the strategy left
floating, or one still open the market day before expiration, is closed that day, as live.
"""
import bisect
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app import auto_plan
from app.backtest.option_history import OptionHistory
from app.backtest.stats import Outcome
from app.marketdata.bars import NY
from app.marketdata.base import DailyClose
from app.paper_rules import Book, market_price
from app.strategy.base import TIMEFRAME_SECONDS

HUNDRED = Decimal(100)
STRIKE_WINDOW = 0.35  # strikes looked at: within this share of the price


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


def simulate(structure: str, settings: auto_plan.TradeSettings, trades: list[dict], entries: list[dict],
             history: OptionHistory, symbol: str, tf: str, daily: list[DailyClose], rule: str,
             prior: PriorHistory) -> tuple[list[Outcome], list[dict]]:
    """Every trade replayed as an option trade of one structure: the money outcomes, and one row per
    trade (with the reason when no option trade could be made)."""
    days = [c.day for c in daily]
    risk = Decimal(str(settings.risk_usd))
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
            otype = auto_plan.option_type_for(structure, t["dir"])
            lo, hi = leg.price * (1 - STRIKE_WINDOW), leg.price * (1 + STRIKE_WINDOW)
            chain = []
            for k in history.strikes(symbol, at, leg.price):
                if lo <= float(k) <= hi:
                    bid, ask = history.quote(symbol, otype, k, exp, at, leg.price)
                    chain.append(auto_plan.Leg(k, bid, ask, f"{otype}{k}"))
            choice = auto_plan.choose(structure, t["dir"], Decimal(str(leg.price)), rules.distance_pct, settings.side, chain)
            if isinstance(choice, str):
                row["problem"] = choice
                continue
            sizing = auto_plan.size(choice, rule, risk)
            if isinstance(sizing, str):
                row["problem"] = sizing
                continue

            # The exit: when the strategy exits, unless it left the trade floating or the expiration comes first.
            safety = last_day_before(exp)
            exit_at = close_moment(t["exit_time"], tf)
            under = t["exit_price"]
            note = None
            if t["reason"] == "flot" or exit_at.date() > safety:
                exit_at = datetime(safety.year, safety.month, safety.day, 16, 0, tzinfo=NY)
                under = close_on(daily, days, safety) or under
                note = "closed the day before expiration" if t["reason"] != "flot" else \
                    "left floating by the strategy; closed the day before expiration"
            exit_price = _exit_price(choice, history, symbol, exp, exit_at, under, rule)
            if exit_price is None:
                row["problem"] = "No price to close at."
                continue
            q = sizing.quantity
            if structure == "credit_spread":
                pnl = (sizing.price - exit_price) * HUNDRED * q
            else:
                pnl = (exit_price - sizing.price) * HUNDRED * q
            held = (exit_at - at).total_seconds() / 86400.0
            outcomes.append(Outcome(leg.entry_time, int(exit_at.timestamp()), float(pnl), held))
            row.update({
                "description": auto_plan.describe(choice, exp, symbol), "quantity": q, "entry": float(sizing.price),
                "exit": float(exit_price), "pnl": round(float(pnl), 2), "max_loss": float(sizing.unit_risk * q),
                "payout": sizing.payout, "distance_pct": rules.distance_pct, "hold_days": rules.hold_days,
                "expiration": exp.isoformat(), "exit_time": int(exit_at.timestamp()), "note": note,
            })
    return outcomes, rows


def _exit_price(choice: auto_plan.Choice, history: OptionHistory, symbol: str, exp: date, at: datetime,
                under: float, rule: str) -> Decimal | None:
    def book(k: Decimal) -> Book:
        return Book(*history.quote(symbol, choice.option_type, k, exp, at, under))

    if choice.structure == "directional":
        return market_price("sell", book(choice.bought.strike), rule)
    return auto_plan.spread_price(choice.structure, book(choice.sold.strike), book(choice.bought.strike), rule, False,
                                  choice.width)

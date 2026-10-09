"""Backtest statistics: pure functions, no database or network.

`script_results` gives the script's own results tables (the engine's `results`) for only the trades
opened inside a date range. It is the engine's own `tables` function on fewer trades, so over the
whole history it gives exactly what the engine gives.

`money_results` turns a list of trade results in dollars into the Backtest summary: total return,
win rate, biggest drop, account-value curve, streaks, average length, by year.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from app.strategy.swing import tables


def _year(t: int) -> int:
    return datetime.fromtimestamp(t, timezone.utc).year


def script_results(trades: list[dict], entries: list[dict], bars: list, closed: int, inputs: dict,
                   start: int | None) -> dict:
    """The engine's results tables for trades whose entry is at or after `start` (Unix seconds)."""
    first = 0 if start is None else next((i for i, b in enumerate(bars) if b.time >= start), len(bars))
    return tables(trades, entries, bars, closed, inputs, first)


@dataclass(frozen=True)
class Outcome:
    """One finished trade in dollars, for the money summary."""

    entry_time: int
    exit_time: int
    pnl: float
    days: float


def money_results(outcomes: list[Outcome], starting_cash: float) -> dict:
    """Total return, win rate, biggest drop, account value after each trade, streaks, by year.

    The account value moves when a trade closes (results are counted at exit, in exit order). The
    biggest drop is the largest fall from a high point of that curve, in dollars and percent."""
    rows = sorted(outcomes, key=lambda o: (o.exit_time, o.entry_time))
    value = peak = starting_cash
    drop = drop_pct = 0.0
    curve = [{"time": rows[0].entry_time, "value": starting_cash}] if rows else []
    wins = losses = sw = sl = mw = ml = 0
    win_sum = loss_sum = 0.0
    by_year: dict[int, dict] = {}
    for o in rows:
        value += o.pnl
        curve.append({"time": o.exit_time, "value": round(value, 2)})
        peak = max(peak, value)
        if peak - value > drop:
            drop = peak - value
            drop_pct = drop / peak * 100.0 if peak > 0 else 0.0
        y = by_year.setdefault(_year(o.exit_time), {"year": _year(o.exit_time), "trades": 0, "wins": 0, "pnl": 0.0})
        y["trades"] += 1
        y["pnl"] += o.pnl
        if o.pnl > 0:
            wins += 1
            win_sum += o.pnl
            y["wins"] += 1
            sw, sl = sw + 1, 0
            mw = max(mw, sw)
        else:
            losses += 1
            loss_sum += o.pnl
            sl, sw = sl + 1, 0
            ml = max(ml, sl)
    count = len(rows)
    total = value - starting_cash
    return {
        "trades": count, "wins": wins, "losses": losses,
        "win_rate": 100.0 * wins / count if count else None,
        "total": round(total, 2), "total_pct": total / starting_cash * 100.0 if starting_cash else None,
        "final_value": round(value, 2),
        "average_win": win_sum / wins if wins else None, "average_loss": loss_sum / losses if losses else None,
        "max_drop": round(drop, 2), "max_drop_pct": drop_pct,
        "avg_days": sum(o.days for o in rows) / count if count else None,
        "max_win_streak": mw, "max_loss_streak": ml,
        "years": [{**y, "pnl": round(y["pnl"], 2)} for y in sorted(by_year.values(), key=lambda y: y["year"])],
        "curve": curve,
    }

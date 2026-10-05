"""Backtest statistics: pure functions, no database or network.

`script_results` rebuilds the script's own results tables (the engine's `results`) from the engine's
trade list, for only the trades opened inside a date range. Over the whole history it gives exactly
what the engine gives (tested), so a range is the same measurement on fewer trades.

`money_results` turns a list of trade results in dollars into the Backtest summary: total return,
win rate, biggest drop, account-value curve, streaks, average length, by year.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

TYPES = ("LLENA", "FLECO", "ENGULFING")


def _year(t: int) -> int:
    return datetime.fromtimestamp(t, timezone.utc).year


def script_results(trades: list[dict], entries: list[dict], bars: list, closed: int, mode: str,
                   start: int | None, objective_pct: float) -> dict:
    """The engine's results tables for trades whose entry is at or after `start`.

    Win/loss as the script counts them: a counted trade wins when its result is 0% or better
    ("floating" trades are kept apart). The gap is the next candle's open against the entry, measured
    for every target/stop entry, as the script does."""
    first = 0 if start is None else next((i for i, b in enumerate(bars) if b.time >= start), len(bars))
    inr = [t for t in trades if t["entry_i"] >= first]
    n = {k: {"wins": 0, "losses": 0, "floating": 0, "sF": 0.0, "rS": 0.0, "dW": 0.0, "dWn": 0, "dL": 0.0,
             "dLn": 0, "gS": 0.0, "gN": 0, "gW": 0} for k in TYPES}
    years: dict[int, list[int]] = {}
    streak_w = streak_l = max_w = max_l = 0
    dur_n, dur_bars, dur_days, path = 0, 0, 0.0, 0
    for t in inr:
        row = n[t["type"]]
        if not t["counted"]:
            row["floating"] += 1
            row["sF"] += t["ret_pct"]
            continue
        row["rS"] += t["ret_pct"]
        dur_n += 1
        dur_bars += t["bars"]
        dur_days += t["days"]
        path += 1 if t.get("path") else 0
        y = years.setdefault(_year(t["entry_time"]), [0, 0])
        if t["ret_pct"] >= 0:
            row["wins"] += 1
            row["dW"] += t["days"]
            row["dWn"] += 1
            y[0] += 1
            streak_w, streak_l = streak_w + 1, 0
            max_w = max(max_w, streak_w)
        else:
            row["losses"] += 1
            row["dL"] += t["days"]
            row["dLn"] += 1
            y[1] += 1
            streak_l, streak_w = streak_l + 1, 0
            max_l = max(max_l, streak_l)
    if mode == "target_stop":
        o = objective_pct / 100.0
        for e in entries:
            i = e["i"]
            if i < first or i + 1 >= closed:
                continue
            row = n[e["type"]]
            nxt = bars[i + 1].open
            row["gS"] += (nxt - e["price"]) / e["price"] * 100.0 * e["dir"]
            row["gN"] += 1
            tgt = e["price"] * (1 + o) if e["dir"] == 1 else e["price"] * (1 - o)
            if (e["dir"] == 1 and nxt >= tgt) or (e["dir"] == -1 and nxt <= tgt):
                row["gW"] += 1
    rows = []
    for k in TYPES:
        r = n[k]
        tot = r["wins"] + r["losses"]
        rows.append({
            "type": k, "wins": r["wins"], "losses": r["losses"], "win_rate": 100.0 * r["wins"] / tot if tot else None,
            "floating": r["floating"], "floating_avg": r["sF"] / r["floating"] if r["floating"] else None,
            "gap_avg": r["gS"] / r["gN"] if r["gN"] else None, "gap_wins": r["gW"], "gap_n": r["gN"],
            "r_avg": r["rS"] / tot if tot else None,
            "days_win": r["dW"] / r["dWn"] if r["dWn"] else None,
            "days_loss": r["dL"] / r["dLn"] if r["dLn"] else None,
        })
    tw, tl = sum(r["wins"] for r in rows), sum(r["losses"] for r in rows)
    tot = tw + tl
    dwn, dln = sum(n[k]["dWn"] for k in TYPES), sum(n[k]["dLn"] for k in TYPES)
    return {
        "by_type": rows,
        "total": {"wins": tw, "losses": tl, "win_rate": 100.0 * tw / tot if tot else None,
                  "floating": sum(r["floating"] for r in rows),
                  "r_avg": sum(n[k]["rS"] for k in TYPES) / tot if tot else None,
                  "days_win": sum(n[k]["dW"] for k in TYPES) / dwn if dwn else None,
                  "days_loss": sum(n[k]["dL"] for k in TYPES) / dln if dln else None},
        "years": [{"year": y, "wins": v[0], "losses": v[1]} for y, v in sorted(years.items())],
        "candles_measured": max(0, closed - first),
        "max_win_streak": max_w,
        "max_loss_streak": max_l,
        "real_path_trades": path,
        "avg_days": dur_days / dur_n if dur_n else None,
        "avg_bars": dur_bars / dur_n if dur_n else None,
        "mode": mode,
    }


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

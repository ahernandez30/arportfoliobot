"""One backtest: the same engine as Master Chart and the worker (plan section 7), over a date range.

The engine always runs on the whole history up to the end date, so moving averages, the ladder and
every other filter see exactly what TradingView sees; only trades opened inside the range are
counted. Results come three ways:
- the script's own results tables and luck test (percent moves of the stock),
- the stock-price result in dollars: a fixed dollar amount per trade (Rafa's choice, no compounding),
- option trades per structure, priced by an OptionHistory (an estimate until real data is added).
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app import auto_plan
from app.backtest import options, stats
from app.backtest.option_history import RATE, VOL_DAYS, OptionHistory
from app.marketdata.base import Bar, DailyClose
from app.strategy.base import Strategy, StrategyData


@dataclass(frozen=True)
class Setup:
    start: date | None
    end: date | None
    starting_cash: float
    stock_dollars: float  # dollars put into each trade for the stock-price result
    structures: tuple[str, ...]  # option structures to replay, may be empty
    trade: auto_plan.TradeSettings
    fill_rule: str


def day_start(d: date) -> int:
    """Unix seconds at 00:00 UTC of a day: daily candles carry that time; intraday ones come later."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def cut(data: StrategyData, end: date | None) -> StrategyData:
    """The candles up to and including the end date."""
    if end is None:
        return data
    limit = day_start(end + timedelta(days=1))
    bars = [b for b in data.bars if b.time < limit]
    return StrategyData(data.symbol, data.timeframe, bars, min(data.closed, len(bars)), data.other)


def daily_closes(bars: list[Bar]) -> list[DailyClose]:
    return [DailyClose(datetime.fromtimestamp(b.time, timezone.utc).date(), b.close) for b in bars]


def run(strategy: Strategy, data: StrategyData, inputs: dict, setup: Setup, history: OptionHistory | None,
        daily: list[DailyClose]) -> dict:
    data = cut(data, setup.end)
    start_ts = day_start(setup.start) if setup.start else None
    out = strategy.run(data, inputs, luck=True, luck_from=start_ts)
    closed, bars = data.closed, data.bars
    trades = [t for t in out["trades"] if start_ts is None or t["entry_time"] >= start_ts]
    results = stats.script_results(out["trades"], out.get("entries", []), bars, closed, inputs, start_ts)

    # Stock price, fixed dollars per entry.
    stock = [stats.Outcome(t["entry_time"], t["exit_time"], setup.stock_dollars * t.get("entries", 1) * t["ret_pct"] / 100.0,
                           t["days"]) for t in trades]
    columns = {"stock": stats.money_results(stock, setup.starting_cash)}

    rows = [{"entry_time": t["entry_time"], "exit_time": t["exit_time"], "dir": t["dir"], "type": t["type"],
             "entry_price": t["entry_price"], "exit_price": t["exit_price"], "ret_pct": t["ret_pct"], "reason": t["reason"],
             "counted": t["counted"], "days": t["days"], "entries": t.get("entries", 1),
             "stock_pnl": round(setup.stock_dollars * t.get("entries", 1) * t["ret_pct"] / 100.0, 2), "options": {}}
            for t in trades]
    skipped = {}
    if setup.structures and history is not None:
        prior = options.PriorHistory(out["trades"], data.timeframe)
        for structure in setup.structures:
            outcomes, opt_rows = options.simulate(structure, setup.trade, trades, out.get("entries", []), history,
                                                  data.symbol, data.timeframe, daily, setup.fill_rule, prior)
            columns[structure] = stats.money_results(outcomes, setup.starting_cash)
            skipped[structure] = sum(1 for r in opt_rows if "problem" in r)
            # Basket trades open one option position per entry; the table shows them under their trade.
            k = 0
            for row, t in zip(rows, trades):
                n = len(options.legs_of(t, out.get("entries", [])))
                row["options"][structure] = opt_rows[k:k + n]
                k += n

    first_bar = next((b.time for b in bars[:closed] if start_ts is None or b.time >= start_ts), None)
    return {
        "results": results,
        "luck": out.get("luck"),
        "credit_spreads": out.get("credit_spreads"),
        "columns": columns,
        "skipped": skipped,
        "trades": rows,
        "range": {"first": first_bar, "last": bars[closed - 1].time if closed else None,
                  "history_from": bars[0].time if bars else None},
        "notes": {
            "options_source": history.source if history is not None and setup.structures else None,
            "rate_pct": RATE * 100, "vol_days": VOL_DAYS, "fill_rule": setup.fill_rule,
        },
    }

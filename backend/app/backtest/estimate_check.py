"""How far the option-price estimate is from real prices (Backtest plan, step 2).

Takes real OPRA best bid/ask samples (Databento cbbo-1m Parquet files: ts_recv, symbol, bid_px_00,
ask_px_00), and for each sample on a half-hour mark of the regular session prices the same
contract with the estimate (app.backtest.option_history.ModelOptionHistory) at that moment, with
the stock at the close of the 30-minute candle that just ended. Reports the difference of the
middle price and of the bid/ask spread, overall and by days to expiration and distance from the
price, so a Backtest that uses the estimate can say how far off it is likely to be.
"""
import math
import statistics
from collections import defaultdict
from datetime import date, datetime

from app.backtest.option_history import ModelOptionHistory
from app.backtest.real_options import parse_occ
from app.marketdata.bars import NY
from app.marketdata.base import Bar, DailyClose

DTE_BUCKETS = ((0, 7, "0-7 days"), (8, 30, "8-30 days"), (31, 90, "31-90 days"), (91, 180, "91-180 days"),
               (181, 10_000, "over 180 days"))
MONEY_BUCKETS = ((0.0, 0.05, "within 5% of the price"), (0.05, 0.15, "5-15% away"), (0.15, 10.0, "over 15% away"))


def daily_from(bars: list[Bar]) -> list[DailyClose]:
    """Each day's last close from intraday candles."""
    by_day: dict[date, float] = {}
    for b in bars:
        by_day[datetime.fromtimestamp(b.time, NY).date()] = b.close
    return [DailyClose(d, c) for d, c in sorted(by_day.items())]


class Sample:
    __slots__ = ("dte", "money", "kind", "real_mid", "model_mid", "real_half", "model_half")

    def __init__(self, dte, money, kind, real_mid, model_mid, real_half, model_half):
        self.dte, self.money, self.kind = dte, money, kind
        self.real_mid, self.model_mid = real_mid, model_mid
        self.real_half, self.model_half = real_half, model_half


def samples(rows, bars30: list[Bar], symbol: str) -> list[Sample]:
    """rows: (ts_recv seconds, OCC symbol, bid, ask). Only half-hour marks inside the session with a
    two-sided real quote worth at least 10 cents, so tiny prices do not swamp the percentages."""
    model = ModelOptionHistory({symbol: daily_from(bars30)})
    closes = {b.time + 1800: b.close for b in bars30}  # the stock at each candle's close
    out = []
    for ts, sym, bid, ask in rows:
        if ts % 1800 or bid is None or ask is None or bid <= 0 or ask < bid:
            continue
        spot = closes.get(ts)
        occ = parse_occ(sym)
        if spot is None or occ is None or occ[0] != symbol:
            continue
        at = datetime.fromtimestamp(ts, NY)
        real_mid = (bid + ask) / 2
        if real_mid < 0.10:
            continue
        _, exp, kind, strike = occ
        mb, ma = model.quote(symbol, kind, strike, exp, at, spot)
        if mb is None or ma is None:
            continue
        model_mid = float(mb + ma) / 2
        dte = (exp - at.date()).days
        out.append(Sample(dte, abs(float(strike) / spot - 1), kind, real_mid, model_mid, (ask - bid) / 2,
                          float(ma - mb) / 2))
    return out


def _summary(group: list[Sample]) -> dict:
    errs = [s.model_mid / s.real_mid - 1 for s in group]
    spread_real = [s.real_half / s.real_mid for s in group]
    spread_model = [s.model_half / s.real_mid for s in group]
    q = statistics.quantiles(errs, n=10) if len(errs) >= 10 else [min(errs), max(errs)]
    return {
        "n": len(group),
        "median_error_pct": 100 * statistics.median(errs),
        "median_abs_error_pct": 100 * statistics.median(abs(e) for e in errs),
        "middle_80_pct": [100 * q[0], 100 * q[-1]],
        "real_half_spread_pct": 100 * statistics.median(spread_real),
        "model_half_spread_pct": 100 * statistics.median(spread_model),
    }


def report(all_samples: list[Sample]) -> dict:
    """Overall, by days to expiration, by distance from the price, and by type."""
    if not all_samples:
        return {"n": 0}
    by_dte, by_money, by_kind = defaultdict(list), defaultdict(list), defaultdict(list)
    for s in all_samples:
        by_dte[next(label for lo, hi, label in DTE_BUCKETS if lo <= s.dte <= hi)].append(s)
        by_money[next(label for lo, hi, label in MONEY_BUCKETS if lo <= s.money < hi)].append(s)
        by_kind[s.kind].append(s)
    return {
        "overall": _summary(all_samples),
        "by_days_to_expiration": {label: _summary(by_dte[label]) for _, _, label in DTE_BUCKETS if by_dte[label]},
        "by_distance": {label: _summary(by_money[label]) for _, _, label in MONEY_BUCKETS if by_money[label]},
        "by_type": {k: _summary(v) for k, v in sorted(by_kind.items())},
    }


def read_quote_rows(path: str) -> list[tuple[int, str, float, float]]:
    """(seconds, symbol, bid, ask) from a cbbo-1m Parquet file, half-hour marks only."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    names = pq.ParquetFile(path).schema_arrow.names
    t = pq.read_table(path, columns=[c for c in ("ts_recv", "symbol", "bid_px_00", "ask_px_00") if c in names])
    if "symbol" not in names:  # one contract per file, named after it: "2025-09-25_TSLA250926P00422500"
        import os

        name = os.path.basename(path).rsplit(".", 1)[0].split("_", 1)[-1]
        t = t.append_column("symbol", pa.array([f"{name[:-15]:<6}{name[-15:]}"] * t.num_rows))
    secs = t.column("ts_recv").cast("int64").to_numpy() // 1_000_000_000
    idx = np.nonzero(secs % 1800 == 0)[0]
    if not len(idx):
        return []
    sub = t.take(idx)
    bids = sub.column("bid_px_00").to_numpy(zero_copy_only=False)
    asks = sub.column("ask_px_00").to_numpy(zero_copy_only=False)
    return [(int(s), sym, float(b), float(a)) for s, sym, b, a in
            zip(secs[idx], sub.column("symbol").to_pylist(), bids, asks) if not (math.isnan(b) or math.isnan(a))]


def run_files(paths: list[str], bars30: list[Bar], symbol: str) -> dict:
    rows = [r for p in paths for r in read_quote_rows(p)]
    return report(samples(rows, bars30, symbol))

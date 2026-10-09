"""Parity check (plan 7.5): the engine against signal dates exported from TradingView.

TradingView's "Export chart data" gives a CSV with each candle's time, open, high, low, close
and the indicator's plots, including the BUY ("Señal alcista") and SELL ("Señal bajista") arrows.

Two comparisons:
1. Logic: the engine run on TradingView's OWN prices must give exactly TradingView's signals.
   This proves the translation, independent of where prices come from.
2. Data: the engine run on our provider's prices, against TradingView's signal dates. Any
   difference is listed with both sets of prices for that candle, which is the usual cause.
"""
import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.marketdata.bars import NY, date_to_epoch
from app.marketdata.base import Bar
from app.strategy.base import Strategy, StrategyData
from app.strategy.swing import classify

MAX_LISTED = 300


class ParityError(ValueError):
    """The file cannot be read as a TradingView export; the message says why."""


@dataclass(frozen=True)
class TvRow:
    key: str  # the candle, as a date (daily/weekly) or New York time (intraday)
    bar: Bar
    up: bool
    dn: bool


def _flag(cell: str) -> bool:
    cell = (cell or "").strip()
    if not cell or cell.lower() in ("nan", "na", "false"):
        return False
    try:
        v = float(cell)
    except ValueError:
        return True
    return not math.isnan(v) and v != 0


def _parse_time(cell: str) -> datetime:
    cell = cell.strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", cell):
        v = float(cell)
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, timezone.utc)
    try:
        t = datetime.fromisoformat(cell.replace("Z", "+00:00"))
    except ValueError:
        raise ParityError(f"Cannot read the time “{cell[:40]}”.") from None
    return t if t.tzinfo else t.replace(tzinfo=NY)


def candle_key(t: datetime, tf: str) -> str:
    """The same candle named the same way in both sources."""
    ny = t.astimezone(NY)
    if tf == "1D":
        return ny.date().isoformat()
    if tf == "1W":
        d = ny.date()
        return (d - timedelta(days=d.weekday())).isoformat()
    return ny.strftime("%Y-%m-%d %H:%M")


def our_key(b: Bar, tf: str) -> str:
    if tf in ("1D", "1W"):
        d = datetime.fromtimestamp(b.time, timezone.utc).date()
        if tf == "1W":
            d = d - timedelta(days=d.weekday())
        return d.isoformat()
    return datetime.fromtimestamp(b.time, NY).strftime("%Y-%m-%d %H:%M")


def parse_export(text: str, tf: str) -> list[TvRow]:
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if len(rows) < 2:
        raise ParityError("The file is empty.")
    head = [h.strip().lower() for h in rows[0]]

    def col(*names: str) -> int:
        for n in names:
            if n in head:
                return head.index(n)
        raise ParityError(f"No “{names[0]}” column. Use TradingView's Export chart data.")

    ti, oi, hi, li, ci = col("time"), col("open"), col("high"), col("low"), col("close")
    ups = [i for i, h in enumerate(head) if ("alcista" in h) and "bloque" not in h and "swing" not in h]
    dns = [i for i, h in enumerate(head) if ("bajista" in h) and "bloque" not in h and "swing" not in h]
    if not ups or not dns:
        raise ParityError("No “Señal alcista” / “Señal bajista” columns. Add the indicator to the chart before "
                          "exporting, with its arrows (Ver FLECHAS) on.")
    out: list[TvRow] = []
    for r in rows[1:]:
        if len(r) <= max(ti, oi, hi, li, ci):
            continue
        try:
            o, h, lo, c = float(r[oi]), float(r[hi]), float(r[li]), float(r[ci])
        except ValueError:
            continue
        t = _parse_time(r[ti])
        key = candle_key(t, tf)
        if tf in ("1D", "1W"):
            stamp = date_to_epoch(date.fromisoformat(key))
        else:
            stamp = int(t.timestamp())
        out.append(TvRow(key, Bar(stamp, o, h, lo, c, 0.0), any(_flag(r[i]) for i in ups if i < len(r)),
                         any(_flag(r[i]) for i in dns if i < len(r))))
    if not out:
        raise ParityError("No candles found in the file.")
    out.sort(key=lambda x: x.bar.time)
    return out


def _sig_map(result: dict, bars: list[Bar], tf: str) -> dict[str, dict]:
    """One signal per candle; when a candle opens both a buy and a sell, the buy (as the export is read)."""
    out: dict[str, dict] = {}
    for s in result["signals"]:
        out.setdefault(our_key(bars[s["i"]], tf), s)
    return out


def _ohlc(b: Bar | None) -> dict | None:
    return None if b is None else {"open": b.open, "high": b.high, "low": b.low, "close": b.close}


def _measure(b: Bar | None, p: Bar | None, inputs: dict) -> dict | None:
    if b is None:
        return None
    c = classify(b, p, inputs["cuerpoLlena"], inputs["mechaFleco"], inputs["cuerpoMinFleco"], inputs["cuerpoEngulf"])
    return {"body_pct": round(c.relleno, 2), "wick_pct": round(c.m_max, 2)}


def _biggest_gap(a: Bar, b: Bar) -> float:
    return max(abs(x - y) / abs(y) * 100 if y else 0 for x, y in
               ((a.open, b.open), (a.high, b.high), (a.low, b.low), (a.close, b.close)))


def compare(strategy: Strategy, inputs: dict, tv: list[TvRow], ours: list[Bar], tf: str) -> tuple[dict, dict]:
    """Runs both comparisons. Returns (summary, detail)."""
    # 1. Logic: the engine on TradingView's prices.
    tv_bars = [r.bar for r in tv]
    logic = strategy.run(StrategyData("TV", tf, tv_bars, len(tv_bars)), inputs)
    logic_sigs = _sig_map(logic, tv_bars, tf)
    logic_mismatch = []
    for i, r in enumerate(tv):
        tv_dir = 1 if r.up else (-1 if r.dn else 0)
        s = logic_sigs.get(r.key)
        eng_dir = s["dir"] if s else 0
        if tv_dir != eng_dir:
            logic_mismatch.append({"candle": r.key, "tradingview": tv_dir, "engine": eng_dir,
                                   "ohlc": _ohlc(r.bar), "measure": _measure(r.bar, tv_bars[i - 1] if i else None, inputs)})

    # 2. Data: the engine on our prices, over the dates both sources cover.
    our_res = strategy.run(StrategyData("OURS", tf, ours, len(ours)), inputs)
    our_sigs = _sig_map(our_res, ours, tf)
    our_by_key = {our_key(b, tf): (i, b) for i, b in enumerate(ours)}
    tv_by_key = {r.key: (i, r) for i, r in enumerate(tv)}
    start = max(tv[0].key, our_key(ours[0], tf)) if ours else tv[0].key
    end = min(tv[-1].key, our_key(ours[-1], tf)) if ours else tv[-1].key
    tv_sig = {r.key: (1 if r.up else -1) for r in tv if (r.up or r.dn) and start <= r.key <= end}
    ours_sig = {k: s["dir"] for k, s in our_sigs.items() if start <= k <= end}
    matched = sorted(k for k in tv_sig if ours_sig.get(k) == tv_sig[k])
    differences = []
    for k in sorted(set(tv_sig) | set(ours_sig)):
        if tv_sig.get(k) == ours_sig.get(k):
            continue
        ti = tv_by_key.get(k)
        oi = our_by_key.get(k)
        tv_bar = ti[1].bar if ti else None
        tv_prev = tv[ti[0] - 1].bar if ti and ti[0] else None
        our_bar = oi[1] if oi else None
        our_prev = ours[oi[0] - 1] if oi and oi[0] else None
        if our_bar is None:
            why = "This candle is missing from our price data."
        elif tv_bar is None:
            why = "This candle is missing from the TradingView file."
        else:
            gap = max(_biggest_gap(our_bar, tv_bar), _biggest_gap(our_prev, tv_prev) if our_prev and tv_prev else 0)
            if gap > 0.0005:
                why = (f"The prices differ (by up to {gap:.3f}% on this or the previous candle), which changes the "
                       "candle's measurements.")
            else:
                why = ("Same prices. The difference comes from earlier candles (a filter or open position carried "
                       "over), or from the start of the history.")
        differences.append({
            "candle": k, "tradingview": tv_sig.get(k, 0), "ours": ours_sig.get(k, 0), "why": why,
            "tv_ohlc": _ohlc(tv_bar), "our_ohlc": _ohlc(our_bar),
            "tv_measure": _measure(tv_bar, tv_prev, inputs), "our_measure": _measure(our_bar, our_prev, inputs),
        })
    summary = {
        "candles_in_file": len(tv), "range": [start, end],
        "tv_signals": len(tv_sig), "our_signals": len(ours_sig), "matched": len(matched),
        "differences": len(differences),
        "logic_candles": len(tv), "logic_mismatches": len(logic_mismatch),
        "logic_ok": not logic_mismatch,
    }
    detail = {"logic_mismatches": logic_mismatch[:MAX_LISTED], "differences": differences[:MAX_LISTED],
              "matched": matched[-MAX_LISTED:]}
    return summary, detail


def guess_from_filename(name: str) -> tuple[str | None, str | None]:
    """TradingView names exports like 'NASDAQ_TSLA, 1D_abc12.csv'."""
    m = re.search(r"(?:[A-Z]+_)?([A-Z][A-Z0-9.]{0,9}),\s*(\d+[SDWM]?|[DWM])", name)
    if not m:
        return None, None
    tf = {"1D": "1D", "D": "1D", "1W": "1W", "W": "1W", "60": "1h", "15": "15m", "5": "5m", "1": "1m"}.get(m.group(2))
    return m.group(1), tf

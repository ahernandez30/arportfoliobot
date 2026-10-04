"""Indicators exactly as TradingView's Pine Script computes them (ta.sma, ta.rma, ta.dmi).
None stands for Pine's na."""
from app.marketdata.base import Bar


def sma(values: list[float | None], length: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        window = values[i - length + 1:i + 1] if i + 1 >= length else None
        out.append(None if window is None or any(v is None for v in window) else sum(window) / length)
    return out


def rma(values: list[float | None], length: int) -> list[float | None]:
    """Wilder's average: seeded with the simple average of the first `length` values, then
    alpha = 1/length. Like Pine, it stays na until a full window of values exists."""
    alpha = 1.0 / length
    seed = sma(values, length)
    out: list[float | None] = []
    prev: float | None = None
    for i, v in enumerate(values):
        if prev is None:
            cur = seed[i]
        else:
            # Pine: alpha * na is na, and the average restarts from a simple average.
            cur = None if v is None else alpha * v + (1 - alpha) * prev
        out.append(cur)
        prev = cur
    return out


def true_range(bars: list[Bar]) -> list[float | None]:
    """ta.tr: na on the first candle (no previous close)."""
    out: list[float | None] = [None]
    for i in range(1, len(bars)):
        b, pc = bars[i], bars[i - 1].close
        out.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return out[:len(bars)]


def adx(bars: list[Bar], di_length: int, adx_smoothing: int) -> list[float | None]:
    """ta.dmi(di_length, adx_smoothing)'s ADX."""
    n = len(bars)
    plus_dm: list[float | None] = [None] * n
    minus_dm: list[float | None] = [None] * n
    for i in range(1, n):
        up = bars[i].high - bars[i - 1].high
        down = -(bars[i].low - bars[i - 1].low)
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    tr = rma(true_range(bars), di_length)
    rp = rma(plus_dm, di_length)
    rm = rma(minus_dm, di_length)
    plus: list[float | None] = []
    minus: list[float | None] = []
    last_p = last_m = None  # fixnan: keep the last value when the division gives na
    for i in range(n):
        p = 100 * rp[i] / tr[i] if rp[i] is not None and tr[i] else None
        m = 100 * rm[i] / tr[i] if rm[i] is not None and tr[i] else None
        last_p = p if p is not None else last_p
        last_m = m if m is not None else last_m
        plus.append(last_p)
        minus.append(last_m)
    dx: list[float | None] = []
    for p, m in zip(plus, minus):
        if p is None or m is None:
            dx.append(None)
        else:
            s = p + m
            dx.append(abs(p - m) / (s if s != 0 else 1))
    return [None if v is None else 100 * v for v in rma(dx, adx_smoothing)]

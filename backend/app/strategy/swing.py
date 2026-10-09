"""'Swing — Vela Diaria/Semanal v9.35', translated from docs/swing_diario_semanal_v9_35.pine.

The translation follows the script statement by statement and in the script's order; comments
give the script's own names so the two can be read side by side. Where the plan's summary and the
script differ, the script wins (plan section 7).

Conventions shared with TradingView:
- Each candle is evaluated at its close. A candle still in progress is previewed (its signal
  shown) but never opens or closes a trade (plan section 7.5).
- Daily and weekly candles count as starting at 9:30 New York time (their session open), which
  is what the script's session checks (first candle of the session, opening window, forced close
  time) see on those charts.
- On a daily chart every candle is the last candle of its session (session.islastbar_regular),
  so a weekly ladder level changes at Friday's close. On intraday charts the last candle is the
  one that ends at 16:00; on early-close days (13:00) TradingView may treat the 12:00 candle as the
  last, which the parity check should confirm (until then, the change waits for Monday's first candle).

What the site leaves out: the script's drawing options (arrows, labels, colours, table positions)
and the ATR target/stop, which the script keeps switched off. The script's 4-hour ladder level
(f_finTF "240") needs 4-hour candles, which the site does not offer yet; a level set to 1h reads
the last closed hour like any other level, as the script does for timeframes f_finTF does not know.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.marketdata.base import Bar
from app.strategy import indicators
from app.strategy.base import TIMEFRAME_SECONDS, InputDef, Strategy, StrategyData

NY = ZoneInfo("America/New_York")
TYPES = ("LLENA", "FLECO", "ENGULFING", "RACHA")
INTRADAY = ("1m", "5m", "15m", "1h")
VERSION = "v9.35"

G_SIGNAL = "1 · Candle types (Tipos de vela)"
G_FILTERS = "2 · Signal filters (Filtros)"
G_LADDER = "3 · Higher-timeframe ladder (Escalera)"
G_LEVEL = "3{} · Ladder level {} ({})"
G_NORMAL = "4 · Normal mode: each signal its own trade"
G_SS = "5 · Signal to signal and basket (cesta)"
G_CAP = "6 · Capital (stock)"
G_OPEN = "7 · Opening window (Apertura)"
G_CS = "8 · Credit spreads (statistics)"
G_MA = "9 · Moving average and ADX (rarely used)"


def _level_inputs(n: int, letter: str, name: str, tf: str, ll: tuple) -> list[InputDef]:
    """One ladder level's own settings (script groups 3a, 3b, 3c)."""
    use_l, use_f, use_e, use_r, cl, mf, mfm, ce, nr, nv = ll
    g = G_LEVEL.format(letter, n, name)
    return [
        InputDef(f"e{n}L", "LLENA", "bool", use_l, g),
        InputDef(f"e{n}F", "FLECO", "bool", use_f, g),
        InputDef(f"e{n}E", "ENGULFING", "bool", use_e, g),
        InputDef(f"e{n}R", "RACHA", "bool", use_r, g),
        InputDef(f"e{n}Ll", "LLENA: body ≥ %", "float", cl, g, 1, 100),
        InputDef(f"e{n}Fl", "FLECO: wick ≥ %", "float", mf, g, 1, 100),
        InputDef(f"e{n}FlM", "FLECO: minimum body %", "float", mfm, g, 0, 90),
        InputDef(f"e{n}En", "ENGULFING: body ≥ % (0 = any size)", "float", ce, g, 0, 100),
        InputDef(f"e{n}nR", "RACHA: N red candles → buy", "int", nr, g, 1, 20),
        InputDef(f"e{n}nV", "RACHA: N green candles → sell", "int", nv, g, 1, 20),
    ]


INPUTS: list[InputDef] = [
    # 1 · Tipos de vela
    InputDef("verLlena", "LLENA: use", "bool", True, G_SIGNAL),
    InputDef("cuerpoLlena", "LLENA: body ≥ % of range", "float", 85.0, G_SIGNAL, 1, 100),
    InputDef("verFleco", "FLECO: use", "bool", True, G_SIGNAL),
    InputDef("mechaFleco", "FLECO: wick ≥ % of range", "float", 65.0, G_SIGNAL, 1, 100),
    InputDef("cuerpoMinFleco", "FLECO: minimum body %", "float", 4.0, G_SIGNAL, 0, 90,
             help="0 = use all, even tiny-bodied dojis."),
    InputDef("verEngulf", "ENGULFING: use", "bool", True, G_SIGNAL),
    InputDef("cuerpoEngulf", "ENGULFING: minimum body %", "float", 61.0, G_SIGNAL, 0, 100,
             help="0 = size does not matter."),
    InputDef("noEngPrimera", "No ENGULFING on the first candle of the session", "bool", False, G_SIGNAL,
             help="Avoids the overnight gap."),
    InputDef("usarRacha", "RACHA: candles in a row of the same colour (exhaustion, reversed)", "bool", True, G_SIGNAL),
    InputDef("nRojas", "RACHA: N red candles in a row → buy", "int", 3, G_SIGNAL, 1, 20),
    InputDef("nVerdes", "RACHA: N green candles in a row → sell", "int", 3, G_SIGNAL, 1, 20),
    InputDef("rachaFleco", "RACHA: require a wick on the reversal side", "bool", False, G_SIGNAL,
             help="If candle N has none, keeps looking while the run of the same colour continues."),
    InputDef("rachaFlecoMecha", "RACHA wick: minimum % of range", "float", 40.0, G_SIGNAL, 1, 100),
    # 2 · Filtros de la señal
    InputDef("usarContinua", "CONTINUATION: only signals following the last one on this chart", "bool", False, G_FILTERS),
    InputDef("maxTradesSesion", "Most trades per day (0 = no limit)", "int", 0, G_FILTERS, 0, 1000),
    InputDef("ganSeguidas", "WINNERS IN A ROW: after N in one direction, the next must be the other (0 = off)", "int", 0,
             G_FILTERS, 0, 1000),
    InputDef("perSeguidas", "LOSERS IN A ROW: after N in one direction, the next must be the other (0 = off)", "int", 0,
             G_FILTERS, 0, 1000),
    # 3 · Escalera
    InputDef("usarEscalera", "LADDER: only signals agreeing with the higher timeframes", "bool", False, G_LADDER),
    InputDef("filtro1", "Level 1 on", "bool", True, G_LADDER),
    InputDef("tfS1", "Level 1 timeframe", "timeframe", "1W", G_LADDER),
    InputDef("filtro2", "Level 2 on", "bool", False, G_LADDER),
    InputDef("tfS2", "Level 2 timeframe", "timeframe", "1D", G_LADDER),
    InputDef("filtro3", "Level 3 on", "bool", False, G_LADDER),
    InputDef("tfS3", "Level 3 timeframe", "timeframe", "1h", G_LADDER,
             help="The script's default is 4 hours, which the site does not offer yet."),
    InputDef("maxPerdW", "Most LOSSES per level-1 (weekly) signal, then wait for the next (0 = off)", "int", 0,
             G_LADDER, 0, 1000),
    InputDef("cierraCambioW", "Normal mode: CLOSE opposite trades when level 1 (weekly) changes", "bool", False, G_LADDER),
    *_level_inputs(1, "a", "Weekly", "1W", (True, True, True, True, 85.0, 65.0, 4.0, 61.0, 3, 7)),
    *_level_inputs(2, "b", "Daily", "1D", (True, True, False, True, 85.0, 70.0, 4.0, 61.0, 2, 7)),
    *_level_inputs(3, "c", "4 hours", "1h", (True, True, False, True, 70.0, 55.0, 8.0, 0.0, 2, 5)),
    # 4 · Modo NORMAL
    InputDef("objPct", "TARGET % (objetivo)", "float", 15.0, G_NORMAL, 0.1, 1000),
    InputDef("stopPct", "STOP %", "float", 13.0, G_NORMAL, 0.1, 100),
    InputDef("maxVelas", "Candles until closing at market / floating", "int", 13, G_NORMAL, 1, 1000),
    InputDef("cierraMercado", "Close at market after N candles (else leave floating)", "bool", True, G_NORMAL),
    InputDef("unaVez", "ONE AT A TIME: no new position until the last closes", "bool", False, G_NORMAL),
    InputDef("maxAbiertas", "Most trades open at once (0 = no limit)", "int", 10, G_NORMAL, 0, 1000),
    InputDef("normalCuenta", "How to count", "choice", "Repartido", G_NORMAL,
             options=("Repartido", "Cada operación su %"),
             help="Repartido = each trade is worth 1/most-open of the account in the R column."),
    InputDef("cierraContra", "CLOSE open trades on the OPPOSITE signal", "bool", False, G_NORMAL),
    InputDef("dirOper", "DIRECTION: which side to trade", "choice", "Ambas", G_NORMAL,
             options=("Ambas", "Solo largos", "Solo cortos"),
             help="Ambas = both. In signal to signal and basket, a signal of the switched-off side still closes."),
    InputDef("usarIntra", "Real path: look inside the candle for the true order", "bool", True, G_NORMAL),
    InputDef("tfIntra", "Real path timeframe", "timeframe", "1h", G_NORMAL,
             help="E.g. 1D on weekly, 1h on daily. Must not be above the chart's timeframe."),
    # 5 · Señal a señal y basket
    InputDef("modoSenal", "SIGNAL TO SIGNAL: hold until the opposite signal", "bool", False, G_SS),
    InputDef("tpPctSS", "Take profit % (0 = none, opposite signal only)", "float", 0.0, G_SS, 0, 1000),
    InputDef("slPctSS", "Stop loss % (0 = none, opposite signal only)", "float", 0.0, G_SS, 0, 100),
    InputDef("horaCierreSS", "Close no matter what at this time (HH:MM New York)", "time", "", G_SS,
             help="Empty = none. No new entries after it."),
    InputDef("modoCesta", "BASKET: add same-side signals, close the basket together", "bool", False, G_SS,
             help="Only with signal to signal on. Take profit and stop apply to the average entry."),
    InputDef("maxCesta", "Basket: most entries (0 = no limit)", "int", 5, G_SS, 0, 1000),
    InputDef("cestaCuenta", "Basket: how to count", "choice", "Repartido", G_SS,
             options=("Repartido", "Cada entrada su %")),
    # 6 · Capital
    InputDef("capIni", "Starting capital ($)", "float", 10000.0, G_CAP, 1, 1e12),
    InputDef("capPct", "% of capital per trade", "float", 2.0, G_CAP, 0.01, 100),
    InputDef("capComp", "Compounding (the % is of the account at that moment)", "bool", False, G_CAP),
    # 7 · Franja de apertura
    InputDef("soloApertura", "Only signals INSIDE the opening window", "bool", False, G_OPEN),
    InputDef("sesApertura", "Opening window (New York)", "choice", "0930-1100", G_OPEN,
             options=("0930-1000", "0930-1030", "0930-1100", "0930-1130", "0930-1200", "0930-1600")),
    # 9 · Credit spreads (estadística)
    InputDef("csAncho", "Spread width, % of price", "float", 1.33, G_CS, 0.05, 100,
             help="$5 wide with TSLA at 375 ≈ 1.33."),
    InputDef("csD1", "Short strike, % from price: row 1", "float", 4.0, G_CS, 0, 100),
    InputDef("csD2", "Row 2", "float", 6.0, G_CS, 0, 100),
    InputDef("csD3", "Row 3", "float", 8.0, G_CS, 0, 100),
    InputDef("csD4", "Row 4", "float", 10.0, G_CS, 0, 100),
    InputDef("csH1", "Calendar days to expiration: block 1", "int", 14, G_CS, 1, 3650),
    InputDef("csH2", "Block 2", "int", 21, G_CS, 1, 3650),
    InputDef("csH3", "Block 3", "int", 28, G_CS, 1, 3650),
    InputDef("csPagaObj", "Target payout (x:1): highlights the closest row", "float", 2.0, G_CS, 0.1, 100),
    InputDef("csSimH", "SIMULATION: days block to use (1, 2 or 3)", "int", 2, G_CS, 1, 3),
    InputDef("csSimJ", "SIMULATION: strike row to use (1 to 4)", "int", 2, G_CS, 1, 4),
    InputDef("csSimPaga", "SIMULATION: real payout of the spread (x:1)", "float", 2.0, G_CS, 0.05, 100),
    InputDef("csSimRiesgo", "SIMULATION: $ risked on each spread", "float", 5000.0, G_CS, 1, 1e12),
    InputDef("csSimCuenta", "SIMULATION: starting account $", "float", 100000.0, G_CS, 1, 1e12),
    # 9 · Media móvil y ADX
    InputDef("maCual", "Moving average filter", "choice", "Ninguna", G_MA,
             options=("Ninguna", "MA1", "MA2", "MA3"), help="Ninguna = none. Buys only above it, sells only below."),
    InputDef("maLen1", "MA1 length", "int", 50, G_MA, 1, 1000),
    InputDef("maLen2", "MA2 length", "int", 100, G_MA, 1, 1000),
    InputDef("maLen3", "MA3 length", "int", 200, G_MA, 1, 1000),
    InputDef("usarADX", "ADX filter", "bool", False, G_MA),
    InputDef("adxCond", "ADX: trade when it is", "choice", "mayor", G_MA, options=("mayor", "menor"),
             help="mayor = above the level (trend), menor = below (range)."),
    InputDef("adxUmbral", "ADX level", "float", 25.0, G_MA, 0, 100),
    InputDef("adxLen", "ADX period", "int", 14, G_MA, 1, 500),
]


# ---------- candle measurements and types (la señal, and f_sig / f_sigX) ----------


@dataclass(frozen=True)
class Candle:
    up: bool  # LLENA / FLECO / ENGULFING buy that its switch allows (sigUpRaw without the RACHA)
    dn: bool
    tipo: int  # 0 LLENA, 1 FLECO, 2 ENGULFING, as the script names a candle without its RACHA
    relleno: float
    m_max: float
    m_sup: float = 0.0
    m_inf: float = 0.0
    en_up: bool = False  # dEnUp, dFlUp, ... before the switches
    en_dn: bool = False
    fl_up: bool = False
    fl_dn: bool = False


def classify(b: Bar, p: Bar | None, cuerpo_llena: float, mecha_fleco: float, cuerpo_min_fleco: float,
             cuerpo_engulf: float, ver_llena: bool = True, ver_fleco: bool = True, ver_engulf: bool = True,
             eng_bloq: bool = False) -> Candle:
    rng = b.high - b.low
    cuerpo = abs(b.close - b.open)
    relleno = cuerpo / rng * 100.0 if rng > 0 else 0.0
    m_sup = (b.high - max(b.open, b.close)) / rng * 100.0 if rng > 0 else 0.0
    m_inf = (min(b.open, b.close) - b.low) / rng * 100.0 if rng > 0 else 0.0
    m_max = max(m_sup, m_inf)
    verde = b.close >= b.open
    # Pine: on the first candle close[1] is na, and every comparison with na is false.
    ap_verde = p is not None and p.close >= p.open
    es_llena = relleno >= cuerpo_llena
    es_eng = p is not None and (verde != ap_verde) and cuerpo > abs(p.close - p.open) and relleno >= cuerpo_engulf
    es_fleco = m_max >= mecha_fleco and relleno >= cuerpo_min_fleco
    d_ll_up = es_llena and not es_eng and not es_fleco and verde
    d_ll_dn = es_llena and not es_eng and not es_fleco and not verde
    d_en_up = es_eng and verde and not eng_bloq
    d_en_dn = es_eng and not verde and not eng_bloq
    d_fl_up = es_fleco and not es_eng and m_inf > m_sup and p is not None and b.close >= p.low
    d_fl_dn = es_fleco and not es_eng and m_sup >= m_inf and p is not None and b.close <= p.high
    up = (ver_llena and d_ll_up) or (ver_engulf and d_en_up) or (ver_fleco and d_fl_up)
    dn = (ver_llena and d_ll_dn) or (ver_engulf and d_en_dn) or (ver_fleco and d_fl_dn)
    tipo = 2 if (ver_engulf and (d_en_up or d_en_dn)) else (1 if (ver_fleco and (d_fl_up or d_fl_dn)) else 0)
    return Candle(up, dn, tipo, relleno, m_max, m_sup, m_inf, d_en_up, d_en_dn, d_fl_up, d_fl_dn)


def level_settings(x: dict, n: int) -> dict:
    """A ladder level's own settings, as f_sig takes them."""
    return {"vL": x[f"e{n}L"], "vF": x[f"e{n}F"], "vE": x[f"e{n}E"], "uR": x[f"e{n}R"], "cLl": x[f"e{n}Ll"],
            "mFl": x[f"e{n}Fl"], "cMinFl": x[f"e{n}FlM"], "cEng": x[f"e{n}En"], "nR": x[f"e{n}nR"], "nV": x[f"e{n}nV"]}


def sig_x(lv: dict, b: Bar, p: Bar | None, ro1: int, ve1: int) -> tuple[bool, bool]:
    """f_sigX (and f_sig): a level's signal for candle b after candle p. ro1 / ve1 are the red and
    green candles in a row up to p."""
    c = classify(b, p, lv["cLl"], lv["mFl"], lv["cMinFl"], lv["cEng"], lv["vL"], lv["vF"], lv["vE"])
    n_ro = ro1 + 1 if b.close < b.open else 0
    n_ve = ve1 + 1 if b.close > b.open else 0
    return c.up or (lv["uR"] and n_ro == lv["nR"]), c.dn or (lv["uR"] and n_ve == lv["nV"])


def runs_in_a_row(bars: list[Bar]) -> tuple[list[int], list[int]]:
    """nz(ta.barssince(not (close < open))) and the same for green: candles of one colour in a row
    up to each candle. Pine's barssince is na (so 0) until the condition has been true once."""
    reds, greens = [], []
    last_not_red = last_not_green = None
    for k, b in enumerate(bars):
        if not (b.close < b.open):
            last_not_red = k
        if not (b.close > b.open):
            last_not_green = k
        reds.append(k - last_not_red if last_not_red is not None else 0)
        greens.append(k - last_not_green if last_not_green is not None else 0)
    return reds, greens


def htf_index(chart: list[Bar], htf: list[Bar]) -> list[int]:
    """For each chart candle, the higher-timeframe candle that contains it (-1 before the first)."""
    out, k = [], -1
    for b in chart:
        while k + 1 < len(htf) and htf[k + 1].time <= b.time:
            k += 1
        out.append(k)
    return out


# ---------- time helpers ----------


def session_moment(b: Bar, tf: str) -> datetime:
    """When the candle starts in New York time, as the script's session checks see it."""
    if tf in INTRADAY:
        return datetime.fromtimestamp(b.time, NY)
    d = datetime.fromtimestamp(b.time, timezone.utc).date()
    return datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY)


def tv_time(b: Bar, tf: str) -> int:
    """The candle's `time` as the script sees it: daily and weekly candles at 9:30 New York, which
    moves against UTC when the clocks change. Day counts and credit-spread expirations use it."""
    return int(session_moment(b, tf).timestamp())


def bar_year(b: Bar, tf: str) -> int:
    if tf in INTRADAY:
        return datetime.fromtimestamp(b.time, NY).year
    return datetime.fromtimestamp(b.time, timezone.utc).year


def in_session(minute: int, session: str) -> bool:
    start = int(session[:2]) * 60 + int(session[2:4])
    end = int(session[5:7]) * 60 + int(session[7:9])
    return start <= minute < end


def last_regular_bar(b: Bar, tf: str) -> bool:
    """session.islastbar_regular: the candle that ends the regular session (16:00 New York)."""
    if tf not in INTRADAY:
        return True
    start = datetime.fromtimestamp(b.time, NY)
    m = start.hour * 60 + start.minute
    return m < 960 <= m + TIMEFRAME_SECONDS[tf] // 60


def ends_higher_candle(b: Bar, tf: str, level_tf: str) -> bool:
    """f_finTF: this chart candle is the last one of its higher-timeframe candle."""
    last = last_regular_bar(b, tf)
    if level_tf == "1W":
        return last and session_moment(b, tf).weekday() == 4
    if level_tf == "1D":
        return last
    return False


def group_inside(bars: list[Bar], subs: list[Bar]) -> list[list[Bar]]:
    """Lower-timeframe candles inside each chart candle, in order (request.security_lower_tf)."""
    out: list[list[Bar]] = [[] for _ in bars]
    j = 0
    for i, b in enumerate(bars):
        end = bars[i + 1].time if i + 1 < len(bars) else math.inf
        while j < len(subs) and subs[j].time < b.time:
            j += 1
        k = j
        while k < len(subs) and subs[k].time < end:
            out[i].append(subs[k])
            k += 1
        j = k
    return out


# ---------- the fixed target/stop rule shared by the backtest and the luck test ----------


def hit_target_or_stop(d: int, tgt: float, stp: float, b: Bar, inside: list[Bar], use_inside: bool) -> tuple[int, bool]:
    """1 target, -1 stop, 0 neither; and whether the candle opened past one of them (v9.35: then the
    trade closes at the open). A gap decides first; then the path inside the candle, or the
    candle's own high and low with the stop first."""
    if d == 1:
        h = -1 if b.open <= stp else (1 if b.open >= tgt else 0)
    else:
        h = -1 if b.open >= stp else (1 if b.open <= tgt else 0)
    if h != 0:
        return h, True
    if use_inside and inside:
        for s in inside:
            if d == 1:
                h = -1 if s.low <= stp else (1 if s.high >= tgt else 0)
            else:
                h = -1 if s.high >= stp else (1 if s.low <= tgt else 0)
            if h != 0:
                return h, False
        return 0, False
    if d == 1:
        h = -1 if b.low <= stp else (1 if b.high >= tgt else 0)
    else:
        h = -1 if b.high >= stp else (1 if b.low <= tgt else 0)
    return h, False


def move_pct(d: int, entry: float, price: float) -> float:
    return (price - entry) / entry * 100.0 if d == 1 else (entry - price) / entry * 100.0


@dataclass
class OpenTrade:
    entry: float
    d: int
    bar: int
    tipo: int
    year: int
    time: int
    tv: int  # the entry as TradingView times it (see tv_time)


def f_cap(eq: float, mx: float, dd: float, raw: float, x: dict) -> tuple[float, float, float]:
    """The account in stock (section 6): capPct of the starting (or current) capital per trade."""
    inv = (eq if x["capComp"] else x["capIni"]) * x["capPct"] / 100.0
    nq = eq + inv * raw / 100.0
    mx = max(mx, nq)
    dd = max(dd, (mx - nq) / mx * 100.0) if mx > 0 else dd
    return nq, mx, dd


def f_seg(d: int, w: int, seg: dict, x: dict) -> None:
    """Winners / losers in a row in one direction (v9.14): after N, that direction is blocked."""
    if w == 0:
        return
    if d == seg["dir"] and w == seg["res"]:
        seg["n"] += 1
    else:
        seg["dir"], seg["res"], seg["n"] = d, w, 1
    tope = x["ganSeguidas"] if w == 1 else x["perSeguidas"]
    if tope > 0 and seg["n"] >= tope:
        seg["bloq"] = d


def _op(tipo: int, d: int, entry_i: int, entry_time: int, entry_year: int, exit_i: int, exit_year: int, res: int,
        r_book: float, cap: float | None, path: bool = False, dur: dict | None = None) -> dict:
    """One operation as the script counts it: res 1 won, -1 lost, 0 floating; r_book what goes into
    the R column (divided when shared); cap the move the capital row applies; dur the duration and
    days-to-win/lose this operation adds (a basket adds it once, on its first entry)."""
    return {"tipo": tipo, "dir": d, "entry_i": entry_i, "entry_time": entry_time, "entry_year": entry_year,
            "exit_i": exit_i, "exit_year": exit_year, "res": res, "r_book": r_book, "cap": cap, "path": path,
            "dur": dur}


# ---------- luck test helpers (PRUEBA DE AZAR) ----------


def ncdf(x: float) -> float:
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    d = 0.3989422804 * math.exp(-x * x / 2.0)
    p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
    return 1.0 - p if x >= 0 else p


def sd(n: float, sm: float, sm2: float) -> float | None:
    return math.sqrt(max((sm2 - sm * sm / n) / (n - 1), 0.0)) if n > 1 else None


class Stats4:
    """[n resolved, wins (r >= 0), sum r, sum r²] for one direction."""

    def __init__(self):
        self.v = [0.0, 0.0, 0.0, 0.0]

    def add(self, r: float) -> None:
        self.v[0] += 1
        self.v[1] += 1 if r >= 0 else 0
        self.v[2] += r
        self.v[3] += r * r


@dataclass
class Shadow:
    e: float
    d: int
    b: int


def book_step(book: list[Shadow], i: int, b: Bar, inside: list[Bar], use_inside: bool, obj: float, stop: float,
              max_velas: int, cierra: bool, st_l: Stats4, st_s: Stats4, results: dict | None) -> None:
    """f_bookStep."""
    zo, zs = obj / 100.0, stop / 100.0
    for zi in range(len(book) - 1, -1, -1):
        z = book[zi]
        tgt = z.e * (1 + zo) if z.d == 1 else z.e * (1 - zo)
        stp = z.e * (1 - zs) if z.d == 1 else z.e * (1 + zs)
        res, r, counts = False, 0.0, True
        if i > z.b:
            h, gap = hit_target_or_stop(z.d, tgt, stp, b, inside, use_inside)
            if h != 0 and gap:
                r, res = move_pct(z.d, z.e, b.open), True
            elif h == 1:
                r, res = obj, True
            elif h == -1:
                r, res = -stop, True
            if not res and (i - z.b) >= max_velas:
                r = move_pct(z.d, z.e, b.close)
                res, counts = True, cierra
        if res:
            if counts:
                (st_l if z.d == 1 else st_s).add(r)
            if results is not None:
                results[z.b * 2 + (0 if z.d == 1 else 1)] = r if counts else None
            del book[zi]


# ---------- credit spreads (estadística a vencimiento) ----------


class CreditSpreads:
    """For each signal that opens, where the price closed N calendar days later, and what a credit
    spread with its short strike X% beyond the price in the signal's direction would have done; the
    same on every candle for comparison; and a dollar simulation of one row (v9.33, v9.34)."""

    def __init__(self, x: dict):
        self.x = x
        self.dist = [x["csD1"], x["csD2"], x["csD3"], x["csD4"]]
        self.secs = [x["csH1"] * 86400, x["csH2"] * 86400, x["csH3"] * 86400]
        self.n = [0.0] * 12
        self.loss = [0.0] * 48
        self.win = [0.0] * 48
        self.full = [0.0] * 48
        self.sig: list[tuple[float, int, int]] = []  # entry close, direction, time
        self.sig_p = [0, 0, 0]
        self.rnd: list[tuple[float, int]] = []
        self.rnd_p = [0, 0, 0]
        self.eq = self.peak = self.max_dd = self.min_eq = self.run = self.worst_run = 0.0
        self.sim_n = self.sim_w = self.sim_full = self.max_open = 0
        self.years: dict[int, float] = {}

    def _add(self, book: int, side: int, h: int, m: float) -> None:
        ni = (book * 2 + side) * 3 + h
        self.n[ni] += 1
        w = self.x["csAncho"]
        for j, d in enumerate(self.dist):
            ix = ni * 4 + j
            self.loss[ix] += max(0.0, min(w, d - m))
            if m >= d:
                self.win[ix] += 1
            if m <= d - w:
                self.full[ix] += 1

    def step(self, b: Bar, now: int, year: int, sig_dir: int, take_random: bool) -> None:
        """`now` is the candle's time as the script sees it (tv_time)."""
        x = self.x
        for h in range(3):
            while self.sig_p[h] < len(self.sig) and now >= self.sig[self.sig_p[h]][2] + self.secs[h]:
                e, d, _ = self.sig[self.sig_p[h]]
                mv = (b.close - e) / e * 100.0 * d
                self._add(0, 0 if d == 1 else 1, h, mv)
                if h == x["csSimH"] - 1:
                    ls = max(0.0, min(x["csAncho"], self.dist[x["csSimJ"] - 1] - mv)) / x["csAncho"]
                    cf = x["csSimPaga"] / (1 + x["csSimPaga"])
                    pnl = x["csSimRiesgo"] * (cf - ls) / (1 - cf)
                    self.eq += pnl
                    self.sim_n += 1
                    self.sim_w += 1 if pnl >= 0 else 0
                    self.sim_full += 1 if ls >= 1.0 else 0
                    self.peak = max(self.peak, self.eq)
                    self.max_dd = max(self.max_dd, self.peak - self.eq)
                    self.min_eq = min(self.min_eq, self.eq)
                    self.run = self.run + pnl if pnl < 0 else 0.0
                    self.worst_run = min(self.worst_run, self.run)
                    self.years[year] = self.years.get(year, 0.0) + pnl
                self.sig_p[h] += 1
            while self.rnd_p[h] < len(self.rnd) and now >= self.rnd[self.rnd_p[h]][1] + self.secs[h]:
                e, _ = self.rnd[self.rnd_p[h]]
                mr = (b.close - e) / e * 100.0
                self._add(1, 0, h, mr)
                self._add(1, 1, h, -mr)
                self.rnd_p[h] += 1
        if sig_dir:
            self.sig.append((b.close, sig_dir, now))
            self.max_open = max(self.max_open, len(self.sig) - self.sig_p[x["csSimH"] - 1])
        if take_random:
            self.rnd.append((b.close, now))

    def table(self) -> dict:
        x, w = self.x, self.x["csAncho"]

        def payout(loss: float | None) -> float | None:
            return None if loss is None or w - loss <= 0 else loss / (w - loss)

        blocks = []
        for h in range(3):
            n_sl, n_ss, n_rl, n_rs = self.n[h], self.n[3 + h], self.n[6 + h], self.n[9 + h]
            best, best_d = None, 1e9
            for j in range(4):
                pay = payout(self.loss[(6 + h) * 4 + j] / n_rl if n_rl else None)
                if pay is not None and abs(pay - x["csPagaObj"]) < best_d:
                    best, best_d = j, abs(pay - x["csPagaObj"])
            rows = []
            for j in range(4):
                i_sl, i_ss, i_rl, i_rs = h * 4 + j, (3 + h) * 4 + j, (6 + h) * 4 + j, (9 + h) * 4 + j
                n_s = n_sl + n_ss
                p_l = n_sl / n_s if n_s else 0.5
                sides = {}
                for name, n_sig, n_rnd, i_sig, i_rnd in (("long", n_sl, n_rl, i_sl, i_rl), ("short", n_ss, n_rs, i_ss, i_rs)):
                    sig_loss = self.loss[i_sig] / n_sig if n_sig else None
                    rnd_loss = self.loss[i_rnd] / n_rnd if n_rnd else None
                    ev = (None if sig_loss is None or rnd_loss is None or w - rnd_loss <= 0
                          else (rnd_loss - sig_loss) / (w - rnd_loss))
                    sides[name] = {"n": int(n_sig), "needed": payout(sig_loss), "random_payout": payout(rnd_loss), "r": ev}
                ev_l, ev_s = sides["long"]["r"], sides["short"]["r"]
                ev_t = (None if ev_l is None and ev_s is None else
                        ev_l if ev_s is None else ev_s if ev_l is None else p_l * ev_l + (1 - p_l) * ev_s)
                rows.append({
                    "strike_pct": self.dist[j],
                    "win_all": 100.0 * (self.win[i_sl] + self.win[i_ss]) / n_s if n_s else None,
                    "win_all_random": (100.0 * (p_l * self.win[i_rl] / n_rl + (1 - p_l) * self.win[i_rs] / n_rs)
                                       if n_rl and n_rs else None),
                    "lose_all": 100.0 * (self.full[i_sl] + self.full[i_ss]) / n_s if n_s else None,
                    "long": sides["long"], "short": sides["short"], "total_r": ev_t, "closest": j == best,
                })
            blocks.append({"days": x[f"csH{h + 1}"], "rows": rows})
        broke = x["csSimCuenta"] + self.min_eq <= 0
        return {
            "width_pct": w, "target_payout": x["csPagaObj"], "blocks": blocks,
            "simulation": {
                "days": x[f"csH{x['csSimH']}"], "strike_pct": self.dist[x["csSimJ"] - 1], "payout": x["csSimPaga"],
                "risk_per_spread": x["csSimRiesgo"], "account": x["csSimCuenta"], "total": self.eq,
                "total_pct": self.eq / x["csSimCuenta"] * 100.0, "spreads": self.sim_n,
                "win_rate": 100.0 * self.sim_w / self.sim_n if self.sim_n else None,
                "lose_all_rate": 100.0 * self.sim_full / self.sim_n if self.sim_n else None,
                "max_drop": self.max_dd, "max_drop_pct": self.max_dd / x["csSimCuenta"] * 100.0,
                "worst_run": -self.worst_run, "max_open": self.max_open, "max_at_risk": self.max_open * x["csSimRiesgo"],
                "broke": broke, "lowest_account": x["csSimCuenta"] + self.min_eq,
                "years": [{"year": y, "pnl": v} for y, v in sorted(self.years.items()) if v != 0],
            },
        }


# ---------- the strategy ----------


class SwingStrategy(Strategy):
    # The id is the one the first translation (v9.8) was saved under, so pegged settings, automatic
    # trades and saved backtests keep pointing at this strategy.
    id = "swing_v98"
    name = "Swing, Vela Diaria/Semanal"
    version = VERSION
    inputs = INPUTS

    def needs(self, timeframe: str, inputs: dict) -> set[str]:
        chart_sec = TIMEFRAME_SECONDS[timeframe]
        out: set[str] = set()
        if inputs["usarIntra"] and TIMEFRAME_SECONDS[inputs["tfIntra"]] <= chart_sec and inputs["tfIntra"] != timeframe:
            out.add(inputs["tfIntra"])
        if inputs["usarEscalera"]:
            for n in (1, 2, 3):
                tf = inputs[f"tfS{n}"]
                if inputs[f"filtro{n}"] and TIMEFRAME_SECONDS[tf] > chart_sec:
                    out.add(tf)
        return out

    def run(self, data: StrategyData, inputs: dict, *, luck: bool = False,  # noqa: C901 (mirrors the script)
            luck_from: int | None = None) -> dict:
        x = inputs
        bars, tf = data.bars, data.timeframe
        n = len(bars)
        closed = min(data.closed, n)
        chart_sec = TIMEFRAME_SECONDS[tf]

        # Real path inside each candle.
        intra_ok = x["usarIntra"] and TIMEFRAME_SECONDS[x["tfIntra"]] <= chart_sec
        if intra_ok and x["tfIntra"] == tf:
            inside = [[b] for b in bars]
        elif intra_ok:
            inside = group_inside(bars, data.other.get(x["tfIntra"], []))
        else:
            inside = [[] for _ in bars]

        # Ladder: three levels, each a higher timeframe with its own settings. A level counts only
        # if its timeframe is strictly higher than the chart's.
        levels = []
        for k in (1, 2, 3):
            ltf = x[f"tfS{k}"]
            active = x["usarEscalera"] and x[f"filtro{k}"] and TIMEFRAME_SECONDS[ltf] > chart_sec
            lv = {"tf": ltf, "active": active, "state": 0, "set": level_settings(x, k)}
            if active:
                htf = data.other.get(ltf, [])
                reds, greens = runs_in_a_row(htf)
                # f_sig on each higher candle, read by the chart one candle later ([1], lookahead_on).
                st = lv["set"]
                sig = []
                for j in range(len(htf)):
                    cj = classify(htf[j], htf[j - 1] if j else None, st["cLl"], st["mFl"], st["cMinFl"], st["cEng"],
                                  st["vL"], st["vF"], st["vE"])
                    sig.append((cj.up or (st["uR"] and reds[j] == st["nR"]),
                                cj.dn or (st["uR"] and greens[j] == st["nV"])))
                lv.update(htf=htf, reds=reds, greens=greens, sig=sig, idx=htf_index(bars, htf),
                          k_o=None, k_h=None, k_l=None, k_at=None)
            levels.append(lv)

        # Moving averages and ADX.
        closes = [b.close for b in bars]
        ma_len = {"MA1": x["maLen1"], "MA2": x["maLen2"], "MA3": x["maLen3"]}.get(x["maCual"])
        ma = indicators.sma(closes, ma_len) if ma_len else [None] * n
        adx = indicators.adx(bars, x["adxLen"], x["adxLen"]) if x["usarADX"] else [None] * n

        # Forced close time.
        hc_min = None
        if x["horaCierreSS"]:
            hc_min = int(x["horaCierreSS"][:2]) * 60 + int(x["horaCierreSS"][3:])

        modo_senal, modo_cesta = x["modoSenal"], x["modoCesta"]
        div_n = x["maxAbiertas"] if (x["normalCuenta"] == "Repartido" and x["maxAbiertas"] > 0) else 1

        signals: list[dict] = []
        blocked: list[dict] = []
        preview: dict | None = None
        trades: list[dict] = []
        entries: list[dict] = []  # every position the rules opened, for the worker's automatic trades
        open_trades: list[OpenTrade] = []
        dia_marcado = hc_dia = prev_day = None
        ult_dir = 0
        trades_sesion = 0
        n_up = n_dn = 0  # RACHA counters
        up_disp = dn_disp = False
        perd_w = 0
        w1_reseteado = False
        prev_s1 = 0
        seg = {"dir": 0, "res": 0, "n": 0, "bloq": 0}
        # Signal to signal
        sig_entry, sig_dir, sig_tipo, sig_year, sig_bar, sig_time, sig_tv = None, 0, 0, 0, 0, 0, 0
        # Basket: each entry (price, type, year, candle, time)
        cst_dir, cst_tipo, cst_bar, cst_time, cst_tv = 0, 0, 0, 0, 0
        cst: list[tuple[float, int, int, int, int]] = []
        # Luck test
        sb: list[Shadow] = []
        ab: list[Shadow] = []
        st_sbL, st_sbS, st_abL, st_abS = Stats4(), Stats4(), Stats4(), Stats4()
        sb_map: dict[int, float | None] = {}
        pending: list[tuple[int, int]] = []
        az_sig = [0.0] * 6
        az_edge = [0.0] * 3
        cs = CreditSpreads(x) if luck else None
        # State at the last candle, for the "Now" row.
        now = {}

        for i, b in enumerate(bars):
            live = i >= closed
            p = bars[i - 1] if i else None
            when = session_moment(b, tf)
            min_et = when.hour * 60 + when.minute
            dia_et = when.date()

            # First regular-session candle of the day.
            primera = False
            if 570 <= min_et < 960 and dia_et != dia_marcado:
                primera = True
                dia_marcado = dia_et
            # First candle at or after the forced close time, once a day.
            hora_cierre_hit = False
            if hc_min is not None and min_et >= hc_min and dia_et != hc_dia:
                hora_cierre_hit = True
                hc_dia = dia_et

            c = classify(b, p, x["cuerpoLlena"], x["mechaFleco"], x["cuerpoMinFleco"], x["cuerpoEngulf"],
                         x["verLlena"], x["verFleco"], x["verEngulf"], eng_bloq=x["noEngPrimera"] and primera)

            # RACHA: candles in a row (v9.9), with the optional wick on the reversal side (v9.13).
            n_up = n_up + 1 if b.close > b.open else 0
            n_dn = n_dn + 1 if b.close < b.open else 0
            if n_dn == 0:
                up_disp = False
            if n_up == 0:
                dn_disp = False
            fl_rev_up = c.m_inf >= x["rachaFlecoMecha"] and c.m_inf > c.m_sup
            fl_rev_dn = c.m_sup >= x["rachaFlecoMecha"] and c.m_sup >= c.m_inf
            if x["rachaFleco"]:
                r_up = x["usarRacha"] and n_dn >= x["nRojas"] and fl_rev_up and not up_disp
                r_dn = x["usarRacha"] and n_up >= x["nVerdes"] and fl_rev_dn and not dn_disp
                up_disp = up_disp or r_up
                dn_disp = dn_disp or r_dn
            else:
                r_up = x["usarRacha"] and n_dn == x["nRojas"]
                r_dn = x["usarRacha"] and n_up == x["nVerdes"]

            raw_up = c.up or r_up
            raw_dn = c.dn or r_dn
            ve, vf = x["verEngulf"], x["verFleco"]
            tipo_sig = 2 if ve and (c.en_up or c.en_dn) else (1 if vf and (c.fl_up or c.fl_dn) else (3 if (r_up or r_dn) else 0))
            tipo_up = 2 if ve and c.en_up else (1 if vf and c.fl_up else (3 if r_up else 0))
            tipo_dn = 2 if ve and c.en_dn else (1 if vf and c.fl_dn else (3 if r_dn else 0))

            # ---- ladder ----
            for lv in levels:
                # old: the last CLOSED higher candle's signal ([1], lookahead_on), read all through
                # the next one. fin/up_c/dn_c: this chart candle ends the higher candle, and that
                # candle's own signal, built from the chart's candles (v9.30).
                lv.update(old=(False, False), new=False, fin=False, up_c=False, dn_c=False)
                if not lv["active"]:
                    continue
                k = lv["idx"][i]
                if k >= 1:
                    lv["old"] = lv["sig"][k - 1]
                new_period = k != lv["k_at"] or lv["k_o"] is None
                if new_period:
                    lv["k_o"], lv["k_h"], lv["k_l"], lv["k_at"] = b.open, b.high, b.low, k
                else:
                    lv["k_h"], lv["k_l"] = max(lv["k_h"], b.high), min(lv["k_l"], b.low)
                lv["new"] = new_period and i > 0  # ta.change(time(tf)) is na on the first candle
                lv["fin"] = ends_higher_candle(b, tf, lv["tf"])
                lv["up_c"], lv["dn_c"] = sig_x(lv["set"], Bar(b.time, lv["k_o"], lv["k_h"], lv["k_l"], b.close, 0.0),
                                               lv["htf"][k - 1] if k >= 1 else None,
                                               lv["reds"][k - 1] if k >= 1 else 0, lv["greens"][k - 1] if k >= 1 else 0)
            # Each state persists until its timeframe flips; a buy and a sell together: the buy wins.
            for lv in levels:
                if lv["old"][1]:
                    lv["state"] = -1
                if lv["old"][0]:
                    lv["state"] = 1
            for lv in levels:
                if lv["fin"] and lv["dn_c"]:
                    lv["state"] = -1
                if lv["fin"] and lv["up_c"]:
                    lv["state"] = 1
            l1 = levels[0]
            s1, a1 = l1["state"], l1["active"]
            # Most losses per weekly signal: the counter restarts once per signal (v9.35).
            if l1["fin"] and (l1["up_c"] or l1["dn_c"]):
                perd_w = 0
                w1_reseteado = True
            cambio_w1 = s1 != 0 and s1 != prev_s1
            if l1["new"]:
                if (l1["old"][0] or l1["old"][1]) and not w1_reseteado:
                    perd_w = 0
                w1_reseteado = False
            perm_up = all(not lv["active"] or lv["state"] >= 0 for lv in levels)
            perm_dn = all(not lv["active"] or lv["state"] <= 0 for lv in levels)

            # Continuation compares with the previous RAW signal.
            cont_up = not x["usarContinua"] or ult_dir >= 0
            cont_dn = not x["usarContinua"] or ult_dir <= 0
            if raw_up:
                ult_dir = 1
            if raw_dn:
                ult_dir = -1

            use_ma = x["maCual"] != "Ninguna"
            ma_up = not use_ma or (ma[i] is not None and b.close > ma[i])
            ma_dn = not use_ma or (ma[i] is not None and b.close < ma[i])
            if x["usarADX"]:
                adx_ok = adx[i] is not None and (adx[i] > x["adxUmbral"] if x["adxCond"] == "mayor" else adx[i] < x["adxUmbral"])
            else:
                adx_ok = True

            sig_up = raw_up and perm_up and cont_up and ma_up and adx_ok
            sig_dn = raw_dn and perm_dn and cont_dn and ma_dn and adx_ok
            tipo_sig = tipo_up if (sig_up and not sig_dn) else (tipo_dn if (sig_dn and not sig_up) else tipo_sig)
            for d, raw, ok, perm, cont, mok in ((1, raw_up, sig_up, perm_up, cont_up, ma_up),
                                                (-1, raw_dn, sig_dn, perm_dn, cont_dn, ma_dn)):
                if raw and not ok:
                    why = [w for w, good in (("ladder", perm), ("continuation", cont), ("moving average", mok),
                                             ("ADX", adx_ok)) if not good]
                    blocked.append({"i": i, "time": b.time, "dir": d, "type": TYPES[tipo_up if d == 1 else tipo_dn],
                                    "why": why, "preview": live})

            # Opening window, most per day and the weekly loss cap stop ENTRIES only (v9.35).
            if dia_et != prev_day:  # timeframe.change("D")
                trades_sesion = 0
                prev_day = dia_et
            en_apertura = in_session(min_et, x["sesApertura"])
            ok_apertura = not x["soloApertura"] or en_apertura
            ok_sesion = x["maxTradesSesion"] == 0 or trades_sesion < x["maxTradesSesion"]
            bloq_perd_w = x["maxPerdW"] > 0 and a1 and perd_w >= x["maxPerdW"]
            puede_abrir = ok_apertura and ok_sesion and not bloq_perd_w
            up_v = sig_up and puede_abrir and x["dirOper"] != "Solo cortos"
            dn_v = sig_dn and puede_abrir and x["dirOper"] != "Solo largos"
            up_f, dn_f = up_v, dn_v

            if live:
                # A candle in progress is only previewed: no trades open or close on it.
                if up_v or dn_v:
                    preview = {"i": i, "time": b.time, "dir": 1 if up_v else -1, "type": TYPES[tipo_sig],
                               "price": b.close, "body_pct": c.relleno, "wick_pct": c.m_max}
                continue

            year = bar_year(b, tf)
            tvt = int(when.timestamp())  # tv_time(b, tf)

            # ---------- normal mode: each signal its own trade ----------
            if not modo_senal and open_trades:
                o_pct, s_pct = x["objPct"], x["stopPct"]
                for k in range(len(open_trades) - 1, -1, -1):
                    t = open_trades[k]
                    e, d = t.entry, t.d
                    tgt = e * (1 + o_pct / 100.0) if d == 1 else e * (1 - o_pct / 100.0)
                    stp = e * (1 - s_pct / 100.0) if d == 1 else e * (1 + s_pct / 100.0)
                    res, res_w, motivo, r_t, exit_price, path = False, 0, "", 0.0, None, False
                    if i > t.bar:
                        h, gap = hit_target_or_stop(d, tgt, stp, b, inside[i], intra_ok)
                        uso_intra = intra_ok and bool(inside[i])
                        if h != 0:
                            r_t = move_pct(d, e, b.open) if gap else (o_pct if h == 1 else -s_pct)
                            exit_price = b.open if gap else (tgt if h == 1 else stp)
                            res, res_w, motivo, path = True, h, "TP" if h == 1 else "SL", uso_intra
                        # Level 1 changed direction: close the other side at this close, after TP / SL.
                        if not res and x["cierraCambioW"] and a1 and cambio_w1 and d == -s1:
                            r_t = move_pct(d, e, b.close)
                            res, res_w, motivo, exit_price = True, 1 if r_t >= 0 else -1, "cambio W", b.close
                        # The opposite signal (past the filters) closes at this close.
                        if not res and x["cierraContra"] and ((d == 1 and sig_dn) or (d == -1 and sig_up)):
                            r_t = move_pct(d, e, b.close)
                            res, res_w, motivo, exit_price = True, 1 if r_t >= 0 else -1, "contra", b.close
                        if not res and (i - t.bar) >= x["maxVelas"]:
                            r_t = move_pct(d, e, b.close)
                            if x["cierraMercado"]:
                                res_w, motivo = (1 if r_t >= 0 else -1), "corte"
                            else:
                                motivo = "flot"
                            res, exit_price = True, b.close
                    if res:
                        days = (tvt - t.tv) / 86400.0
                        del open_trades[k]
                        if res_w == -1:
                            perd_w += 1
                        f_seg(d, res_w, seg, x)
                        dur = {"days": days, "bars": i - t.bar, "tipo": t.tipo, "win": res_w} if res_w else None
                        op = _op(t.tipo, d, t.bar, t.time, t.year, i, year, res_w, r_t / div_n,
                                 r_t if res_w else None, path, dur)
                        trades.append({"entry_i": t.bar, "entry_time": t.time, "entry_price": e, "dir": d,
                                       "type": TYPES[t.tipo], "exit_i": i, "exit_time": b.time,
                                       "exit_price": exit_price, "reason": motivo, "ret_pct": r_t,
                                       "counted": res_w != 0, "bars": i - t.bar, "days": days, "path": path, "ops": [op]})

            # ---------- signal to signal ----------
            if modo_senal and not modo_cesta:
                cerro, ret, motivo, exit_price = False, 0.0, "", b.close
                if sig_dir != 0 and hora_cierre_hit:
                    ret, cerro, motivo = move_pct(sig_dir, sig_entry, b.close), True, "HORA"
                tp, sl = x["tpPctSS"], x["slPctSS"]
                if not cerro and sig_dir != 0 and (tp > 0 or sl > 0):
                    tp_l, sl_l = sig_entry * (1 + tp / 100.0), sig_entry * (1 - sl / 100.0)
                    tp_c, sl_c = sig_entry * (1 - tp / 100.0), sig_entry * (1 + sl / 100.0)
                    gap_sl = sl > 0 and (b.open <= sl_l if sig_dir == 1 else b.open >= sl_c)
                    gap_tp = tp > 0 and (b.open >= tp_l if sig_dir == 1 else b.open <= tp_c)
                    if gap_sl or gap_tp:  # v9.35: opened past it -> closes at the open
                        ret, cerro, motivo, exit_price = move_pct(sig_dir, sig_entry, b.open), True, "SL" if gap_sl else "TP", b.open
                    elif sig_dir == 1:
                        if sl > 0 and b.low <= sl_l:
                            ret, cerro, motivo, exit_price = -sl, True, "SL", sl_l
                        elif tp > 0 and b.high >= tp_l:
                            ret, cerro, motivo, exit_price = tp, True, "TP", tp_l
                    else:
                        if sl > 0 and b.high >= sl_c:
                            ret, cerro, motivo, exit_price = -sl, True, "SL", sl_c
                        elif tp > 0 and b.low <= tp_c:
                            ret, cerro, motivo, exit_price = tp, True, "TP", tp_c
                # Any valid signal of the other side closes, whatever the entry filters say (v9.35).
                opuesta = (sig_up or sig_dn) and sig_dir != 0 and ((1 if sig_up else -1) != sig_dir)
                if opuesta and not cerro:
                    ret, cerro, motivo, exit_price = move_pct(sig_dir, sig_entry, b.close), True, "SIG", b.close
                if cerro:
                    w = 1 if ret >= 0 else -1
                    f_seg(sig_dir, w, seg, x)
                    if w == -1:
                        perd_w += 1
                    days = (tvt - sig_tv) / 86400.0
                    op = _op(sig_tipo, sig_dir, sig_bar, sig_time, sig_year, i, year, w, ret, ret, False,
                             {"days": days, "bars": i - sig_bar, "tipo": sig_tipo, "win": w})
                    trades.append({"entry_i": sig_bar, "entry_time": sig_time, "entry_price": sig_entry, "dir": sig_dir,
                                   "type": TYPES[sig_tipo], "exit_i": i, "exit_time": b.time, "exit_price": exit_price,
                                   "reason": motivo, "ret_pct": ret, "counted": True, "bars": i - sig_bar, "days": days,
                                   "ops": [op]})
                    sig_entry, sig_dir = None, 0
                antes = hc_min is None or min_et < hc_min
                d_ss = 1 if up_v else -1
                if (up_v or dn_v) and sig_dir == 0 and antes and seg["bloq"] != 0 and d_ss == seg["bloq"]:
                    up_f = dn_f = False  # that direction is blocked by the winners/losers in a row
                elif (up_v or dn_v) and sig_dir == 0 and antes:
                    if seg["bloq"] != 0:  # opening the other side lifts the block
                        seg.update(bloq=0, dir=0, res=0, n=0)
                    sig_entry, sig_dir, sig_tipo = b.close, d_ss, tipo_sig
                    sig_year, sig_bar, sig_time, sig_tv = year, i, b.time, tvt
                    entries.append({"i": i, "time": b.time, "price": b.close, "dir": sig_dir, "type": TYPES[tipo_sig]})
                elif up_v or dn_v:
                    up_f = dn_f = False  # already in a position that way, or past the close time

            # ---------- basket ----------
            if modo_senal and modo_cesta:
                cerro, ret, motivo = False, 0.0, ""
                avg = sum(e[0] for e in cst) / len(cst) if cst else None
                px = b.close  # the basket's exit price
                if cst_dir != 0 and hora_cierre_hit:
                    ret, cerro, motivo = move_pct(cst_dir, avg, b.close), True, "HORA"
                tp, sl = x["tpPctSS"], x["slPctSS"]
                if not cerro and cst_dir != 0 and (tp > 0 or sl > 0):
                    tp_l, sl_l = avg * (1 + tp / 100.0), avg * (1 - sl / 100.0)
                    tp_c, sl_c = avg * (1 - tp / 100.0), avg * (1 + sl / 100.0)
                    gap_sl = sl > 0 and (b.open <= sl_l if cst_dir == 1 else b.open >= sl_c)
                    gap_tp = tp > 0 and (b.open >= tp_l if cst_dir == 1 else b.open <= tp_c)
                    if gap_sl or gap_tp:
                        ret, px, cerro, motivo = move_pct(cst_dir, avg, b.open), b.open, True, "SL" if gap_sl else "TP"
                    elif cst_dir == 1:
                        if sl > 0 and b.low <= sl_l:
                            ret, px, cerro, motivo = -sl, sl_l, True, "SL"
                        elif tp > 0 and b.high >= tp_l:
                            ret, px, cerro, motivo = tp, tp_l, True, "TP"
                    else:
                        if sl > 0 and b.high >= sl_c:
                            ret, px, cerro, motivo = -sl, sl_c, True, "SL"
                        elif tp > 0 and b.low <= tp_c:
                            ret, px, cerro, motivo = tp, tp_c, True, "TP"
                opuesta = (sig_up or sig_dn) and cst_dir != 0 and ((1 if sig_up else -1) != cst_dir)
                if opuesta and not cerro:
                    ret, cerro, motivo = move_pct(cst_dir, avg, b.close), True, "SIG"
                if cerro:
                    f_seg(cst_dir, 1 if ret >= 0 else -1, seg, x)
                    days = (tvt - cst_tv) / 86400.0
                    ops = []
                    n_e = len(cst)
                    for q, (eq_, tq, yq, iq, timeq) in enumerate(cst):
                        own = move_pct(cst_dir, eq_, px)
                        rq = ret / (x["maxCesta"] if x["maxCesta"] > 0 else n_e) if x["cestaCuenta"] == "Repartido" else own
                        w = 1 if rq >= 0 else -1
                        if w == -1:
                            perd_w += 1
                        dur = ({"days": days, "bars": i - cst_bar, "tipo": cst_tipo, "win": 1 if ret >= 0 else -1}
                               if q == 0 else None)
                        ops.append(_op(tq, cst_dir, iq, timeq, yq, i, year, w, rq, own, False, dur))
                    trades.append({"entry_i": cst_bar, "entry_time": cst_time, "entry_price": avg, "dir": cst_dir,
                                   "type": TYPES[cst_tipo], "exit_i": i, "exit_time": b.time, "exit_price": px,
                                   "reason": motivo, "ret_pct": ret, "counted": True, "bars": i - cst_bar, "days": days,
                                   "entries": n_e, "entry_times": [e[4] for e in cst], "ops": ops})
                    cst_dir, cst = 0, []
                antes = hc_min is None or min_et < hc_min
                if (up_v or dn_v) and antes:
                    dc = 1 if up_v else -1
                    if cst_dir == 0 and seg["bloq"] != 0 and dc == seg["bloq"]:
                        up_f = dn_f = False
                    elif cst_dir == 0:
                        if seg["bloq"] != 0:
                            seg.update(bloq=0, dir=0, res=0, n=0)
                        cst_dir, cst_tipo, cst_bar, cst_time, cst_tv = dc, tipo_sig, i, b.time, tvt
                        cst = [(b.close, tipo_sig, year, i, b.time)]
                        entries.append({"i": i, "time": b.time, "price": b.close, "dir": dc, "type": TYPES[tipo_sig]})
                    elif cst_dir == dc and (x["maxCesta"] == 0 or len(cst) < x["maxCesta"]):
                        cst.append((b.close, tipo_sig, year, i, b.time))
                        entries.append({"i": i, "time": b.time, "price": b.close, "dir": dc, "type": TYPES[tipo_sig]})
                    elif cst_dir == dc:
                        up_f = dn_f = False  # the basket is full
                elif up_v or dn_v:
                    up_f = dn_f = False  # past the close time

            # One at a time / most open: counted AFTER this candle's closes (v9.35).
            if not modo_senal and (up_f or dn_f):
                ab_n = len(open_trades)
                if (x["unaVez"] and ab_n > 0) or (x["maxAbiertas"] > 0 and ab_n >= x["maxAbiertas"]):
                    up_f = dn_f = False
            # Winners / losers in a row in normal mode (this candle's closes already count).
            if not modo_senal and seg["bloq"] != 0:
                if up_f:
                    if seg["bloq"] == 1:
                        up_f = False
                    else:
                        seg.update(bloq=0, dir=0, res=0, n=0)
                if dn_f:
                    if seg["bloq"] == -1:
                        dn_f = False
                    elif seg["bloq"] == 1:
                        seg.update(bloq=0, dir=0, res=0, n=0)
            # Most per day counts only what really opens (v9.35).
            if up_f or dn_f:
                trades_sesion += 1
            if up_f and not modo_senal:
                open_trades.append(OpenTrade(b.close, 1, i, tipo_sig, year, b.time, tvt))
                entries.append({"i": i, "time": b.time, "price": b.close, "dir": 1, "type": TYPES[tipo_sig]})
            if dn_f and not modo_senal:
                open_trades.append(OpenTrade(b.close, -1, i, tipo_sig, year, b.time, tvt))
                entries.append({"i": i, "time": b.time, "price": b.close, "dir": -1, "type": TYPES[tipo_sig]})
            # The arrows: only signals that really open (v9.26).
            for d, f in ((1, up_f), (-1, dn_f)):
                if f:
                    signals.append({"i": i, "time": b.time, "dir": d, "type": TYPES[tipo_sig], "price": b.close,
                                    "body_pct": c.relleno, "wick_pct": c.m_max})

            in_range = luck_from is None or b.time >= luck_from  # Backtest: only its date range
            if luck:
                # ---------- luck test shadow books ----------
                book_step(sb, i, b, inside[i], intra_ok, x["objPct"], x["stopPct"], x["maxVelas"], x["cierraMercado"],
                          st_sbL, st_sbS, sb_map)
                book_step(ab, i, b, inside[i], intra_ok, x["objPct"], x["stopPct"], x["maxVelas"], x["cierraMercado"],
                          st_abL, st_abS, None)
                for k in range(len(pending) - 1, -1, -1):
                    pb, pdir = pending[k]
                    if pb * 2 in sb_map and pb * 2 + 1 in sb_map:
                        r_l, r_s = sb_map.pop(pb * 2), sb_map.pop(pb * 2 + 1)
                        r_sig = r_l if pdir == 1 else r_s
                        r_op = r_s if pdir == 1 else r_l
                        if r_sig is not None:
                            az_sig[0] += 1
                            az_sig[1] += 1 if r_sig >= 0 else 0
                            az_sig[2] += r_sig
                            az_sig[3] += r_sig * r_sig
                            az_sig[4 if pdir == 1 else 5] += 1
                        if r_sig is not None and r_op is not None:
                            ed = (r_sig - r_op) / 2.0
                            az_edge[0] += 1
                            az_edge[1] += ed
                            az_edge[2] += ed * ed
                        del pending[k]
                if (up_f or dn_f) and in_range:
                    sb.append(Shadow(b.close, 1, i))
                    sb.append(Shadow(b.close, -1, i))
                    pending.append((i, 1 if up_f else -1))
                if ok_apertura and in_range:
                    ab.append(Shadow(b.close, 1, i))
                    ab.append(Shadow(b.close, -1, i))
                # ---------- credit spreads ----------
                cs.step(b, tvt, year, (1 if up_f else -1) if (up_f or dn_f) and in_range else 0, ok_apertura and in_range)

            prev_s1 = s1
            now = {"perd_w": perd_w, "bloq_perd_w": bloq_perd_w, "en_apertura": en_apertura,
                   "trades_sesion": trades_sesion, "perm_up": perm_up, "perm_dn": perm_dn}

        # The open position for the target/stop lines.
        o, st = x["objPct"] / 100.0, x["stopPct"] / 100.0
        position = None
        if not modo_senal and open_trades:
            t = open_trades[-1]
            position = {"entry": t.entry, "dir": t.d, "entry_time": t.time, "type": TYPES[t.tipo],
                        "target": t.entry * (1 + o) if t.d == 1 else t.entry * (1 - o),
                        "stop": t.entry * (1 - st) if t.d == 1 else t.entry * (1 + st)}
        elif modo_senal and not modo_cesta and sig_dir != 0:
            position = _ss_position(sig_entry, sig_dir, sig_time, sig_tipo, x)
        elif modo_senal and modo_cesta and cst_dir != 0:
            position = _ss_position(sum(e[0] for e in cst) / len(cst), cst_dir, cst_time, cst_tipo, x) | {
                "entries": len(cst), "entry_times": [e[4] for e in cst]}

        n_up_open = sum(1 for t in open_trades if t.d == 1)
        out = {
            "signals": signals,
            "preview": preview,
            "blocked": blocked,
            "trades": trades,
            "entries": entries,
            "open_trades": [{"entry": t.entry, "dir": t.d, "entry_time": t.time, "type": TYPES[t.tipo],
                             "target": t.entry * (1 + o) if t.d == 1 else t.entry * (1 - o),
                             "stop": t.entry * (1 - st) if t.d == 1 else t.entry * (1 + st)}
                            for t in open_trades],
            "position": position,
            # "Velas medidas" counts the candle in progress too (bar_index + 1).
            "results": tables(trades, entries, bars, closed, x) | {"candles_measured": n},
            "ladder": [{"tf": lv["tf"], "active": lv["active"], "state": lv["state"]} for lv in levels],
            "now": _now(x, now, levels, seg["bloq"], len(open_trades), len(cst)),
            "open_now": {"longs": n_up_open, "shorts": len(open_trades) - n_up_open},
            "intrabar": {"on": intra_ok, "tf": x["tfIntra"],
                         "covered_from": next((bars[i].time for i in range(closed) if inside[i]), None)},
            "ma": [{"time": bars[i].time, "value": ma[i]} for i in range(n) if ma[i] is not None] if ma_len else [],
        }
        if luck:
            out["luck"] = _luck(x, az_sig, az_edge, st_sbL, st_sbS, st_abL, st_abS)
            out["credit_spreads"] = cs.table()
        return out


def _ss_position(entry: float, d: int, t: int, tipo: int, x: dict) -> dict:
    tp, sl = x["tpPctSS"], x["slPctSS"]
    return {"entry": entry, "dir": d, "entry_time": t, "type": TYPES[tipo],
            "target": (entry * (1 + tp / 100.0) if d == 1 else entry * (1 - tp / 100.0)) if tp > 0 else None,
            "stop": (entry * (1 - sl / 100.0) if d == 1 else entry * (1 + sl / 100.0)) if sl > 0 else None}


def _now(x: dict, st: dict, levels: list, bloq_dir: int, n_open: int, n_cst: int) -> list[str]:
    """The 'Ahora' row: what is stopping new signals right now (v9.24)."""
    if not st:
        return []
    out = []
    normal = not x["modoSenal"]
    if normal and x["unaVez"] and n_open > 0:
        out.append(f"one at a time ({n_open} open)")
    if normal and x["maxAbiertas"] > 0 and n_open >= x["maxAbiertas"]:
        out.append(f"most open ({n_open}/{x['maxAbiertas']})")
    if st["bloq_perd_w"]:
        out.append(f"weekly losses ({st['perd_w']}/{x['maxPerdW']})")
    if bloq_dir == 1:
        out.append("in a row: no BUY")
    elif bloq_dir == -1:
        out.append("in a row: no SELL")
    if x["modoSenal"] and x["modoCesta"] and x["maxCesta"] > 0 and n_cst >= x["maxCesta"]:
        out.append(f"basket full ({n_cst})")
    if x["usarEscalera"]:
        if st["perm_up"] and not st["perm_dn"]:
            out.append("BUYS only (ladder)")
        elif st["perm_dn"] and not st["perm_up"]:
            out.append("SELLS only (ladder)")
        elif not st["perm_up"] and not st["perm_dn"]:
            out.append("ladder MIXED: nothing")
    if x["soloApertura"] and not st["en_apertura"]:
        out.append("outside the opening window")
    if x["maxTradesSesion"] > 0 and st["trades_sesion"] >= x["maxTradesSesion"]:
        out.append("most for the day")
    return out


def tables(trades: list[dict], entries: list[dict], bars: list[Bar], closed: int, x: dict, first: int = 0) -> dict:
    """The script's results tables, from the operations of the trades opened at or after candle
    `first`, in the order the script closed them. The engine (first = 0) and Backtest's date
    ranges both use this, so a range is the same measurement on fewer trades."""
    mode = "basket" if x["modoSenal"] and x["modoCesta"] else ("signal" if x["modoSenal"] else "target_stop")
    k4 = range(4)
    nW, nL, nF = [0] * 4, [0] * 4, [0] * 4
    sF, rS = [0.0] * 4, [0.0] * 4
    gS, gN, gW = [0.0] * 4, [0] * 4, [0] * 4
    dWs, dWn, dLs, dLn = [0.0] * 4, [0] * 4, [0.0] * 4, [0] * 4
    yW: dict[int, int] = {}
    yL: dict[int, int] = {}
    yPL: dict[int, float] = {}
    dirW, dirL, dirR = [0, 0], [0, 0], [0.0, 0.0]
    racha_w = racha_l = max_rw = max_rl = n_camino = 0
    dur_n, dur_bars, dur_days = 0, 0.0, 0.0
    eq, mx, dd, cap_n = x["capIni"], x["capIni"], 0.0, 0
    golpe_l = golpe_w = max_gl = max_gw = 0
    racha_pl = peor = 0.0
    bar_exit, bar_year_, eq_before = None, None, eq

    def close_bar() -> None:
        nonlocal golpe_l, golpe_w, max_gl, max_gw, racha_pl, peor
        if bar_exit is None:
            return
        pl = eq - eq_before
        yPL[bar_year_] = yPL.get(bar_year_, 0.0) + pl
        if pl < 0:
            golpe_l, golpe_w = golpe_l + 1, 0
            racha_pl += pl
            max_gl = max(max_gl, golpe_l)
            peor = min(peor, racha_pl)
        else:
            golpe_w, golpe_l = golpe_w + 1, 0
            racha_pl = 0.0
            max_gw = max(max_gw, golpe_w)

    for t in trades:
        if t["entry_i"] < first:
            continue
        for op in t["ops"]:
            tp = op["tipo"]
            if op["res"] == 0:
                nF[tp] += 1
                sF[tp] += op["r_book"]
                continue
            # A "golpe": everything that closes on one candle counts once (v9.31).
            if op["exit_i"] != bar_exit:
                close_bar()
                bar_exit, bar_year_, eq_before = op["exit_i"], op["exit_year"], eq
            eq, mx, dd = f_cap(eq, mx, dd, op["cap"], x)
            cap_n += 1
            rS[tp] += op["r_book"]
            side = 0 if op["dir"] == 1 else 1
            dirR[side] += op["r_book"]
            if op["path"]:
                n_camino += 1
            if op["res"] == 1:
                nW[tp] += 1
                dirW[side] += 1
                racha_w, racha_l = racha_w + 1, 0
                max_rw = max(max_rw, racha_w)
                yW[op["entry_year"]] = yW.get(op["entry_year"], 0) + 1
            else:
                nL[tp] += 1
                dirL[side] += 1
                racha_l, racha_w = racha_l + 1, 0
                max_rl = max(max_rl, racha_l)
                yL[op["entry_year"]] = yL.get(op["entry_year"], 0) + 1
            if op["dur"]:
                du = op["dur"]
                dur_n += 1
                dur_bars += du["bars"]
                dur_days += du["days"]
                if du["win"] == 1:
                    dWs[du["tipo"]] += du["days"]
                    dWn[du["tipo"]] += 1
                else:
                    dLs[du["tipo"]] += du["days"]
                    dLn[du["tipo"]] += 1
    close_bar()

    # The gap: the next candle's open against the entry, for each target/stop entry.
    if mode == "target_stop":
        o = x["objPct"] / 100.0
        for e in entries:
            i = e["i"]
            if i < first or i + 1 >= closed:
                continue
            tp = TYPES.index(e["type"])
            nxt = bars[i + 1].open
            gS[tp] += (nxt - e["price"]) / e["price"] * 100.0 * e["dir"]
            gN[tp] += 1
            tgt = e["price"] * (1 + o) if e["dir"] == 1 else e["price"] * (1 - o)
            if (e["dir"] == 1 and nxt >= tgt) or (e["dir"] == -1 and nxt <= tgt):
                gW[tp] += 1

    rows = []
    for k in k4:
        tot = nW[k] + nL[k]
        rows.append({
            "type": TYPES[k], "wins": nW[k], "losses": nL[k], "win_rate": 100.0 * nW[k] / tot if tot else None,
            "floating": nF[k], "floating_avg": sF[k] / nF[k] if nF[k] else None,
            "gap_avg": gS[k] / gN[k] if gN[k] else None, "gap_wins": gW[k], "gap_n": gN[k],
            "r_avg": rS[k] / tot if tot else None,
            "days_win": dWs[k] / dWn[k] if dWn[k] else None,
            "days_loss": dLs[k] / dLn[k] if dLn[k] else None,
        })
    tw, tl = sum(nW), sum(nL)
    tot = tw + tl
    years = sorted(y for y in set(yW) | set(yL) | set(yPL) if yW.get(y, 0) + yL.get(y, 0) > 0 or yPL.get(y, 0) != 0)
    cap_ini = x["capIni"]
    return {
        "by_type": rows,
        "total": {"wins": tw, "losses": tl, "win_rate": 100.0 * tw / tot if tot else None, "floating": sum(nF),
                  "r_avg": sum(rS) / tot if tot else None,
                  "days_win": sum(dWs) / sum(dWn) if sum(dWn) else None,
                  "days_loss": sum(dLs) / sum(dLn) if sum(dLn) else None},
        "years": [{"year": y, "wins": yW.get(y, 0), "losses": yL.get(y, 0), "pnl": yPL.get(y, 0.0),
                   "pnl_pct": yPL.get(y, 0.0) / cap_ini * 100.0} for y in years],
        "by_side": [{"side": s, "wins": dirW[j], "losses": dirL[j],
                     "win_rate": 100.0 * dirW[j] / (dirW[j] + dirL[j]) if dirW[j] + dirL[j] else None,
                     "r_avg": dirR[j] / (dirW[j] + dirL[j]) if dirW[j] + dirL[j] else None}
                    for j, s in enumerate(("long", "short"))],
        "capital": {"start": cap_ini, "pct_per_trade": x["capPct"], "compound": x["capComp"], "final": eq,
                    "gain": eq - cap_ini, "return_pct": (eq / cap_ini - 1) * 100.0, "trades": cap_n,
                    "max_drop_pct": dd},
        "hits": {"max_won_in_a_row": max_gw, "max_lost_in_a_row": max_gl, "worst_run": -peor,
                 "worst_run_pct": -peor / cap_ini * 100.0},
        "candles_measured": max(0, closed - first),
        "max_win_streak": max_rw,
        "max_loss_streak": max_rl,
        "real_path_trades": n_camino,
        "avg_days": dur_days / dur_n if dur_n else None,
        "avg_bars": dur_bars / dur_n if dur_n else None,
        "mode": mode,
    }


def _luck(x: dict, az_sig: list, az_edge: list, sbL: Stats4, sbS: Stats4, abL: Stats4, abS: Stats4) -> dict:
    """The luck test table and verdicts (PRUEBA DE AZAR)."""
    def mean(st: Stats4):
        return st.v[2] / st.v[0] if st.v[0] else None

    def wins(st: Stats4):
        return 100.0 * st.v[1] / st.v[0] if st.v[0] else None

    nS = az_sig[0]
    mS = az_sig[2] / nS if nS else None
    wS = 100.0 * az_sig[1] / nS if nS else None
    sdS = sd(nS, az_sig[2], az_sig[3])
    nLg, nCt = az_sig[4], az_sig[5]
    pL = nLg / (nLg + nCt) if (nLg + nCt) > 0 else 0.5
    nBM = sbL.v[0] + sbS.v[0]
    mBM = (sbL.v[2] + sbS.v[2]) / nBM if nBM else None
    wBM = 100.0 * (sbL.v[1] + sbS.v[1]) / nBM if nBM else None
    nAM = abL.v[0] + abS.v[0]
    mAM = (abL.v[2] + abS.v[2]) / nAM if nAM else None
    wAM = 100.0 * (abL.v[1] + abS.v[1]) / nAM if nAM else None
    mAL, mAS, wAL, wAS = mean(abL), mean(abS), wins(abL), wins(abS)
    mMix = None if mAL is None or mAS is None else pL * mAL + (1 - pL) * mAS
    wMix = None if wAL is None or wAS is None else pL * wAL + (1 - pL) * wAS
    seS = sdS / math.sqrt(nS) if sdS else None
    tGan = mS / seS if seS else None
    nE = az_edge[0]
    mE = az_edge[1] / nE if nE else None
    sdE = sd(nE, az_edge[1], az_edge[2])
    tDir = mE / (sdE / math.sqrt(nE)) if sdE else None
    tMom = (mS - mMix) / seS if seS and mMix is not None else None

    def row(label, w, m, count):
        return {"label": label, "win_rate": w, "r_avg": m, "n": int(count)}

    def verdict(label, t, count):
        if t is None:
            return {"label": label, "t": None, "luck_pct": None, "verdict": "-"}
        pv = 100.0 * (1.0 - ncdf(t))
        if count < 30:
            v = f"few ({int(count)})"
        else:
            v = "yes" if t >= 2 else ("weak" if t >= 1 else "no")
        return {"label": label, "t": t, "luck_pct": pv, "verdict": v}

    return {
        "rule": {"objPct": x["objPct"], "stopPct": x["stopPct"], "maxVelas": x["maxVelas"],
                 # The real trades follow other exits too; the luck test always uses the fixed rule.
                 "fixed": x["modoSenal"] or x["cierraContra"] or x["cierraCambioW"]},
        "rows": [
            row("SIGNAL (its own direction)", wS, mS, nS),
            row("Longs only · same candles", wins(sbL), mean(sbL), sbL.v[0]),
            row("Shorts only · same candles", wins(sbS), mean(sbS), sbS.v[0]),
            row("Coin flip · same candles", wBM, mBM, nBM),
            row("Long · any candle", wAL, mAL, abL.v[0]),
            row("Short · any candle", wAS, mAS, abS.v[0]),
            row("Coin flip · any candle", wAM, mAM, nAM),
            row(f"Mix {pL * 100:.0f}% longs · random moment", wMix, mMix, nAM),
        ],
        "verdicts": [
            verdict("Does it make money? (R > 0)", tGan, nS),
            verdict("Does it get the DIRECTION right? (vs coin flip)", tDir, nE),
            verdict("Does it get the MOMENT right? (vs random mix)", tMom, nS),
        ],
    }


STRATEGIES: dict[str, Strategy] = {SwingStrategy.id: SwingStrategy()}

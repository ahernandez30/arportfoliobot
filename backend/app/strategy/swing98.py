"""'Swing — Vela Diaria/Semanal v9.8', translated from docs/swing_diario_semanal_v9_8.pine.

The translation follows the script statement by statement; comments give the script's own
names and line numbers so the two can be read side by side. Where the plan's summary and the
script differ, the script wins (plan section 7).

Conventions shared with TradingView:
- Each candle is evaluated at its close. A candle still in progress is previewed (its signal
  shown) but never opens or closes a trade (plan section 7.5).
- Daily and weekly candles count as starting at 9:30 New York time (their session open), which
  is what the script's session checks (first candle of the session, opening window, forced close
  time) see on those charts.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.marketdata.base import Bar
from app.strategy import indicators
from app.strategy.base import TIMEFRAME_SECONDS, InputDef, Strategy, StrategyData

NY = ZoneInfo("America/New_York")
TYPES = ("LLENA", "FLECO", "ENGULFING")
INTRADAY = ("1m", "5m", "15m", "1h")

G_SIGNAL = "Signal (Señal)"
G_FILTERS = "Filters (Filtros)"
G_TRADES = "Trade rules (Backtest)"
G_LADDER = "Higher-timeframe ladder (Escalera)"
G_CONT = "Continuation (Continuación)"
G_OPEN = "Opening window (Apertura)"

INPUTS: list[InputDef] = [
    # Señal (script lines 31-38)
    InputDef("cuerpoLlena", "LLENA: body ≥ % of range", "float", 85.0, G_SIGNAL, 1, 100),
    InputDef("mechaFleco", "FLECO: wick ≥ % of range", "float", 65.0, G_SIGNAL, 1, 100),
    InputDef("cuerpoMinFleco", "FLECO: minimum body %", "float", 4.0, G_SIGNAL, 0, 90,
             help="0 = use all, even tiny-bodied dojis."),
    InputDef("cuerpoEngulf", "ENGULFING: minimum body %", "float", 61.0, G_SIGNAL, 0, 100,
             help="0 = size does not matter."),
    InputDef("verLlena", "Use LLENA signals", "bool", True, G_SIGNAL),
    InputDef("verFleco", "Use FLECO signals", "bool", True, G_SIGNAL),
    InputDef("verEngulf", "Use ENGULFING signals", "bool", True, G_SIGNAL),
    InputDef("noEngPrimera", "No ENGULFING on the first candle of the session", "bool", False, G_SIGNAL,
             help="Avoids the overnight gap."),
    # Filtros (lines 49-58)
    InputDef("maCual", "Moving average filter", "choice", "Ninguna", G_FILTERS,
             options=("Ninguna", "MA1", "MA2", "MA3"), help="Ninguna = none. Buys only above it, sells only below."),
    InputDef("maLen1", "MA1 length", "int", 50, G_FILTERS, 1, 1000),
    InputDef("maLen2", "MA2 length", "int", 100, G_FILTERS, 1, 1000),
    InputDef("maLen3", "MA3 length", "int", 200, G_FILTERS, 1, 1000),
    InputDef("usarADX", "ADX filter", "bool", False, G_FILTERS),
    InputDef("adxCond", "ADX: trade when it is", "choice", "mayor", G_FILTERS, options=("mayor", "menor"),
             help="mayor = above the level (trend), menor = below (range)."),
    InputDef("adxUmbral", "ADX level", "float", 25.0, G_FILTERS, 0, 100),
    InputDef("adxLen", "ADX period", "int", 14, G_FILTERS, 1, 500),
    # Backtest (lines 60-74)
    InputDef("objPct", "Target % (objetivo)", "float", 15.0, G_TRADES, 0.1, 1000),
    InputDef("stopPct", "Stop %", "float", 13.0, G_TRADES, 0.1, 100),
    InputDef("maxVelas", "Candles until closing at market / floating", "int", 13, G_TRADES, 1, 1000),
    InputDef("unaVez", "One at a time (no new position until the last closes)", "bool", False, G_TRADES),
    InputDef("maxTradesSesion", "Most trades per day (0 = no limit)", "int", 0, G_TRADES, 0, 1000),
    InputDef("cierraMercado", "Close at market after N candles (else leave floating)", "bool", True, G_TRADES),
    InputDef("usarIntra", "Real path: look inside the candle for the true order", "bool", True, G_TRADES),
    InputDef("tfIntra", "Real path timeframe", "timeframe", "1h", G_TRADES,
             help="E.g. 1D on weekly, 1h on daily. Must not be above the chart's timeframe."),
    InputDef("modoSenal", "SIGNAL TO SIGNAL: hold until the opposite signal", "bool", False, G_TRADES),
    InputDef("tpPctSS", "Signal to signal: take profit % (0 = none)", "float", 0.0, G_TRADES, 0, 1000),
    InputDef("slPctSS", "Signal to signal: stop loss % (0 = none)", "float", 0.0, G_TRADES, 0, 100),
    InputDef("modoCesta", "BASKET: add same-side signals, close the basket together", "bool", False, G_TRADES,
             help="Only with signal to signal on. Target and stop apply to the average entry."),
    InputDef("maxCesta", "Basket: most entries (0 = no limit)", "int", 5, G_TRADES, 0, 1000),
    InputDef("horaCierreSS", "Signal to signal: forced close time (HH:MM New York)", "time", "", G_TRADES,
             help="Empty = none. No new entries after it."),
    # Escalera (lines 83-93)
    InputDef("usarEscalera", "Only signals agreeing with the higher timeframes", "bool", False, G_LADDER),
    InputDef("filtro1", "Level 1 on", "bool", True, G_LADDER),
    InputDef("tfS1", "Level 1 timeframe", "timeframe", "1W", G_LADDER),
    InputDef("filtro2", "Level 2 on", "bool", False, G_LADDER),
    InputDef("tfS2", "Level 2 timeframe", "timeframe", "1D", G_LADDER),
    InputDef("filtro3", "Level 3 on", "bool", False, G_LADDER),
    InputDef("tfS3", "Level 3 timeframe", "timeframe", "1h", G_LADDER),
    InputDef("cuerpoLlenaW", "Ladder LLENA: body ≥ %", "float", 85.0, G_LADDER, 1, 100),
    InputDef("mechaFlecoW", "Ladder FLECO: wick ≥ %", "float", 65.0, G_LADDER, 1, 100),
    InputDef("cuerpoMinFlecoW", "Ladder FLECO: minimum body %", "float", 4.0, G_LADDER, 0, 90),
    InputDef("cuerpoEngulfW", "Ladder ENGULFING: minimum body %", "float", 61.0, G_LADDER, 0, 100),
    # Continuación (line 102)
    InputDef("usarContinua", "Only signals in the same direction as the previous one", "bool", False, G_CONT),
    # Apertura (lines 115-119)
    InputDef("soloApertura", "Only signals inside the opening window", "bool", False, G_OPEN),
    InputDef("sesApertura", "Opening window (New York)", "choice", "0930-1100", G_OPEN,
             options=("0930-1000", "0930-1030", "0930-1100", "0930-1130", "0930-1200", "0930-1600")),
]


# ---------- candle measurements and types (lines 122-176, and f_sig 181-196) ----------


@dataclass(frozen=True)
class Candle:
    up: bool
    dn: bool
    tipo: int  # 0 LLENA, 1 FLECO, 2 ENGULFING
    relleno: float
    m_max: float


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
    tipo = 2 if (d_en_up or d_en_dn) else (1 if (d_fl_up or d_fl_dn) else 0)
    return Candle(up, dn, tipo, relleno, m_max)


# ---------- time helpers ----------


def session_moment(b: Bar, tf: str) -> datetime:
    """When the candle starts in New York time, as the script's session checks see it."""
    if tf in INTRADAY:
        return datetime.fromtimestamp(b.time, NY)
    d = datetime.fromtimestamp(b.time, timezone.utc).date()
    return datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY)


def bar_year(b: Bar, tf: str) -> int:
    if tf in INTRADAY:
        return datetime.fromtimestamp(b.time, NY).year
    return datetime.fromtimestamp(b.time, timezone.utc).year


def in_session(minute: int, session: str) -> bool:
    start = int(session[:2]) * 60 + int(session[2:4])
    end = int(session[5:7]) * 60 + int(session[7:9])
    return start <= minute < end


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


def higher_signals(chart: list[Bar], htf: list[Bar], th: tuple[float, float, float, float]) -> list[int]:
    """For each chart candle: the signal (1, -1 or 0) of the last CLOSED higher-timeframe candle,
    i.e. request.security(tf, sig[1], lookahead_on) (lines 204-209)."""
    sig = [0] * len(htf)
    for k in range(len(htf)):
        c = classify(htf[k], htf[k - 1] if k else None, *th)
        sig[k] = 1 if c.up else (-1 if c.dn else 0)
    out: list[int] = []
    k = -1
    for b in chart:
        while k + 1 < len(htf) and htf[k + 1].time <= b.time:
            k += 1
        out.append(sig[k - 1] if k >= 1 else 0)
    return out


# ---------- the fixed target/stop rule shared by the backtest and the luck test ----------


def hit_target_or_stop(d: int, tgt: float, stp: float, b: Bar, inside: list[Bar], use_inside: bool) -> tuple[int, bool]:
    """1 target, -1 stop, 0 neither; and whether the candle's inside path decided it (lines 396-412)."""
    if use_inside and inside:
        for s in inside:
            if d == 1:
                h = -1 if s.low <= stp else (1 if s.high >= tgt else 0)
            else:
                h = -1 if s.high >= stp else (1 if s.low <= tgt else 0)
            if h != 0:
                return h, True
        return 0, True
    if d == 1:
        h = 1 if b.open >= tgt else (-1 if b.open <= stp else (-1 if b.low <= stp else (1 if b.high >= tgt else 0)))
    else:
        h = 1 if b.open <= tgt else (-1 if b.open >= stp else (-1 if b.high >= stp else (1 if b.low <= tgt else 0)))
    return h, False


@dataclass
class OpenTrade:
    entry: float
    d: int
    bar: int
    tipo: int
    year: int
    time: int


@dataclass
class Book:
    """Results kept per candle type (index 0 LLENA, 1 FLECO, 2 ENGULFING), like the script's arrays."""

    nW: list = field(default_factory=lambda: [0, 0, 0])
    nL: list = field(default_factory=lambda: [0, 0, 0])
    nF: list = field(default_factory=lambda: [0, 0, 0])
    sF: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rS: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    gS: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    gN: list = field(default_factory=lambda: [0, 0, 0])
    gW: list = field(default_factory=lambda: [0, 0, 0])
    dWsum: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    dWn: list = field(default_factory=lambda: [0, 0, 0])
    dLsum: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    dLn: list = field(default_factory=lambda: [0, 0, 0])
    yW: dict = field(default_factory=dict)
    yL: dict = field(default_factory=dict)
    rachaW: int = 0
    rachaL: int = 0
    maxRW: int = 0
    maxRL: int = 0
    nCamino: int = 0
    durN: int = 0
    durBars: float = 0.0
    durDias: float = 0.0

    def win(self, tipo: int, year: int, days: float) -> None:
        self.nW[tipo] += 1
        self.rachaW += 1
        self.rachaL = 0
        self.maxRW = max(self.maxRW, self.rachaW)
        self.dWsum[tipo] += days
        self.dWn[tipo] += 1
        self.yW[year] = self.yW.get(year, 0) + 1

    def loss(self, tipo: int, year: int, days: float) -> None:
        self.nL[tipo] += 1
        self.rachaL += 1
        self.rachaW = 0
        self.maxRL = max(self.maxRL, self.rachaL)
        self.dLsum[tipo] += days
        self.dLn[tipo] += 1
        self.yL[year] = self.yL.get(year, 0) + 1

    def duration(self, bars: int, days: float) -> None:
        self.durN += 1
        self.durBars += bars
        self.durDias += days


# ---------- luck test helpers (lines 695-753, 824-829) ----------


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
    zo, zs = obj / 100.0, stop / 100.0
    for zi in range(len(book) - 1, -1, -1):
        z = book[zi]
        tgt = z.e * (1 + zo) if z.d == 1 else z.e * (1 - zo)
        stp = z.e * (1 - zs) if z.d == 1 else z.e * (1 + zs)
        res, r, counts = False, 0.0, True
        if i > z.b:
            h, _ = hit_target_or_stop(z.d, tgt, stp, b, inside, use_inside)
            if h == 1:
                r, res = obj, True
            elif h == -1:
                r, res = -stop, True
            if not res and (i - z.b) >= max_velas:
                r = (b.close - z.e) / z.e * 100.0 if z.d == 1 else (z.e - b.close) / z.e * 100.0
                res, counts = True, cierra
        if res:
            if counts:
                (st_l if z.d == 1 else st_s).add(r)
            if results is not None:
                results[z.b * 2 + (0 if z.d == 1 else 1)] = r if counts else None
            del book[zi]


# ---------- the strategy ----------


class SwingV98(Strategy):
    id = "swing_v98"
    name = "Swing, Vela Diaria/Semanal"
    version = "v9.8"
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

    def run(self, data: StrategyData, inputs: dict, *, luck: bool = False) -> dict:  # noqa: C901 (mirrors the script)
        x = inputs
        bars, tf = data.bars, data.timeframe
        n = len(bars)
        closed = min(data.closed, n)
        chart_sec = TIMEFRAME_SECONDS[tf]

        # Real path inside each candle (lines 324-329).
        intra_ok = x["usarIntra"] and TIMEFRAME_SECONDS[x["tfIntra"]] <= chart_sec
        if intra_ok and x["tfIntra"] == tf:
            inside = [[b] for b in bars]
        elif intra_ok:
            inside = group_inside(bars, data.other.get(x["tfIntra"], []))
        else:
            inside = [[] for _ in bars]

        # Ladder (lines 204-238): a level counts only if its timeframe is strictly higher.
        ladder_th = (x["cuerpoLlenaW"], x["mechaFlecoW"], x["cuerpoMinFlecoW"], x["cuerpoEngulfW"])
        levels = []
        for k in (1, 2, 3):
            ltf = x[f"tfS{k}"]
            active = x["usarEscalera"] and x[f"filtro{k}"] and TIMEFRAME_SECONDS[ltf] > chart_sec
            sigs = higher_signals(bars, data.other.get(ltf, []), ladder_th) if active else [0] * n
            levels.append({"tf": ltf, "active": active, "sig": sigs, "state": 0})

        # Moving averages and ADX (lines 252-260).
        closes = [b.close for b in bars]
        ma_len = {"MA1": x["maLen1"], "MA2": x["maLen2"], "MA3": x["maLen3"]}.get(x["maCual"])
        ma = indicators.sma(closes, ma_len) if ma_len else [None] * n
        adx = indicators.adx(bars, x["adxLen"], x["adxLen"]) if x["usarADX"] else [None] * n

        # Forced close time (lines 145-149).
        hc_min = None
        if x["horaCierreSS"]:
            hc_min = int(x["horaCierreSS"][:2]) * 60 + int(x["horaCierreSS"][3:])

        o, st = x["objPct"] / 100.0, x["stopPct"] / 100.0
        modo_senal, modo_cesta = x["modoSenal"], x["modoCesta"]

        signals: list[dict] = []
        blocked: list[dict] = []
        preview: dict | None = None
        trades: list[dict] = []
        # Every position the rules opened (on closed candles), for the worker's automatic trades.
        entries: list[dict] = []
        book = Book()
        open_trades: list[OpenTrade] = []
        dia_marcado = None
        hc_dia = None
        ult_dir = 0
        hay_pos = False
        trades_sesion = 0
        prev_day = None
        # Signal to signal (lines 355-358, 364-365)
        sig_entry, sig_dir, sig_tipo, sig_year, sig_bar, sig_time = None, 0, 0, 0, 0, 0
        # Basket (lines 359-367)
        cst_dir, cst_sum, cst_n, cst_tipo, cst_year, cst_bar, cst_time = 0, 0.0, 0, 0, 0, 0, 0
        cst_times: list[int] = []
        # Luck test (lines 756-771)
        sb: list[Shadow] = []
        ab: list[Shadow] = []
        st_sbL, st_sbS, st_abL, st_abS = Stats4(), Stats4(), Stats4(), Stats4()
        sb_map: dict[int, float | None] = {}
        pending: list[tuple[int, int]] = []
        az_sig = [0.0] * 6
        az_edge = [0.0] * 3

        for i, b in enumerate(bars):
            live = i >= closed
            p = bars[i - 1] if i else None
            when = session_moment(b, tf)
            min_et = when.hour * 60 + when.minute
            dia_et = when.date()

            # First regular-session candle of the day (lines 136-143).
            primera = False
            if 570 <= min_et < 960 and dia_et != dia_marcado:
                primera = True
                dia_marcado = dia_et
            # First candle at or after the forced close time, once a day (lines 150-154).
            hora_cierre_hit = False
            if hc_min is not None and min_et >= hc_min and dia_et != hc_dia:
                hora_cierre_hit = True
                hc_dia = dia_et

            c = classify(b, p, x["cuerpoLlena"], x["mechaFleco"], x["cuerpoMinFleco"], x["cuerpoEngulf"],
                         x["verLlena"], x["verFleco"], x["verEngulf"], eng_bloq=x["noEngPrimera"] and primera)

            # Ladder states persist until their timeframe flips (lines 212-238).
            for lv in levels:
                if lv["sig"][i] == 1:
                    lv["state"] = 1
                elif lv["sig"][i] == -1:
                    lv["state"] = -1
            perm_up = all(not lv["active"] or lv["state"] >= 0 for lv in levels)
            perm_dn = all(not lv["active"] or lv["state"] <= 0 for lv in levels)

            # Continuation compares with the previous RAW signal (lines 243-249).
            cont_up = not x["usarContinua"] or ult_dir >= 0
            cont_dn = not x["usarContinua"] or ult_dir <= 0
            if c.up:
                ult_dir = 1
            if c.dn:
                ult_dir = -1

            use_ma = x["maCual"] != "Ninguna"
            ma_up = not use_ma or (ma[i] is not None and b.close > ma[i])
            ma_dn = not use_ma or (ma[i] is not None and b.close < ma[i])
            if x["usarADX"]:
                adx_ok = adx[i] is not None and (adx[i] > x["adxUmbral"] if x["adxCond"] == "mayor" else adx[i] < x["adxUmbral"])
            else:
                adx_ok = True

            sig_up = c.up and perm_up and cont_up and ma_up and adx_ok
            sig_dn = c.dn and perm_dn and cont_dn and ma_dn and adx_ok
            if (c.up and not sig_up) or (c.dn and not sig_dn):
                why = []
                if not (perm_up if c.up else perm_dn):
                    why.append("ladder")
                if not (cont_up if c.up else cont_dn):
                    why.append("continuation")
                if not (ma_up if c.up else ma_dn):
                    why.append("moving average")
                if not adx_ok:
                    why.append("ADX")
                blocked.append({"i": i, "time": b.time, "dir": 1 if c.up else -1, "type": TYPES[c.tipo],
                                "why": why, "preview": live})

            # One at a time / opening window / most per day (lines 272-283).
            if dia_et != prev_day:  # timeframe.change("D")
                trades_sesion = 0
                prev_day = dia_et
            en_apertura = in_session(min_et, x["sesApertura"])
            ok_apertura = not x["soloApertura"] or en_apertura
            ok_sesion = x["maxTradesSesion"] == 0 or trades_sesion < x["maxTradesSesion"]
            puede_abrir = (modo_senal or not x["unaVez"] or not hay_pos) and ok_apertura and ok_sesion
            up_v = sig_up and puede_abrir
            dn_v = sig_dn and puede_abrir
            if up_v or dn_v:
                trades_sesion += 1
                s = {"i": i, "time": b.time, "dir": 1 if up_v else -1, "type": TYPES[c.tipo],
                     "price": b.close, "body_pct": c.relleno, "wick_pct": c.m_max}
                if live:
                    preview = s
                else:
                    signals.append(s)

            if live:
                # A candle in progress is only previewed: no trades open or close on it.
                continue

            # ---------- target/stop mode (lines 373-488) ----------
            if not modo_senal and open_trades:
                for k in range(len(open_trades) - 1, -1, -1):
                    t = open_trades[k]
                    e, d = t.entry, t.d
                    tgt = e * (1 + o) if d == 1 else e * (1 - o)
                    stp = e * (1 - st) if d == 1 else e * (1 + st)
                    if i == t.bar + 1:
                        gp = (b.open - e) / e * 100.0 * d
                        book.gS[t.tipo] += gp
                        book.gN[t.tipo] += 1
                        if (d == 1 and b.open >= tgt) or (d == -1 and b.open <= tgt):
                            book.gW[t.tipo] += 1
                    res, res_w, motivo, ret, exit_price = False, 0, "", 0.0, None
                    if i > t.bar:
                        h, via = hit_target_or_stop(d, tgt, stp, b, inside[i], intra_ok)
                        if h == 1:
                            book.rS[t.tipo] += x["objPct"]
                            res, res_w, motivo, ret, exit_price = True, 1, "TP", x["objPct"], tgt
                        elif h == -1:
                            book.rS[t.tipo] -= x["stopPct"]
                            res, res_w, motivo, ret, exit_price = True, -1, "SL", -x["stopPct"], stp
                        if res and via and inside[i]:
                            book.nCamino += 1
                        if not res and (i - t.bar) >= x["maxVelas"]:
                            mov = (b.close - e) / e * 100.0 if d == 1 else (e - b.close) / e * 100.0
                            if x["cierraMercado"]:
                                res_w = 1 if mov >= 0 else -1
                                book.rS[t.tipo] += mov
                                motivo = "corte"
                            else:
                                book.nF[t.tipo] += 1
                                book.sF[t.tipo] += mov
                                motivo = "flot"
                            res, ret, exit_price = True, mov, b.close
                    if res:
                        days = (b.time - t.time) / 86400.0
                        del open_trades[k]
                        if res_w != 0:
                            book.duration(i - t.bar, days)
                        if res_w == 1:
                            book.win(t.tipo, t.year, days)
                        elif res_w == -1:
                            book.loss(t.tipo, t.year, days)
                        trades.append({"entry_i": t.bar, "entry_time": t.time, "entry_price": e, "dir": d,
                                       "type": TYPES[t.tipo], "exit_i": i, "exit_time": b.time,
                                       "exit_price": exit_price, "reason": motivo, "ret_pct": ret,
                                       "counted": res_w != 0, "bars": i - t.bar, "days": days})

            # ---------- signal to signal (lines 489-573) ----------
            if modo_senal and not modo_cesta:
                cerro, ret, motivo = False, 0.0, ""
                if sig_dir != 0 and hora_cierre_hit:
                    ret = (b.close - sig_entry) / sig_entry * 100.0 if sig_dir == 1 else (sig_entry - b.close) / sig_entry * 100.0
                    cerro, motivo = True, "HORA"
                exit_price = b.close
                if not cerro and sig_dir != 0 and (x["tpPctSS"] > 0 or x["slPctSS"] > 0):
                    tp_l, sl_l = sig_entry * (1 + x["tpPctSS"] / 100.0), sig_entry * (1 - x["slPctSS"] / 100.0)
                    tp_c, sl_c = sig_entry * (1 - x["tpPctSS"] / 100.0), sig_entry * (1 + x["slPctSS"] / 100.0)
                    if sig_dir == 1:
                        if x["slPctSS"] > 0 and b.low <= sl_l:
                            ret, cerro, motivo, exit_price = -x["slPctSS"], True, "SL", sl_l
                        elif x["tpPctSS"] > 0 and b.high >= tp_l:
                            ret, cerro, motivo, exit_price = x["tpPctSS"], True, "TP", tp_l
                    else:
                        if x["slPctSS"] > 0 and b.high >= sl_c:
                            ret, cerro, motivo, exit_price = -x["slPctSS"], True, "SL", sl_c
                        elif x["tpPctSS"] > 0 and b.low <= tp_c:
                            ret, cerro, motivo, exit_price = x["tpPctSS"], True, "TP", tp_c
                opuesta = (up_v or dn_v) and sig_dir != 0 and ((1 if up_v else -1) != sig_dir)
                if opuesta and not cerro:
                    ret = (b.close - sig_entry) / sig_entry * 100.0 if sig_dir == 1 else (sig_entry - b.close) / sig_entry * 100.0
                    cerro, motivo, exit_price = True, "SIG", b.close
                if cerro:
                    book.rS[sig_tipo] += ret
                    days = (b.time - sig_time) / 86400.0
                    book.duration(i - sig_bar, days)
                    if ret >= 0:
                        book.win(sig_tipo, sig_year, days)
                    else:
                        book.loss(sig_tipo, sig_year, days)
                    trades.append({"entry_i": sig_bar, "entry_time": sig_time, "entry_price": sig_entry, "dir": sig_dir,
                                   "type": TYPES[sig_tipo], "exit_i": i, "exit_time": b.time, "exit_price": exit_price,
                                   "reason": motivo, "ret_pct": ret, "counted": True, "bars": i - sig_bar, "days": days})
                    sig_entry, sig_dir = None, 0
                antes = hc_min is None or min_et < hc_min
                if (up_v or dn_v) and sig_dir == 0 and antes:
                    sig_entry, sig_dir, sig_tipo = b.close, (1 if up_v else -1), c.tipo
                    sig_year, sig_bar, sig_time = bar_year(b, tf), i, b.time
                    entries.append({"i": i, "time": b.time, "price": b.close, "dir": sig_dir, "type": TYPES[c.tipo]})

            # ---------- basket (lines 580-667) ----------
            if modo_senal and modo_cesta:
                cerro, ret, motivo = False, 0.0, ""
                avg = cst_sum / cst_n if cst_n > 0 else None
                exit_price = b.close
                if cst_dir != 0 and hora_cierre_hit:
                    ret = (b.close - avg) / avg * 100.0 if cst_dir == 1 else (avg - b.close) / avg * 100.0
                    cerro, motivo = True, "HORA"
                if not cerro and cst_dir != 0 and (x["tpPctSS"] > 0 or x["slPctSS"] > 0):
                    tp_l, sl_l = avg * (1 + x["tpPctSS"] / 100.0), avg * (1 - x["slPctSS"] / 100.0)
                    tp_c, sl_c = avg * (1 - x["tpPctSS"] / 100.0), avg * (1 + x["slPctSS"] / 100.0)
                    if cst_dir == 1:
                        if x["slPctSS"] > 0 and b.low <= sl_l:
                            ret, cerro, motivo, exit_price = -x["slPctSS"], True, "SL", sl_l
                        elif x["tpPctSS"] > 0 and b.high >= tp_l:
                            ret, cerro, motivo, exit_price = x["tpPctSS"], True, "TP", tp_l
                    else:
                        if x["slPctSS"] > 0 and b.high >= sl_c:
                            ret, cerro, motivo, exit_price = -x["slPctSS"], True, "SL", sl_c
                        elif x["tpPctSS"] > 0 and b.low <= tp_c:
                            ret, cerro, motivo, exit_price = x["tpPctSS"], True, "TP", tp_c
                opuesta = (up_v or dn_v) and cst_dir != 0 and ((1 if up_v else -1) != cst_dir)
                if opuesta and not cerro:
                    ret = (b.close - avg) / avg * 100.0 if cst_dir == 1 else (avg - b.close) / avg * 100.0
                    cerro, motivo, exit_price = True, "SIG", b.close
                if cerro:
                    book.rS[cst_tipo] += ret
                    days = (b.time - cst_time) / 86400.0
                    book.duration(i - cst_bar, days)
                    if ret >= 0:
                        book.win(cst_tipo, cst_year, days)
                    else:
                        book.loss(cst_tipo, cst_year, days)
                    trades.append({"entry_i": cst_bar, "entry_time": cst_time, "entry_price": avg, "dir": cst_dir,
                                   "type": TYPES[cst_tipo], "exit_i": i, "exit_time": b.time, "exit_price": exit_price,
                                   "reason": motivo, "ret_pct": ret, "counted": True, "bars": i - cst_bar,
                                   "days": days, "entries": cst_n, "entry_times": list(cst_times)})
                    cst_dir, cst_sum, cst_n = 0, 0.0, 0
                    cst_times = []
                antes = hc_min is None or min_et < hc_min
                if (up_v or dn_v) and antes:
                    dc = 1 if up_v else -1
                    if cst_dir == 0:
                        cst_dir, cst_sum, cst_n, cst_tipo = dc, b.close, 1, c.tipo
                        cst_year, cst_bar, cst_time = bar_year(b, tf), i, b.time
                        cst_times = [b.time]
                        entries.append({"i": i, "time": b.time, "price": b.close, "dir": dc, "type": TYPES[c.tipo]})
                    elif cst_dir == dc and (x["maxCesta"] == 0 or cst_n < x["maxCesta"]):
                        cst_sum += b.close
                        cst_n += 1
                        cst_times.append(b.time)
                        entries.append({"i": i, "time": b.time, "price": b.close, "dir": dc, "type": TYPES[c.tipo]})

            if up_v and not modo_senal:
                open_trades.append(OpenTrade(b.close, 1, i, c.tipo, bar_year(b, tf), b.time))
                entries.append({"i": i, "time": b.time, "price": b.close, "dir": 1, "type": TYPES[c.tipo]})
            if dn_v and not modo_senal:
                open_trades.append(OpenTrade(b.close, -1, i, c.tipo, bar_year(b, tf), b.time))
                entries.append({"i": i, "time": b.time, "price": b.close, "dir": -1, "type": TYPES[c.tipo]})
            hay_pos = len(open_trades) > 0

            # ---------- luck test shadow books (lines 773-821) ----------
            if luck:
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
                if up_v or dn_v:
                    sb.append(Shadow(b.close, 1, i))
                    sb.append(Shadow(b.close, -1, i))
                    pending.append((i, 1 if up_v else -1))
                if ok_apertura:
                    ab.append(Shadow(b.close, 1, i))
                    ab.append(Shadow(b.close, -1, i))

        # The open position for the target/stop lines (lines 841-845).
        position = None
        if not modo_senal and open_trades:
            t = open_trades[-1]
            position = {"entry": t.entry, "dir": t.d, "entry_time": t.time, "type": TYPES[t.tipo],
                        "target": t.entry * (1 + o) if t.d == 1 else t.entry * (1 - o),
                        "stop": t.entry * (1 - st) if t.d == 1 else t.entry * (1 + st)}
        elif modo_senal and not modo_cesta and sig_dir != 0:
            position = _ss_position(sig_entry, sig_dir, sig_time, sig_tipo, x)
        elif modo_senal and modo_cesta and cst_dir != 0:
            position = _ss_position(cst_sum / cst_n, cst_dir, cst_time, cst_tipo, x) | {"entries": cst_n,
                                                                                        "entry_times": list(cst_times)}

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
            "results": _results(book, closed, x),
            "ladder": [{"tf": lv["tf"], "active": lv["active"], "state": lv["state"]} for lv in levels],
            "intrabar": {"on": intra_ok, "tf": x["tfIntra"],
                         "covered_from": next((bars[i].time for i in range(closed) if inside[i]), None)},
            "ma": [{"time": bars[i].time, "value": ma[i]} for i in range(n) if ma[i] is not None] if ma_len else [],
        }
        if luck:
            out["luck"] = _luck(x, az_sig, az_edge, st_sbL, st_sbS, st_abL, st_abS)
        return out


def _ss_position(entry: float, d: int, t: int, tipo: int, x: dict) -> dict:
    tp, sl = x["tpPctSS"], x["slPctSS"]
    return {"entry": entry, "dir": d, "entry_time": t, "type": TYPES[tipo],
            "target": (entry * (1 + tp / 100.0) if d == 1 else entry * (1 - tp / 100.0)) if tp > 0 else None,
            "stop": (entry * (1 - sl / 100.0) if d == 1 else entry * (1 + sl / 100.0)) if sl > 0 else None}


def _results(book: Book, measured: int, x: dict) -> dict:
    """The script's results tables (lines 870-990)."""
    rows = []
    for k in range(3):
        w, lo = book.nW[k], book.nL[k]
        tot = w + lo
        fl = book.nF[k]
        rows.append({
            "type": TYPES[k], "wins": w, "losses": lo, "win_rate": 100.0 * w / tot if tot else None,
            "floating": fl, "floating_avg": book.sF[k] / fl if fl else None,
            "gap_avg": book.gS[k] / book.gN[k] if book.gN[k] else None, "gap_wins": book.gW[k], "gap_n": book.gN[k],
            "r_avg": book.rS[k] / tot if tot else None,
            "days_win": book.dWsum[k] / book.dWn[k] if book.dWn[k] else None,
            "days_loss": book.dLsum[k] / book.dLn[k] if book.dLn[k] else None,
        })
    tw, tl = sum(book.nW), sum(book.nL)
    tot = tw + tl
    dwn, dln = sum(book.dWn), sum(book.dLn)
    years = sorted(set(book.yW) | set(book.yL))
    return {
        "by_type": rows,
        "total": {"wins": tw, "losses": tl, "win_rate": 100.0 * tw / tot if tot else None, "floating": sum(book.nF),
                  "r_avg": sum(book.rS) / tot if tot else None,
                  "days_win": sum(book.dWsum) / dwn if dwn else None,
                  "days_loss": sum(book.dLsum) / dln if dln else None},
        "years": [{"year": y, "wins": book.yW.get(y, 0), "losses": book.yL.get(y, 0)} for y in years],
        "candles_measured": measured,
        "max_win_streak": book.maxRW,
        "max_loss_streak": book.maxRL,
        "real_path_trades": book.nCamino,
        "avg_days": book.durDias / book.durN if book.durN else None,
        "avg_bars": book.durBars / book.durN if book.durN else None,
        "mode": "basket" if x["modoSenal"] and x["modoCesta"] else ("signal" if x["modoSenal"] else "target_stop"),
    }


def _luck(x: dict, az_sig: list, az_edge: list, sbL: Stats4, sbS: Stats4, abL: Stats4, abS: Stats4) -> dict:
    """The luck test table and verdicts (lines 993-1069)."""
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
        "rule": {"objPct": x["objPct"], "stopPct": x["stopPct"], "maxVelas": x["maxVelas"]},
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


STRATEGIES: dict[str, Strategy] = {SwingV98.id: SwingV98()}

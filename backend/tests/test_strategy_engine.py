"""The strategy engine against hand-worked candle sequences (plan rule 5: strategy logic gets the
most tests). Names refer to docs/swing_diario_semanal_v9_39.pine."""
from datetime import datetime, timedelta, timezone

import pytest

from app.marketdata.base import Bar
from app.strategy import indicators
from app.strategy.base import InputError, StrategyData, check_inputs
from app.strategy.swing import INPUTS, NY, SwingStrategy, _months, classify, dist_ok, group_inside, runs_in_a_row

S = SwingStrategy()
DAY0 = datetime(2024, 1, 1, tzinfo=timezone.utc)  # a Monday


def day(i: int) -> int:
    return int((DAY0 + timedelta(days=i)).timestamp())


def bar(i: int, o: float, h: float, lo: float, c: float, t: int | None = None) -> Bar:
    return Bar(day(i) if t is None else t, o, h, lo, c, 1000.0)


def flat(i: int, price: float = 100.0) -> Bar:
    """A doji that is no signal of any kind and breaks any run of one colour."""
    return bar(i, price, price + 0.5, price - 0.5, price)


def green_full(i: int, base: float) -> Bar:
    """LLENA up: body 90% of range."""
    return bar(i, base, base + 10.5, base - 0.5, base + 10)


def red_full(i: int, base: float) -> Bar:
    return bar(i, base, base + 0.5, base - 10.5, base - 10)


def red_small(i: int, base: float) -> Bar:
    """A red candle that is no candle type: body 50%, wicks 25% each."""
    return bar(i, base, base + 0.5, base - 1.5, base - 1)


# The v9.38 defaults the hand-made sequences below were written for (v9.39 starts with the 30M-1 set).
V938 = {"cuerpoLlena": 85.0, "mechaFleco": 65.0, "cuerpoMinFleco": 4.0, "cuerpoEngulf": 61.0, "noEngPrimera": False,
        "nVerdes": 3, "usarEscalera": False, "maxPerdW": 0, "maxVelas": 13, "maxAbiertas": 10, "cierraContra": False,
        "maCual": "Ninguna", "maLen1": 50}


def run(bars, tf="1D", closed=None, luck=False, other=None, **changes):
    """The engine with the v9.38 defaults above, and the RACHA off unless a test turns it on, so
    the hand-made sequences below are only the candles they mean to be."""
    inputs = check_inputs(INPUTS, V938 | {"usarRacha": False} | changes)
    # The engine still follows the script's groups 8 and 9, which the site hides at their defaults.
    inputs |= {d.key: d.check(changes[d.key]) for d in INPUTS if d.hidden and d.key in changes}
    data = StrategyData("TEST", tf, bars, len(bars) if closed is None else closed, other or {})
    return S.run(data, inputs, luck=luck)


# ---------- candle types ----------


def c(b, p=None, **kw):
    th = dict(cuerpo_llena=85, mecha_fleco=65, cuerpo_min_fleco=4, cuerpo_engulf=61) | kw
    return classify(b, p, **th)


def test_llena_threshold_is_inclusive():
    exactly = bar(1, 100, 120, 100, 117)  # body 17 of range 20: exactly 85%
    r = c(exactly)
    assert r.relleno == pytest.approx(85.0) and r.up and r.tipo == 0
    assert not c(bar(1, 100, 110.2, 99.8, 108.4)).up  # body 84% -> nothing


def test_llena_direction_follows_color():
    assert c(green_full(1, 100)).up
    r = c(red_full(1, 100))
    assert r.dn and not r.up and r.tipo == 0


def test_engulfing_needs_opposite_color_bigger_body_and_min_size():
    prev = bar(0, 100, 101, 97, 98)  # red, body 2
    eng = bar(1, 98, 101.5, 97.5, 101)  # green, body 3 of range 4 = 75%
    r = c(eng, prev)
    assert r.up and r.tipo == 2
    assert not c(eng, prev, cuerpo_engulf=80).up  # 75% < 80%
    assert not c(eng, bar(0, 100, 104, 99, 103)).up  # same color: not engulfing (and not LLENA/FLECO)
    assert not c(bar(1, 98, 101.5, 97.5, 99.5), prev).up  # body 1.5 not bigger than 2


def test_engulfing_size_zero_means_any_size():
    prev = bar(0, 100, 100.5, 99, 99.9)  # red, body 0.1
    small = bar(1, 99.9, 103, 97, 100.1)  # green, body 0.2 of range 6 (3%)
    assert c(small, prev, cuerpo_engulf=0).tipo == 2
    assert c(small, prev, cuerpo_engulf=0).up


def test_engulfing_beats_fleco_and_llena():
    prev = bar(0, 100, 100.5, 99.5, 99.6)  # red, small body
    big = bar(1, 99.6, 110, 99.5, 109.9)  # green, ~98% body: LLENA too, but ENGULFING wins
    r = c(big, prev)
    assert r.tipo == 2 and r.up


def test_fleco_reversal_by_wick():
    prev = bar(0, 100, 101, 95, 99)
    hammer = bar(1, 99, 99.6, 90, 99.4)  # lower wick 9 of 9.6 = 94%, body 4.2%
    r = c(hammer, prev)
    assert r.tipo == 1 and r.up  # rejection below -> buy
    # ...but only if it holds: a close under the previous low is no buy.
    r = c(bar(1, 94.5, 94.9, 85, 94.8), prev)
    assert not r.up and not r.dn
    star = bar(1, 100, 110, 99.5, 100.5)  # upper wick 90%, body 4.8%
    assert c(star, prev).dn


def test_fleco_minimum_body():
    prev = bar(0, 100, 101, 95, 99)
    doji = bar(1, 99, 99.5, 90, 99.05)  # body 0.5% of range
    assert not c(doji, prev).up
    assert c(doji, prev, cuerpo_min_fleco=0).up


def test_first_candle_has_no_engulfing_or_fleco():
    assert not c(bar(0, 99, 99.6, 90, 99.4)).up  # FLECO needs the previous low
    big = bar(0, 100, 110, 99.9, 109.9)
    assert c(big).up and c(big).tipo == 0


def test_switched_off_engulfing_blocks_the_candle_entirely():
    prev = bar(0, 100, 100.5, 99.5, 99.6)
    big = bar(1, 99.6, 110, 99.5, 109.9)  # ENGULFING and LLENA
    r = classify(big, prev, 85, 65, 4, 61, ver_engulf=False)
    assert not r.up  # LLENA only counts when the candle is not ENGULFING


def test_flat_range_candle():
    r = c(bar(1, 100, 100, 100, 100), flat(0))
    assert r.relleno == 0 and not r.up and not r.dn


# ---------- RACHA (v9.9, v9.13) ----------


def test_racha_buys_after_n_red_candles_in_a_row():
    bars = [flat(0), red_small(1, 100), red_small(2, 99), red_small(3, 98), flat(4, 97)]
    out = run(bars, usarRacha=True, usarIntra=False)
    (s,) = out["signals"]
    assert s["i"] == 3 and s["dir"] == 1 and s["type"] == "RACHA"
    assert not run(bars, usarRacha=True, nRojas=4)["signals"]
    # Three green in a row sell (the doji counts as neither colour).
    greens = [flat(0)] + [bar(i, 100 + i, 101.5 + i, 99.5 + i, 101 + i) for i in range(1, 4)]
    assert [(x["i"], x["dir"]) for x in run(greens, usarRacha=True)["signals"]] == [(3, -1)]


def test_racha_with_wick_keeps_looking_while_the_run_lasts():
    hammer_red = bar(4, 97, 97.1, 95, 96.5)  # red, lower wick 71% of range
    bars = [flat(0), red_small(1, 100), red_small(2, 99), red_small(3, 98), hammer_red,
            bar(5, 96.5, 96.6, 94.5, 96)]  # another red with a lower wick: same run, no second signal
    out = run(bars, usarRacha=True, rachaFleco=True, verFleco=False)
    assert [(s["i"], s["type"]) for s in out["signals"]] == [(4, "RACHA")]


def test_runs_in_a_row_count_like_barssince():
    bars = [red_small(0, 100), red_small(1, 99), flat(2, 98), red_small(3, 98), red_small(4, 97)]
    reds, greens = runs_in_a_row(bars)
    assert reds == [0, 0, 0, 1, 2]  # na (0) until a candle that is not red has been seen
    assert greens == [0, 0, 0, 0, 0]  # none is green


# ---------- normal mode: target / stop ----------


def test_target_hit_by_ohlc():
    bars = [flat(0), green_full(1, 100), bar(2, 110, 127, 109, 120)]  # entry 110, target 126.5
    out = run(bars, usarIntra=False)
    (t,) = out["trades"]
    assert t["reason"] == "TP" and t["ret_pct"] == 15 and t["exit_price"] == pytest.approx(126.5)
    r = out["results"]
    # v9.39: no "Repartido", each trade counts its whole % in the R column.
    assert r["total"]["wins"] == 1 and r["by_type"][0]["r_avg"] == 15
    assert r["months"] == {"won": 1, "lost": 0, "max_won_row": 1, "max_lost_row": 0, "now_won_row": 1,
                           "now_lost_row": None, "losing_runs": []}
    # Capital: 2% of $10,000 into a +15% trade.
    assert r["capital"]["final"] == pytest.approx(10030) and r["capital"]["trades"] == 1
    assert r["years"] == [{"year": 2024, "wins": 1, "losses": 0, "pnl": pytest.approx(30), "pnl_pct": pytest.approx(0.3)}]
    assert r["by_side"][0]["wins"] == 1 and r["by_side"][1]["wins"] == 0


def test_stop_counts_first_when_both_touch_in_one_candle():
    bars = [flat(0), green_full(1, 100), bar(2, 110, 127, 95, 120)]
    (t,) = run(bars, usarIntra=False)["trades"]
    assert t["reason"] == "SL" and t["ret_pct"] == -13


def test_gap_past_the_stop_or_target_closes_at_the_open():
    # v9.35: a candle that opens past the stop closes the trade at that open, not at -13%.
    bars = [flat(0), green_full(1, 100), bar(2, 90, 92, 89, 91)]
    (t,) = run(bars, usarIntra=False)["trades"]
    assert t["reason"] == "SL" and t["exit_price"] == 90 and t["ret_pct"] == pytest.approx((90 - 110) / 110 * 100)
    bars = [flat(0), green_full(1, 100), bar(2, 130, 131, 90, 95)]  # opens over the target
    out = run(bars, usarIntra=False)
    t = out["trades"][0]
    assert t["reason"] == "TP" and t["exit_price"] == 130 and t["ret_pct"] == pytest.approx((130 - 110) / 110 * 100)
    g = out["results"]["by_type"][0]
    assert g["gap_n"] == 1 and g["gap_wins"] == 1 and g["gap_avg"] == pytest.approx((130 - 110) / 110 * 100)


def test_time_exit_after_n_candles():
    bars = [flat(0), green_full(1, 100), *[flat(i, 111) for i in range(2, 5)]]
    out = run(bars, usarIntra=False, maxVelas=3)
    (t,) = out["trades"]
    assert t["reason"] == "corte" and t["bars"] == 3 and t["ret_pct"] == pytest.approx((111 - 110) / 110 * 100)
    assert out["results"]["total"]["wins"] == 1
    # Not closing at market: the trade floats and counts as neither win nor loss.
    out = run(bars, usarIntra=False, maxVelas=3, cierraMercado=False)
    assert out["trades"][0]["reason"] == "flot" and not out["trades"][0]["counted"]
    assert out["results"]["total"]["wins"] == 0 and out["results"]["by_type"][0]["floating"] == 1
    assert out["results"]["capital"]["trades"] == 0


def test_short_trade():
    bars = [flat(0), red_full(1, 100), bar(2, 90, 90.5, 76, 80)]  # entry 90, target 76.5
    (t,) = run(bars, usarIntra=False)["trades"]
    assert t["dir"] == -1 and t["reason"] == "TP"


def test_real_path_decides_the_order_inside_the_candle():
    bars = [flat(0), green_full(1, 100), bar(2, 110, 127, 95, 120)]  # OHLC alone says stop
    t2 = day(2)
    hours = [Bar(t2 + 14 * 3600, 110, 127, 109, 126, 1), Bar(t2 + 15 * 3600, 126, 126, 95, 96, 1)]
    out = run(bars, other={"1h": hours})
    assert out["trades"][0]["reason"] == "TP"  # the target came first, hour by hour
    assert out["results"]["real_path_trades"] == 1
    hours = [Bar(t2 + 14 * 3600, 110, 111, 95, 96, 1), Bar(t2 + 15 * 3600, 96, 127, 96, 126, 1)]
    assert run(bars, other={"1h": hours})["trades"][0]["reason"] == "SL"


def test_old_candles_without_inside_data_fall_back_to_ohlc():
    bars = [flat(0), green_full(1, 100), bar(2, 110, 127, 95, 120)]
    out = run(bars, other={"1h": []})
    assert out["trades"][0]["reason"] == "SL" and out["results"]["real_path_trades"] == 0


def test_several_trades_at_once_and_one_at_a_time():
    bars = [flat(0), green_full(1, 100), green_full(2, 110), flat(3, 121)]
    assert len(run(bars, usarIntra=False)["open_trades"]) == 2
    out = run(bars, usarIntra=False, unaVez=True)
    assert len(out["open_trades"]) == 1 and len(out["signals"]) == 1  # the second signal is not taken
    assert out["now"] == ["one at a time (1 open)"]
    assert len(run(bars, usarIntra=False, maxAbiertas=1)["open_trades"]) == 1


def test_a_trade_closing_on_the_candle_makes_room_for_its_signal():
    # v9.35: the trade hits its target on candle 2, which is itself a buy: one at a time takes it.
    bars = [flat(0), green_full(1, 100), bar(2, 111, 127, 110.5, 126.9)]
    for opt in ({"unaVez": True}, {"maxAbiertas": 1}):
        out = run(bars, usarIntra=False, **opt)
        assert [s["i"] for s in out["signals"]] == [1, 2], opt
        assert out["trades"][0]["reason"] == "TP" and out["open_trades"][0]["entry"] == 126.9


def test_opposite_signal_closes_when_asked():
    bars = [flat(0), green_full(1, 100), red_full(2, 110)]
    out = run(bars, usarIntra=False, cierraContra=True)
    (t,) = out["trades"]
    assert t["reason"] == "contra" and t["ret_pct"] == pytest.approx((100 - 110) / 110 * 100)
    assert out["open_trades"][0]["dir"] == -1  # and the sell opens
    assert len(run(bars, usarIntra=False)["open_trades"]) == 2


def test_direction_setting():
    bars = [flat(0), green_full(1, 100), red_full(2, 110)]
    assert [s["dir"] for s in run(bars, dirOper="Solo largos")["signals"]] == [1]
    assert [s["dir"] for s in run(bars, dirOper="Solo cortos")["signals"]] == [-1]


def test_two_trades_closing_on_one_candle_are_one_hit():
    bars = [flat(0), green_full(1, 100), green_full(2, 110), bar(3, 100, 101, 90, 91)]
    out = run(bars, usarIntra=False)
    r = out["results"]
    assert r["total"]["losses"] == 2 and r["max_loss_streak"] == 2
    assert r["hits"]["max_lost_in_a_row"] == 1  # both stops on candle 3: one hit
    lost = 10000 * 0.02 * (13 + (120 - 100) / 120 * 100) / 100
    assert r["hits"]["worst_run"] == pytest.approx(lost) and r["capital"]["final"] == pytest.approx(10000 - lost)


def test_compounding():
    up = [flat(0)]
    for _ in range(2):
        i = len(up)
        up += [green_full(i, 100), bar(i + 1, 110, 127, 109, 112)]
    simple = run(up, usarIntra=False)["results"]["capital"]["final"]
    comp = run(up, usarIntra=False, capComp=True)["results"]["capital"]["final"]
    assert simple == pytest.approx(10060) and comp == pytest.approx(10000 * 1.003 * 1.003)


def test_streaks_and_years():
    up = [flat(0)]
    for _ in range(3):  # three winners
        i = len(up)
        up += [green_full(i, 100), bar(i + 1, 110, 127, 109, 112)]
    out = run(up, usarIntra=False)
    r = out["results"]
    assert r["max_win_streak"] == 3 and r["max_loss_streak"] == 0
    assert [(y["year"], y["wins"], y["losses"]) for y in r["years"]] == [(2024, 3, 0)]
    assert r["avg_bars"] == 1 and r["avg_days"] == 1
    assert r["hits"]["max_won_in_a_row"] == 3


def test_winners_in_a_row_force_the_other_direction():
    up = [flat(0)]
    for _ in range(3):
        i = len(up)
        up += [green_full(i, 100), bar(i + 1, 110, 127, 109, 112)]
    out = run(up, usarIntra=False, ganSeguidas=2)
    assert len(out["signals"]) == 2 and out["now"] == ["in a row: no BUY"]  # the third buy waits for a sell


# ---------- signal to signal and basket ----------


def test_signal_to_signal_reverses_on_the_opposite_signal():
    bars = [flat(0), green_full(1, 100), flat(2, 111), red_full(3, 115)]
    out = run(bars, modoSenal=True)
    (t,) = out["trades"]
    assert t["reason"] == "SIG" and t["ret_pct"] == pytest.approx((105 - 110) / 110 * 100)
    assert out["position"]["dir"] == -1 and out["position"]["entry"] == 105


def test_signal_to_signal_take_profit_and_stop():
    bars = [flat(0), green_full(1, 100), bar(2, 111, 125, 109, 112)]
    (t,) = run(bars, modoSenal=True, tpPctSS=10, slPctSS=5)["trades"]
    assert t["reason"] == "TP" and t["ret_pct"] == 10
    bars = [flat(0), green_full(1, 100), bar(2, 111, 125, 104, 112)]  # both touched: stop first
    (t,) = run(bars, modoSenal=True, tpPctSS=10, slPctSS=5)["trades"]
    assert t["reason"] == "SL" and t["ret_pct"] == -5
    bars = [flat(0), green_full(1, 100), bar(2, 100, 101, 99, 100.5)]  # opens under the stop
    (t,) = run(bars, modoSenal=True, tpPctSS=10, slPctSS=5)["trades"]
    assert t["reason"] == "SL" and t["exit_price"] == 100 and t["ret_pct"] == pytest.approx(-100 / 11)


def test_forced_close_time_intraday():
    base = int(datetime(2024, 1, 2, 9, 30, tzinfo=NY).timestamp())

    def m(k, o, h, lo, cl):
        return Bar(base + k * 3600, o, h, lo, cl, 1)

    bars = [m(0, 100, 100.5, 99.5, 100), m(1, 100, 110.5, 99.5, 110), m(2, 110, 110.5, 109.5, 110),
            m(3, 110, 110.5, 109.5, 110), m(4, 110, 110.5, 109.5, 110), m(5, 110, 120.5, 109.5, 120),
            m(6, 120, 120.5, 119.5, 120)]
    out = run(bars, tf="1h", modoSenal=True, horaCierreSS="14:30")
    (t,) = out["trades"]
    assert t["reason"] == "HORA" and t["exit_i"] == 5
    # The LLENA at 14:30 is at the close time, so it does not reopen.
    assert out["position"] is None


def test_opposite_signal_closes_outside_the_opening_window():
    # v9.35: the window stops new entries only; the 12:30 sell still closes the 10:30 buy.
    base = int(datetime(2024, 1, 2, 9, 30, tzinfo=NY).timestamp())
    bars = [Bar(base, 100, 100.5, 99.5, 100, 1), Bar(base + 3600, 100, 110.5, 99.5, 110, 1),
            Bar(base + 3 * 3600, 110, 110.5, 99.5, 100, 1)]
    out = run(bars, tf="1h", modoSenal=True, soloApertura=True)
    (t,) = out["trades"]
    assert t["reason"] == "SIG" and t["exit_i"] == 2 and out["position"] is None


def test_most_per_day_counts_only_what_opens():
    # v9.35: in signal to signal the second buy (already long) no longer uses up the day.
    base = int(datetime(2024, 1, 2, 9, 30, tzinfo=NY).timestamp())
    bars = [Bar(base, 100, 100.5, 99.5, 100, 1), Bar(base + 900, 100, 110.5, 99.5, 110, 1),
            Bar(base + 1800, 110, 120.5, 109.5, 120, 1), Bar(base + 2700, 120, 120.5, 109.5, 110, 1)]
    out = run(bars, tf="15m", modoSenal=True, maxTradesSesion=2)
    assert [t["reason"] for t in out["trades"]] == ["SIG"] and out["position"]["dir"] == -1


def test_most_trades_per_day_intraday():
    base = int(datetime(2024, 1, 2, 9, 30, tzinfo=NY).timestamp())
    bars = [Bar(base, 100, 100.5, 99.5, 100, 1)]
    for k in range(1, 4):
        p = 100 + 10 * (k - 1)
        bars.append(Bar(base + k * 900, p, p + 10.5, p - 0.5, p + 10, 1))
    assert len(run(bars, tf="15m", maxTradesSesion=2)["signals"]) == 2


def test_basket_averages_and_closes_together():
    bars = [flat(0), green_full(1, 100), green_full(2, 110), bar(3, 120, 121, 110, 111), red_full(4, 111)]
    out = run(bars, modoSenal=True, modoCesta=True)
    (t,) = out["trades"]
    assert t["entries"] == 2 and t["entry_price"] == 115  # average of 110 and 120
    assert t["reason"] == "SIG" and t["ret_pct"] == pytest.approx((101 - 115) / 115 * 100)
    # Each entry counts as one operation (v9.16), with its own % (v9.39: no "Repartido").
    r = out["results"]
    assert r["total"]["losses"] == 2
    assert r["total"]["r_avg"] == pytest.approx(((101 - 110) / 110 + (101 - 120) / 120) / 2 * 100)
    own = 10000 * 0.02 * ((101 - 110) / 110 + (101 - 120) / 120)
    assert r["capital"]["final"] == pytest.approx(10000 + own)
    # Most entries:
    out = run(bars[:4], modoSenal=True, modoCesta=True, maxCesta=1)
    assert out["position"]["entries"] == 1 and out["now"] == ["basket full (1)"]


def test_basket_gap_past_the_stop_closes_at_the_open():
    # v9.39: the stop is on the AVERAGE of the entries' %: at 105 it is (-4.5% - 12.5%) / 2.
    bars = [flat(0), green_full(1, 100), green_full(2, 110), bar(3, 105, 106, 104, 105.5)]
    (t,) = run(bars, modoSenal=True, modoCesta=True, slPctSS=5)["trades"]
    mean = ((105 - 110) / 110 + (105 - 120) / 120) / 2 * 100
    assert t["reason"] == "SL" and t["exit_price"] == 105 and t["ret_pct"] == pytest.approx(mean)
    assert [op["cap"] for op in t["ops"]] == pytest.approx([(105 - 110) / 110 * 100, (105 - 120) / 120 * 100])


# ---------- filters ----------


def test_moving_average_filter():
    bars = [flat(i, 100) for i in range(5)] + [green_full(5, 100)]
    assert run(bars, maCual="MA1", maLen1=3)["signals"]  # close 110 above the average
    bars = [flat(i, 200) for i in range(5)] + [green_full(5, 100)]
    out = run(bars, maCual="MA1", maLen1=3)
    assert not out["signals"] and out["blocked"][0]["why"] == ["moving average"]
    assert out["ma"] and out["ma"][0]["time"] == day(2)


def test_continuation_filter_vetoes_the_first_reversal():
    bars = [flat(0), green_full(1, 100), red_full(2, 110), red_full(3, 100)]
    out = run(bars, usarContinua=True)
    assert [s["dir"] for s in out["signals"]] == [1, -1]  # the first SELL is blocked but arms the change
    assert out["blocked"][0]["i"] == 2


def test_opening_window():
    base = int(datetime(2024, 1, 2, 9, 30, tzinfo=NY).timestamp())
    bars = [Bar(base, 100, 100.5, 99.5, 100, 1), Bar(base + 3600, 100, 110.5, 99.5, 110, 1),
            Bar(base + 3 * 3600, 110, 120.5, 109.5, 120, 1)]  # 10:30 and 12:30
    out = run(bars, tf="1h", soloApertura=True)
    assert [s["i"] for s in out["signals"]] == [1]


def test_no_engulfing_on_first_session_candle():
    base = int(datetime(2024, 1, 2, 9, 30, tzinfo=NY).timestamp())
    prev_day = int(datetime(2024, 1, 1, 15, 30, tzinfo=NY).timestamp())
    bars = [Bar(prev_day, 100, 100.5, 99.5, 99.6, 1), Bar(base, 99.6, 110, 99.5, 109.9, 1)]
    assert run(bars, tf="1h")["signals"]
    assert not run(bars, tf="1h", noEngPrimera=True)["signals"]


# ---------- ladder ----------


def test_ladder_reads_the_last_closed_higher_candle():
    weeks = [bar(0, 100, 110.5, 99.5, 110), bar(7, 110, 110.5, 99.5, 100.5)]  # week 1 up, week 2 down
    days = [flat(i) for i in (0, 1, 2, 3)] + [flat(i) for i in (7, 8)]
    out = run(days, usarEscalera=True, other={"1W": weeks})
    # Week 1's BUY, read in week 2. Week 1 built from these dojis has no signal at its close, and
    # week 2's SELL is not read until week 2 has closed.
    assert out["ladder"][0] == {"tf": "1W", "active": True, "state": 1}


def test_ladder_blocks_signals_against_the_higher_timeframe():
    weeks = [bar(0, 100, 100.5, 89.5, 90), bar(7, 90, 90.5, 89.5, 90.1)]  # week 1 SELL
    days = [flat(i, 90) for i in (7, 8, 9)] + [green_full(10, 90)]  # BUY in week 2, before Friday
    out = run(days, usarEscalera=True, other={"1W": weeks})
    assert not out["signals"] and out["blocked"][0]["why"] == ["ladder"]
    assert out["ladder"][0] == {"tf": "1W", "active": True, "state": -1}
    assert out["now"] == ["SELLS only (ladder)"]
    # A level that is not higher than the chart is ignored.
    assert run(days, tf="1D", usarEscalera=True, tfS1="1D", other={"1D": days})["signals"]


def _week_flip(friday: Bar) -> tuple[list[Bar], list[Bar]]:
    """A long bought Tuesday; the week, built from these candles, is a red LLENA at Friday's close."""
    days = [bar(0, 112, 112.5, 111.5, 112), green_full(1, 100), flat(2, 110), flat(3, 110), friday]
    weeks = [bar(0, 112, 112.5, min(99.5, friday.low), friday.close)]
    return days, weeks


def test_weekly_level_changes_at_fridays_close():
    days, weeks = _week_flip(bar(4, 110, 110.2, 100, 100.5))
    out = run(days, usarIntra=False, usarEscalera=True, other={"1W": weeks})
    assert out["ladder"][0]["state"] == -1
    assert [(s["i"], s["dir"]) for s in out["signals"]] == [(1, 1), (4, -1)]  # Friday's sell passes already


def test_weekly_change_closes_the_other_side_only_after_target_and_stop():
    days, weeks = _week_flip(bar(4, 110, 110.2, 100, 100.5))
    out = run(days, usarIntra=False, usarEscalera=True, cierraCambioW=True, other={"1W": weeks})
    t = out["trades"][0]
    assert t["reason"] == "cambio W" and t["ret_pct"] == pytest.approx((100.5 - 110) / 110 * 100)
    # v9.35: the stop was touched on Friday before the close, so it is a stop at -13%.
    days, weeks = _week_flip(bar(4, 110, 110.2, 90, 90.5))
    t = run(days, usarIntra=False, usarEscalera=True, cierraCambioW=True, other={"1W": weeks})["trades"][0]
    assert t["reason"] == "SL" and t["ret_pct"] == -13


def test_weekly_loss_cap_counts_friday_losses_once():
    # The week's Friday close is a weekly BUY (LLENA at 50%), and the long loses on that Friday.
    days = [flat(0), green_full(1, 100), flat(2, 110), flat(3, 110), bar(4, 110, 111, 90, 110.5),
            green_full(7, 110)]
    weeks = [bar(0, 100, 111, 90, 110.5), flat(7, 120)]
    common = dict(usarIntra=False, usarEscalera=True, e1Ll=50.0, other={"1W": weeks})
    out = run(days, maxPerdW=1, **common)
    # v9.39: the long lost on Friday was opened BEFORE that weekly signal, so it does not count and
    # Monday's buy opens. (v9.35: Monday does not reset the counter again; with it counted, it waited.)
    assert [s["i"] for s in out["signals"]] == [1, 5] and out["now"] == ["BUYS only (ladder)"]


def test_needs_only_what_is_used():
    inputs = check_inputs(INPUTS, {"usarEscalera": True, "filtro2": True})
    assert S.needs("1D", inputs) == {"1h", "1W"}  # path inside daily candles, and the weekly level
    assert S.needs("1W", check_inputs(INPUTS, {"tfIntra": "1D"})) == {"1D"}
    assert S.needs("1h", check_inputs(INPUTS, {"usarEscalera": False})) == set()  # path on the chart's own timeframe
    assert S.needs("30m", check_inputs(INPUTS, {"usarEscalera": False, "usarD1": True})) == {"1D"}  # v9.39


# ---------- live candles (plan 7.5) ----------


def test_candle_in_progress_is_previewed_not_traded():
    bars = [flat(0), green_full(1, 100), bar(2, 110, 127, 109, 120)]
    out = run(bars, closed=2, usarIntra=False)
    assert out["trades"] == [] and len(out["open_trades"]) == 1  # the open trade waits for the close
    bars = [flat(0), flat(1), green_full(2, 100)]
    out = run(bars, closed=2)
    assert out["signals"] == [] and out["preview"]["i"] == 2 and out["open_trades"] == []


# ---------- luck test ----------


def test_luck_test_counts():
    bars = [flat(0), green_full(1, 100), bar(2, 110, 127, 109, 112)]
    out = run(bars, usarIntra=False, luck=True)
    luck = out["luck"]
    sig, longs, shorts = luck["rows"][0], luck["rows"][1], luck["rows"][2]
    assert sig["n"] == 1 and sig["win_rate"] == 100 and sig["r_avg"] == 15
    assert longs["n"] == 1 and shorts["n"] == 1 and shorts["r_avg"] == -13  # the short shadow hit its stop at 124.3
    assert luck["verdicts"][0]["verdict"] == "-"  # one trade: no spread, no t-statistic
    assert not luck["rule"]["fixed"]


def test_luck_shadows_close_at_the_open_on_a_gap():
    bars = [flat(0), green_full(1, 100), bar(2, 90, 92, 89, 91)]
    rows = run(bars, usarIntra=False, luck=True)["luck"]["rows"]
    assert rows[0]["r_avg"] == pytest.approx((90 - 110) / 110 * 100)


def test_luck_verdict_wording():
    from app.strategy.swing import ncdf

    assert ncdf(0) == pytest.approx(0.5, abs=1e-6)
    assert ncdf(2) == pytest.approx(0.97725, abs=1e-4)


# ---------- credit spreads (v9.33, v9.34) ----------


def test_credit_spreads_at_expiration():
    bars = [flat(0), green_full(1, 100)] + [flat(i, 112) for i in range(2, 40)]
    out = run(bars, usarIntra=False, luck=True, csD1=1.0, csSimH=1, csSimJ=1)
    cs = out["credit_spreads"]
    row = cs["blocks"][0]["rows"][0]  # 14 days, short strike 1% above: the price ended 1.8% up
    assert cs["blocks"][0]["days"] == 14 and row["long"]["n"] == 1 and row["short"]["n"] == 0
    assert row["win_all"] == 100 and row["lose_all"] == 0
    assert cs["blocks"][0]["rows"][1]["lose_all"] == 100  # strike 6% above: the whole width lost
    sim = cs["simulation"]
    # Paying 2:1 means a credit of 2/3 of the width: $5,000 risked earns $10,000 when it wins.
    assert sim["spreads"] == 1 and sim["total"] == pytest.approx(10000) and sim["max_open"] == 1


# ---------- indicators ----------


def test_sma_and_rma():
    assert indicators.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    r = indicators.rma([None, 2, 4, 6, 8], 2)
    assert r[:2] == [None, None] and r[2] == 3 and r[3] == pytest.approx(4.5) and r[4] == pytest.approx(6.25)


def test_adx_behaves():
    rising = [Bar(i, 100 + i, 101 + i, 99.5 + i, 100.8 + i, 1) for i in range(60)]
    a = indicators.adx(rising, 14, 14)
    assert all(v is None for v in a[:27]) and a[27] is not None  # seeded after 2 x 14 - 1 candles
    assert a[-1] > 90  # a straight trend


def test_group_inside():
    days = [bar(0, 1, 1, 1, 1), bar(1, 1, 1, 1, 1)]
    hours = [Bar(day(0) - 3600, 1, 1, 1, 1, 1), Bar(day(0) + 50000, 1, 1, 1, 1, 1), Bar(day(1) + 50000, 1, 1, 1, 1, 1)]
    assert [len(g) for g in group_inside(days, hours)] == [1, 1]


# ---------- inputs ----------


def test_inputs_are_checked():
    assert check_inputs(INPUTS, {})["cuerpoLlena"] == 70
    assert check_inputs(INPUTS, {"cuerpoLlena": 80, "unknown": 1})["cuerpoLlena"] == 80
    with pytest.raises(InputError, match="at most 100"):
        check_inputs(INPUTS, {"cuerpoLlena": 120})
    with pytest.raises(InputError, match="whole number"):
        check_inputs(INPUTS, {"maxVelas": 2.5})
    with pytest.raises(InputError, match="HH:MM"):
        check_inputs(INPUTS, {"horaCierreSS": "25:00"})
    with pytest.raises(InputError, match="choose one"):
        check_inputs(INPUTS, {"dirOper": "Neither"})
    assert check_inputs(INPUTS, {"horaCierreSS": "15:30"})["horaCierreSS"] == "15:30"
    # Settings saved under v9.8 still load: the old shared ladder thresholds are simply dropped.
    x = check_inputs(INPUTS, {"cuerpoLlenaW": 70.0, "objPct": 9.0})
    assert x["objPct"] == 9 and x["e1Ll"] == 85 and "cuerpoLlenaW" not in x


def test_script_defaults():
    x = S.defaults()
    # v9.39 starts with the uploaded script's 30M-1 set.
    assert (x["usarRacha"], x["nRojas"], x["nVerdes"], x["maxAbiertas"], x["noEngPrimera"]) == (True, 3, 5, 30, True)
    assert (x["cuerpoLlena"], x["mechaFleco"], x["cuerpoMinFleco"], x["cuerpoEngulf"]) == (70, 50, 3, 55)
    assert (x["usarEscalera"], x["maxPerdW"], x["objPct"], x["stopPct"], x["maxVelas"], x["cierraContra"]) == \
        (True, 3, 15, 13, 800, True)
    assert (x["maCual"], x["maLen1"], x["usarADX"], x["invPerdW"], x["invSinContra"], x["usarD1"], x["d1MA"]) == \
        ("MA1", 8, False, False, True, False, 20)
    assert (x["cestaPerd"], x["distContra"], x["distFavor"]) == (0, 0, 0)
    assert not {"normalCuenta", "cestaCuenta", "cestaNMide", "cestaMin"} & set(x)
    hidden = {d.key for d in INPUTS if d.hidden}
    assert {"rachaFleco", "maxTradesSesion", "ganSeguidas", "perSeguidas", "cierraCambioW", "maLen2"} <= hidden
    assert not {"maCual", "maLen1", "usarADX", "adxUmbral"} & hidden
    assert (x["e1nV"], x["e2Fl"], x["capIni"], x["csAncho"]) == (7, 70, 10000, 1.33)
    # v9.37: level 3 is 30 minutes and starts with the pegged TSLA 30m settings; CESTA is off.
    assert (x["tfS3"], x["e3L"], x["e3E"], x["e3Ll"], x["e3Fl"], x["e3FlM"], x["e3En"], x["e3nR"], x["e3nV"]) == \
        ("30m", False, True, 75, 55, 3, 61, 3, 5)
    assert (x["e3NoEng1"], x["e3MA"], x["e3Sigue"], x["cestaN"]) == (False, 0, True, 0)


def test_days_follow_new_york_time_across_the_clock_change():
    # Daily candles count from 9:30 New York, as in the script: the week over the March 2025 clock
    # change is 7 days minus an hour, and a 14-day expiration from Monday 3 March lands on Tuesday 18th.
    d0 = datetime(2025, 3, 3, tzinfo=timezone.utc)
    t = [int((d0 + timedelta(days=k)).timestamp()) for k in range(30)]
    bars = [Bar(t[0], 100, 100.5, 99.5, 100, 1), Bar(t[1], 100, 110.5, 99.5, 110, 1)]
    bars += [Bar(t[k], 112 + k, 112.5 + k, 111.5 + k, 112 + k, 1) for k in range(2, 30)]
    out = run(bars, usarIntra=False, luck=True, maxVelas=7, csD1=15.0, csSimH=1, csSimJ=1)
    assert out["trades"][0]["days"] == pytest.approx(7 - 1 / 24)
    assert out["results"]["candles_measured"] == 30
    # Settled on the close of the 18th (127, +15.45%), which clears a 15% strike; the 17th (126, +14.5%) would not.
    sim = out["credit_spreads"]["simulation"]
    assert sim["spreads"] == 1 and out["credit_spreads"]["blocks"][0]["rows"][0]["win_all"] == 100


# ---------- the exchange calendar (v9.36) ----------


def test_exchange_calendar():
    from datetime import date

    from app.strategy.swing import easter, last_trading_day_of_week, nyse_closed, nyse_early_close

    assert easter(2025) == date(2025, 4, 20) and nyse_closed(date(2025, 4, 18))  # Good Friday
    assert nyse_closed(date(2026, 7, 3)) and nyse_closed(date(2021, 12, 24))  # Saturday holidays, Friday closed
    assert nyse_closed(date(2026, 6, 19)) and not nyse_closed(date(2021, 6, 18))  # Juneteenth only from 2022
    assert not nyse_closed(date(2021, 12, 31))  # New Year's Day on a Saturday closes no Friday
    assert nyse_early_close(date(2025, 11, 28)) and nyse_early_close(date(2025, 12, 24)) and nyse_early_close(date(2025, 7, 3))
    assert not nyse_early_close(date(2026, 7, 3))  # a holiday that year
    assert last_trading_day_of_week(date(2025, 4, 17))  # Thursday before Good Friday
    assert not last_trading_day_of_week(date(2025, 4, 16))
    assert last_trading_day_of_week(date(2001, 9, 10))  # the market stayed shut until the next Monday


def test_weekly_level_changes_on_thursday_when_friday_is_a_holiday():
    # Good Friday 2025: the week of 14 April ends on Thursday the 17th.
    d0 = datetime(2025, 4, 14, tzinfo=timezone.utc)

    def d(k, o, h, lo, cl):
        return Bar(int((d0 + timedelta(days=k)).timestamp()), o, h, lo, cl, 1)

    days = [d(0, 112, 112.5, 111.5, 112), d(1, 112, 112.5, 111.5, 112), d(2, 112, 112.5, 111.5, 112),
            d(3, 110, 110.2, 100, 100.5)]  # Thursday: the week is a red LLENA
    weeks = [d(0, 112, 112.5, 100, 100.5)]
    out = run(days, usarIntra=False, usarEscalera=True, other={"1W": weeks})
    assert out["ladder"][0]["state"] == -1 and [(s["i"], s["dir"]) for s in out["signals"]] == [(3, -1)]


def test_early_close_day_ends_on_the_1pm_candle():
    from app.strategy.swing import last_regular_bar

    t = int(datetime(2025, 11, 28, 12, 30, tzinfo=NY).timestamp())  # the Friday after Thanksgiving
    assert last_regular_bar(Bar(t, 1, 1, 1, 1, 1), "30m")
    assert not last_regular_bar(Bar(int(datetime(2025, 11, 21, 12, 30, tzinfo=NY).timestamp()), 1, 1, 1, 1, 1), "30m")
    assert last_regular_bar(Bar(int(datetime(2025, 11, 21, 15, 30, tzinfo=NY).timestamp()), 1, 1, 1, 1, 1), "30m")


def test_thirty_minute_candles():
    from app.marketdata.bars import aggregate

    base = int(datetime(2025, 11, 21, 9, 30, tzinfo=NY).timestamp())
    fifteen = [Bar(base + k * 900, 100 + k, 101 + k, 99 + k, 100.5 + k, 10) for k in range(4)]
    out = aggregate(fifteen, "30m")
    assert [b.time for b in out] == [base, base + 1800]
    assert (out[0].open, out[0].high, out[0].low, out[0].close, out[0].volume) == (100, 102, 99, 101.5, 20)


# ---------- v9.37: level 3 on 30 minutes, and the CESTA of normal mode ----------


def test_cesta_closes_every_open_trade_when_their_average_reaches_the_percent():
    # Longs at 110 and 120. v9.39: the AVERAGE of their %, looked at inside candle 3 (open 115, high 125.5):
    # +8% is reached on the way up, and both close at the exact price where the average is +8%.
    bars = [flat(0), green_full(1, 100), green_full(2, 110), green_full(3, 115)]
    a = 1 / 110 + 1 / 120
    px = (2 + 2 * 0.08) / a
    out = run(bars, usarIntra=False, maxAbiertas=2, cestaN=8.0)
    assert [(t["reason"], t["exit_i"]) for t in out["trades"]] == [("CESTA", 3), ("CESTA", 3)]
    assert [t["exit_price"] for t in out["trades"]] == pytest.approx([px, px])
    assert sum(t["ret_pct"] for t in out["trades"]) / 2 == pytest.approx(8.0)
    # Candle 3 is also a buy, which the CESTA makes room for.
    assert [s["i"] for s in out["signals"]] == [1, 2, 3] and out["open_trades"][0]["entry"] == 125
    assert out["open_now"]["cesta"] == {"pct": pytest.approx(0.0), "target": 8.0, "loss": 0.0, "closed": 1}
    # The high only takes the average to +9.3%: at 10% nothing closes, and the full book stops candle 3.
    o = run(bars, usarIntra=False, maxAbiertas=2, cestaN=10.0)
    assert o["trades"] == [] and [s["i"] for s in o["signals"]] == [1, 2]
    assert run(bars, usarIntra=False, maxAbiertas=2)["open_now"]["cesta"] is None  # off


def test_cesta_loss_box_and_opening_past_it():
    # Longs at 110 and 120; candle 3 falls to 105, where the average is -8.5% (the long from 120 keeps its
    # stop at 104.4). No signal on candle 3.
    a = 1 / 110 + 1 / 120
    bars = [flat(0), green_full(1, 100), green_full(2, 110), bar(3, 116, 117, 105, 107)]
    out = run(bars, usarIntra=False, cestaPerd=8.0)
    assert [t["reason"] for t in out["trades"]] == ["CESTA", "CESTA"]
    assert out["trades"][0]["exit_price"] == pytest.approx((2 - 2 * 0.08) / a)
    # Opening already past it: they close at the open.
    bars[3] = bar(3, 104.6, 105, 104.5, 104.8)
    out = run(bars, usarIntra=False, cestaPerd=8.0)
    assert [t["exit_price"] for t in out["trades"]] == [104.6, 104.6]


def test_cesta_needs_two_left_after_the_candles_own_closes():
    # The long from 110 reaches its target (126.5) on candle 3: a TP. Only the long from 120 is left
    # (+5.8% at the high), and with ONE left open the CESTA does nothing (v9.39).
    bars = [flat(0), green_full(1, 100), green_full(2, 110), bar(3, 120, 127, 119.5, 126)]
    out = run(bars, usarIntra=False, cestaN=5.0)
    assert [t["reason"] for t in out["trades"]] == ["TP"] and out["open_trades"][0]["entry"] == 120
    assert out["open_now"]["cesta"] == {"pct": pytest.approx(5.0), "target": 5.0, "loss": 0.0, "closed": 0}


def test_basket_take_profit_on_the_average_inside_the_candle():
    # Entries 110 and 120; candle 3 reaches 129.5, where the average of their % is +12.8%: the basket
    # closes at the exact price where it is +12%.
    bars = [flat(0), green_full(1, 100), green_full(2, 110), bar(3, 120, 129.5, 119.8, 121)]
    (t,) = run(bars, modoSenal=True, modoCesta=True, tpPctSS=12)["trades"]
    assert t["reason"] == "TP" and t["ret_pct"] == 12 and t["entries"] == 2
    assert t["exit_price"] == pytest.approx(2 * 1.12 / (1 / 110 + 1 / 120))


def test_distance_between_trades_of_the_same_side():
    # The second buy is 9.1% above the first (in favour).
    bars = [flat(0), green_full(1, 100), green_full(2, 110)]
    assert [s["i"] for s in run(bars, usarIntra=False, distFavor=10.0)["signals"]] == [1]
    assert [s["i"] for s in run(bars, usarIntra=False, distFavor=5.0)["signals"]] == [1, 2]
    assert [s["i"] for s in run(bars, usarIntra=False, distContra=5.0)["signals"]] == [1, 2]  # not against
    out = run(bars, modoSenal=True, modoCesta=True, distFavor=10.0)
    assert out["position"]["entries"] == 1
    # Against: a buy 3% below the last one opens only with distContra at most 3.
    x = {"distContra": 5.0, "distFavor": 0.0}
    assert not dist_ok(x, 1, 100.0, 97.0) and dist_ok(x | {"distContra": 3.0}, 1, 100.0, 97.0)
    assert dist_ok(x, -1, 100.0, 105.0) and not dist_ok(x, -1, 100.0, 103.0)  # sells: against is up
    assert dist_ok(x, 1, None, 50.0)  # the first of a side always opens


def test_reversed_after_the_weekly_losses():
    # The weekly level is a BUY from Friday's close. Monday's long (120) is stopped on Tuesday: one loss
    # since the weekly signal. Wednesday's sell (LLENA, against the weekly) is blocked as usual; with
    # AL REVÉS it opens. Thursday's buy would close it, but the opposite signal is off until the next
    # weekly signal.
    days = [flat(0), flat(1, 110), flat(2, 110), flat(3, 110), bar(4, 110, 111, 109, 110.5),
            green_full(7, 110), bar(8, 120, 121, 104, 106), red_full(9, 106), green_full(10, 96)]
    weeks = [bar(0, 100, 111, 90, 110.5), flat(7, 120)]
    common = dict(usarIntra=False, usarEscalera=True, e1Ll=50.0, maxPerdW=1, cierraContra=True, other={"1W": weeks})
    out = run(days, **common)
    assert [s["i"] for s in out["signals"]] == [5] and out["now"] == ["weekly losses (1/1)", "BUYS only (ladder)"]
    out = run(days, invPerdW=True, **common)
    assert [(s["i"], s["dir"]) for s in out["signals"]] == [(5, 1), (7, -1)]
    assert out["open_trades"][0]["dir"] == -1
    assert out["now"] == ["REVERSED (new losses 0/1): SELLS only", "opposite signal OFF until the next weekly signal"]
    out = run(days, invPerdW=True, invSinContra=False, **common)
    assert [t["reason"] for t in out["trades"]][-1] == "contra"


def test_daily_1_bias_from_yesterday():
    # A 30-minute buy on Wednesday 3 January opens only if Tuesday's daily candle left a Daily 1 BUY.
    chart = [Bar(_ny(3, 10, 0), 110, 110.5, 109.5, 110, 1), Bar(_ny(3, 10, 30), 110, 120.5, 109.5, 120, 1)]
    # (After a doji a red LLENA is an ENGULFING, which Daily 1 leaves out; so the sell follows a red candle.)
    for mon, tue, bias, opens in ((flat(0), green_full(1, 100), "BUYS only", True),
                                  (red_small(0, 111), red_full(1, 110), "SELLS only", False)):
        days = [mon, tue, flat(2, 110)]
        out = run(chart, tf="30m", usarIntra=False, usarD1=True, d1MA=0, d1R=False, other={"1D": days})
        assert bool(out["signals"]) == opens and out["now"] == [f"DAILY 1: {bias}"]
        assert out["daily1"] == {"bias": 1 if opens else -1}
    # Without Wednesday's daily candle in the feed yet, it is still Tuesday's bias.
    out = run(chart, tf="30m", usarIntra=False, usarD1=True, d1MA=0, d1R=False, other={"1D": [flat(0), green_full(1, 100)]})
    assert out["daily1"] == {"bias": 1}
    out = run(chart, tf="30m", usarIntra=False, usarD1=True, d1MA=0, other={"1D": [flat(0), flat(1), flat(2)]})
    assert out["signals"] == [] and out["now"] == ["DAILY 1: no signal yet, nothing"]


def test_months_rows():
    m = _months({202401: 5.0, 202402: -1.0, 202403: 0.0, 202404: -2.0, 202405: 3.0, 202406: -1.0})
    # March has no change: it neither breaks nor adds to the run of losing months.
    assert m == {"won": 2, "lost": 3, "max_won_row": 1, "max_lost_row": 2, "now_won_row": None, "now_lost_row": 1,
                 "losing_runs": [{"months": 1, "times": 1}, {"months": 2, "times": 1}]}


def _ny(d: int, hh: int, mm: int) -> int:
    return int(datetime(2024, 1, d, hh, mm, tzinfo=NY).timestamp())


def _level3(h0: Bar, prev: Bar | None = None, weekly_sell: bool = False, **changes):
    """A 15-minute chart on Tuesday 9 January 2024 at 10:00 and 10:15, under the 30-minute candle of
    9:30 (h0) and its doji of 10:00; the weekly level is a SELL from the week before, or nothing."""
    halves = ([prev] if prev else []) + [h0, Bar(_ny(9, 10, 0), 110, 110.5, 109.5, 110, 1)]
    week0 = bar(0, 100, 100.5, 89.5, 90) if weekly_sell else flat(0)
    weeks = [week0, flat(7, 110)]
    chart = [Bar(_ny(9, 10, 0), 110, 110.5, 109.5, 110, 1), Bar(_ny(9, 10, 15), 110, 110.5, 109.5, 110, 1)]
    out = run(chart, tf="15m", usarIntra=False, usarEscalera=True, filtro3=True,
              other={"1W": weeks, "30m": halves}, **changes)
    return out["ladder"][2]


def test_level_3_reads_the_30_minute_candle_with_its_own_settings():
    green = Bar(_ny(9, 9, 30), 100, 110.5, 99.5, 110, 1)  # LLENA up: level 3 has LLENA off by default
    assert _level3(green)["state"] == 0
    assert _level3(green, e3L=True) == {"tf": "30m", "active": True, "state": 1}
    # Only in favour of the active levels 1 and 2: a weekly SELL keeps the 30-minute BUY out.
    assert _level3(green, weekly_sell=True, e3L=True)["state"] == 0
    assert _level3(green, weekly_sell=True, e3L=True, e3Sigue=False)["state"] == 1
    # Its own moving average: the 30-minute BUY needs a close above it (no average yet -> no signal).
    assert _level3(green, e3L=True, e3MA=2)["state"] == 0


def test_level_3_can_skip_the_engulfing_of_the_930_candle():
    prev = Bar(_ny(8, 15, 30), 100, 101, 97, 98, 1)  # red, body 2
    eng = Bar(_ny(9, 9, 30), 98, 101.5, 97.5, 101, 1)  # green ENGULFING at 9:30
    assert _level3(eng, prev)["state"] == 1
    assert _level3(eng, prev, e3NoEng1=True)["state"] == 0


def test_minutes_levels_end_every_n_minutes_from_930():
    from app.strategy.swing import ends_higher_candle

    def ends(hh, mm, level, tf="15m"):
        return ends_higher_candle(Bar(_ny(9, hh, mm), 1, 1, 1, 1, 1), tf, level)

    assert ends(9, 45, "30m") and not ends(9, 30, "30m") and ends(10, 15, "30m")
    assert ends(10, 15, "1h") and not ends(9, 45, "1h") and ends(15, 45, "1h")  # 16:00 ends the session
    assert ends(9, 59, "30m", tf="1m") and not ends(9, 58, "30m", tf="1m")


# ---------- v9.38: HIJO 30 minutes -> NIETO 5 minutes ----------


def _hn_chart(*window: Bar) -> list[Bar]:
    """A 5-minute Tuesday morning: 9:30-9:55 climb 100 -> 110 in candles of no type (the 30-minute
    candle is a green LLENA, body 86%), then the given candles from 10:00 on."""
    out, step = [], 10 / 6
    for k in range(6):
        o = 100 + k * step
        out.append(Bar(_ny(9, 9, 30 + 5 * k), o, o + step * 1.5, o - step / 2, o + step, 1))
    return out + list(window)


def _m5(hh: int, mm: int, kind: str = "doji", base: float = 110.0) -> Bar:
    t = _ny(9, hh, mm)
    if kind == "up":  # LLENA up, body 90%
        return Bar(t, base, base + 10.5, base - 0.5, base + 10, 1)
    if kind == "down":
        return Bar(t, base, base + 0.5, base - 10.5, base - 10, 1)
    return Bar(t, base, base + 0.5, base - 0.5, base, 1)


def _hn_run(chart: list[Bar], **changes):
    halves = [Bar(_ny(9, 9, 30), 100, 110.8, 99.2, 110, 1), Bar(_ny(9, 10, 0), 110, 120.5, 99.5, 110, 1),
              Bar(_ny(9, 10, 30), 110, 120.5, 109.5, 120, 1)]
    other = {"30m": halves, "1W": [flat(0), flat(7, 110)], "1D": [flat(7), flat(8, 110)]}
    return run(chart, tf="5m", usarIntra=False, other=other,
               **({"hijoNieto": True, "nietoEstudio": False, "hMA": 0, "hLl": 85.0} | changes))


def test_hijo_nieto_settings():
    from app.strategy.swing import effective_inputs

    x = S.defaults()
    assert (x["hijoNieto"], x["nietoObj"], x["nietoStop"], x["nietoDias"], x["nieto1a"], x["nietoDaily"]) == \
        (False, 4, 4, 5, False, False)
    # The HIJO starts with the file's 30M-1 settings, not the level-3 ones.
    assert (x["hL"], x["hLl"], x["hFl"], x["hFlM"], x["hEn"], x["hnR"], x["hnV"], x["hNoEng1"], x["hMA"], x["hSoloW"]) == \
        (True, 70, 50, 3, 55, 3, 5, True, 8, True)
    assert (x["hObj"], x["hStop"], x["hVelas"], x["hMax"], x["hPerdW"], x["hContra"]) == (15, 13, 800, 30, 3, True)
    on = effective_inputs(x | {"hijoNieto": True}, "5m")
    # 5 market days of 6.5 hours on 5 minutes = 390 candles; the NIETO's own candle rules and no caps.
    assert (on["objPct"], on["stopPct"], on["maxVelas"], on["cierraMercado"]) == (4, 4, 390, True)
    assert (on["cuerpoLlena"], on["mechaFleco"], on["cuerpoEngulf"], on["noEngPrimera"], on["nVerdes"]) == (70, 50, 55, True, 5)
    assert (on["maxAbiertas"], on["cierraContra"], on["maCual"], on["maLen1"]) == (0, False, "MA1", 8)
    assert effective_inputs(x | {"hijoNieto": True}, "30m") == x | {"hijoNieto": True}  # 30 minutes and up: off
    assert S.needs("5m", x | {"hijoNieto": True}) == {"30m", "1W", "1D"}


def test_nieto_opens_only_in_the_30_minutes_after_a_hijo_signal_in_its_direction():
    chart = _hn_chart(_m5(10, 0), _m5(10, 5, "up"), _m5(10, 10, "down", 120), _m5(10, 15), _m5(10, 20), _m5(10, 25),
                      _m5(10, 30), _m5(10, 35, "up"))
    out = _hn_run(chart)
    assert [(s["i"], s["dir"]) for s in out["hijo"]["signals"]] == [(5, 1)]  # the 9:55 candle closes the HIJO's candle
    assert [(s["i"], s["dir"]) for s in out["signals"]] == [(7, 1)]
    # The sell in the window and the buy after it are kept out by the window.
    assert [(b["i"], b["why"]) for b in out["blocked"]] == [(8, ["HIJO window"]), (13, ["HIJO window"])]
    # 4% / 4%: the red candle takes the long's stop.
    assert [(t["reason"], t["ret_pct"]) for t in out["trades"]] == [("SL", -4.0)]
    assert out["now"] == ["outside the HIJO window: nothing"]
    # Off, the same chart trades both 5-minute candles (and the sell).
    assert len(_hn_run(chart, hijoNieto=False)["signals"]) == 3


def test_hijo_filters():
    chart = _hn_chart(_m5(10, 0), _m5(10, 5, "up"), _m5(10, 10), _m5(10, 15, "up"))
    assert [s["i"] for s in _hn_run(chart)["signals"]] == [7, 9]
    assert [s["i"] for s in _hn_run(chart, nieto1a=True)["signals"]] == [7]  # only the 1st entry per HIJO signal
    assert _hn_run(chart, hMA=8)["hijo"]["count"] == 0  # its moving average needs 8 candles
    assert _hn_run(chart, hL=False)["signals"] == []
    # The weekly filter: a weekly SELL keeps the HIJO's buy out, unless switched off.
    weeks = [bar(0, 100, 100.5, 89.5, 90), flat(7, 110)]
    other = {"30m": [Bar(_ny(9, 9, 30), 100, 110.8, 99.2, 110, 1)], "1W": weeks, "1D": [flat(7), flat(8, 110)]}
    base = dict(tf="5m", usarIntra=False, other=other, hijoNieto=True, nietoEstudio=False, hMA=0, hLl=85.0)
    assert run(chart, **base)["hijo"]["count"] == 0
    assert run(chart, **base, hSoloW=False)["hijo"]["count"] == 1

"""The strategy engine against hand-worked candle sequences (plan rule 5: strategy logic gets the
most tests). Names refer to docs/swing_diario_semanal_v9_35.pine."""
from datetime import datetime, timedelta, timezone

import pytest

from app.marketdata.base import Bar
from app.strategy import indicators
from app.strategy.base import InputError, StrategyData, check_inputs
from app.strategy.swing import INPUTS, NY, SwingStrategy, classify, group_inside, runs_in_a_row

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


def run(bars, tf="1D", closed=None, luck=False, other=None, **changes):
    """The engine with the script's defaults, except the RACHA, which is off unless a test turns it
    on, so the hand-made sequences below are only the candles they mean to be."""
    inputs = check_inputs(INPUTS, {"usarRacha": False} | changes)
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
    # "Repartido" with at most 10 open: each trade is a tenth of the account in the R column.
    assert r["total"]["wins"] == 1 and r["by_type"][0]["r_avg"] == pytest.approx(1.5)
    assert run(bars, usarIntra=False, normalCuenta="Cada operación su %")["results"]["by_type"][0]["r_avg"] == 15
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
    # Each entry counts as one operation (v9.16); "Repartido": the basket's % over the most entries.
    r = out["results"]
    assert r["total"]["losses"] == 2 and r["total"]["r_avg"] == pytest.approx(t["ret_pct"] / 5)
    own = 10000 * 0.02 * ((101 - 110) / 110 + (101 - 120) / 120)
    assert r["capital"]["final"] == pytest.approx(10000 + own)
    # Most entries:
    out = run(bars[:4], modoSenal=True, modoCesta=True, maxCesta=1)
    assert out["position"]["entries"] == 1 and out["now"] == ["basket full (1)"]


def test_basket_gap_past_the_stop_closes_at_the_open():
    bars = [flat(0), green_full(1, 100), green_full(2, 110), bar(3, 105, 106, 104, 105.5)]
    (t,) = run(bars, modoSenal=True, modoCesta=True, slPctSS=5)["trades"]
    assert t["reason"] == "SL" and t["exit_price"] == 105 and t["ret_pct"] == pytest.approx((105 - 115) / 115 * 100)
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
    # v9.35: Monday does not reset the counter again, so Monday's buy waits for the next weekly signal.
    assert [s["i"] for s in out["signals"]] == [1] and out["now"] == ["weekly losses (1/1)", "BUYS only (ladder)"]
    assert [s["i"] for s in run(days, maxPerdW=2, **common)["signals"]] == [1, 5]


def test_needs_only_what_is_used():
    inputs = check_inputs(INPUTS, {"usarEscalera": True, "filtro2": True})
    assert S.needs("1D", inputs) == {"1h", "1W"}  # path inside daily candles, and the weekly level
    assert S.needs("1W", check_inputs(INPUTS, {"tfIntra": "1D"})) == {"1D"}
    assert S.needs("1h", check_inputs(INPUTS, {})) == set()  # path on the chart's own timeframe


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
    assert check_inputs(INPUTS, {})["cuerpoLlena"] == 85
    assert check_inputs(INPUTS, {"cuerpoLlena": 70, "unknown": 1})["cuerpoLlena"] == 70
    with pytest.raises(InputError, match="at most 100"):
        check_inputs(INPUTS, {"cuerpoLlena": 120})
    with pytest.raises(InputError, match="whole number"):
        check_inputs(INPUTS, {"maxVelas": 2.5})
    with pytest.raises(InputError, match="HH:MM"):
        check_inputs(INPUTS, {"horaCierreSS": "25:00"})
    with pytest.raises(InputError, match="choose one"):
        check_inputs(INPUTS, {"maCual": "MA9"})
    assert check_inputs(INPUTS, {"horaCierreSS": "15:30"})["horaCierreSS"] == "15:30"
    # Settings saved under v9.8 still load: the old shared ladder thresholds are simply dropped.
    x = check_inputs(INPUTS, {"cuerpoLlenaW": 70.0, "objPct": 9.0})
    assert x["objPct"] == 9 and x["e1Ll"] == 85 and "cuerpoLlenaW" not in x


def test_script_defaults():
    x = S.defaults()
    assert (x["usarRacha"], x["nRojas"], x["nVerdes"], x["maxAbiertas"], x["normalCuenta"]) == (True, 3, 3, 10, "Repartido")
    assert (x["e1nV"], x["e2Fl"], x["e3Ll"], x["capIni"], x["csAncho"]) == (7, 70, 70, 10000, 1.33)


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

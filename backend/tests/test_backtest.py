"""Backtest (Stage 7): results rebuilt for a date range, money results, the option-price estimate,
option replays without look-ahead, and the endpoints."""
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from app import routes_market
from app.backtest import options, stats
from app.backtest import run as bt
from app.backtest.option_history import ModelOptionHistory, black_scholes, historical_vol, strike_step
from app.marketdata import service
from app.marketdata.base import Bar
from app.marketdata.bars import NY
from app.strategy.base import StrategyData
from app.strategy.swing import STRATEGIES
from tests.conftest import make_user, signed_in
from tests.fakes import FakeMarketData

D0 = datetime(2023, 1, 2, tzinfo=timezone.utc)
S = STRATEGIES["swing_v98"]


def day(i):
    return int((D0 + timedelta(days=i)).timestamp())


def daily(n):
    """Quiet candles with full green candles every 10 days and full red ones every 15, plus a wobble."""
    out, price = [], 100.0
    for i in range(n):
        w = 0.6 * math.sin(i / 3.0)
        if i % 10 == 5:
            b = Bar(day(i), price, price + 10.5, price - 0.5, price + 10, 1)
            price += 10
        elif i % 15 == 7:
            b = Bar(day(i), price, price + 0.5, price - 10.5, price - 10, 1)
            price -= 10
        else:
            b = Bar(day(i), price - 0.2 + w, price + 0.5 + w, price - 0.5 + w, price + 0.2 + w, 1)
        out.append(b)
    return out


BARS = daily(400)


def engine_run(inputs=None, bars=BARS):
    x = S.defaults() | (inputs or {})
    return S.run(StrategyData("TSLA", "1D", bars, len(bars), {}), x), x


# ---------- the script's tables for a range ----------


@pytest.mark.parametrize("inputs", [
    {}, {"objPct": 5.0, "stopPct": 4.0, "maxVelas": 6}, {"cierraMercado": False, "maxVelas": 4},
    {"modoSenal": True}, {"modoSenal": True, "tpPctSS": 8.0, "slPctSS": 5.0},
    {"modoSenal": True, "modoCesta": True, "maxCesta": 3},
])
def test_rebuilt_tables_equal_the_engine_over_the_whole_history(inputs):
    out, x = engine_run(inputs)
    mine = stats.script_results(out["trades"], out["entries"], BARS, len(BARS), x, None)
    eng = out["results"]
    for k in ("total", "years", "candles_measured", "max_win_streak", "max_loss_streak", "real_path_trades", "mode"):
        assert mine[k] == pytest.approx(eng[k]), k
    assert mine["avg_days"] == pytest.approx(eng["avg_days"]) and mine["avg_bars"] == pytest.approx(eng["avg_bars"])
    for a, b in zip(mine["by_type"], eng["by_type"]):
        assert a == pytest.approx(b), a["type"]


def test_a_range_counts_only_trades_opened_in_it():
    out, x = engine_run()
    start = day(200)
    mine = stats.script_results(out["trades"], out["entries"], BARS, len(BARS), x, start)
    expected = [t for t in out["trades"] if t["entry_time"] >= start and t["counted"]]
    assert mine["total"]["wins"] + mine["total"]["losses"] == len(expected) > 0
    assert mine["candles_measured"] == 200


# ---------- money ----------


def test_money_results_curve_drop_and_streaks():
    o = [stats.Outcome(1, 10, 100.0, 1), stats.Outcome(2, 20, -300.0, 2), stats.Outcome(3, 30, -100.0, 3),
         stats.Outcome(4, 40, 500.0, 4)]
    m = stats.money_results(o, 1000.0)
    assert m["total"] == 200 and m["total_pct"] == 20 and m["final_value"] == 1200
    assert [p["value"] for p in m["curve"]] == [1000, 1100, 800, 700, 1200]
    # From the 1,100 high to 700.
    assert m["max_drop"] == 400 and m["max_drop_pct"] == pytest.approx(400 / 1100 * 100)
    assert (m["wins"], m["losses"], m["max_win_streak"], m["max_loss_streak"]) == (2, 2, 1, 2)
    assert m["average_win"] == 300 and m["average_loss"] == -200 and m["avg_days"] == 2.5
    assert stats.money_results([], 1000)["trades"] == 0


# ---------- the option-price estimate ----------


def test_black_scholes_known_value_and_parity():
    c = black_scholes("call", 100, 100, 1.0, 0.2, 0.05)
    p = black_scholes("put", 100, 100, 1.0, 0.2, 0.05)
    assert c == pytest.approx(10.4506, abs=1e-3)
    assert c - p == pytest.approx(100 - 100 * math.exp(-0.05), abs=1e-9)
    assert black_scholes("put", 90, 100, 0, 0.3) == 10 and black_scholes("call", 90, 100, 0, 0.3) == 0


def test_volatility_and_strikes():
    flat = historical_vol([100.0] * 40)
    assert flat == 0.05  # never below the floor
    swing = historical_vol([100.0, 102.0] * 20)
    assert 0.2 < swing < 0.5
    assert strike_step(80) == 1 and strike_step(240) == 2.5 and strike_step(420) == 5


def test_model_quotes_use_only_the_past():
    closes = bt.daily_closes(BARS)
    h = ModelOptionHistory({"TSLA": closes})
    at = datetime(2023, 6, 1, 16, 0, tzinfo=NY)
    exps = h.expirations("TSLA", at)
    assert all(e.weekday() == 4 and e > at.date() for e in exps[:5])
    bid, ask = h.quote("TSLA", "call", h.strikes("TSLA", at, 150.0)[50], exps[3], at, 150.0)
    assert bid is not None and ask > bid
    # Changing prices after `at` does not change the quote.
    later = [c if c.day <= at.date() else type(c)(c.day, c.close * 3) for c in closes]
    assert ModelOptionHistory({"TSLA": later}).quote("TSLA", "call", h.strikes("TSLA", at, 150.0)[50], exps[3], at,
                                                      150.0) == (bid, ask)


def test_strike_distance_uses_only_trades_closed_before_the_signal():
    out, _ = engine_run()
    prior = options.PriorHistory(out["trades"], "1D")
    first_close = min(options.close_moment(t["exit_time"], "1D") for t in out["trades"] if t["counted"])
    assert prior.at(first_close).winners == 0 and prior.at(first_close).avg_days is None
    assert prior.at(first_close + timedelta(days=1000)).avg_days is not None


def replay(option, bars=BARS, inputs=None):
    out, x = engine_run(inputs, bars)
    closes = bt.daily_closes(bars)
    setup = bt.Setup(None, None, 100_000, 10_000, option, "bid_ask")
    return bt.run(S, StrategyData("TSLA", "1D", bars, len(bars), {}), x, setup, ModelOptionHistory({"TSLA": closes}), closes)


def priced(r, structure):
    return [o for t in r["trades"] for o in t["options"][structure] if "pnl" in o]


@pytest.mark.parametrize("structure", ["directional", "debit_spread", "credit_spread"])
def test_option_replay_for_each_structure(structure):
    r = replay(options.OptionSetup(structure=structure, risk_usd=1000, width_usd=10, strike_pct=2))
    assert set(r["columns"]) == {"stock", structure}
    col = r["columns"][structure]
    assert col["trades"] + r["skipped"][structure] == len(r["trades"]) > 0 and col["trades"] > 0
    rows = priced(r, structure)
    for o in rows:
        assert o["max_loss"] <= 1000
        assert o["strike_pct"] == pytest.approx(2, abs=2.5)  # $2.50 strikes near $100
        if structure == "directional":
            assert o["width"] is None and len(o["transactions"][0]["legs"]) == 1
        else:
            assert o["width"] == 10 and len(o["transactions"][0]["legs"]) == 2 and "spread" in o["description"]
        # The transactions add up to the result.
        assert sum(x["cash"] for x in o["transactions"]) == pytest.approx(o["pnl"], abs=0.02)
    # Every result is the stock-price trade too, at a fixed amount per trade.
    assert r["columns"]["stock"]["trades"] == len(r["trades"])
    assert r["trades"][0]["stock_pnl"] == pytest.approx(10_000 * r["trades"][0]["ret_pct"] / 100, abs=0.01)
    assert r["notes"]["options_source"] == "estimate" and r["luck"] is not None


def test_spread_legs_sit_where_the_setup_says():
    for structure, kind_buy, kind_sell in [("debit_spread", "call", "put"), ("credit_spread", "put", "call")]:
        r = replay(options.OptionSetup(structure=structure, width_usd=5, strike_pct=0, risk_usd=5000))
        assert priced(r, structure)
        for t in r["trades"]:
            for o in t["options"][structure]:
                if "pnl" not in o:
                    continue
                legs = o["transactions"][0]["legs"]
                strikes = {lg["action"]: float(lg["contract"].split()[4]) for lg in legs}
                kind = legs[0]["contract"].split()[-1]
                assert kind == (kind_buy if t["dir"] == 1 else kind_sell)
                # The other leg is further out of the money, the width away.
                sign = 1 if kind == "call" else -1
                placed, other = ((strikes["Buy to open"], strikes["Sell to open"]) if structure == "debit_spread"
                                 else (strikes["Sell to open"], strikes["Buy to open"]))
                assert (other - placed) * sign == pytest.approx(5)


def test_strike_by_delta():
    r = replay(options.OptionSetup(structure="directional", strike_by="delta", strike_delta=0.30, risk_usd=5000))
    rows = priced(r, "directional")
    assert rows and all(abs(o["delta"] - 0.30) < 0.08 for o in rows)
    assert all(o["strike_pct"] > 0 for o in rows)  # a 0.30 delta is out of the money


def test_fees_come_off_each_leg_both_ways():
    plain = priced(replay(options.OptionSetup(structure="credit_spread", width_usd=5)), "credit_spread")
    fees = priced(replay(options.OptionSetup(structure="credit_spread", width_usd=5, commission=0.65)), "credit_spread")
    a, b = plain[0], fees[0]
    assert b["fees"] == pytest.approx(0.65 * 4 * b["quantity"])
    assert a["pnl"] - b["pnl"] == pytest.approx(b["fees"], abs=0.02)


def test_positions_before_a_split_are_priced_in_that_days_terms(monkeypatch):
    from app import history
    split = date(2023, 9, 1)
    monkeypatch.setitem(history.SPLITS, "TSLA", [(split, 3.0)])
    r = replay(options.OptionSetup(structure="debit_spread", width_usd=10, risk_usd=5000))
    before = after = 0
    for t in r["trades"]:
        for o in t["options"]["debit_spread"]:
            if "pnl" not in o:
                continue
            opened = o["transactions"][0]
            entry_day = datetime.fromtimestamp(opened["time"], NY).date()
            f = 3.0 if entry_day < split else 1.0
            assert opened["underlying"] == pytest.approx(t["entry_price"] * f, rel=1e-6)
            assert o["width"] == pytest.approx(10, abs=2.5) and o["split_factor"] == (f if f != 1 else None)
            before += f == 3.0
            after += f == 1.0
    assert before and after


def test_implied_vol_and_delta_round_trip():
    from app.backtest.option_history import delta, implied_vol, strike_for_delta
    p = black_scholes("put", 100, 95, 0.1, 0.45)
    assert implied_vol("put", p, 100, 95, 0.1) == pytest.approx(0.45, abs=1e-4)
    k = strike_for_delta("call", 100, 0.25, 0.1, 0.5)
    assert delta("call", 100, k, 0.1, 0.5) == pytest.approx(0.25, abs=1e-6)
    k = strike_for_delta("put", 100, 0.25, 0.1, 0.5)
    assert delta("put", 100, k, 0.1, 0.5) == pytest.approx(-0.25, abs=1e-6)
    assert implied_vol("call", 0.0001, 100, 50, 0.1) is None  # below what it is worth now


def test_end_date_cuts_the_history():
    closes = bt.daily_closes(BARS)
    setup = bt.Setup(date(2023, 3, 1), date(2023, 6, 30), 100_000, 10_000, None, "bid_ask")
    r = bt.run(S, StrategyData("TSLA", "1D", BARS, len(BARS), {}), S.defaults(), setup, None, closes)
    lo, hi = bt.day_start(date(2023, 3, 1)), bt.day_start(date(2023, 7, 1))
    assert r["trades"] and all(lo <= t["entry_time"] and t["exit_time"] < hi for t in r["trades"])
    assert set(r["columns"]) == {"stock"}


# ---------- endpoints ----------


@pytest.fixture
def md(monkeypatch):
    fake = FakeMarketData(bars={("TSLA", "1D"): BARS, ("TSLA", "1h"): []})
    monkeypatch.setattr(routes_market, "providers", service.ProviderCache())
    monkeypatch.setattr(routes_market, "cache", service.TTLCache())
    monkeypatch.setattr(service, "build_provider", lambda provider, secret: fake)
    return fake


@pytest.fixture
def rafa():
    make_user("rafa@example.com", role="admin")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    return c


def body(**kw):
    return {"strategy": "swing_v98", "symbol": "TSLA", "timeframe": "1D", "inputs_source": "defaults"} | kw


def test_run_saves_and_reopens(md, rafa):
    r = rafa.post("/api/backtest/run", json=body(option={"structure": "debit_spread", "width_usd": 10}))
    assert r.status_code == 200, r.text
    run = r.json()
    assert set(run["summary"]) == {"stock", "debit_spread"} and run["setup"]["option"]["width_usd"] == 10
    assert run["result"]["results"]["mode"] == "target_stop" and run["setup"]["inputs"]["objPct"] == 15
    listed = rafa.get("/api/backtest/runs").json()
    assert [x["id"] for x in listed] == [run["id"]] and "result" not in listed[0]
    assert rafa.get(f"/api/backtest/runs/{run['id']}").json()["result"]["columns"]["stock"]["trades"] > 0
    assert rafa.delete(f"/api/backtest/runs/{run['id']}").status_code == 200
    assert rafa.get("/api/backtest/runs").json() == []


def test_pegged_inputs_and_given_inputs(md, rafa):
    r = rafa.post("/api/backtest/run", json=body(inputs_source="pegged"))
    assert r.status_code == 409 and "Nothing is pegged" in r.json()["detail"]
    rafa.put("/api/strategy/presets", json={"strategy": "swing_v98", "symbol": "TSLA", "timeframe": "1D",
                                            "inputs": {"objPct": 7.0}})
    r = rafa.post("/api/backtest/run", json=body(inputs_source="pegged"))
    assert r.status_code == 200 and r.json()["setup"]["inputs"]["objPct"] == 7
    r = rafa.post("/api/backtest/run", json=body(inputs_source="given", inputs={"objPct": 9.0}))
    assert r.json()["setup"]["inputs"]["objPct"] == 9 and set(r.json()["summary"]) == {"stock"}
    bad = rafa.post("/api/backtest/run", json=body(start="2024-01-01", end="2023-01-01"))
    assert bad.status_code == 422


def test_runs_are_private(md, rafa):
    run = rafa.post("/api/backtest/run", json=body()).json()
    make_user("other@example.com")
    other = signed_in("other@example.com")
    assert other.get("/api/backtest/runs").json() == []
    assert other.get(f"/api/backtest/runs/{run['id']}").status_code == 404
    assert other.delete(f"/api/backtest/runs/{run['id']}").status_code == 404

"""Master Chart endpoints: running the engine, pegging per symbol and timeframe, parity."""
from datetime import datetime, timedelta, timezone

import pytest

from app import routes_market
from app.marketdata import service
from app.marketdata.base import Bar
from app.strategy import parity
from app.strategy.swing import STRATEGIES
from tests.conftest import make_user, signed_in
from tests.fakes import FakeMarketData

D0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def day(i):
    return int((D0 + timedelta(days=i)).timestamp())


def daily(n=60):
    """A zig-zag of quiet candles with a full green candle every 10 days and a full red one every 15."""
    out = []
    price = 100.0
    for i in range(n):
        if i % 10 == 5:
            b = Bar(day(i), price, price + 10.5, price - 0.5, price + 10, 1)
            price += 10
        elif i % 15 == 7:
            b = Bar(day(i), price, price + 0.5, price - 10.5, price - 10, 1)
            price -= 10
        else:
            b = Bar(day(i), price - 0.2, price + 0.5, price - 0.5, price + 0.2, 1)
        out.append(b)
    return out


BARS = daily()


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    md = FakeMarketData(bars={("TSLA", "1D"): BARS, ("SPY", "1D"): BARS})
    monkeypatch.setattr(routes_market, "providers", service.ProviderCache())
    monkeypatch.setattr(routes_market, "cache", service.TTLCache())
    monkeypatch.setattr(service, "build_provider", lambda provider, secret: md)
    return md


@pytest.fixture
def rafa():
    make_user("rafa@example.com", role="admin")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    return c


def body(**kw):
    return {"strategy": "swing_v98", "symbol": "TSLA", "timeframe": "1D", "inputs": {}} | kw


def test_strategy_list():
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    (s,) = c.get("/api/strategy/strategies").json()
    assert s["id"] == "swing_v98" and s["version"] == "v9.37"
    keys = [i["key"] for i in s["inputs"]]
    assert "cuerpoLlena" in keys and "horaCierreSS" in keys and len(keys) == len(STRATEGIES["swing_v98"].inputs)


def test_run(rafa):
    # The quiet candles here are all green, so the RACHA (on by default) is switched off.
    r = rafa.post("/api/strategy/run", json=body(luck=True, inputs={"usarRacha": False}))
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["bars"]) == 60 and out["closed"] == 60  # old candles are all closed
    assert [s["dir"] for s in out["signals"]][:2] == [1, -1]
    assert out["inputs"]["cuerpoLlena"] == 85
    assert out["results"]["mode"] == "target_stop" and "luck" in out and "credit_spreads" in out


def test_run_refuses_bad_inputs(rafa):
    r = rafa.post("/api/strategy/run", json=body(inputs={"cuerpoLlena": 500}))
    assert r.status_code == 422 and "at most 100" in r.json()["detail"]
    assert rafa.post("/api/strategy/run", json=body(timeframe="2h")).status_code == 422


def test_run_needs_a_key():
    make_user("ana@example.com")
    ana = signed_in("ana@example.com")
    r = ana.post("/api/strategy/run", json=body())
    assert r.status_code == 409 and "Tradier key" in r.json()["detail"]


def test_state_round_trip(rafa):
    assert rafa.get("/api/strategy/state").json()["timeframe"] == "1D"
    rafa.put("/api/strategy/state", json=body(symbol="spy", timeframe="1W", inputs={"objPct": 20}))
    s = rafa.get("/api/strategy/state").json()
    assert s["symbol"] == "SPY" and s["timeframe"] == "1W" and s["inputs"]["objPct"] == 20
    assert s["inputs"]["stopPct"] == 13  # the rest at their defaults


def test_pegging_is_per_symbol_and_timeframe(rafa):
    rafa.put("/api/strategy/presets", json=body(inputs={"cuerpoLlena": 80}))
    rafa.put("/api/strategy/presets", json=body(symbol="SPY", inputs={"cuerpoLlena": 70}))
    rafa.put("/api/strategy/presets", json=body(timeframe="1W", inputs={"cuerpoLlena": 90}))
    p = rafa.put("/api/strategy/presets", json=body(inputs={"cuerpoLlena": 81})).json()  # re-peg replaces
    rows = rafa.get("/api/strategy/presets?strategy=swing_v98").json()
    assert [(r["symbol"], r["timeframe"], r["inputs"]["cuerpoLlena"]) for r in rows] == [
        ("SPY", "1D", 70), ("TSLA", "1D", 81), ("TSLA", "1W", 90)]
    assert rafa.delete(f"/api/strategy/presets/{p['id']}").status_code == 200
    assert len(rafa.get("/api/strategy/presets?strategy=swing_v98").json()) == 2


def pbody(**kw):
    """Parity always uses the script's default inputs, so none are sent."""
    return {"strategy": "swing_v98", "symbol": "TSLA", "timeframe": "1D"} | kw


def tv_csv(bars, flip_at=None, bump_at=None):
    """A TradingView-style export of the engine's own signals on these candles."""
    s = STRATEGIES["swing_v98"]
    from app.strategy.base import StrategyData

    if bump_at is not None:  # TradingView's prices differ on this candle, and so may its signal
        b = bars[bump_at]
        bars = [*bars[:bump_at], Bar(b.time, b.open, b.high, b.low, b.open + 0.01, b.volume), *bars[bump_at + 1:]]
    res = s.run(StrategyData("X", "1D", bars, len(bars)), s.defaults())
    ups = {x["i"] for x in res["signals"] if x["dir"] == 1}
    dns = {x["i"] for x in res["signals"] if x["dir"] == -1}
    lines = ["time,open,high,low,close,MA filtro,Bloqueada alcista,Bloqueada bajista,Señal alcista,Señal bajista"]
    for i, b in enumerate(bars):
        t = datetime.fromtimestamp(b.time, timezone.utc).strftime("%Y-%m-%dT14:30:00Z")
        o, h, lo, c = b.open, b.high, b.low, b.close
        up, dn = i in ups, i in dns
        if i == flip_at:
            up, dn = dn, up
        lines.append(f"{t},{o},{h},{lo},{c},NaN,NaN,NaN,{1 if up else 'NaN'},{1 if dn else 'NaN'}")
    return "\n".join(lines)


def test_parity_all_match(rafa):
    r = rafa.post("/api/strategy/parity", json=pbody(filename="NASDAQ_TSLA, 1D_ab12.csv", csv=tv_csv(BARS)))
    assert r.status_code == 200, r.text
    s = r.json()["summary"]
    assert s["logic_ok"] and s["differences"] == 0 and s["matched"] == s["tv_signals"] > 0


def test_parity_explains_price_differences(rafa):
    green = next(i for i, b in enumerate(BARS) if b.close - b.open > 5)
    r = rafa.post("/api/strategy/parity", json=pbody(csv=tv_csv(BARS, bump_at=green))).json()
    s = r["summary"]
    # TradingView's own prices give TradingView's signals: the translation is right...
    assert s["logic_ok"]
    # ...and the one difference is the candle whose prices differ.
    (d,) = r["detail"]["differences"]
    assert d["candle"] == "2024-01-06" and d["tradingview"] == 0 and d["ours"] == 1
    assert "prices differ" in d["why"]


def test_parity_flags_logic_differences(rafa):
    green = next(i for i, b in enumerate(BARS) if b.close - b.open > 5)
    r = rafa.post("/api/strategy/parity", json=pbody(csv=tv_csv(BARS, flip_at=green))).json()
    assert not r["summary"]["logic_ok"] and r["detail"]["logic_mismatches"][0]["candle"] == "2024-01-06"


def test_parity_bad_file(rafa):
    r = rafa.post("/api/strategy/parity", json=pbody(csv="time,open,high,low,close\n1,1,1,1,1"))
    assert r.status_code == 422 and "Señal alcista" in r.json()["detail"]


def test_parity_sign_off_and_isolation(rafa):
    cid = rafa.post("/api/strategy/parity", json=pbody(csv=tv_csv(BARS))).json()["id"]
    out = rafa.post(f"/api/strategy/parity/{cid}/sign-off", json={"note": "all match"}).json()
    assert out["signed_off_at"] and out["sign_off_note"] == "all match"
    assert len(rafa.get("/api/strategy/parity").json()) == 1
    make_user("ana@example.com")
    ana = signed_in("ana@example.com")
    assert ana.get("/api/strategy/parity").json() == []
    assert ana.get(f"/api/strategy/parity/{cid}").status_code == 404
    assert ana.post(f"/api/strategy/parity/{cid}/sign-off", json={}).status_code == 404
    pid = rafa.put("/api/strategy/presets", json=body()).json()["id"]
    assert ana.delete(f"/api/strategy/presets/{pid}").status_code == 404
    assert ana.get("/api/strategy/presets?strategy=swing_v98").json() == []


def test_filename_guess():
    assert parity.guess_from_filename("NASDAQ_TSLA, 1D_ab12.csv") == ("TSLA", "1D")
    assert parity.guess_from_filename("AMEX_SPY, 1W.csv") == ("SPY", "1W")
    assert parity.guess_from_filename("export.csv") == (None, None)


def test_tv_times_map_to_the_same_candles():
    assert parity.candle_key(datetime(2024, 1, 5, 14, 30, tzinfo=timezone.utc), "1D") == "2024-01-05"
    assert parity.candle_key(datetime(2024, 1, 5, 5, 0, tzinfo=timezone.utc), "1D") == "2024-01-05"  # 00:00 New York
    assert parity.candle_key(datetime(2024, 1, 10, 14, 30, tzinfo=timezone.utc), "1W") == "2024-01-08"


def test_closed_candles():
    from app.strategy.service import candle_closed

    fri = Bar(int(datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp()), 1, 1, 1, 1, 1)
    ny = __import__("zoneinfo").ZoneInfo("America/New_York")
    assert not candle_closed(fri, "1D", datetime(2026, 10, 2, 15, 59, tzinfo=ny))
    assert candle_closed(fri, "1D", datetime(2026, 10, 2, 16, 0, tzinfo=ny))
    mon = Bar(int(datetime(2026, 9, 28, tzinfo=timezone.utc).timestamp()), 1, 1, 1, 1, 1)
    assert not candle_closed(mon, "1W", datetime(2026, 10, 1, 12, 0, tzinfo=ny))
    assert candle_closed(mon, "1W", datetime(2026, 10, 2, 16, 1, tzinfo=ny))
    hr = Bar(int(datetime(2026, 10, 2, 15, 30, tzinfo=ny).timestamp()), 1, 1, 1, 1, 1)
    assert not candle_closed(hr, "1h", datetime(2026, 10, 2, 15, 59, tzinfo=ny))
    assert candle_closed(hr, "1h", datetime(2026, 10, 2, 16, 0, tzinfo=ny))  # the 30-minute last hour

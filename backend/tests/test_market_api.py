"""Market data endpoints and saved layouts, including per-user separation."""
import pytest
from sqlalchemy import select

from app import routes_market
from app.marketdata import service
from app.models import ApiKey
from tests.conftest import make_user, signed_in
from tests.fakes import FakeMarketData, quote


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch):
    monkeypatch.setattr(routes_market, "providers", service.ProviderCache())
    monkeypatch.setattr(routes_market, "cache", service.TTLCache())


@pytest.fixture
def fake_providers(monkeypatch):
    """Each user's saved key becomes a FakeMarketData labelled with that key's secret."""
    built = []

    def build(provider, secret):
        md = FakeMarketData(realtime=(provider == "tradier"), label=secret,
                            quotes={"SPY": quote("SPY", 600, 590), "TSLA": quote("TSLA", 300, 310)})
        built.append(md)
        return md

    monkeypatch.setattr(service, "build_provider", build)
    return built


def test_without_key_screens_explain_what_to_do(fake_providers):
    make_user("ana@example.com")
    c = signed_in("ana@example.com")
    for path in ("/api/market/candles?symbol=SPY&tf=1D", "/api/market/watchlist", "/api/market/quote?symbol=SPY"):
        r = c.get(path)
        assert r.status_code == 409
        assert "Add a Tradier key" in r.json()["detail"]
    s = c.get("/api/market/status").json()
    assert s["provider"] is None and "Add a Tradier key" in s["detail"]


def test_candles_and_checks(fake_providers):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    r = c.get("/api/market/candles?symbol=spy&tf=1h")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "SPY" and body["realtime"] is True and len(body["bars"]) == 2
    assert c.get("/api/market/candles?symbol=SPY&tf=2h").status_code == 422
    assert c.get("/api/market/candles?symbol=bad%20sym&tf=1D").status_code == 422
    r = c.get("/api/market/candles?symbol=ERR&tf=1D")
    assert r.status_code == 502 and "Tradier" in r.json()["detail"]


def test_candles_are_cached_briefly(fake_providers):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    c.get("/api/market/candles?symbol=SPY&tf=1D")
    c.get("/api/market/candles?symbol=SPY&tf=1D")
    assert fake_providers[0].calls.count(("candles", "SPY", "1D")) == 1


def test_quote_and_unknown_symbol(fake_providers):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    assert c.get("/api/market/quote?symbol=SPY").json()["last"] == 600
    assert c.get("/api/market/quote?symbol=ZZZZ").status_code == 404


def test_watchlist_uses_saved_symbols(fake_providers):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    c.patch("/api/me/settings", json={"watchlist": {"symbols": ["TSLA", "SPY"]}})
    rows = c.get("/api/market/watchlist").json()["rows"]
    assert [r["symbol"] for r in rows] == ["TSLA", "SPY"]
    assert rows[0]["change"]["1D"] == pytest.approx((300 - 310) / 310 * 100)


def test_status_and_test_button(fake_providers):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier_sandbox", json={"secret": "rafa-paper-key-0002"})
    s = c.get("/api/market/status").json()
    assert s["provider"] == "tradier_sandbox" and s["realtime"] is False and s["clock"]["state"] == "open"
    msg = c.post("/api/market/test").json()["message"]
    assert "delayed 15 minutes" in msg
    # A live key wins over a practice key.
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    assert c.get("/api/market/status").json()["realtime"] is True


def test_each_user_uses_only_their_own_key(fake_providers):
    make_user("rafa@example.com")
    make_user("ana@example.com")
    rafa, ana = signed_in("rafa@example.com"), signed_in("ana@example.com")
    rafa.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    rafa.get("/api/market/candles?symbol=SPY&tf=1D")
    # Ana has no key: she does not get Rafa's data, cached or otherwise.
    assert ana.get("/api/market/candles?symbol=SPY&tf=1D").status_code == 409
    ana.put("/api/me/keys/tradier", json={"secret": "ana-live-key-0009"})
    ana.get("/api/market/candles?symbol=SPY&tf=1D")
    labels = {md.label for md in fake_providers}
    assert labels == {"rafa-live-key-0001", "ana-live-key-0009"}
    by_label = {md.label: md for md in fake_providers}
    # Ana's request went through Ana's own key, not Rafa's cached result.
    assert ("candles", "SPY", "1D") in by_label["ana-live-key-0009"].calls


def test_replacing_or_deleting_key_takes_effect(fake_providers, db):
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    c.get("/api/market/quote?symbol=SPY")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0002"})
    c.get("/api/market/quote?symbol=SPY")
    assert [md.label for md in fake_providers] == ["rafa-live-key-0001", "rafa-live-key-0002"]
    c.delete("/api/me/keys/tradier")
    assert c.get("/api/market/quote?symbol=SPY").status_code == 409


def test_market_endpoints_need_sign_in():
    from tests.conftest import client

    for path in ("/api/market/status", "/api/market/candles?symbol=SPY&tf=1D", "/api/market/watchlist",
                 "/api/layouts/charts", "/api/layouts/dashboard"):
        assert client().get(path).status_code == 401


# ---------- layouts ----------


def test_chart_layout_defaults_save_and_validate():
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    c.patch("/api/me/settings", json={"watchlist": {"default_ticker": "QQQ"}})
    layout = c.get("/api/layouts/charts").json()
    assert len(layout["panes"]) == 4 and layout["panes"][0]["symbol"] == "QQQ"
    layout["panes"][2] = {"symbol": "nvda", "timeframe": "5m"}
    assert c.put("/api/layouts/charts", json=layout).json()["panes"][2] == {"symbol": "NVDA", "timeframe": "5m"}
    assert c.get("/api/layouts/charts").json()["panes"][2]["symbol"] == "NVDA"
    bad = {"panes": layout["panes"][:3]}
    assert c.put("/api/layouts/charts", json=bad).status_code == 422
    bad = {"panes": [*layout["panes"][:3], {"symbol": "SPY", "timeframe": "2h"}]}
    assert c.put("/api/layouts/charts", json=bad).status_code == 422


def test_dashboard_layout_save_reset_and_rules():
    make_user("rafa@example.com")
    c = signed_in("rafa@example.com")
    tiles = c.get("/api/layouts/dashboard").json()["tiles"]
    assert [t["kind"] for t in tiles] == ["totals", "watchlist", "chart", "trades"]
    tiles = [t for t in tiles if t["kind"] != "trades"]
    tiles[1]["x"], tiles[1]["w"] = 7, 5
    assert c.put("/api/layouts/dashboard", json={"tiles": tiles}).status_code == 200
    assert len(c.get("/api/layouts/dashboard").json()["tiles"]) == 3
    # Does not fit the 12-column grid.
    off = [dict(tiles[1], x=10, w=5)]
    assert c.put("/api/layouts/dashboard", json={"tiles": off}).status_code == 422
    # Duplicate ids, chart without symbol.
    assert c.put("/api/layouts/dashboard", json={"tiles": [tiles[0], tiles[0]]}).status_code == 422
    nosym = [dict(tiles[2], symbol=None)]
    assert c.put("/api/layouts/dashboard", json={"tiles": nosym}).status_code == 422
    assert len(c.delete("/api/layouts/dashboard").json()["tiles"]) == 4


def test_layouts_are_per_user():
    make_user("rafa@example.com")
    make_user("ana@example.com")
    rafa, ana = signed_in("rafa@example.com"), signed_in("ana@example.com")
    layout = rafa.get("/api/layouts/charts").json()
    layout["panes"][0]["symbol"] = "AMZN"
    rafa.put("/api/layouts/charts", json=layout)
    rafa.put("/api/layouts/dashboard", json={"tiles": []})
    assert ana.get("/api/layouts/charts").json()["panes"][0]["symbol"] != "AMZN"
    assert len(ana.get("/api/layouts/dashboard").json()["tiles"]) == 4

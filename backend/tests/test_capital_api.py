"""Capital Tracking endpoints: positions, buys and sells, deposits, valuation, snapshots."""
import asyncio
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app import capital, routes_market, snapshots
from app.marketdata import service
from app.marketdata.bars import NY
from app.marketdata.base import Quote
from app.models import CapitalSnapshot
from tests.conftest import make_user, signed_in
from tests.fakes import FakeMarketData

FUTURE = (date.today() + timedelta(days=120)).isoformat()
OCC = "TSLA" + date.fromisoformat(FUTURE).strftime("%y%m%d") + "C00450000"


def option_quote(bid, ask, last=None):
    return Quote(symbol=OCC, last=last, bid=bid, ask=ask)


QUOTES = {"TSLA": Quote("TSLA", last=460.0, prev_close=455.0), OCC: option_quote(6.40, 6.60, 6.0),
          "SPY": Quote("SPY", last=600.0)}


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(routes_market, "providers", service.ProviderCache())
    monkeypatch.setattr(service, "build_provider", lambda provider, secret: FakeMarketData(quotes=QUOTES))


@pytest.fixture
def rafa():
    make_user("rafa@example.com", role="admin")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    return c


def tsla_call(**kw):
    body = {"kind": "option", "symbol": "tsla", "option_type": "call", "strike": 450, "expiration": FUTURE,
            "quantity": 2, "price": 5.00, "fees": 1.30, "day": "2026-09-01"}
    return body | kw


def test_add_option_position_and_value_it_at_mid(rafa):
    rafa.post("/api/flows", json={"account": "long_term", "kind": "deposit", "amount": 10000, "day": "2026-08-01"})
    r = rafa.post("/api/capital/positions", json=tsla_call())
    assert r.status_code == 200, r.text
    body = r.json()
    (pos,) = body["open"]
    assert pos["symbol"] == "TSLA" and pos["label"].startswith("TSLA 450 call")
    assert pos["quantity"] == 2
    assert pos["cost"] == 1001.30
    assert pos["average_price"] == 5.0065
    assert pos["price"] == 6.50 and pos["price_source"] == "mid"
    assert pos["value"] == 1300.00
    assert pos["gain"] == 298.70
    t = body["totals"]
    assert t["put_in"] == 10000 and t["cash"] == 8998.70
    assert t["total"] == 10298.70 and t["gain"] == 298.70
    assert pos["pct_of_capital"] == pytest.approx(1300 / 10298.70 * 100, abs=1e-3)
    assert body["prices"]["available"] is True


def test_buying_the_same_contract_again_averages_in(rafa):
    rafa.post("/api/capital/positions", json=tsla_call(quantity=1, price=4, fees=0))
    body = rafa.post("/api/capital/positions", json=tsla_call(quantity=3, price=6, fees=0, note="added")).json()
    (pos,) = body["open"]
    assert pos["quantity"] == 4 and pos["average_price"] == 5.5
    assert [t["note"] for t in pos["trades"]] == ["", "added"]


def test_sell_part_then_all(rafa):
    pid = rafa.post("/api/capital/positions", json=tsla_call(fees=0)).json()["open"][0]["id"]
    r = rafa.post(f"/api/capital/positions/{pid}/trades",
                  json={"side": "sell", "quantity": 1, "price": 7, "fees": 0.65, "day": "2026-09-10"})
    assert r.status_code == 200
    (pos,) = r.json()["open"]
    assert pos["quantity"] == 1 and pos["realized"] == 199.35
    r = rafa.post(f"/api/capital/positions/{pid}/trades",
                  json={"side": "sell", "quantity": 1, "price": 3, "fees": 0, "day": "2026-09-11"})
    body = r.json()
    assert body["open"] == []
    (closed,) = body["closed"]
    assert closed["realized"] == -0.65 and closed["closed"] == "2026-09-11"
    assert body["totals"]["gain"] == pytest.approx(-0.65)


def test_overselling_is_refused(rafa):
    pid = rafa.post("/api/capital/positions", json=tsla_call()).json()["open"][0]["id"]
    r = rafa.post(f"/api/capital/positions/{pid}/trades",
                  json={"side": "sell", "quantity": 3, "price": 7, "day": "2026-09-10"})
    assert r.status_code == 409 and "only 2 was held" in r.json()["detail"]


def test_deleting_a_buy_that_a_sale_depends_on_is_refused(rafa):
    pid = rafa.post("/api/capital/positions", json=tsla_call()).json()["open"][0]["id"]
    body = rafa.post(f"/api/capital/positions/{pid}/trades",
                     json={"side": "sell", "quantity": 1, "price": 7, "day": "2026-09-10"}).json()
    buy_id, sell_id = [t["id"] for t in body["open"][0]["trades"]]
    assert rafa.delete(f"/api/capital/trades/{buy_id}").status_code == 409
    assert rafa.delete(f"/api/capital/trades/{sell_id}").status_code == 200
    body = rafa.delete(f"/api/capital/trades/{buy_id}").json()
    assert body["open"] == [] and body["closed"] == []  # the last trade takes the position with it


def test_edit_a_trade(rafa):
    pos = rafa.post("/api/capital/positions", json=tsla_call(fees=0)).json()["open"][0]
    tid = pos["trades"][0]["id"]
    r = rafa.put(f"/api/capital/trades/{tid}", json={"side": "buy", "quantity": 3, "price": 4, "fees": 0,
                                                     "day": "2026-09-02", "note": "fixed"})
    (pos,) = r.json()["open"]
    assert pos["quantity"] == 3 and pos["cost"] == 1200


def test_stock_position(rafa):
    body = rafa.post("/api/capital/positions", json={"kind": "stock", "symbol": "SPY", "quantity": 10, "price": 550,
                                                     "day": "2026-09-01", "option_type": "put"}).json()
    (pos,) = body["open"]
    assert pos["option_type"] is None and pos["label"] == "SPY shares"
    assert pos["value"] == 6000 and pos["gain"] == 500 and pos["price_source"] == "last"


@pytest.mark.parametrize("change,needle", [
    ({"strike": None}, "an option needs"),
    ({"quantity": 0}, "Quantity"),
    ({"price": -1}, "Price"),
    ({"symbol": "not a symbol"}, "not a valid symbol"),
    ({"day": (date.today() + timedelta(days=5)).isoformat()}, "future"),
])
def test_bad_input_is_refused(rafa, change, needle):
    r = rafa.post("/api/capital/positions", json=tsla_call(**change))
    assert r.status_code == 422 and needle in r.json()["detail"]


def test_without_a_key_positions_count_at_cost():
    make_user("ana@example.com")
    ana = signed_in("ana@example.com")
    body = ana.post("/api/capital/positions", json=tsla_call(fees=0)).json()
    (pos,) = body["open"]
    assert pos["price"] is None and pos["value"] == 1000 and pos["gain"] is None
    assert body["totals"]["estimated"] is True
    assert "Add a Tradier key" in body["prices"]["detail"]


def test_expired_option_is_valued_at_what_it_is_in_the_money(rafa):
    body = rafa.post("/api/capital/positions", json=tsla_call(expiration="2026-09-18", fees=0)).json()
    (pos,) = body["open"]
    assert pos["expired"] is True
    assert pos["price_source"] == "intrinsic" and pos["price"] == 10  # TSLA 460 vs 450 strike


def test_flows_listed_and_deleted(rafa):
    f = rafa.post("/api/flows", json={"account": "long_term", "kind": "withdrawal", "amount": 250.5,
                                      "day": "2026-09-01", "note": "rent"}).json()
    rafa.post("/api/flows", json={"account": "short_term", "kind": "deposit", "amount": 5000, "day": "2026-09-01"})
    assert [x["amount"] for x in rafa.get("/api/flows?account=long_term").json()] == [250.5]
    assert rafa.get("/api/capital").json()["totals"]["put_in"] == -250.5
    assert rafa.delete(f"/api/flows/{f['id']}").status_code == 200
    assert rafa.get("/api/flows?account=long_term").json() == []
    assert rafa.post("/api/flows", json={"account": "long_term", "kind": "deposit", "amount": 0,
                                         "day": "2026-09-01"}).status_code == 422


def test_delete_position(rafa):
    pid = rafa.post("/api/capital/positions", json=tsla_call()).json()["open"][0]["id"]
    assert rafa.delete(f"/api/capital/positions/{pid}").json()["open"] == []
    assert rafa.delete(f"/api/capital/positions/{pid}").status_code == 404


def test_snapshot_job(rafa, engine):
    rafa.post("/api/flows", json={"account": "long_term", "kind": "deposit", "amount": 10000, "day": "2026-08-01"})
    rafa.post("/api/capital/positions", json=tsla_call(fees=0))
    friday_after_close = datetime(2026, 10, 2, 16, 30, tzinfo=NY)
    assert snapshots.due(datetime(2026, 10, 2, 15, 0, tzinfo=NY)) is None
    assert snapshots.due(datetime(2026, 10, 3, 17, 0, tzinfo=NY)) is None  # Saturday
    providers = service.ProviderCache()
    assert asyncio.run(snapshots.run_once(engine, providers, friday_after_close)) == 1
    assert asyncio.run(snapshots.run_once(engine, providers, friday_after_close)) == 0  # once a day
    with Session(engine) as db:
        (snap,) = db.query(CapitalSnapshot).all()
        assert snap.day == date(2026, 10, 2)
        assert float(snap.total) == 10300 and float(snap.put_in) == 10000 and not snap.estimated
    hist = rafa.get("/api/capital/history").json()
    assert hist["snapshots"] == [{"day": "2026-10-02", "total": 10300.0, "put_in": 10000.0, "estimated": False}]
    assert len(hist["flows"]) == 1


def test_dashboard_totals(rafa):
    rafa.post("/api/flows", json={"account": "long_term", "kind": "deposit", "amount": 10000, "day": "2026-08-01"})
    rafa.post("/api/flows", json={"account": "short_term", "kind": "deposit", "amount": 3000, "day": "2026-08-01"})
    t = rafa.get("/api/totals").json()
    assert t["long_term"]["total"] == 10000 and t["long_term"]["has_records"]
    assert t["short_term"]["value"] == 3000 and t["short_term"]["has_records"]


def test_holding_helper_matches_api(rafa, db):
    rafa.post("/api/capital/positions", json=tsla_call())
    (p,) = capital.positions(db, 1)
    assert capital.holding(p, capital.trades_by_position(db, 1)[p.id]).quantity == 2


def test_expired_options_are_not_quoted():
    from app.models import LongTermPosition
    live = LongTermPosition(kind="option", symbol="TSLA", option_type="call", strike=450,
                            expiration=date(2026, 12, 18))
    gone = LongTermPosition(kind="option", symbol="SPY", option_type="put", strike=500,
                            expiration=date(2026, 9, 18))
    stock = LongTermPosition(kind="stock", symbol="TSLA")
    assert capital.quote_symbols([live, gone, stock], date(2026, 10, 4)) == ["TSLA", "TSLA261218C00450000", "SPY"]

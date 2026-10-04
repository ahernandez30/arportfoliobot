"""Paper trading engine and Live Trader endpoints (plan sections 8 and 10)."""
import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import paper, paper_worker, routes_market, routes_paper
from app.broker.base import OptionOrder
from app.broker.paper import PaperBroker
from app.marketdata import service
from app.marketdata.bars import NY
from app.marketdata.base import Quote
from app.models import PaperEvent, PaperOrder
from app.paper import PaperError
from app.paper_rules import Book
from tests.conftest import make_user, signed_in
from tests.fakes import FakeMarketData, closes_for

EXP = date.today() + timedelta(days=40)
OCC = "SPY" + EXP.strftime("%y%m%d") + "C00600000"
MARKET_OPEN = datetime.combine(date.today(), datetime.min.time(), tzinfo=NY).replace(hour=11)
# A weekday at 11:00 for worker runs (the fake clock always says "open").
while MARKET_OPEN.weekday() >= 5:
    MARKET_OPEN -= timedelta(days=1)


class World:
    """One fake provider shared by every user key, with prices the test can move."""

    def __init__(self):
        self.md = FakeMarketData(quotes={"SPY": Quote("SPY", last=601.0)})
        self.open = True
        self.quote(2.10, 2.30)

    def quote(self, bid, ask):
        self.md._quotes[OCC] = Quote(OCC, bid=bid, ask=ask, last=(bid + ask) / 2)


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(routes_market, "providers", service.ProviderCache())
    monkeypatch.setattr(routes_market, "cache", service.TTLCache())
    monkeypatch.setattr(service, "build_provider", lambda provider, secret: w.md)

    async def clock_open(db, user, md):
        return w.open

    monkeypatch.setattr(routes_paper, "_clock_open", clock_open)
    return w


@pytest.fixture
def rafa(world):
    make_user("rafa@example.com", role="admin")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    return c


def ticket(**kw):
    return {"symbol": "SPY", "option_type": "call", "strike": 600, "expiration": EXP.isoformat(), "quantity": 2,
            "limit_price": 2.30, "take_profit_pct": 30, "stop_loss_pct": 20} | kw


def run_worker(engine, now=None):
    asyncio.run(paper_worker.run_once(engine, service.ProviderCache(), paper_worker.ClockCache(), now or MARKET_OPEN))


def test_review_shows_cost_target_and_stop(rafa):
    r = rafa.post("/api/paper/orders/review", json=ticket())
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["max_cost"] == 460 and v["fill_now"] and v["fill_price"] == 2.30
    assert v["take_profit_price"] == 2.99 and v["stop_loss_price"] == 1.84
    assert v["problem"] is None


def test_order_fills_at_the_ask_when_market_open(rafa):
    body = rafa.post("/api/paper/orders", json=ticket(limit_price=2.50)).json()
    assert body["order"]["status"] == "filled" and body["order"]["fill_price"] == 2.30
    (pos,) = body["positions"]
    assert pos["entry_price"] == 2.30 and pos["quantity"] == 2
    assert pos["take_profit_price"] == 2.99 and pos["stop_loss_price"] == 1.84
    assert pos["price"] == 2.10 and pos["pl"] == -40  # valued at what it would sell for (the bid)
    assert body["account"]["cash"] == 100_000 - 460
    assert body["account"]["total"] == 100_000 - 460 + 420


def test_order_waits_outside_market_hours_and_worker_fills_it(rafa, world, engine):
    world.open = False
    body = rafa.post("/api/paper/orders", json=ticket(limit_price=2.20)).json()
    assert body["order"]["status"] == "working"
    assert body["account"]["reserved"] == 440 and body["account"]["free_cash"] == 100_000 - 440
    run_worker(engine, MARKET_OPEN.replace(hour=8))  # before the open: waits
    assert rafa.get("/api/paper").json()["orders"]
    run_worker(engine)  # open, but ask 2.30 is above the 2.20 limit
    assert rafa.get("/api/paper").json()["orders"]
    world.quote(2.05, 2.15)
    run_worker(engine)
    body = rafa.get("/api/paper").json()
    assert body["orders"] == [] and body["positions"][0]["entry_price"] == 2.15


def test_target_reached_closes_and_lands_in_account_manager(rafa, world, engine):
    rafa.post("/api/paper/orders", json=ticket())
    world.quote(2.95, 3.05)
    run_worker(engine)
    assert len(rafa.get("/api/paper").json()["positions"]) == 1  # bid 2.95 is under the 2.99 target
    world.quote(3.00, 3.10)
    run_worker(engine)
    body = rafa.get("/api/paper").json()
    assert body["positions"] == []
    assert body["account"]["cash"] == 100_000 - 460 + 600
    trades = rafa.get("/api/trades?mode=paper&period=all").json()
    (t,) = trades["trades"]
    assert t["close_reason"] == "take_profit" and t["result"] == 140 and t["exit_price"] == 3.00
    assert t["editable"] is False and t["source"] == "manual"
    assert trades["account_value"] == 100_140
    assert rafa.get("/api/trades?mode=real&period=all").json()["trades"] == []  # never mixed


def test_stop_loss(rafa, world, engine):
    rafa.post("/api/paper/orders", json=ticket())
    world.quote(1.80, 1.95)
    run_worker(engine)
    (t,) = rafa.get("/api/trades?mode=paper&period=all").json()["trades"]
    assert t["close_reason"] == "stop_loss" and t["result"] == -100


def test_one_bad_tick_does_not_stop_out(rafa, world, engine):
    rafa.post("/api/paper/orders", json=ticket())
    world.quote(0, 0)
    run_worker(engine)
    assert len(rafa.get("/api/paper").json()["positions"]) == 1


def test_close_button_and_partial_close(rafa, world):
    pid = rafa.post("/api/paper/orders", json=ticket(quantity=3)).json()["positions"][0]["id"]
    world.quote(2.50, 2.60)
    body = rafa.post(f"/api/paper/positions/{pid}/close", json={"quantity": 1}).json()
    assert body["positions"][0]["quantity"] == 2
    body = rafa.post(f"/api/paper/positions/{pid}/close", json={}).json()
    assert body["positions"] == []
    trades = rafa.get("/api/trades?mode=paper&period=all").json()["trades"]
    assert sorted(t["quantity"] for t in trades) == [1, 2]
    assert all(t["close_reason"] == "manual" and t["exit_price"] == 2.50 for t in trades)


def test_sell_limit_waits_then_target_replaces_it(rafa, world, engine):
    pid = rafa.post("/api/paper/orders", json=ticket()).json()["positions"][0]["id"]
    body = rafa.post(f"/api/paper/positions/{pid}/close", json={"limit_price": 5}).json()
    assert body["orders"][0]["side"] == "sell"
    r = rafa.post(f"/api/paper/positions/{pid}/close", json={"quantity": 1, "limit_price": 6})
    assert r.status_code == 409  # all contracts already have a closing order
    world.quote(3.00, 3.10)
    run_worker(engine)
    body = rafa.get("/api/paper").json()
    assert body["positions"] == [] and body["orders"] == []


def test_mid_fill_rule(rafa):
    rafa.patch("/api/me/settings", json={"paper": {"fill_rule": "mid"}})
    body = rafa.post("/api/paper/orders", json=ticket()).json()
    assert body["order"]["fill_price"] == 2.20


@pytest.mark.parametrize("change,settings,needle", [
    ({"quantity": 30}, {}, "largest order"),
    ({"quantity": 1}, {"paper": {"starting_balance": 1000}}, None),
])
def test_limits(rafa, change, settings, needle):
    if settings:
        rafa.patch("/api/me/settings", json=settings)
    r = rafa.post("/api/paper/orders", json=ticket(**change))
    if needle:
        assert r.status_code == 409 and needle in r.json()["detail"]
    else:
        assert r.status_code == 200


def test_refusals_are_logged_and_cash_is_checked(rafa, engine):
    rafa.patch("/api/me/settings", json={"trading": {"max_order_usd": 1_000_000}})
    r = rafa.post("/api/paper/orders", json=ticket(quantity=500, limit_price=2.30))  # 115,000
    assert r.status_code == 409 and "Not enough paper cash" in r.json()["detail"]
    events = rafa.get("/api/paper/events").json()
    assert any(e["event"] == "order_refused" for e in events)


def test_daily_loss_limit(rafa, world):
    rafa.patch("/api/me/settings", json={"trading": {"max_daily_loss_usd": 100}})
    pid = rafa.post("/api/paper/orders", json=ticket()).json()["positions"][0]["id"]
    world.quote(1.85, 1.95)
    rafa.post(f"/api/paper/positions/{pid}/close", json={})  # loses 90
    assert rafa.post("/api/paper/orders", json=ticket(quantity=1)).status_code == 200
    pid = rafa.get("/api/paper").json()["positions"][0]["id"]
    world.quote(1.85, 1.95)
    rafa.post(f"/api/paper/positions/{pid}/close", json={})  # loses 10 more: 100 today
    r = rafa.post("/api/paper/orders", json=ticket(quantity=1, limit_price=2))
    assert r.status_code == 409 and "daily limit" in r.json()["detail"]


def test_stop_all_trading(rafa, world, engine):
    rafa.patch("/api/me/settings", json={"trading": {"auto_trading": "paper"}})
    world.open = False
    rafa.post("/api/paper/orders", json=ticket())
    body = rafa.post("/api/paper/stop-all").json()
    assert body["orders"] == [] and body["controls"]["halted"]
    assert body["controls"]["auto_trading"] == "off"
    assert body["recent"][0]["status"] == "cancelled"
    r = rafa.post("/api/paper/orders", json=ticket())
    assert r.status_code == 409 and "stopped" in r.json()["detail"]
    # Survives a restart: it is stored, not held in memory.
    with Session(engine) as db:
        assert paper.controls(db, 1).halted
    assert not rafa.post("/api/paper/resume").json()["controls"]["halted"]
    assert rafa.post("/api/paper/orders", json=ticket()).status_code == 200


def test_pause_automatic_trading(rafa):
    assert rafa.post("/api/paper/auto-pause", json={"paused": True}).json()["controls"]["auto_paused"]
    assert not rafa.post("/api/paper/auto-pause", json={"paused": False}).json()["controls"]["auto_paused"]


def test_expired_position_settles_at_official_close(rafa, world, engine, monkeypatch):
    rafa.post("/api/paper/orders", json=ticket())
    world.md._closes["SPY"] = closes_for([(EXP.isoformat(), 603.25)])
    run_worker(engine, datetime.combine(EXP, datetime.min.time(), tzinfo=NY).replace(hour=15))
    assert rafa.get("/api/paper").json()["positions"]  # not yet: 16:00 has not passed
    run_worker(engine, datetime.combine(EXP, datetime.min.time(), tzinfo=NY).replace(hour=16, minute=5))
    assert rafa.get("/api/paper").json()["positions"] == []
    (t,) = rafa.get("/api/trades?mode=paper&period=all").json()["trades"]
    assert t["close_reason"] == "expired" and t["exit_price"] == 3.25 and t["result"] == 190


def test_reset(rafa, world):
    rafa.post("/api/paper/orders", json=ticket())
    world.open = False
    rafa.post("/api/paper/orders", json=ticket(limit_price=1))
    rafa.patch("/api/me/settings", json={"paper": {"starting_balance": 50000}})
    body = rafa.post("/api/paper/reset").json()
    assert body["positions"] == [] and body["orders"] == []
    assert body["account"]["cash"] == 50000 and body["account"]["starting_balance"] == 50000
    assert any(e["event"] == "account_reset" for e in rafa.get("/api/paper/events").json())


def test_edit_target_and_stop(rafa):
    pid = rafa.post("/api/paper/orders", json=ticket()).json()["positions"][0]["id"]
    body = rafa.patch(f"/api/paper/positions/{pid}", json={"take_profit_price": 4, "stop_loss_price": None}).json()
    assert body["positions"][0]["take_profit_price"] == 4 and body["positions"][0]["stop_loss_price"] is None
    assert rafa.patch(f"/api/paper/positions/{pid}", json={"take_profit_price": 2, "stop_loss_price": 3}).status_code == 422


def test_a_fill_happens_once(rafa, world, engine):
    world.open = False
    oid = rafa.post("/api/paper/orders", json=ticket()).json()["order"]["id"]
    with Session(engine) as db:
        assert paper.try_fill(db, oid, Book.of(2.1, 2.3), "bid_ask")
        db.commit()
    with Session(engine) as db:
        assert not paper.try_fill(db, oid, Book.of(2.1, 2.3), "bid_ask")
        assert db.get(PaperOrder, oid).status == "filled"


def test_same_signal_never_orders_twice(rafa, engine):
    o = OptionOrder(symbol="SPY", option_type="call", strike=Decimal(600), expiration=EXP, quantity=1,
                    limit_price=Decimal("2.30"), source="Swing v9.8", idempotency_key="swing|SPY|1D|1790000000")
    with Session(engine) as db:
        PaperBroker(db, 1).place_order(o)
        db.commit()
    with Session(engine) as db:
        with pytest.raises(PaperError, match="already placed"):
            PaperBroker(db, 1).place_order(o)


def test_users_cannot_touch_each_others_paper_trading(rafa, world):
    world.open = False
    oid = rafa.post("/api/paper/orders", json=ticket()).json()["order"]["id"]
    world.open = True
    pid = rafa.post("/api/paper/orders", json=ticket()).json()["positions"][0]["id"]
    make_user("ana@example.com")
    ana = signed_in("ana@example.com")
    ana.put("/api/me/keys/tradier", json={"secret": "ana-live-key-0002"})
    view = ana.get("/api/paper").json()
    assert view["positions"] == [] and view["orders"] == [] and view["account"]["cash"] == 100_000
    assert ana.get("/api/paper/events").json()[-1]["event"] == "account_opened"
    assert ana.delete(f"/api/paper/orders/{oid}").status_code == 404
    assert ana.post(f"/api/paper/positions/{pid}/close", json={}).status_code == 404
    assert ana.patch(f"/api/paper/positions/{pid}", json={"take_profit_price": 9}).status_code == 404
    ana.post("/api/paper/stop-all")
    assert rafa.get("/api/paper").json()["orders"] and not rafa.get("/api/paper").json()["controls"]["halted"]


def test_option_chain(rafa, world):
    from app.marketdata.base import OptionQuote

    async def chain(symbol, expiration):
        return [OptionQuote(OCC, "SPY", "call", 600.0, EXP, 2.1, 2.3, 2.2, 10, 100),
                OptionQuote("SPYP", "SPY", "put", 600.0, EXP, 1.9, 2.0, 1.95, 5, 50),
                OptionQuote("SPYC605", "SPY", "call", 605.0, EXP, 1.0, 1.1, 1.05, 1, 1)]

    async def exps(symbol):
        return [date.today() - timedelta(days=1), EXP]

    world.md.option_chain = chain
    world.md.option_expirations = exps
    assert rafa.get("/api/market/expirations?symbol=spy").json()["expirations"] == [EXP.isoformat()]
    body = rafa.get(f"/api/market/chain?symbol=SPY&expiration={EXP}").json()
    assert body["underlying"]["last"] == 601.0
    assert [r["strike"] for r in body["rows"]] == [600.0, 605.0]
    assert body["rows"][0]["call"]["ask"] == 2.3 and body["rows"][0]["put"]["bid"] == 1.9
    assert body["rows"][1]["put"] is None

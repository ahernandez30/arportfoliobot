"""Paper fill and exit rules (plan section 8)."""
from datetime import date, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from app import paper_rules as r
from app.paper_rules import Book

NY = ZoneInfo("America/New_York")
LIMITS = r.Limits(max_order_usd=D(5000), max_daily_loss_usd=D(1000))


def book(bid, ask):
    return Book.of(bid, ask)


def test_default_rule_buys_at_ask_sells_at_bid():
    b = book(2.10, 2.30)
    assert r.market_price("buy", b, "bid_ask") == D("2.30")
    assert r.market_price("sell", b, "bid_ask") == D("2.10")


def test_mid_rule():
    assert r.market_price("buy", book(2.10, 2.30), "mid") == D("2.20")
    assert r.market_price("sell", book(2.10, 2.30), "mid") == D("2.20")
    assert r.market_price("buy", book(None, 2.30), "mid") is None


def test_no_usable_price():
    assert r.market_price("buy", book(1, 0), "bid_ask") is None
    assert r.market_price("buy", book(None, None), "bid_ask") is None
    assert r.market_price("sell", book(0, None), "bid_ask") is None
    assert r.market_price("sell", book(0, 0.05), "bid_ask") == 0  # worthless option still sells


@pytest.mark.parametrize("side,bid,ask,limit,expected", [
    ("buy", 2.10, 2.30, "2.30", "2.30"),  # ask at the limit: fills
    ("buy", 2.10, 2.30, "2.50", "2.30"),  # ask below the limit: fills at the ask, not the limit
    ("buy", 2.10, 2.30, "2.25", None),  # ask above the limit: waits
    ("sell", 2.10, 2.30, "2.10", "2.10"),
    ("sell", 2.10, 2.30, "2.00", "2.10"),  # better than the limit
    ("sell", 2.10, 2.30, "2.20", None),
    ("buy", 2.10, 2.30, None, "2.30"),  # market order
])
def test_limit_fills(side, bid, ask, limit, expected):
    got = r.fill_price(side, book(bid, ask), "bid_ask", D(limit) if limit else None)
    assert got == (D(expected) if expected else None)


def test_limit_with_mid_rule():
    assert r.fill_price("buy", book(2.10, 2.30), "mid", D("2.20")) == D("2.20")
    assert r.fill_price("buy", book(2.10, 2.30), "mid", D("2.19")) is None


def test_exit_prices():
    assert r.exit_prices(D("2.30"), D(30), D(20)) == (D("2.99"), D("1.84"))
    assert r.exit_prices(D("2.30"), None, D(20)) == (None, D("1.84"))
    assert r.exit_prices(D("1.00"), D(30), D(100)) == (D("1.30"), D("0.00"))


def test_exit_triggers_on_the_sell_price():
    tp, sl = D("2.99"), D("1.84")
    assert r.exit_trigger(book(2.99, 3.10), "bid_ask", tp, sl) == "take_profit"
    assert r.exit_trigger(book(2.95, 3.10), "bid_ask", tp, sl) is None  # ask past target is not enough
    assert r.exit_trigger(book(1.84, 1.95), "bid_ask", tp, sl) == "stop_loss"
    assert r.exit_trigger(book(1.90, 2.00), "bid_ask", tp, sl) is None
    assert r.exit_trigger(book(2.90, 3.10), "mid", tp, sl) == "take_profit"  # mid 3.00


def test_bad_quotes_never_trigger_exits():
    tp, sl = D("2.99"), D("1.84")
    assert r.exit_trigger(book(None, 2.00), "bid_ask", tp, sl) is None
    assert r.exit_trigger(book(0, 0), "bid_ask", tp, sl) is None
    assert r.exit_trigger(book(2.50, 2.00), "bid_ask", tp, sl) is None  # crossed


def test_settlement():
    assert r.settlement_price("call", D(450), D("462.37")) == D("12.37")
    assert r.settlement_price("put", D(450), D("462.37")) == 0
    assert r.settlement_price("put", D(450), D("449.995")) == D("0.01")


def test_session_hours():
    assert r.session_open("open", datetime(2026, 10, 5, 9, 30, tzinfo=NY))
    assert not r.session_open("open", datetime(2026, 10, 5, 9, 29, tzinfo=NY))
    assert not r.session_open("open", datetime(2026, 10, 5, 16, 0, tzinfo=NY))
    assert not r.session_open("closed", datetime(2026, 10, 5, 11, 0, tzinfo=NY))  # holiday per provider
    assert not r.session_open("postmarket", datetime(2026, 10, 5, 16, 30, tzinfo=NY))


def test_expired():
    exp = date(2026, 10, 16)
    assert not r.expired(exp, datetime(2026, 10, 16, 15, 59, tzinfo=NY))
    assert r.expired(exp, datetime(2026, 10, 16, 16, 0, tzinfo=NY))
    assert r.expired(exp, datetime(2026, 10, 17, 9, 0, tzinfo=NY))


def problem(**kw):
    args = dict(halted=False, quantity=2, limit=D("2.30"), available_cash=D(100000), realized_today=D(0),
                limits=LIMITS, expiration=date(2026, 11, 20), today=date(2026, 10, 5))
    return r.opening_order_problem(**(args | kw))


def test_order_checks():
    assert problem() is None
    assert "stopped" in problem(halted=True)
    assert "largest order" in problem(quantity=30)  # 30 x 2.30 x 100 = 6,900
    assert problem(quantity=21, limit=D("2.38")) is None  # 4,998 is under 5,000
    assert "daily limit" in problem(realized_today=D(-1000))
    assert problem(realized_today=D("-999.99")) is None
    assert "Not enough paper cash" in problem(available_cash=D(400))
    assert "expired" in problem(expiration=date(2026, 10, 2))

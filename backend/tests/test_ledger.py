"""Money math for Capital Tracking and Account Manager (plan rule 5: money math gets the most tests)."""
from datetime import date
from decimal import Decimal as D

import pytest

from app import ledger
from app.ledger import Fill, Valued


def buy(q, p, day, fees="0", id=0):
    return Fill("buy", D(q), D(p), D(fees), date.fromisoformat(day), id)


def sell(q, p, day, fees="0", id=0):
    return Fill("sell", D(q), D(p), D(fees), date.fromisoformat(day), id)


# ---------- basics ----------


def test_option_contract_is_100_shares():
    assert ledger.multiplier("option") == 100
    assert ledger.multiplier("stock") == 1


@pytest.mark.parametrize("symbol,typ,strike,exp,expected", [
    ("TSLA", "call", "450", "2026-12-18", "TSLA261218C00450000"),
    ("SPY", "put", "582.5", "2027-01-15", "SPY270115P00582500"),
    ("QQQ", "call", "0.5", "2026-11-20", "QQQ261120C00000500"),
    ("AAPL", "put", "1250", "2028-06-16", "AAPL280616P01250000"),
])
def test_occ_symbols(symbol, typ, strike, exp, expected):
    assert ledger.occ_symbol(symbol, typ, D(strike), date.fromisoformat(exp)) == expected


def test_mid_price_rules():
    assert ledger.mid_price(5.10, 5.30, 4.0) == D("5.20")
    assert ledger.mid_price(0, 0.10, None) == D("0.05")  # far out of the money: bid 0 is fine
    assert ledger.mid_price(None, 5.30, 5.0) == D("5.0")  # no bid: last trade
    assert ledger.mid_price(5.5, 5.3, 5.0) == D("5.0")  # crossed quote: last trade
    assert ledger.mid_price(0, 0, 0.02) == D("0.02")  # no market: last trade
    assert ledger.mid_price(None, None, None) is None


def test_intrinsic_value():
    assert ledger.intrinsic("call", D(450), D(470)) == 20
    assert ledger.intrinsic("call", D(450), D(440)) == 0
    assert ledger.intrinsic("put", D(450), D(440)) == 10
    assert ledger.intrinsic("put", D(450), D(450)) == 0


# ---------- positions ----------


def test_single_option_buy():
    h = ledger.replay([buy(2, "5.20", "2026-01-05", fees="1.30")], "option")
    assert h.quantity == 2
    assert h.cost == D("1041.30")  # 2 x 5.20 x 100 + fees
    assert h.cash_out == D("1041.30")
    assert h.average_price == D("5.2065")  # as quoted, fees spread over the 200 shares
    assert h.realized == 0


def test_average_cost_across_buys():
    h = ledger.replay([buy(1, "4.00", "2026-01-05"), buy(3, "6.00", "2026-02-05")], "option")
    assert h.quantity == 4
    assert h.cost == D("2200")
    assert h.average_price == D("5.50")


def test_partial_sale_realizes_against_average_cost():
    h = ledger.replay([
        buy(1, "4.00", "2026-01-05"),
        buy(3, "6.00", "2026-02-05"),
        sell(2, "8.00", "2026-03-05", fees="2.00"),
    ], "option")
    # Average 5.50 -> 2 contracts cost 1100; sold for 1600, less 2 fees.
    assert h.realized == D("498.00")
    assert h.quantity == 2
    assert h.cost == D("1100")
    assert h.average_price == D("5.50")
    assert h.cash_in == D("1598.00")
    assert h.closed is None


def test_full_close_and_reopen():
    h = ledger.replay([
        buy(10, "100", "2026-01-05"),
        sell(10, "90", "2026-02-05"),
    ], "stock")
    assert h.quantity == 0 and h.cost == 0
    assert h.realized == D("-100")
    assert h.closed == date(2026, 2, 5)
    h2 = ledger.replay([buy(10, "100", "2026-01-05"), sell(10, "90", "2026-02-05"), buy(5, "80", "2026-03-01")], "stock")
    assert h2.quantity == 5 and h2.cost == D("400") and h2.closed is None
    assert h2.realized == D("-100")


def test_cannot_sell_more_than_held():
    with pytest.raises(ledger.LedgerError, match="only 1 was held"):
        ledger.replay([buy(1, "5", "2026-01-05"), sell(2, "6", "2026-01-06")], "option")


def test_sale_dated_before_the_buy_is_refused():
    with pytest.raises(ledger.LedgerError):
        ledger.replay([buy(1, "5", "2026-02-05"), sell(1, "6", "2026-01-06")], "option")


def test_same_day_buy_and_sell_counts_the_buy_first():
    h = ledger.replay([sell(1, "6", "2026-01-05", id=1), buy(1, "5", "2026-01-05", id=2)], "option")
    assert h.realized == D("100") and h.quantity == 0


def test_thirds_do_not_leak_cents():
    h = ledger.replay([buy(3, "1.00", "2026-01-05", fees="1.00"), sell(1, "1", "2026-01-06"),
                       sell(1, "1", "2026-01-07"), sell(1, "1", "2026-01-08")], "option")
    assert h.quantity == 0 and h.cost == 0
    assert ledger.cents(h.realized) == D("-1.00")  # exactly the fees, nothing lost to rounding


# ---------- valuation and totals ----------


def test_valued_position():
    v = Valued(quantity=D(2), cost=D("1000"), price=D("6.50"), kind="option")
    assert v.value == D("1300")
    assert v.gain == D("300")
    assert v.gain_pct == D("30")


def test_position_without_price_counts_at_cost():
    v = Valued(quantity=D(2), cost=D("1000"), price=None, kind="option")
    assert v.value == D("1000") and v.gain == 0


def test_net_flows():
    assert ledger.net_flows([("deposit", D("10000")), ("withdrawal", D("2500")), ("deposit", D("500"))]) == D("8000")


def test_capital_totals_keep_deposits_out_of_gains():
    """Deposit 10,000; buy 2 calls at 5.00 (+2 fees); sell 1 at 7.00 (+1 fee); the other is now 6.00."""
    h = ledger.replay([buy(2, "5.00", "2026-01-05", fees="2"), sell(1, "7.00", "2026-02-05", fees="1")], "option")
    v = Valued(quantity=h.quantity, cost=h.cost, price=D("6.00"), kind="option")
    t = ledger.capital_totals(D("10000"), [h], [v])
    assert t.cash == D("10000") - D("1002") + D("699")  # 9697
    assert t.positions_value == D("600")
    assert t.total == D("10297")
    assert t.gain == D("297")
    assert t.gain == t.realized + t.unrealized  # 198 + 99
    assert t.realized == D("198") and t.unrealized == D("99")
    assert t.gain_pct == D("2.97")
    # Another deposit raises the total and what was put in, but not the gain.
    t2 = ledger.capital_totals(D("15000"), [h], [v])
    assert t2.total - t.total == D("5000") and t2.gain == t.gain


def test_totals_with_nothing_put_in():
    t = ledger.capital_totals(D("0"), [], [])
    assert t.total == 0 and t.gain_pct is None and not t.estimated


def test_totals_flag_missing_prices():
    h = ledger.replay([buy(1, "5", "2026-01-05")], "option")
    t = ledger.capital_totals(D("1000"), [h], [Valued(h.quantity, h.cost, None, "option")])
    assert t.estimated and t.total == D("1000")


# ---------- closed trades ----------


def test_long_option_trade_result():
    dollars, pct = ledger.trade_result("long", "option", D(2), D("3.00"), D("3.45"), D("1.30"))
    assert dollars == D("88.70")  # 0.45 x 2 x 100 - 1.30
    assert ledger.cents(pct) == D("14.78")  # 88.70 / 600


def test_short_trade_gains_when_price_falls():
    dollars, pct = ledger.trade_result("short", "stock", D(100), D("50"), D("48"), D("0"))
    assert dollars == D("200") and pct == D("4")


def test_losing_trade_and_zero_entry():
    dollars, _ = ledger.trade_result("long", "option", D(1), D("2.00"), D("0"), D("0.65"))
    assert dollars == D("-200.65")
    assert ledger.trade_result("long", "option", D(1), D("0"), D("1"), D("0"))[1] is None


def test_trade_stats():
    s = ledger.trade_stats([(D("100"), "take_profit"), (D("-40"), "stop_loss"), (D("60"), "take_profit"),
                            (D("0"), "manual"), (D("-20"), "stop_loss")])
    assert s.count == 5 and s.wins == 2 and s.losses == 2
    assert s.win_rate == D("40")
    assert s.average_win == D("80") and s.average_loss == D("-30")
    assert s.total == D("100")
    assert s.by_reason["take_profit"] == {"count": 2, "total": D("160")}
    assert s.by_reason["manual"]["count"] == 1


def test_empty_stats():
    s = ledger.trade_stats([])
    assert s.win_rate is None and s.average_win is None and s.average_loss is None

"""Turning a signal into an option trade (plan 7.6, Rafa's rules of 2026-10-05): pure rules, no database."""
from datetime import date
from decimal import Decimal as D

import pytest

from app import auto_plan
from app.auto_plan import History, Leg, TradeSettings
from app.paper_rules import Book


def legs(strikes, otype="put", spread=D("0.10"), price_of=None):
    """A chain of one option type: each strike quoted around a made-up price."""
    out = []
    for k in strikes:
        mid = price_of(D(k)) if price_of else D("5.00")
        out.append(Leg(D(k), mid - spread / 2, mid + spread / 2, f"X{otype[0].upper()}{k}"))
    return out


STRIKES = [90, 95, 100, 105, 110]


# ---------- settings ----------


def test_defaults_and_bad_saved_values_fall_back():
    s = auto_plan.load_settings({"risk_usd": -5, "side": "away", "nonsense": 1})
    assert s.side == "away" and s.risk_usd == 500 and s.structure == "credit_spread"
    assert auto_plan.load_settings(None) == TradeSettings()


def test_compare_runs_every_structure_in_its_own_sub_account():
    s = TradeSettings(structure="compare")
    assert s.structures() == ("directional", "credit_spread", "debit_spread")
    assert [s.account_for(x) for x in s.structures()] == ["directional", "credit_spread", "debit_spread"]
    one = TradeSettings(structure="debit_spread")
    assert one.structures() == ("debit_spread",) and one.account_for("debit_spread") == "main"


# ---------- the strategy's history ----------


def run_with(trades, avg_days=None, days_loss=None):
    return {"trades": trades, "results": {"avg_days": avg_days, "total": {"days_loss": days_loss}}}


def test_history_averages_only_winning_moves():
    run = run_with([{"counted": True, "ret_pct": 4.0}, {"counted": True, "ret_pct": 6.0},
                    {"counted": True, "ret_pct": -13.0}, {"counted": False, "ret_pct": 20.0}], 20.0, 31.0)
    h = auto_plan.history_of(run)
    assert h == History(2, 5.0, 20.0, 31.0)


def test_distance_auto_uses_average_winning_move_else_the_fixed_value():
    h = History(5, 4.25, 20.0, 10.0)
    assert auto_plan.rules_for(TradeSettings(), h).distance_pct == 4.25
    assert auto_plan.rules_for(TradeSettings(distance_mode="fixed", distance_pct=2.5), h).distance_pct == 2.5
    none = auto_plan.rules_for(TradeSettings(distance_pct=3), History(0, None, None, None))
    assert none.distance_pct == 3 and "no winning trades" in none.distance_note


def test_hold_is_average_plus_margin_or_average_loss_whichever_longer():
    # 20 days x 1.5 = 30 beats the 25-day average loss.
    assert auto_plan.rules_for(TradeSettings(), History(3, 5, 20.0, 25.0)).hold_days == 30
    # A long average loss wins: 10 x 1.5 = 15 < 40.
    assert auto_plan.rules_for(TradeSettings(), History(3, 5, 10.0, 40.0)).hold_days == 40
    # Margin is adjustable; fractions round up.
    assert auto_plan.rules_for(TradeSettings(margin_pct=0), History(3, 5, 20.2, None)).hold_days == 21
    fixed = auto_plan.rules_for(TradeSettings(expiry_mode="fixed", expiry_days=45), History(3, 5, 20.0, 25.0))
    assert fixed.hold_days == 45


def test_first_expiration_at_least_hold_days_away():
    exps = [date(2026, 10, 9), date(2026, 10, 16), date(2026, 11, 20), date(2026, 12, 18)]
    assert auto_plan.pick_expiration(exps, date(2026, 10, 5), 11) == date(2026, 10, 16)
    assert auto_plan.pick_expiration(exps, date(2026, 10, 5), 12) == date(2026, 11, 20)
    assert auto_plan.pick_expiration(exps, date(2026, 10, 5), 100) is None


# ---------- strikes ----------


@pytest.mark.parametrize("structure,direction,side,expected", [
    ("directional", 1, "toward", D("104")), ("directional", 1, "away", D("96")),
    ("directional", -1, "toward", D("96")), ("credit_spread", 1, "toward", D("104")),
    ("credit_spread", 1, "away", D("96")), ("credit_spread", -1, "toward", D("96")),
    ("debit_spread", -1, "away", D("104")),
])
def test_target_strike_toward_is_beyond_the_price_in_the_signal_direction(structure, direction, side, expected):
    assert auto_plan.target_strike(D("100"), direction, 4.0, side, structure) == expected


def test_directional_buy_takes_the_call_nearest_the_target():
    c = auto_plan.choose("directional", 1, D("100"), 4.0, "toward", legs(STRIKES, "call"))
    assert c.option_type == "call" and c.sold is None and c.bought.strike == 105


def test_bull_put_credit_spread_sells_near_target_buys_next_lower():
    c = auto_plan.choose("credit_spread", 1, D("100"), 4.0, "toward", legs(STRIKES, "put"))
    assert c.option_type == "put" and c.sold.strike == 105 and c.bought.strike == 100 and c.width == 5


def test_bear_call_credit_spread_buys_next_higher():
    c = auto_plan.choose("credit_spread", -1, D("100"), 4.0, "toward", legs(STRIKES, "call"))
    assert c.option_type == "call" and c.sold.strike == 95 and c.bought.strike == 100


def test_debit_spread_uses_the_same_strikes_with_the_other_option_type():
    bull = auto_plan.choose("debit_spread", 1, D("100"), 4.0, "toward", legs(STRIKES, "call"))
    assert bull.option_type == "call" and bull.bought.strike == 100 and bull.sold.strike == 105
    bear = auto_plan.choose("debit_spread", -1, D("100"), 4.0, "toward", legs(STRIKES, "put"))
    assert bear.option_type == "put" and bear.bought.strike == 100 and bear.sold.strike == 95


def test_no_strike_beyond_the_sold_leg_is_refused():
    msg = auto_plan.choose("credit_spread", 1, D("100"), 10.0, "away", legs(STRIKES, "put"))
    assert isinstance(msg, str) and "below 90" in msg


def test_one_sided_quotes_are_skipped():
    chain = legs(STRIKES, "call")
    chain[3] = Leg(D(105), None, D("1.00"), "X105")
    c = auto_plan.choose("directional", 1, D("100"), 4.0, "toward", chain)
    assert c.bought.strike in (D(100), D(110))


# ---------- prices and size ----------


def test_spread_prices_each_leg_by_the_fill_rule():
    sold, bought = Book(D("6.00"), D("6.20")), Book(D("3.00"), D("3.10"))
    # Credit: sell the sold leg at its bid, buy the other at its ask.
    assert auto_plan.spread_price("credit_spread", sold, bought, "bid_ask", True, D(5)) == D("2.90")
    # Buying it back: the sold leg at its ask, the other sold at its bid.
    assert auto_plan.spread_price("credit_spread", sold, bought, "bid_ask", False, D(5)) == D("3.20")
    assert auto_plan.spread_price("credit_spread", sold, bought, "mid", True, D(5)) == D("3.05")
    # Debit spread: buy the long leg at its ask, sell the short one at its bid.
    assert auto_plan.spread_price("debit_spread", Book(D("3.00"), D("3.10")), Book(D("6.00"), D("6.20")),
                                  "bid_ask", True, D(5)) == D("3.20")
    # Never outside zero and the width.
    assert auto_plan.spread_price("credit_spread", Book(D("9"), D("9.1")), Book(D("1"), D("1.1")),
                                  "bid_ask", True, D(5)) == D(5)
    assert auto_plan.spread_price("credit_spread", Book(None, D(1)), bought, "bid_ask", True, D(5)) is None


def credit_choice():
    p = {D(100): D("3.05"), D(105): D("6.10")}
    return auto_plan.choose("credit_spread", 1, D("100"), 4.0, "toward", legs([100, 105], "put", price_of=p.get))


def test_credit_spread_size_risk_and_payout():
    s = auto_plan.size(credit_choice(), "bid_ask", D("1000"))
    # credit 6.05 - 3.10 = 2.95; can lose (5 - 2.95) x 100 = 205; make 295.
    assert s.price == D("2.95") and s.unit_risk == D("205.00") and s.unit_reward == D("295.00")
    assert s.quantity == 4 and s.payout == 1.44


def test_directional_size_and_too_small_a_risk():
    c = auto_plan.choose("directional", 1, D("100"), 4.0, "toward", legs(STRIKES, "call"))
    s = auto_plan.size(c, "bid_ask", D("1100"))
    assert s.price == D("5.05") and s.quantity == 2 and s.payout is None
    msg = auto_plan.size(c, "bid_ask", D("100"))
    assert isinstance(msg, str) and "less than what one contract can lose" in msg


def test_debit_spread_size_and_refusals():
    p = {D(100): D("5.00"), D(105): D("2.50")}
    c = auto_plan.choose("debit_spread", 1, D("100"), 4.0, "toward", legs([100, 105], "call", price_of=p.get))
    s = auto_plan.size(c, "bid_ask", D("1000"))
    assert s.price == D("2.60") and s.unit_risk == D("260.00") and s.unit_reward == D("240.00") and s.quantity == 3
    # A credit spread paying nothing is refused.
    flat = auto_plan.choose("credit_spread", 1, D("100"), 4.0, "toward", legs([100, 105], "put"))
    assert "no credit" in auto_plan.size(flat, "bid_ask", D("1000"))


def test_describe_names_the_spread():
    text = auto_plan.describe(credit_choice(), date(2026, 11, 20), "TSLA")
    assert text == "Bull put spread TSLA: sell 105 put, buy 100 put, Nov 20 2026"

"""Automatic paper trading (Stage 6): signals on closed candles become exactly one paper trade per
structure, exits follow the strategy on the stock chart, and the safeguards of plan section 10."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app import auto_trader, ledger, paper, routes_market, user_settings
from app.marketdata import service
from app.marketdata.bars import NY
from app.marketdata.base import Bar, OptionQuote, Quote
from app.models import ClosedTrade, PaperPosition, StrategyPreset, StrategyTrade
from tests.conftest import make_user, signed_in
from tests.fakes import FakeMarketData

D0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def day(i):
    return int((D0 + timedelta(days=i)).timestamp())


def daily(n):
    """Quiet candles with a full green candle every 10 days (i % 10 == 5) and a full red one every 15."""
    out, price = [], 100.0
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


BARS = daily(56)  # the last candle (i = 55) is a full green one: a BUY
LAST = BARS[-1]
# Day 55 is Sunday 2024-02-25 in this made-up history; act on Monday morning, market open.
NOW = datetime(2024, 2, 26, 10, 0, tzinfo=NY)
EXPIRATIONS = [date(2024, 3, 1), date(2024, 3, 15), date(2024, 4, 19), date(2024, 6, 21), date(2025, 1, 17)]


def chain_for(symbol, price, exp):
    out = []
    for k in range(50, 250, 5):  # a fixed grid, so held strikes stay listed as the price moves
        for otype in ("call", "put"):
            intrinsic = max(price - k, 0) if otype == "call" else max(k - price, 0)
            mid = round(intrinsic + max(0.2, 3.0 - 0.1 * abs(k - price)), 2)  # time value fades away from the price
            occ = ledger.occ_symbol(symbol, otype, D(k), exp)
            out.append(OptionQuote(occ, symbol, otype, float(k), exp, mid - 0.05, mid + 0.05, mid, 10, 100))
    return out


def market(price=LAST.close, bars=BARS, trade_time=None, clock="open", at=NOW):
    trade_time = trade_time or int(at.timestamp() * 1000)
    chains = {("TSLA", e): chain_for("TSLA", price, e) for e in EXPIRATIONS}
    quotes = {"TSLA": Quote("TSLA", last=price, trade_time=trade_time)}
    for opts in chains.values():
        for o in opts:
            quotes[o.symbol] = Quote(o.symbol, bid=o.bid, ask=o.ask, last=o.last)
    # No hourly candles: the strategy falls back to its own order inside a daily candle.
    return FakeMarketData(bars={("TSLA", "1D"): bars, ("TSLA", "1h"): []}, quotes=quotes, expirations={"TSLA": EXPIRATIONS},
                          chains=chains, clock_state=clock)


@pytest.fixture
def md(monkeypatch):
    holder = {"md": market()}
    monkeypatch.setattr(routes_market, "providers", service.ProviderCache())
    monkeypatch.setattr(routes_market, "cache", service.TTLCache())
    monkeypatch.setattr(service, "build_provider", lambda provider, secret: holder["md"])
    return holder


@pytest.fixture
def rafa():
    u = make_user("rafa@example.com", role="admin")
    c = signed_in("rafa@example.com")
    c.put("/api/me/keys/tradier", json={"secret": "rafa-live-key-0001"})
    c.user_id = u.id
    return c


def switch_on(c, db, trade=None, *, since=None, paper_auto=True):
    """Pegs TSLA 1D, saves what to trade and switches automatic paper trades on."""
    r = c.put("/api/strategy/presets", json={"strategy": "swing_v98", "symbol": "TSLA", "timeframe": "1D", "inputs": {}})
    assert r.status_code == 200, r.text
    r = c.put("/api/auto/plan", json={"strategy": "swing_v98", "symbol": "TSLA", "timeframe": "1D",
                                      "trade": trade or {}, "auto": True})
    assert r.status_code == 200, r.text
    preset = db.scalar(select(StrategyPreset))
    # Only the last candle (closing 2024-02-25 16:00) is after the switch.
    preset.auto_since = since or datetime(2024, 2, 25, 15, 0, tzinfo=NY)
    if paper_auto:
        s = user_settings.load(db, c.user_id)
        user_settings.save(db, c.user_id, user_settings.apply_changes(s, {"trading": {"auto_trading": "paper"}}))
    db.commit()


def cycle(engine, user_id, now=NOW, trader=None):
    trader = trader or auto_trader.AutoTrader(engine)
    asyncio.run(trader.run_user(user_id, now))
    return trader


def strategy_trades(db):
    db.expire_all()
    return list(db.scalars(select(StrategyTrade).order_by(StrategyTrade.id)))


# ---------- pure helpers ----------


def test_candle_close_times():
    assert auto_trader.candle_close_time(day(55), "1D") == datetime(2024, 2, 25, 16, 0, tzinfo=NY)
    # A week counts as closing on its Friday.
    monday = int(datetime(2024, 2, 19, tzinfo=timezone.utc).timestamp())
    assert auto_trader.candle_close_time(monday, "1W") == datetime(2024, 2, 23, 16, 0, tzinfo=NY)
    t = int(datetime(2024, 2, 26, 15, 30, tzinfo=NY).timestamp())
    assert auto_trader.candle_close_time(t, "1h") == datetime(2024, 2, 26, 16, 0, tzinfo=NY)


def test_engine_exit_matches_by_entry_and_direction():
    run = {"trades": [{"dir": 1, "entry_time": 5, "reason": "TP", "exit_price": 120.0, "exit_time": 9},
                      {"dir": -1, "entry_time": 7, "reason": "flot", "exit_price": 90.0, "exit_time": 20},
                      {"dir": 1, "entry_time": 30, "entry_times": [30, 31], "reason": "SIG", "exit_price": 1.0,
                       "exit_time": 40}]}
    assert auto_trader.engine_exit(run, 5, 1).reason == "take_profit"
    assert auto_trader.engine_exit(run, 5, -1) is None
    assert auto_trader.engine_exit(run, 7, -1).reason == "floating"
    assert auto_trader.engine_exit(run, 31, 1).reason == "signal"  # a later basket entry


def test_live_levels_stop_checked_first():
    assert auto_trader.level_hit(1, 99.0, 115.0, 100.0) == "stop_loss"
    assert auto_trader.level_hit(1, 115.0, 115.0, 87.0) == "take_profit"
    assert auto_trader.level_hit(-1, 101.0, 85.0, 100.0) == "stop_loss"
    assert auto_trader.level_hit(-1, 90.0, 85.0, 113.0) is None


def test_safety_close_on_the_market_day_before_expiration():
    friday = date(2024, 3, 15)
    assert not auto_trader.safety_close_due(friday, date(2024, 3, 13))
    assert auto_trader.safety_close_due(friday, date(2024, 3, 14))
    # Expiring Monday: closed on the Friday before.
    assert auto_trader.safety_close_due(date(2024, 3, 18), date(2024, 3, 15))


# ---------- the worker, end to end ----------


def test_signal_on_latest_closed_candle_opens_one_credit_spread(md, rafa, db, engine):
    switch_on(rafa, db)
    trader = cycle(engine, rafa.user_id)
    rows = strategy_trades(db)
    assert len(rows) == 1
    t = rows[0]
    assert (t.status, t.direction, t.structure, t.account_name, t.signal_time) == ("open", 1, "credit_spread", "main",
                                                                                 LAST.time)
    pos = db.get(PaperPosition, t.position_id)
    assert pos.structure == "credit_spread" and pos.option_type == "put" and pos.strike > pos.strike2
    assert pos.strike - pos.strike2 == 5  # the two closest strikes
    # The plan records how it was chosen, and the risk is within the dollar amount.
    assert t.plan["max_loss"] <= 500 and t.plan["payout"] > 0 and t.plan["hold_days"] >= 1
    assert date.fromisoformat(t.plan["expiration"]) >= NOW.date() + timedelta(days=t.plan["hold_days"])
    assert t.under_entry == D(str(LAST.close)) and t.under_target is not None and t.under_stop is not None
    acct = paper.account(db, rafa.user_id)
    assert acct.cash == D("100000") - D(str(t.plan["max_loss"]))

    # The same signal never orders twice, however often the worker looks.
    cycle(engine, rafa.user_id, NOW + timedelta(minutes=1), trader)
    cycle(engine, rafa.user_id, NOW + timedelta(minutes=2))
    assert len(strategy_trades(db)) == 1
    assert len(list(db.scalars(select(PaperPosition)))) == 1


def test_strategy_exit_on_the_stock_chart_closes_the_spread(md, rafa, db, engine):
    switch_on(rafa, db)
    cycle(engine, rafa.user_id)
    t = strategy_trades(db)[0]
    # Next day the stock runs past the strategy's +15% target (a quiet-bodied candle, so no new signal).
    p = LAST.close
    up = Bar(day(56), p, p * 1.2, p * 0.99, p + 0.01, 1)
    tue = datetime(2024, 2, 27, 10, 0, tzinfo=NY)
    md["md"] = market(price=p * 1.18, bars=BARS + [up], at=tue)
    cycle(engine, rafa.user_id, tue)
    t = strategy_trades(db)[0]
    assert t.status == "closed" and t.exit_reason == "take_profit"
    trade = db.scalar(select(ClosedTrade))
    assert trade.structure == "credit_spread" and trade.direction == "short" and trade.close_reason == "take_profit"
    assert trade.account_name == "main" and trade.risk == D(str(t.plan["max_loss"]))
    # Both moves are recorded: the stock's (target price) and the option's.
    assert trade.underlying_entry == D(str(LAST.close)) and trade.underlying_exit == D(str(round(p * 1.15, 4)))
    r = rafa.get("/api/trades?mode=paper&period=all").json()
    row = r["trades"][0]
    assert "bull put spread" in row["label"] and row["stock_move_pct"] == pytest.approx(15, abs=0.01)
    # Credit received minus the debit paid to buy it back, per share, x 100 x spreads.
    assert row["result"] == pytest.approx(float((trade.entry_price - trade.exit_price) * 100 * trade.quantity))


def test_live_stop_on_the_stock_price_closes_during_the_session(md, rafa, db, engine):
    switch_on(rafa, db)
    cycle(engine, rafa.user_id)
    t = strategy_trades(db)[0]
    md["md"] = market(price=float(t.under_stop) - 1, at=NOW + timedelta(minutes=5))
    cycle(engine, rafa.user_id, NOW + timedelta(minutes=5))
    t = strategy_trades(db)[0]
    assert t.status == "closed" and t.exit_reason == "stop_loss"
    assert db.scalar(select(ClosedTrade)).close_reason == "stop_loss"


def test_exit_while_market_closed_waits_for_the_open(md, rafa, db, engine):
    switch_on(rafa, db)
    cycle(engine, rafa.user_id)
    p = LAST.close
    bars = BARS + [Bar(day(56), p, p * 1.2, p * 0.99, p + 0.01, 1)]
    md["md"] = market(price=p * 1.18, bars=bars, clock="closed")
    evening = datetime(2024, 2, 26, 17, 0, tzinfo=NY)
    cycle(engine, rafa.user_id, evening)
    t = strategy_trades(db)[0]
    assert t.status == "open" and t.exit_reason == "take_profit" and "when the options market opens" in t.detail
    md["md"] = market(price=p * 1.18, bars=bars, at=datetime(2024, 2, 27, 9, 31, tzinfo=NY))
    cycle(engine, rafa.user_id, datetime(2024, 2, 27, 9, 31, tzinfo=NY))
    assert strategy_trades(db)[0].status == "closed"


def test_compare_runs_all_three_structures_in_sub_accounts(md, rafa, db, engine):
    switch_on(rafa, db, {"structure": "compare", "risk_usd": 2000})
    cycle(engine, rafa.user_id)
    rows = strategy_trades(db)
    assert sorted((t.structure, t.account_name, t.status) for t in rows) == [
        ("credit_spread", "credit_spread", "open"), ("debit_spread", "debit_spread", "open"),
        ("directional", "directional", "open")]
    by = {t.structure: db.get(PaperPosition, t.position_id) for t in rows}
    assert by["directional"].structure == "single" and by["directional"].option_type == "call"
    assert by["debit_spread"].option_type == "call" and by["debit_spread"].strike > by["debit_spread"].strike2
    # Each sub-account pays for its own trade; the main account is untouched.
    names = {a.name: a for a in paper.accounts(db, rafa.user_id)}
    assert "main" not in names or names["main"].cash == D("100000")
    for s in ("directional", "credit_spread", "debit_spread"):
        assert names[s].cash < D("100000")
    live = rafa.get("/api/paper?account=debit_spread").json()
    assert live["account_name"] == "debit_spread" and len(live["positions"]) == 1
    assert "bull call spread" in live["positions"][0]["label"]


def test_paused_or_off_places_nothing_new(md, rafa, db, engine):
    switch_on(rafa, db)
    rafa.post("/api/paper/auto-pause", json={"paused": True})
    cycle(engine, rafa.user_id)
    t = strategy_trades(db)[0]
    assert t.status == "refused" and "paused" in t.detail
    assert not list(db.scalars(select(PaperPosition)))


def test_auto_trading_off_in_config_places_nothing(md, rafa, db, engine):
    switch_on(rafa, db, paper_auto=False)
    cycle(engine, rafa.user_id)
    assert strategy_trades(db)[0].status == "refused"
    assert rafa.get("/api/auto/status").json()["state"] == "off"


def test_older_signals_after_the_switch_are_marked_missed(md, rafa, db, engine):
    switch_on(rafa, db, since=datetime(2024, 2, 1, tzinfo=NY))
    cycle(engine, rafa.user_id)
    rows = strategy_trades(db)
    assert [t.status for t in rows if t.signal_time != LAST.time] and \
        all(t.status == "missed" for t in rows if t.signal_time != LAST.time)
    assert [t.status for t in rows if t.signal_time == LAST.time] == ["open"]


def test_stale_prices_pause_and_warn(md, rafa, db, engine):
    switch_on(rafa, db)
    md["md"] = market(trade_time=int((NOW - timedelta(minutes=20)).timestamp() * 1000))
    cycle(engine, rafa.user_id)
    assert not strategy_trades(db)
    st = rafa.get("/api/auto/status").json()
    assert st["state"] == "problem" and "have not updated" in st["problem"]
    # Fresh prices clear the warning and trading carries on.
    md["md"] = market()
    cycle(engine, rafa.user_id)
    assert rafa.get("/api/auto/status").json()["state"] == "on"
    assert strategy_trades(db)[0].status == "open"


def test_risk_too_small_is_refused_with_a_reason(md, rafa, db, engine):
    switch_on(rafa, db, {"structure": "directional", "risk_usd": 10})
    cycle(engine, rafa.user_id)
    t = strategy_trades(db)[0]
    assert t.status == "refused" and "less than what one contract can lose" in t.detail


def test_size_limit_applies_to_the_most_it_can_lose(md, rafa, db, engine):
    switch_on(rafa, db, {"risk_usd": 50000})
    s = user_settings.load(db, rafa.user_id)
    user_settings.save(db, rafa.user_id, user_settings.apply_changes(s, {"trading": {"max_order_usd": 300}}))
    db.commit()
    cycle(engine, rafa.user_id)
    t = strategy_trades(db)[0]
    assert t.status == "refused" and "could lose" in t.detail and "largest order allowed" in t.detail


def test_closing_a_strategy_spread_by_hand(md, rafa, db, engine):
    switch_on(rafa, db)
    cycle(engine, rafa.user_id)
    t = strategy_trades(db)[0]
    r = rafa.post(f"/api/paper/positions/{t.position_id}/close", json={})
    assert r.status_code == 200, r.text
    assert db.scalar(select(ClosedTrade)).close_reason == "manual"
    cycle(engine, rafa.user_id, NOW + timedelta(minutes=1))
    t = strategy_trades(db)[0]
    assert t.status == "open" or t.status == "closed"
    # Once the strategy exits, the record notes the position was already closed by hand.
    p = LAST.close
    tue = datetime(2024, 2, 27, 10, 0, tzinfo=NY)
    md["md"] = market(price=p * 1.18, bars=BARS + [Bar(day(56), p, p * 1.2, p * 0.99, p + 0.01, 1)], at=tue)
    cycle(engine, rafa.user_id, tue)
    t = strategy_trades(db)[0]
    assert t.status == "closed" and "closed by hand" in t.detail
    assert len(list(db.scalars(select(ClosedTrade)))) == 1


# ---------- the screens' endpoints ----------


def test_plan_needs_pegged_settings(md, rafa):
    r = rafa.put("/api/auto/plan", json={"strategy": "swing_v98", "symbol": "QQQ", "timeframe": "1D", "trade": {},
                                         "auto": True})
    assert r.status_code == 409 and "Peg settings" in r.json()["detail"]
    bad = rafa.put("/api/strategy/presets", json={"strategy": "swing_v98", "symbol": "QQQ", "timeframe": "1D",
                                                  "inputs": {}})
    assert bad.status_code == 200
    r = rafa.put("/api/auto/plan", json={"strategy": "swing_v98", "symbol": "QQQ", "timeframe": "1D",
                                         "trade": {"risk_usd": -1}, "auto": True})
    assert r.status_code == 422


def test_preview_shows_both_signals_for_each_structure(md, rafa):
    r = rafa.post("/api/auto/preview", json={"strategy": "swing_v98", "symbol": "TSLA", "timeframe": "1D",
                                             "inputs": {}, "trade": {"structure": "compare"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["rows"]) == 6 and out["history"]["winners"] >= 0
    assert {row["signal"] for row in out["rows"]} == {"BUY", "SELL"}
    credit_buy = next(r for r in out["rows"] if r["structure"] == "credit_spread" and r["signal"] == "BUY")
    assert "problem" in credit_buy or ("Bull put spread" in credit_buy["description"] and credit_buy["payout"] > 0)


def test_signal_list_and_status_are_private(md, rafa, db, engine):
    switch_on(rafa, db)
    cycle(engine, rafa.user_id)
    mine = rafa.get("/api/auto/trades?symbol=TSLA&timeframe=1D").json()
    assert len(mine) == 1 and mine[0]["signal"] == "BUY" and mine[0]["position_label"]
    st = rafa.get("/api/auto/status").json()
    assert st["runs"][0]["symbol"] == "TSLA" and st["runs"][0]["open"] == 1
    make_user("other@example.com")
    other = signed_in("other@example.com")
    assert other.get("/api/auto/trades").json() == []
    assert other.get("/api/auto/status").json()["runs"] == []
    assert other.get(f"/api/paper?account=credit_spread").json()["positions"] == []


def test_spread_settles_at_expiration_if_still_open(md, rafa, db, engine):
    switch_on(rafa, db)
    cycle(engine, rafa.user_id)
    t = strategy_trades(db)[0]
    pos = db.get(PaperPosition, t.position_id)
    sold, bought, q = D(pos.strike), D(pos.strike2), pos.quantity
    credit = D(pos.entry_price)
    cash_before = paper.account(db, rafa.user_id).cash
    # The stock ends between the two strikes: the sold put is worth (sold - price), the bought one nothing.
    under = (sold + bought) / 2
    assert paper.settle(db, pos.id, under)
    db.commit()
    trade = db.scalar(select(ClosedTrade))
    assert trade.close_reason == "expired" and trade.exit_price == sold - under
    assert paper.account(db, rafa.user_id).cash == cash_before + (sold - bought - (sold - under)) * 100 * q
    assert trade.risk == (sold - bought - credit) * 100 * q


def test_strategy_position_closed_the_day_before_expiration(md, rafa, db, engine):
    switch_on(rafa, db)
    cycle(engine, rafa.user_id)
    t = strategy_trades(db)[0]
    exp = db.get(PaperPosition, t.position_id).expiration
    before = datetime(exp.year, exp.month, exp.day, 10, 0, tzinfo=NY) - timedelta(days=1)
    while before.weekday() >= 5:
        before -= timedelta(days=1)
    md["md"] = market(at=before)
    cycle(engine, rafa.user_id, before)
    t = strategy_trades(db)[0]
    assert t.status == "closed" and t.exit_reason == "time" and "expiration" in t.detail

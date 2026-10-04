"""Account Manager: the closed-trade log, filters, stats, export, and paper/real separation."""
import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app import trade_log
from app.models import ClosedTrade
from tests.conftest import make_user, signed_in

NY = ZoneInfo("America/New_York")


@pytest.fixture
def rafa():
    make_user("rafa@example.com", role="admin")
    return signed_in("rafa@example.com")


def manual(**kw):
    body = {"kind": "option", "symbol": "QQQ", "option_type": "put", "strike": 480, "expiration": "2026-10-16",
            "direction": "long", "quantity": 2, "entry_price": 3.00, "exit_price": 3.45, "fees": 1.30,
            "opened_at": "2026-10-01T10:05", "closed_at": "2026-10-01T14:40", "close_reason": "take_profit"}
    return body | kw


def paper_trade(engine, user_id: int, result_exit: str, closed: datetime, source="Swing v9.8"):
    with Session(engine) as db:
        db.add(ClosedTrade(user_id=user_id, mode="paper", source=source, kind="option", symbol="SPY",
                           option_type="call", strike=Decimal(600), expiration=date(2026, 11, 20),
                           direction="long", quantity=Decimal(1), entry_price=Decimal("2.00"),
                           exit_price=Decimal(result_exit), fees=Decimal(0), opened_at=closed - timedelta(hours=2),
                           closed_at=closed, close_reason="signal"))
        db.commit()


def test_manual_trade_result(rafa):
    r = rafa.post("/api/trades", json=manual())
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["mode"] == "real" and t["editable"]
    assert t["result"] == 88.70 and t["result_pct"] == pytest.approx(14.7833, abs=1e-3)
    # 10:05 New York time (the default time zone) is 14:05 UTC.
    assert t["opened_at"].startswith("2026-10-01T14:05")


def test_close_before_open_refused(rafa):
    r = rafa.post("/api/trades", json=manual(closed_at="2026-09-30T10:00"))
    assert r.status_code == 422 and "before the open" in r.json()["detail"]


def test_mode_cannot_be_chosen_for_manual_trades(rafa):
    assert rafa.post("/api/trades", json=manual(mode="paper")).status_code == 422


def test_stats_and_filters(rafa, engine):
    rafa.post("/api/flows", json={"account": "short_term", "kind": "deposit", "amount": 5000, "day": "2026-01-02"})
    rafa.post("/api/trades", json=manual())  # +88.70, take profit
    rafa.post("/api/trades", json=manual(exit_price=2.5, close_reason="stop_loss", fees=0))  # -100
    rafa.post("/api/trades", json=manual(symbol="TSLA", exit_price=4, fees=0, opened_at="2025-03-03T10:00",
                                         closed_at="2025-03-04T10:00"))  # +200, last year
    paper_trade(engine, 1, "3.00", datetime(2026, 10, 1, 15, tzinfo=timezone.utc))  # paper +100

    all_real = rafa.get("/api/trades?mode=real&period=all").json()
    s = all_real["stats"]
    assert s["count"] == 3 and s["wins"] == 2 and s["losses"] == 1
    assert s["total"] == 188.70
    assert s["win_rate"] == pytest.approx(66.6667, abs=1e-3)
    assert s["average_win"] == 144.35 and s["average_loss"] == -100
    assert all_real["account_value"] == 5188.70
    assert all_real["symbols"] == ["QQQ", "TSLA"]

    by_symbol = rafa.get("/api/trades?mode=real&period=all&symbol=tsla").json()
    assert [t["symbol"] for t in by_symbol["trades"]] == ["TSLA"]

    custom = rafa.get("/api/trades?mode=real&period=custom&start=2025-01-01&end=2025-12-31").json()
    assert custom["stats"]["count"] == 1

    # Paper and real are never mixed.
    paper = rafa.get("/api/trades?mode=paper&period=all").json()
    assert paper["stats"]["count"] == 1 and paper["stats"]["total"] == 100
    assert paper["account_value"] == 100_000  # the paper account itself (these trades were inserted directly)
    assert paper["trades"][0]["editable"] is False


def test_period_windows_use_the_users_time_zone():
    now = datetime(2026, 10, 7, 2, 0, tzinfo=timezone.utc)  # Tue 22:00 in New York
    w = trade_log.period_window("today", NY, now)
    assert w.start == datetime(2026, 10, 6, tzinfo=NY)
    w = trade_log.period_window("week", NY, now)
    assert w.start == datetime(2026, 10, 5, tzinfo=NY) and w.end == datetime(2026, 10, 12, tzinfo=NY)
    w = trade_log.period_window("month", NY, now)
    assert w.start == datetime(2026, 10, 1, tzinfo=NY) and w.end == datetime(2026, 11, 1, tzinfo=NY)
    w = trade_log.period_window("year", NY, datetime(2026, 12, 31, 12, tzinfo=timezone.utc))
    assert w.end == datetime(2027, 1, 1, tzinfo=NY)
    w = trade_log.period_window("custom", NY, now, date(2026, 1, 1), date(2026, 1, 31))
    assert w.end == datetime(2026, 2, 1, tzinfo=NY)
    assert trade_log.period_window("all", NY, now) == trade_log.Window(None, None)


def test_edit_notes_and_delete(rafa, engine):
    tid = rafa.post("/api/trades", json=manual()).json()["id"]
    assert rafa.patch(f"/api/trades/{tid}", json={"notes": "chased it"}).json()["notes"] == "chased it"
    assert rafa.put(f"/api/trades/{tid}", json=manual(quantity=1)).json()["quantity"] == 1
    assert rafa.delete(f"/api/trades/{tid}").status_code == 200
    assert rafa.get("/api/trades?period=all").json()["trades"] == []
    # Trades the site placed keep their numbers; only notes change.
    paper_trade(engine, 1, "3.00", datetime(2026, 10, 1, 15, tzinfo=timezone.utc))
    pid = rafa.get("/api/trades?mode=paper&period=all").json()["trades"][0]["id"]
    assert rafa.put(f"/api/trades/{pid}", json=manual()).status_code == 409
    assert rafa.delete(f"/api/trades/{pid}").status_code == 409
    assert rafa.patch(f"/api/trades/{pid}", json={"notes": "ok"}).status_code == 200


def test_csv_export(rafa):
    rafa.post("/api/trades", json=manual(notes="=HYPERLINK(\"x\")"))
    r = rafa.get("/api/trades.csv?mode=real&period=all")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == trade_log.CSV_COLUMNS
    assert rows[1][0] == "2026-10-01 14:40" and rows[1][11] == "88.70" and rows[1][13] == "Take profit"
    assert rows[1][14].startswith("'=")  # a note can never run as a spreadsheet formula

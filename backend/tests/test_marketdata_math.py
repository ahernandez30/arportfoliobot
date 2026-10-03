"""Candle buckets, 1-hour candles, and watchlist change math."""
import asyncio
from datetime import date, datetime

import pytest

from app.marketdata import service
from app.marketdata.bars import NY, aggregate, bucket_start, date_to_epoch, in_regular_session, ny_wall_to_epoch
from app.marketdata.base import Bar
from tests.fakes import FakeMarketData, closes_for, quote


def ny(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=NY).timestamp())


def test_ny_wall_time_handles_daylight_saving():
    # 9:30 New York is 13:30 UTC in summer and 14:30 UTC in winter.
    assert ny_wall_to_epoch("2026-10-02 09:30:00") == int(datetime.fromisoformat("2026-10-02T13:30:00+00:00").timestamp())
    assert ny_wall_to_epoch("2026-12-02 09:30") == int(datetime.fromisoformat("2026-12-02T14:30:00+00:00").timestamp())
    assert ny_wall_to_epoch("2026-12-02T09:30:00") == ny_wall_to_epoch("2026-12-02 09:30")


def test_daily_dates_are_utc_midnight():
    assert date_to_epoch(date(2026, 10, 2)) == int(datetime.fromisoformat("2026-10-02T00:00:00+00:00").timestamp())


@pytest.mark.parametrize("when, ok", [
    ("2026-10-02T09:29:59", False), ("2026-10-02T09:30:00", True), ("2026-10-02T15:59:59", True),
    ("2026-10-02T16:00:00", False), ("2026-10-03T10:00:00", False),  # Saturday
])
def test_regular_session(when, ok):
    assert in_regular_session(ny(when)) is ok


@pytest.mark.parametrize("tf, when, start", [
    ("1m", "2026-10-02T09:31:42", "2026-10-02T09:31:00"),
    ("5m", "2026-10-02T09:34:59", "2026-10-02T09:30:00"),
    ("15m", "2026-10-02T10:01:00", "2026-10-02T10:00:00"),
    ("1h", "2026-10-02T10:29:59", "2026-10-02T09:30:00"),
    ("1h", "2026-10-02T10:30:00", "2026-10-02T10:30:00"),
    ("1h", "2026-10-02T15:45:00", "2026-10-02T15:30:00"),
    # The day clocks fall back (Nov 1, 2026 is a Sunday, so use Monday Nov 2).
    ("1h", "2026-11-02T09:45:00", "2026-11-02T09:30:00"),
])
def test_bucket_start(tf, when, start):
    assert bucket_start(ny(when), tf) == ny(start)


def test_bucket_outside_session_is_none():
    assert bucket_start(ny("2026-10-02T16:30:00"), "5m") is None


def test_hourly_candles_from_15_minute_candles():
    rows = [
        ("09:30", 10, 11, 9.5, 10.5, 100), ("09:45", 10.5, 12, 10, 11, 100),
        ("10:00", 11, 11.5, 8, 9, 100), ("10:15", 9, 10, 9, 9.8, 100),
        ("10:30", 9.8, 10.2, 9.7, 10, 50),
        ("15:30", 20, 21, 19, 20.5, 10), ("15:45", 20.5, 22, 20, 21.5, 10),
        ("16:00", 99, 99, 99, 99, 999),  # after the close: dropped
    ]
    bars = [Bar(ny(f"2026-10-02T{t}:00"), o, h, l, c, v) for t, o, h, l, c, v in rows]
    out = aggregate(bars, "1h")
    assert [b.time for b in out] == [ny("2026-10-02T09:30:00"), ny("2026-10-02T10:30:00"), ny("2026-10-02T15:30:00")]
    assert out[0] == Bar(ny("2026-10-02T09:30:00"), 10, 12, 8, 9.8, 400)
    assert out[2] == Bar(ny("2026-10-02T15:30:00"), 20, 22, 19, 21.5, 20)


@pytest.mark.parametrize("d, months, expected", [
    (date(2026, 10, 3), 1, date(2026, 9, 3)),
    (date(2026, 10, 3), 3, date(2026, 7, 3)),
    (date(2026, 1, 15), 1, date(2025, 12, 15)),
    (date(2026, 5, 31), 3, date(2026, 2, 28)),
    (date(2028, 5, 31), 3, date(2028, 2, 29)),
    (date(2026, 3, 31), 1, date(2026, 2, 28)),
])
def test_months_back(d, months, expected):
    assert service.months_back(d, months) == expected


def test_reference_dates():
    assert service.reference_dates(date(2026, 10, 3)) == {
        "1W": date(2026, 9, 26), "1M": date(2026, 9, 3), "3M": date(2026, 7, 3),
    }


def test_close_on_or_before_skips_weekends():
    closes = closes_for([("2026-09-24", 100), ("2026-09-25", 101), ("2026-09-28", 105)])
    assert service.close_on_or_before(closes, date(2026, 9, 26)) == 101  # Saturday -> Friday
    assert service.close_on_or_before(closes, date(2026, 9, 28)) == 105
    assert service.close_on_or_before(closes, date(2026, 9, 1)) is None


def test_pct_change():
    assert service.pct_change(110, 100) == pytest.approx(10)
    assert service.pct_change(90, 100) == pytest.approx(-10)
    assert service.pct_change(None, 100) is None
    assert service.pct_change(100, 0) is None


def test_watchlist_rows(monkeypatch):
    class FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 3, 12, 0, tzinfo=tz)

    monkeypatch.setattr(service, "datetime", FixedDate)
    md = FakeMarketData(
        quotes={"TSLA": quote("TSLA", 110, 105)},
        closes={"TSLA": closes_for([("2026-07-02", 50), ("2026-09-03", 100), ("2026-09-25", 88)])},
    )
    rows = asyncio.run(service.watchlist(service.TTLCache(), 1, md, ["TSLA", "NOPE"]))
    tsla, nope = rows
    assert tsla["found"] and not nope["found"]
    assert tsla["change"]["1D"] == pytest.approx((110 - 105) / 105 * 100)
    assert tsla["change"]["1W"] == pytest.approx(25)  # vs Sep 25 (Sep 26 was a Saturday)
    assert tsla["change"]["1M"] == pytest.approx(10)
    assert tsla["change"]["3M"] == pytest.approx(120)  # vs Jul 2 (Jul 3 2026 is a market holiday)
    assert nope["change"] == {"1D": None, "1W": None, "1M": None, "3M": None}


def test_reference_closes_are_cached_per_user():
    md = FakeMarketData(closes={"SPY": closes_for([("2026-09-01", 1)])})
    cache = service.TTLCache()
    asyncio.run(service.reference_closes(cache, 1, md, "SPY", date(2026, 10, 3)))
    asyncio.run(service.reference_closes(cache, 1, md, "SPY", date(2026, 10, 3)))
    assert md.calls.count(("daily_closes", "SPY")) == 1
    # Another user's request does not reuse it (Tradier data is per key owner).
    asyncio.run(service.reference_closes(cache, 2, md, "SPY", date(2026, 10, 3)))
    assert md.calls.count(("daily_closes", "SPY")) == 2

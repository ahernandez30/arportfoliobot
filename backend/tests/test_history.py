"""Stored candle history (Backtest plan, step 1): candles built from 1-minute data the way the
strategy and TradingView see them, split adjustment, storage per user, and the merge with live."""
from datetime import date, datetime

import pytest

from app import cli, history
from app.marketdata.bars import NY
from app.marketdata.base import Bar
from app.models import User
from tests.conftest import make_user


def minute(y, mo, d, h, mi, o=100.0, hi=None, lo=None, c=None, v=10.0):
    t = int(datetime(y, mo, d, h, mi, tzinfo=NY).timestamp())
    return history.Minute(t, o, hi if hi is not None else o + 1, lo if lo is not None else o - 1, c if c is not None else o, v)


def ny(b: Bar) -> str:
    return datetime.fromtimestamp(b.time, NY).strftime("%Y-%m-%d %H:%M")


def test_thirty_minute_candles_from_minutes():
    ms = [minute(2025, 11, 21, 9, 29), minute(2025, 11, 21, 9, 30, 100, 103, 99, 101),
          minute(2025, 11, 21, 9, 59, 101, 102, 98, 100.5), minute(2025, 11, 21, 10, 0, 100.5),
          minute(2025, 11, 21, 16, 0)]  # pre-market and after-hours left out
    bars = history.build(reversed(ms), "30m", [])
    assert [ny(b) for b in bars] == ["2025-11-21 09:30", "2025-11-21 10:00"]
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume) == (100, 103, 98, 100.5, 20)


def test_early_close_and_holidays():
    ms = [minute(2025, 11, 28, 12, 45), minute(2025, 11, 28, 13, 0), minute(2025, 11, 28, 15, 0),  # Black Friday
          minute(2025, 4, 18, 10, 0)]  # Good Friday: closed
    assert [ny(b) for b in history.build(ms, "30m", [])] == ["2025-11-28 12:30"]
    assert [ny(b) for b in history.build(ms, "1h", [])] == ["2025-11-28 12:30"]


def test_split_adjustment():
    splits = history.SPLITS["TSLA"]
    assert history.split_factor(splits, date(2020, 8, 28)) == 15
    assert history.split_factor(splits, date(2020, 8, 31)) == 3
    assert history.split_factor(splits, date(2022, 8, 25)) == 1
    (b,) = history.build([minute(2020, 8, 28, 15, 59, 2212.9, v=3)], "30m", splits)
    assert b.close == pytest.approx(2212.9 / 15) and b.volume == 45


def test_merge_prefers_live_candles():
    old = [Bar(t, 1, 1, 1, 1, 1) for t in (100, 200, 300)]
    live = [Bar(t, 2, 2, 2, 2, 2) for t in (300, 400)]
    assert [(b.time, b.close) for b in history.merge(old, live)] == [(100, 1), (200, 1), (300, 2), (400, 2)]
    assert history.merge([], live) == live and history.merge(old, []) == old


def test_store_is_per_user(db):
    a, b = make_user("a@example.com"), make_user("b@example.com")
    bars = [Bar(1000, 1, 2, 0.5, 1.5, 10), Bar(2800, 1.5, 2, 1, 1.8, 5)]
    assert history.store(db, a.id, "TSLA", "30m", bars, "test") == 2
    history.store(db, a.id, "TSLA", "30m", [Bar(2800, 9, 9, 9, 9, 9)], "test")  # replaces the same time
    db.commit()
    assert [x.close for x in history.stored(db, a.id, "TSLA", "30m")] == [1.5, 9]
    assert history.stored(db, b.id, "TSLA", "30m") == []
    (cov,) = history.coverage(db, a.id)
    assert cov["symbol"] == "TSLA" and cov["timeframe"] == "30m" and cov["candles"] == 2


def test_import_command(tmp_path, capsys, db):
    import pyarrow as pa
    import pyarrow.parquet as pq

    make_user("rafa@example.com")
    ts = [datetime(2025, 11, 21, 9, 30, tzinfo=NY), datetime(2025, 11, 21, 9, 30, tzinfo=NY),
          datetime(2025, 11, 21, 10, 15, tzinfo=NY)]
    table = pa.table({"ts_event": pa.array(ts, pa.timestamp("ns", tz="UTC")), "open": [100.0, 100.2, 101.0],
                      "high": [101.0, 102.0, 101.5], "low": [99.0, 99.5, 100.0], "close": [100.5, 100.6, 101.2],
                      "volume": [10, 5, 7]})
    path = tmp_path / "TSLA.parquet"
    pq.write_table(table, path)
    assert cli.main(["import-candles", "rafa@example.com", "--symbol", "TSLA", "--timeframes", "30m", str(path)]) == 0
    assert "TSLA 30m: 2 candles stored" in capsys.readouterr().out
    rows = history.stored(db, db.query(User).one().id, "TSLA", "30m")
    assert rows[0].high == 102 and rows[0].volume == 15  # two exchanges in the same minute, combined

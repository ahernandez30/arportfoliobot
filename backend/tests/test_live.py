"""Live prices: worker side merging/publishing, and the browser WebSocket."""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from sqlalchemy import select, text
from starlette.websockets import WebSocketDisconnect

from app import feed, live
from app.auth import COOKIE_NAME
from app.db import get_engine
from app.feed import CHANNEL_STATUS, CHANNEL_TICKS, Coalescer, FeedManager
from app.marketdata.base import StreamEvent
from app.models import MarketFeedStatus, MarketWatch
from tests.conftest import PASSWORD, client, make_user
from tests.fakes import FakeMarketData, quote


# ---------- merging updates ----------


def test_coalescer_keeps_range_and_volume_between_flushes():
    c = Coalescer()
    c.trade(1, "SPY", 600.0, 100, 1000)
    c.trade(1, "SPY", 601.5, 50, 2000)
    c.trade(1, "SPY", 599.0, 10, 3000)
    c.quote(1, "SPY", 598.9, 599.1)
    c.trade(2, "SPY", 1.0, 1, 1)
    out = {(m["u"], m["s"]): m["d"] for m in c.drain()}
    assert out[(1, "SPY")] == {"last": 599.0, "hi": 601.5, "lo": 599.0, "vol": 160, "t": 3000, "bid": 598.9, "ask": 599.1}
    assert (2, "SPY") in out
    assert c.drain() == []


def test_quote_only_update():
    c = Coalescer()
    c.quote(1, "QQQ", 1.0, None)
    assert c.drain() == [{"u": 1, "s": "QQQ", "d": {"bid": 1.0}}]


# ---------- hub routing ----------


class FakeWS:
    pass


def test_hub_delivers_only_to_the_owner_and_subscribed_symbols():
    hub = live.Hub()
    a = live.Conn(FakeWS(), 1)
    a.symbols = {"SPY"}
    b = live.Conn(FakeWS(), 2)
    b.symbols = {"SPY", "TSLA"}
    hub.add(a)
    hub.add(b)
    assert hub.dispatch(CHANNEL_TICKS, {"u": 1, "s": "SPY", "d": {"last": 1}}) == 1
    assert hub.dispatch(CHANNEL_TICKS, {"u": 1, "s": "TSLA", "d": {"last": 1}}) == 0
    assert a.queue.get_nowait() == {"type": "tick", "symbol": "SPY", "last": 1}
    assert b.queue.empty()
    assert hub.dispatch(CHANNEL_STATUS, {"u": 2, "state": "live"}) == 1
    assert hub.demand() == {(1, "SPY"), (2, "SPY"), (2, "TSLA")}
    hub.remove(a)
    assert 1 not in hub.conns


def test_clean_symbols():
    assert live.clean_symbols(["spy", " tsla ", "bad sym", 5, "SPY"]) == {"SPY", "TSLA"}
    assert live.clean_symbols("SPY") == set()
    assert len(live.clean_symbols([f"A{i}" for i in range(100)])) == live.MAX_SYMBOLS


# ---------- the WebSocket ----------

ORIGIN = {"origin": "https://testserver"}


def login(c, email):
    r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200


def test_socket_needs_sign_in():
    with pytest.raises(WebSocketDisconnect) as exc:
        with client().websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_socket_refuses_other_sites():
    make_user("rafa@example.com")
    c = client()
    login(c, "rafa@example.com")
    cookie = {"cookie": with_cookie(c)["cookie"]}
    for headers in ({"origin": "https://evil.example", **cookie}, cookie):
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("/ws", headers=headers) as ws:
                ws.receive_json()
        assert exc.value.code == 4403


def with_cookie(c) -> dict:
    # The test client opens ws:// (not wss://), so it would not send the Secure cookie by itself.
    return {**ORIGIN, "cookie": f"{COOKIE_NAME}={c.cookies.get(COOKIE_NAME)}"}


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def notify(channel, payload):
    with get_engine().begin() as conn:
        conn.execute(text("SELECT pg_notify(:c, :p)"), {"c": channel, "p": json.dumps(payload)})


def test_prices_reach_only_their_owner(db):
    rafa = make_user("rafa@example.com")
    ana = make_user("ana@example.com")
    with client() as c:  # runs the app's startup, including the NOTIFY listener
        login(c, "rafa@example.com")
        with c.websocket_connect("/ws", headers=with_cookie(c)) as ws:
            first = ws.receive_json()
            assert first["type"] == "status" and first["state"] == "no_key"
            ws.send_json({"type": "subscribe", "symbols": ["spy", "TSLA"]})
            assert wait_for(lambda: db.scalar(select(MarketWatch.symbol).where(MarketWatch.user_id == rafa.id,
                                                                               MarketWatch.symbol == "TSLA")))
            time.sleep(0.3)  # let the listener start listening
            notify(CHANNEL_TICKS, {"u": ana.id, "s": "SPY", "d": {"last": 1.0}})  # Ana's: must not arrive
            notify(CHANNEL_TICKS, {"u": rafa.id, "s": "QQQ", "d": {"last": 2.0}})  # not subscribed
            notify(CHANNEL_TICKS, {"u": rafa.id, "s": "SPY", "d": {"last": 600.25, "t": 1}})
            msg = ws.receive_json()
            assert msg == {"type": "tick", "symbol": "SPY", "last": 600.25, "t": 1}
            notify(CHANNEL_STATUS, {"u": rafa.id, "state": "live", "detail": ""})
            assert ws.receive_json() == {"type": "status", "state": "live", "detail": ""}


# ---------- worker feeds ----------


def watch(user_id, *symbols, age=0):
    with get_engine().begin() as conn:
        for s in symbols:
            conn.execute(
                MarketWatch.__table__.insert().values(
                    user_id=user_id, symbol=s, seen_at=datetime.now(timezone.utc) - timedelta(seconds=age)))


def test_wanted_symbols_ignores_stale_rows():
    u = make_user("rafa@example.com")
    watch(u.id, "SPY")
    watch(u.id, "OLD", age=feed.WATCH_FRESH_SECONDS + 10)
    assert feed.wanted_symbols(get_engine()) == {u.id: {"SPY"}}


async def _run_manager(manager, seconds):
    await manager.sync_once()
    await asyncio.sleep(seconds)


def test_live_feed_streams_and_publishes(monkeypatch, db):
    u = make_user("rafa@example.com")
    watch(u.id, "SPY")
    md = FakeMarketData(events=[StreamEvent("trade", "SPY", {"last": 600.0, "size": 10, "time": 5}),
                                StreamEvent("trade", "SPY", {"last": 601.0, "size": 5, "time": 6}),
                                StreamEvent("quote", "SPY", {"bid": 600.9, "ask": 601.1})])
    monkeypatch.setattr(feed, "load_provider", lambda engine, providers, user_id: (md, True))
    monkeypatch.setattr(feed, "RESUBSCRIBE_DELAY", 0)
    manager = FeedManager(get_engine())

    received = []

    async def scenario():
        async with await psycopg.AsyncConnection.connect(live._conninfo(), autocommit=True) as conn:
            await conn.execute(f"LISTEN {CHANNEL_TICKS}")
            await _run_manager(manager, 0.3)
            await manager.flush_once()
            gen = conn.notifies(timeout=2)
            async for n in gen:
                received.append(json.loads(n.payload))
                break
            await gen.aclose()  # releases the connection so it can close
        for f in manager.feeds.values():
            await f.stop()

    asyncio.run(scenario())
    assert received == [{"u": u.id, "s": "SPY", "d": {"last": 601.0, "hi": 601.0, "lo": 600.0, "vol": 15.0,
                                                       "t": 6, "bid": 600.9, "ask": 601.1}}]
    status = db.scalar(select(MarketFeedStatus).where(MarketFeedStatus.user_id == u.id))
    assert status.state == "idle"  # stopped at the end
    assert status.last_event_at is not None


def test_practice_key_polls_delayed_quotes(monkeypatch, db):
    u = make_user("rafa@example.com")
    watch(u.id, "TSLA")
    md = FakeMarketData(realtime=False, quotes={"TSLA": quote("TSLA", 300, 290)})
    monkeypatch.setattr(feed, "load_provider", lambda engine, providers, user_id: (md, False))
    monkeypatch.setattr(feed, "RESUBSCRIBE_DELAY", 0)
    manager = FeedManager(get_engine())

    async def scenario():
        await _run_manager(manager, 0.3)
        pending = dict(manager.coalescer.pending)
        state = manager.feeds[u.id].state
        for f in manager.feeds.values():
            f.task.cancel()
        return pending, state

    pending, state = asyncio.run(scenario())
    assert state == "delayed"
    assert pending[(u.id, "TSLA")].last == 300


def test_no_key_status(monkeypatch, db):
    u = make_user("ana@example.com")
    watch(u.id, "SPY")
    monkeypatch.setattr(feed, "RESUBSCRIBE_DELAY", 0)
    manager = FeedManager(get_engine())

    async def scenario():
        await _run_manager(manager, 0.3)
        state = manager.feeds[u.id].state
        manager.feeds[u.id].task.cancel()
        return state

    assert asyncio.run(scenario()) == "no_key"


def test_feeds_stop_when_nothing_is_watched(monkeypatch):
    u = make_user("rafa@example.com")
    watch(u.id, "SPY")
    monkeypatch.setattr(feed, "load_provider", lambda engine, providers, user_id: (FakeMarketData(), True))
    monkeypatch.setattr(feed, "RESUBSCRIBE_DELAY", 0)
    manager = FeedManager(get_engine())

    async def scenario():
        await manager.sync_once()
        assert u.id in manager.feeds
        with get_engine().begin() as conn:
            conn.execute(text("DELETE FROM market_watch"))
        await manager.sync_once()
        return dict(manager.feeds)

    assert asyncio.run(scenario()) == {}


def test_signing_out_everywhere_closes_live_connections(monkeypatch):
    monkeypatch.setattr(live, "SESSION_RECHECK_SECONDS", 0.2)
    make_user("rafa@example.com")
    with client() as c:
        login(c, "rafa@example.com")
        headers = with_cookie(c)
        with c.websocket_connect("/ws", headers=headers) as ws:
            ws.receive_json()
            c.post("/api/me/sessions/end-all")
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4401

"""Live prices, worker side.

The worker keeps one Tradier stream per user (Tradier allows only one per key) for the
symbols that user has on screen, merges the updates, and hands them to the web server
through PostgreSQL NOTIFY, a few times a second at most. A user with only a sandbox
(practice) key gets delayed prices by polling instead, since Tradier does not stream those.

Prices fetched with one user's key are only ever published for that user.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.marketdata.base import MarketData, MarketDataError
from app.marketdata.service import ProviderCache, feed_info
from app.models import MarketFeedStatus, MarketWatch

log = logging.getLogger("arpb.feed")

CHANNEL_TICKS = "arpb_ticks"
CHANNEL_STATUS = "arpb_feed_status"
# A symbol stays streamed this long after a browser last reported showing it.
WATCH_FRESH_SECONDS = 90
FLUSH_SECONDS = 0.25
POLL_SECONDS_DELAYED = 15
RECONNECT_MAX_SECONDS = 60
# Symbol-set changes are applied after this pause, so flipping through symbols does not
# open a new Tradier session for each one.
RESUBSCRIBE_DELAY = 1.5


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SymbolState:
    """Everything that changed for one symbol since the last flush."""

    last: float | None = None
    time: int | None = None  # ms of the last trade
    high: float | None = None  # highest and lowest trade since the last flush,
    low: float | None = None  # so live candles keep their true range
    size: float = 0.0  # shares traded since the last flush
    bid: float | None = None
    ask: float | None = None
    summary: dict = field(default_factory=dict)

    def add_trade(self, price: float, size: float | None, t: int | None) -> None:
        self.last = price
        self.time = t if t is not None else self.time
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)
        self.size += size or 0.0

    def payload(self) -> dict:
        out: dict = {}
        if self.last is not None:
            out.update(last=self.last, hi=self.high, lo=self.low, vol=self.size, t=self.time)
        if self.bid is not None:
            out["bid"] = self.bid
        if self.ask is not None:
            out["ask"] = self.ask
        if self.summary:
            out["summary"] = self.summary
        return out


class Coalescer:
    """Collects updates per (user, symbol) and releases them in batches."""

    def __init__(self):
        self.pending: dict[tuple[int, str], SymbolState] = {}

    def _state(self, user_id: int, symbol: str) -> SymbolState:
        return self.pending.setdefault((user_id, symbol), SymbolState())

    def trade(self, user_id: int, symbol: str, price: float, size: float | None = None, t: int | None = None) -> None:
        self._state(user_id, symbol).add_trade(price, size, t)

    def quote(self, user_id: int, symbol: str, bid: float | None, ask: float | None) -> None:
        s = self._state(user_id, symbol)
        s.bid = bid if bid is not None else s.bid
        s.ask = ask if ask is not None else s.ask

    def summary(self, user_id: int, symbol: str, fields: dict) -> None:
        self._state(user_id, symbol).summary.update(fields)

    def drain(self) -> list[dict]:
        out = [{"u": u, "s": s, "d": st.payload()} for (u, s), st in self.pending.items() if st.payload()]
        self.pending = {}
        return out


# ---------- database helpers (run in a thread) ----------


def wanted_symbols(engine: Engine) -> dict[int, set[str]]:
    since = utcnow() - timedelta(seconds=WATCH_FRESH_SECONDS)
    out: dict[int, set[str]] = {}
    with Session(engine) as db:
        for user_id, symbol in db.execute(
            select(MarketWatch.user_id, MarketWatch.symbol).where(MarketWatch.seen_at >= since)
        ):
            out.setdefault(user_id, set()).add(symbol)
    return out


def publish(engine: Engine, channel: str, payloads: list[dict]) -> None:
    if not payloads:
        return
    with engine.begin() as conn:
        for p in payloads:
            # NOTIFY payloads must stay under 8000 bytes; ours are a few hundred.
            conn.execute(text("SELECT pg_notify(:c, :p)"), {"c": channel, "p": json.dumps(p, separators=(",", ":"))})


def set_status(engine: Engine, user_id: int, state: str, detail: str = "", *, event_at: datetime | None = None) -> None:
    values = {"user_id": user_id, "state": state, "detail": detail, "updated_at": utcnow()}
    if event_at is not None:
        values["last_event_at"] = event_at
    stmt = insert(MarketFeedStatus).values(**values)
    stmt = stmt.on_conflict_do_update(index_elements=["user_id"], set_={k: v for k, v in values.items() if k != "user_id"})
    with engine.begin() as conn:
        conn.execute(stmt)
        conn.execute(
            text("SELECT pg_notify(:c, :p)"),
            {"c": CHANNEL_STATUS, "p": json.dumps({"u": user_id, "state": state, "detail": detail})},
        )


def load_provider(engine: Engine, providers: ProviderCache, user_id: int) -> tuple[MarketData | None, bool]:
    with Session(engine) as db:
        info = feed_info(db, user_id)
        return providers.get(db, user_id), info.realtime


# ---------- per-user feed ----------


class UserFeed:
    """Keeps one user's live prices flowing for a set of symbols, reconnecting as needed."""

    def __init__(self, engine: Engine, providers: ProviderCache, coalescer: Coalescer, user_id: int):
        self.engine = engine
        self.providers = providers
        self.coalescer = coalescer
        self.user_id = user_id
        self.symbols: frozenset[str] = frozenset()
        self.task: asyncio.Task | None = None
        self.state = ""
        self.last_event: datetime | None = None

    async def _status(self, state: str, detail: str = "") -> None:
        if state != self.state:
            self.state = state
            await asyncio.to_thread(set_status, self.engine, self.user_id, state, detail, event_at=self.last_event)

    def set_symbols(self, symbols: set[str]) -> None:
        new = frozenset(symbols)
        if new == self.symbols and self.task and not self.task.done():
            return
        self.symbols = new
        if self.task:
            self.task.cancel()
        self.task = asyncio.create_task(self._run(new), name=f"feed-{self.user_id}")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        await self._status("idle")

    async def _run(self, symbols: frozenset[str]) -> None:
        await asyncio.sleep(RESUBSCRIBE_DELAY)
        backoff = 2
        while True:
            try:
                md, realtime = await asyncio.to_thread(load_provider, self.engine, self.providers, self.user_id)
                if md is None:
                    await self._status("no_key", "No market data key saved.")
                    await asyncio.sleep(30)
                    continue
                if realtime:
                    await self._stream(md, symbols)
                else:
                    await self._poll(md, symbols)
                backoff = 2
            except asyncio.CancelledError:
                raise
            except MarketDataError as exc:
                await self._status("error", str(exc))
            except Exception as exc:  # network drops, Tradier closing an idle stream, ...
                log.info("feed for user %s dropped: %s", self.user_id, type(exc).__name__)
                await self._status("reconnecting", "Reconnecting to the price feed…")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)

    async def _stream(self, md: MarketData, symbols: frozenset[str]) -> None:
        await self._status("connecting", "Connecting to the live price feed…")
        async for ev in md.stream(sorted(symbols)):
            if ev.kind == "ready":
                await self._status("live")
                continue
            self.last_event = utcnow()
            if self.state != "live":
                await self._status("live")
            f = ev.fields
            if ev.kind == "trade" and "last" in f:
                self.coalescer.trade(self.user_id, ev.symbol, f["last"], f.get("size"), f.get("time"))
            elif ev.kind == "quote":
                self.coalescer.quote(self.user_id, ev.symbol, f.get("bid"), f.get("ask"))
            elif ev.kind == "summary":
                self.coalescer.summary(self.user_id, ev.symbol, f)
        # Tradier ended the stream (e.g. after 15 quiet minutes). Reconnect.
        raise ConnectionError("stream ended")

    async def _poll(self, md: MarketData, symbols: frozenset[str]) -> None:
        while True:
            quotes = await md.quotes(sorted(symbols))
            self.last_event = utcnow()
            await self._status("delayed", "Prices are delayed 15 minutes (practice key).")
            for q in quotes.values():
                if q.last is not None:
                    self.coalescer.trade(self.user_id, q.symbol, q.last, None, q.trade_time)
                self.coalescer.quote(self.user_id, q.symbol, q.bid, q.ask)
            await asyncio.sleep(POLL_SECONDS_DELAYED)


class FeedManager:
    """Starts, updates and stops user feeds to match what browsers have on screen."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.providers = ProviderCache()
        self.coalescer = Coalescer()
        self.feeds: dict[int, UserFeed] = {}

    async def sync_once(self) -> None:
        wanted = await asyncio.to_thread(wanted_symbols, self.engine)
        for user_id, symbols in wanted.items():
            feed = self.feeds.get(user_id)
            if feed is None:
                feed = self.feeds[user_id] = UserFeed(self.engine, self.providers, self.coalescer, user_id)
            feed.set_symbols(symbols)
        for user_id in [u for u in self.feeds if u not in wanted]:
            await self.feeds.pop(user_id).stop()

    async def flush_once(self) -> None:
        await asyncio.to_thread(publish, self.engine, CHANNEL_TICKS, self.coalescer.drain())

    async def run(self, stop: asyncio.Event) -> None:
        async def flusher():
            while not stop.is_set():
                try:
                    await self.flush_once()
                except Exception:
                    log.exception("publishing prices failed")
                await asyncio.sleep(FLUSH_SECONDS)

        flush_task = asyncio.create_task(flusher())
        try:
            while not stop.is_set():
                try:
                    await self.sync_once()
                except Exception:
                    log.exception("feed sync failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
        finally:
            flush_task.cancel()
            for feed in self.feeds.values():
                await feed.stop()
            self.feeds.clear()

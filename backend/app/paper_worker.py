"""Worker job: runs the paper engine whether or not anyone has the site open.

Every few seconds, for each user with working orders or open positions: fills orders whose
limit the quote has reached, closes positions at their target or stop, cancels orders on
expired options and settles expired positions. Each user's prices come from that user's key.
"""
import asyncio
import logging
import time
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select, union
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import paper, paper_rules, user_settings
from app.marketdata.bars import NY
from app.marketdata.base import MarketData, MarketDataError
from app.marketdata.service import ProviderCache
from app.models import PaperOrder, PaperPosition
from app.paper_rules import Book

log = logging.getLogger("arpb.paper")

CYCLE_SECONDS = 3
CLOCK_TTL = 60


def active_users(db: Session) -> list[int]:
    q = union(select(PaperOrder.user_id).where(PaperOrder.status == "working"),
              select(PaperPosition.user_id).where(PaperPosition.status == "open"))
    return sorted(db.scalars(select(q.subquery().c.user_id)))


class ClockCache:
    def __init__(self):
        self._items: dict[int, tuple[float, str]] = {}

    async def state(self, user_id: int, md: MarketData) -> str | None:
        hit = self._items.get(user_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        try:
            state = (await md.clock()).state
        except MarketDataError:
            return None
        self._items[user_id] = (time.monotonic() + CLOCK_TTL, state)
        return state


async def official_close(md: MarketData, symbol: str, day: date) -> Decimal | None:
    """The stock's official closing price on `day`, once the provider has it."""
    try:
        closes = await md.daily_closes(symbol, day, day)
    except MarketDataError:
        return None
    for c in closes:
        if c.day == day:
            return Decimal(str(c.close))
    return None


async def run_user(engine: Engine, providers: ProviderCache, clocks: ClockCache, user_id: int,
                   now: datetime | None = None) -> None:
    now = now or datetime.now(NY)
    with Session(engine) as db:
        md = providers.get(db, user_id)
        if md is None:
            return
        rule = user_settings.load(db, user_id).paper.fill_rule
        orders = paper.working_orders(db, user_id)
        positions = paper.open_positions(db, user_id)

    # Expired options: cancel their orders, settle positions at the official close.
    for o in orders:
        if o.intent == "open" and paper_rules.expired(o.expiration, now):
            with Session(engine) as db:
                paper.expire_order(db, o.id)
                db.commit()
    for p in positions:
        if paper_rules.expired(p.expiration, now):
            close = await official_close(md, p.symbol, p.expiration)
            if close is not None:
                with Session(engine) as db:
                    if paper.settle(db, p.id, close, now):
                        log.info("settled expired paper position %s for user %s", p.id, user_id)
                    db.commit()

    if not paper_rules.session_open(await clocks.state(user_id, md), now):
        return  # outside market hours, orders wait
    live_orders = [o for o in orders if not paper_rules.expired(o.expiration, now)]
    live_positions = [p for p in positions if not paper_rules.expired(p.expiration, now)]
    # Spreads have no target or stop of their own (strategy exits come from app.auto_trader), so only
    # single options need quotes here.
    live_positions = [p for p in live_positions if p.structure == "single"]
    symbols = sorted({o.occ_symbol for o in live_orders} | {p.occ_symbol for p in live_positions})
    if not symbols:
        return
    try:
        quotes = await md.quotes(symbols)
    except MarketDataError as exc:
        log.warning("paper engine: no quotes for user %s: %s", user_id, exc)
        return
    for o in live_orders:
        q = quotes.get(o.occ_symbol)
        if q is None:
            continue
        with Session(engine) as db:
            if paper.try_fill(db, o.id, Book.of(q.bid, q.ask), rule, now):
                log.info("filled paper order %s for user %s", o.id, user_id)
            db.commit()
    for p in live_positions:
        q = quotes.get(p.occ_symbol)
        if q is None:
            continue
        with Session(engine) as db:
            hit = paper.check_exits(db, p.id, Book.of(q.bid, q.ask), rule, now)
            if hit:
                log.info("paper position %s for user %s closed: %s", p.id, user_id, hit)
            db.commit()


async def run_once(engine: Engine, providers: ProviderCache, clocks: ClockCache, now: datetime | None = None) -> None:
    with Session(engine) as db:
        users = active_users(db)
    for user_id in users:
        try:
            await run_user(engine, providers, clocks, user_id, now)
        except Exception:
            log.exception("paper engine failed for user %s", user_id)


async def run(engine: Engine, stop: asyncio.Event) -> None:
    providers = ProviderCache()
    clocks = ClockCache()
    while not stop.is_set():
        started = time.monotonic()
        try:
            await run_once(engine, providers, clocks)
        except Exception:
            log.exception("paper engine cycle failed")
        wait = max(0.5, CYCLE_SECONDS - (time.monotonic() - started))
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass

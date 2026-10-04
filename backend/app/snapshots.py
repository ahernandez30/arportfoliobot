"""Worker job: after each market close, record every user's long-term capital for the
capital-over-time chart. Each user's positions are priced with that user's own key."""
import asyncio
import logging
from datetime import date, datetime, time

from sqlalchemy import select, union
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import capital
from app.marketdata.bars import NY
from app.marketdata.service import ProviderCache
from app.models import CapitalFlow, CapitalSnapshot, LongTermPosition

log = logging.getLogger("arpb.snapshots")

# Closing prices settle a few minutes after 16:00 New York time.
TAKE_AFTER = time(16, 20)
CHECK_SECONDS = 300


def due(now_ny: datetime) -> date | None:
    """The market day to record now, or None (weekends, or before the close)."""
    if now_ny.weekday() >= 5 or now_ny.time() < TAKE_AFTER:
        return None
    return now_ny.date()


def users_missing(db: Session, day: date) -> list[int]:
    """Users with long-term records and no snapshot for `day` yet."""
    with_records = union(
        select(LongTermPosition.user_id),
        select(CapitalFlow.user_id).where(CapitalFlow.account == "long_term"),
    ).subquery()
    done = select(CapitalSnapshot.user_id).where(CapitalSnapshot.day == day)
    return list(db.scalars(select(with_records.c.user_id).where(with_records.c.user_id.not_in(done))))


async def take_for(engine: Engine, providers: ProviderCache, user_id: int, day: date) -> None:
    with Session(engine) as db:
        md = providers.get(db, user_id)
        summary, problem = await capital.build_summary(db, user_id, md)
        totals = dict(summary["totals"])
        if md is None or problem:
            totals["estimated"] = True
        capital.save_snapshot(db, user_id, day, totals)
        db.commit()


async def run_once(engine: Engine, providers: ProviderCache, now_ny: datetime | None = None) -> int:
    day = due(now_ny or datetime.now(NY))
    if day is None:
        return 0
    with Session(engine) as db:
        users = users_missing(db, day)
    for user_id in users:
        try:
            await take_for(engine, providers, user_id, day)
        except Exception:
            log.exception("capital snapshot failed for user %s", user_id)
    return len(users)


async def run(engine: Engine, stop: asyncio.Event) -> None:
    providers = ProviderCache()
    while not stop.is_set():
        try:
            n = await run_once(engine, providers)
            if n:
                log.info("recorded end-of-day capital for %d user(s)", n)
        except Exception:
            log.exception("capital snapshots failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=CHECK_SECONDS)
        except asyncio.TimeoutError:
            pass

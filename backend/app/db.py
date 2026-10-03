from collections.abc import Iterator
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import DateTime, String, create_engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class WorkerHeartbeat(Base):
    """Last time each background process reported in. Used to detect a stalled worker."""

    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one database session per request, committed by the handler."""
    with get_sessionmaker()() as db:
        yield db


def record_heartbeat(engine: Engine, name: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    stmt = insert(WorkerHeartbeat).values(name=name, beat_at=now)
    stmt = stmt.on_conflict_do_update(index_elements=["name"], set_={"beat_at": now})
    with engine.begin() as conn:
        conn.execute(stmt)


def last_heartbeat(engine: Engine, name: str) -> datetime | None:
    with engine.connect() as conn:
        return conn.execute(
            select(WorkerHeartbeat.beat_at).where(WorkerHeartbeat.name == name)
        ).scalar_one_or_none()

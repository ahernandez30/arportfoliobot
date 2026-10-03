from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import last_heartbeat

WORKER_NAME = "worker"


def worker_status(beat_at: datetime | None, now: datetime, stale_seconds: int) -> str:
    """'ok' if the worker reported within stale_seconds, 'stale' if older, 'never' if never."""
    if beat_at is None:
        return "never"
    age = (now - beat_at).total_seconds()
    return "ok" if age <= stale_seconds else "stale"


def check(engine: Engine, stale_seconds: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        beat_at = last_heartbeat(engine, WORKER_NAME)
    except Exception:
        return {"status": "error", "database": "error", "worker": "unknown"}
    worker = worker_status(beat_at, now, stale_seconds)
    return {
        "status": "ok" if worker == "ok" else "degraded",
        "database": "ok",
        "worker": worker,
    }

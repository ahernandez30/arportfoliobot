"""Live prices, web server side: one WebSocket per signed-in browser (plan section 4).

A browser tells us which symbols it shows. We record that in market_watch (so the worker
streams them) and forward the worker's price updates, which arrive through PostgreSQL
NOTIFY, to that user's own connections only.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

import psycopg
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import auth
from app.config import get_settings
from app.db import get_sessionmaker
from app.feed import CHANNEL_STATUS, CHANNEL_TICKS
from app.marketdata.service import NO_KEY, feed_info
from app.models import MarketFeedStatus, MarketWatch
from app.user_settings import SYMBOL_RE

log = logging.getLogger("arpb.live")
router = APIRouter()

MAX_SYMBOLS = 60
DEMAND_REFRESH_SECONDS = 30
SESSION_RECHECK_SECONDS = 60
QUEUE_SIZE = 500


class Conn:
    def __init__(self, ws: WebSocket, user_id: int):
        self.ws = ws
        self.user_id = user_id
        self.symbols: set[str] = set()
        self.queue: asyncio.Queue[dict] = asyncio.Queue(QUEUE_SIZE)

    def send(self, message: dict) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            # A browser that cannot keep up misses some ticks rather than slowing others.
            pass


class Hub:
    def __init__(self):
        self.conns: dict[int, set[Conn]] = {}

    def add(self, conn: Conn) -> None:
        self.conns.setdefault(conn.user_id, set()).add(conn)

    def remove(self, conn: Conn) -> None:
        group = self.conns.get(conn.user_id)
        if group:
            group.discard(conn)
            if not group:
                del self.conns[conn.user_id]

    def demand(self) -> set[tuple[int, str]]:
        return {(c.user_id, s) for group in self.conns.values() for c in group for s in c.symbols}

    def dispatch(self, channel: str, payload: dict) -> int:
        """Delivers one worker message to the connections it belongs to. Returns how many got it."""
        user_id = payload.get("u")
        sent = 0
        for conn in list(self.conns.get(user_id, ())):
            if channel == CHANNEL_TICKS:
                if payload.get("s") in conn.symbols:
                    conn.send({"type": "tick", "symbol": payload["s"], **payload.get("d", {})})
                    sent += 1
            elif channel == CHANNEL_STATUS:
                conn.send({"type": "status", "state": payload.get("state"), "detail": payload.get("detail", "")})
                sent += 1
        return sent


hub = Hub()


def _conninfo() -> str:
    url = make_url(get_settings().database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


async def listen(stop: asyncio.Event) -> None:
    """Receives the worker's NOTIFY messages and hands them to the hub. Reconnects if needed."""
    while not stop.is_set():
        try:
            async with await psycopg.AsyncConnection.connect(_conninfo(), autocommit=True) as conn:
                await conn.execute(f"LISTEN {CHANNEL_TICKS}")
                await conn.execute(f"LISTEN {CHANNEL_STATUS}")
                async for note in conn.notifies():
                    try:
                        hub.dispatch(note.channel, json.loads(note.payload))
                    except (ValueError, TypeError):
                        continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("price listener dropped; reconnecting")
            await asyncio.sleep(2)


def record_demand(pairs: set[tuple[int, str]]) -> None:
    if not pairs:
        return
    now = datetime.now(timezone.utc)
    rows = [{"user_id": u, "symbol": s, "seen_at": now} for u, s in pairs]
    stmt = insert(MarketWatch).values(rows)
    stmt = stmt.on_conflict_do_update(index_elements=["user_id", "symbol"], set_={"seen_at": now})
    with get_sessionmaker()() as db:
        db.execute(stmt)
        db.commit()


async def refresh_demand(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(record_demand, hub.demand())
        except Exception:
            log.exception("recording watched symbols failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=DEMAND_REFRESH_SECONDS)
        except asyncio.TimeoutError:
            pass


# ---------- the WebSocket endpoint ----------


def _origin_ok(ws: WebSocket) -> bool:
    # Browsers always send Origin on WebSockets; refuse other sites (cross-site WebSocket hijacking).
    origin = ws.headers.get("origin")
    return origin is not None and urlsplit(origin).netloc == ws.headers.get("host")


def _authenticate(ws: WebSocket) -> int | None:
    with get_sessionmaker()() as db:
        found = auth.find_session(db, ws)  # type: ignore[arg-type]
        if found is None or found[0].mfa_pending:
            return None
        return found[1].id


def _initial_status(user_id: int) -> dict:
    with get_sessionmaker()() as db:
        info = feed_info(db, user_id)
        if info.provider is None:
            return {"type": "status", "state": "no_key", "detail": NO_KEY, "realtime": False}
        row = db.scalar(select(MarketFeedStatus).where(MarketFeedStatus.user_id == user_id))
        state = row.state if row and row.state not in ("idle", "no_key") else "connecting"
        detail = row.detail if row and state == row.state else ""
        return {"type": "status", "state": state, "detail": detail, "realtime": info.realtime}


def clean_symbols(raw: object) -> set[str]:
    if not isinstance(raw, list):
        return set()
    out = set()
    for s in raw[:MAX_SYMBOLS]:
        s = str(s).strip().upper()
        if SYMBOL_RE.match(s):
            out.add(s)
    return out


@router.websocket("/ws")
async def live_socket(ws: WebSocket) -> None:
    if not _origin_ok(ws):
        await ws.close(code=4403)
        return
    user_id = await asyncio.to_thread(_authenticate, ws)
    if user_id is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    conn = Conn(ws, user_id)
    hub.add(conn)
    conn.send(await asyncio.to_thread(_initial_status, user_id))

    async def sender() -> None:
        while True:
            await ws.send_text(json.dumps(await conn.queue.get(), separators=(",", ":")))

    async def recheck() -> None:
        # Sign-out (here or "everywhere") and disabled accounts close live connections too.
        while True:
            await asyncio.sleep(SESSION_RECHECK_SECONDS)
            if await asyncio.to_thread(_authenticate, ws) != user_id:
                await ws.close(code=4401)
                return

    send_task = asyncio.create_task(sender())
    check_task = asyncio.create_task(recheck())
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if isinstance(msg, dict) and msg.get("type") == "subscribe":
                conn.symbols = clean_symbols(msg.get("symbols"))
                await asyncio.to_thread(record_demand, {(user_id, s) for s in conn.symbols})
    except (WebSocketDisconnect, ValueError, RuntimeError):
        pass
    finally:
        hub.remove(conn)
        send_task.cancel()
        check_task.cancel()

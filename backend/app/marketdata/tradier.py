"""Tradier implementation of MarketData. Reference: https://docs.tradier.com (read 2026-10-03).

- Live keys get real-time data and streaming; sandbox keys get 15-minute-delayed data and no streaming.
- Data is for the key owner's personal use only, so one user's data is never shown to another.
- Limit: 120 market-data requests a minute per key (60 in the sandbox).
"""
import asyncio
import json
import time as _time
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import websockets

from app.marketdata.bars import NY, aggregate, date_to_epoch, ny_wall_to_epoch
from app.marketdata.base import (
    INTRADAY,
    Bar,
    Clock,
    DailyClose,
    MarketData,
    MarketDataError,
    OptionQuote,
    Quote,
    StreamEvent,
)

LIVE_URL = "https://api.tradier.com"
SANDBOX_URL = "https://sandbox.tradier.com"
STREAM_URL = "wss://ws.tradier.com/v1/markets/events"

# How far back each chart timeframe loads (calendar days). Tradier keeps 1-minute
# candles for about 20 days and 5/15-minute candles for about 40 days.
INTRADAY_SOURCE = {"1m": ("1min", 5), "5m": ("5min", 25), "15m": ("15min", 40), "1h": ("15min", 40)}
DAILY_SOURCE = {"1D": ("daily", 365 * 3), "1W": ("weekly", 365 * 10)}


def as_list(value: Any) -> list:
    """Tradier returns one item as an object, several as a list, and none as null or 'null'."""
    if value in (None, "null", ""):
        return []
    return value if isinstance(value, list) else [value]


def num(value: Any) -> float | None:
    try:
        return None if value in (None, "", "NaN") else float(value)
    except (TypeError, ValueError):
        return None


def to_tradier(symbol: str) -> str:
    """Tradier writes share classes with a slash: BRK.B -> BRK/B."""
    return symbol.replace(".", "/")


def from_tradier(symbol: str) -> str:
    return symbol.replace("/", ".")


class RateLimiter:
    """Keeps a key under its per-minute request allowance, so Tradier never has to refuse us."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.calls: list[float] = []
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = _time.monotonic()
            self.calls = [t for t in self.calls if now - t < 60]
            if len(self.calls) >= self.per_minute:
                delay = 60 - (now - self.calls[0])
                if delay > 5:
                    raise MarketDataError("Too many market data requests right now. Try again in a minute.", status=429)
                await asyncio.sleep(delay)
            self.calls.append(_time.monotonic())


class TradierMarketData(MarketData):
    def __init__(self, token: str, *, sandbox: bool = False, transport: httpx.AsyncBaseTransport | None = None):
        self.sandbox = sandbox
        self.realtime = not sandbox
        self._token = token
        # Leave some headroom under Tradier's own limit (120 live, 60 sandbox).
        self._limiter = RateLimiter(50 if sandbox else 100)
        self._http = httpx.AsyncClient(
            base_url=SANDBOX_URL if sandbox else LIVE_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(15.0, connect=5.0),
            transport=transport,
        )

    def __repr__(self) -> str:  # never show the token
        return f"TradierMarketData(sandbox={self.sandbox})"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kw) -> dict:
        await self._limiter.wait()
        try:
            resp = await self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise MarketDataError("Cannot reach Tradier right now.") from exc
        if resp.status_code == 401:
            raise MarketDataError("Tradier did not accept the key. Check it in Config → Keys & connections.", status=409)
        if resp.status_code == 429:
            raise MarketDataError("Tradier's request limit was reached. Try again in a minute.", status=429)
        if resp.status_code >= 400:
            raise MarketDataError(f"Tradier returned an error ({resp.status_code}).")
        try:
            return resp.json()
        except ValueError as exc:
            # Tradier answers some bad requests (e.g. an unknown symbol) with plain text.
            raise MarketDataError("Tradier sent an unexpected answer.") from exc

    async def _get(self, path: str, params: dict) -> dict:
        return await self._request("GET", path, params=params)

    # ---------- quotes ----------

    async def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        data = await self._request("POST", "/v1/markets/quotes", data={"symbols": ",".join(map(to_tradier, symbols))})
        out: dict[str, Quote] = {}
        for q in as_list((data.get("quotes") or {}).get("quote")):
            symbol = from_tradier(q.get("symbol", ""))
            trade_date = q.get("trade_date")
            out[symbol] = Quote(
                symbol=symbol,
                description=q.get("description") or "",
                last=num(q.get("last")),
                prev_close=num(q.get("prevclose")),
                change=num(q.get("change")),
                change_pct=num(q.get("change_percentage")),
                bid=num(q.get("bid")),
                ask=num(q.get("ask")),
                open=num(q.get("open")),
                high=num(q.get("high")),
                low=num(q.get("low")),
                volume=num(q.get("volume")),
                trade_time=int(trade_date) if trade_date else None,
            )
        return out

    # ---------- candles ----------

    async def _history(self, symbol: str, interval: str, start: date, end: date) -> list[dict]:
        data = await self._get(
            "/v1/markets/history",
            {"symbol": to_tradier(symbol), "interval": interval, "start": start.isoformat(), "end": end.isoformat()},
        )
        return as_list((data.get("history") or {}).get("day"))

    async def candles(self, symbol: str, timeframe: str, start: date | None = None) -> list[Bar]:
        today = datetime.now(NY).date()
        if timeframe in DAILY_SOURCE:
            interval, days = DAILY_SOURCE[timeframe]
            rows = await self._history(symbol, interval, start or today - timedelta(days=days), today)
            return [
                Bar(date_to_epoch(date.fromisoformat(r["date"])), num(r["open"]), num(r["high"]), num(r["low"]),
                    num(r["close"]), num(r.get("volume")) or 0.0)
                for r in rows
                if num(r.get("open")) is not None and num(r.get("close")) is not None
            ]
        if timeframe not in INTRADAY:
            raise MarketDataError("Unknown timeframe.", status=422)
        interval, days = INTRADAY_SOURCE[timeframe]
        data = await self._get(
            "/v1/markets/timesales",
            {
                "symbol": to_tradier(symbol),
                "interval": interval,
                "start": f"{(today - timedelta(days=days)).isoformat()} 09:30",
                "end": f"{today.isoformat()} 16:00",
                "session_filter": "open",
            },
        )
        bars = [
            Bar(ny_wall_to_epoch(r["time"]), num(r["open"]), num(r["high"]), num(r["low"]), num(r["close"]),
                num(r.get("volume")) or 0.0)
            for r in as_list((data.get("series") or {}).get("data"))
            if r.get("time") and num(r.get("open")) is not None
        ]
        # Aggregating also drops anything outside the regular session.
        return aggregate(bars, timeframe)

    async def daily_closes(self, symbol: str, start: date, end: date) -> list[DailyClose]:
        rows = await self._history(symbol, "daily", start, end)
        return [DailyClose(date.fromisoformat(r["date"]), num(r["close"])) for r in rows if num(r.get("close")) is not None]

    # ---------- options ----------

    async def option_expirations(self, symbol: str) -> list[date]:
        data = await self._get("/v1/markets/options/expirations", {"symbol": to_tradier(symbol), "includeAllRoots": "true"})
        return [date.fromisoformat(d) for d in as_list((data.get("expirations") or {}).get("date"))]

    async def option_chain(self, symbol: str, expiration: date) -> list[OptionQuote]:
        data = await self._get(
            "/v1/markets/options/chains", {"symbol": to_tradier(symbol), "expiration": expiration.isoformat()}
        )
        return [
            OptionQuote(
                symbol=o["symbol"],
                underlying=from_tradier(o.get("underlying") or symbol),
                option_type=o.get("option_type", ""),
                strike=num(o.get("strike")),
                expiration=date.fromisoformat(o.get("expiration_date") or expiration.isoformat()),
                bid=num(o.get("bid")),
                ask=num(o.get("ask")),
                last=num(o.get("last")),
                volume=num(o.get("volume")),
                open_interest=num(o.get("open_interest")),
            )
            for o in as_list((data.get("options") or {}).get("option"))
        ]

    # ---------- market clock ----------

    async def clock(self) -> Clock:
        c = (await self._get("/v1/markets/clock", {})).get("clock") or {}
        return Clock(
            state=c.get("state", "unknown"),
            description=c.get("description", ""),
            next_change=c.get("next_change", ""),
            next_state=c.get("next_state", ""),
        )

    # ---------- live stream ----------

    async def stream(self, symbols: list[str]) -> AsyncIterator[StreamEvent]:
        """Yields live events until the connection drops (then raises). The caller reconnects.
        Tradier allows one market stream per key at a time."""
        if self.sandbox:
            raise MarketDataError("Tradier does not stream sandbox (practice) data.", status=409)
        session = (await self._request("POST", "/v1/markets/events/session")).get("stream") or {}
        session_id = session.get("sessionid")
        if not session_id:
            raise MarketDataError("Tradier did not open a streaming session.")
        # The session answer's "url" is the HTTP-streaming address; the websocket one is fixed.
        async with websockets.connect(STREAM_URL, compression=None, open_timeout=10) as ws:
            await ws.send(json.dumps({
                "symbols": [to_tradier(s) for s in symbols],
                "sessionid": session_id,
                "filter": ["trade", "quote", "summary"],
                "linebreak": True,
                "validOnly": True,
            }))
            # Connected. When the market is closed no prices follow, but the feed is up.
            yield StreamEvent("ready", "")
            async for message in ws:
                for event in parse_stream_message(message):
                    yield event


def parse_stream_message(message: str | bytes) -> list[StreamEvent]:
    """One websocket message can hold several JSON payloads separated by line breaks."""
    if isinstance(message, bytes):
        message = message.decode("utf-8", "replace")
    events: list[StreamEvent] = []
    for line in message.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        if "error" in payload:
            raise MarketDataError(f"Tradier stream error: {payload['error']}")
        kind = payload.get("type")
        symbol = payload.get("symbol")
        if kind not in ("trade", "quote", "summary") or not symbol:
            continue
        if kind == "trade":
            fields = {"last": num(payload.get("price")), "size": num(payload.get("size")),
                      "cvol": num(payload.get("cvol")), "time": int(payload["date"]) if payload.get("date") else None}
        elif kind == "quote":
            fields = {"bid": num(payload.get("bid")), "ask": num(payload.get("ask"))}
        else:
            fields = {"open": num(payload.get("open")), "high": num(payload.get("high")),
                      "low": num(payload.get("low")), "prev_close": num(payload.get("prevClose"))}
        events.append(StreamEvent(kind, from_tradier(symbol), {k: v for k, v in fields.items() if v is not None}))
    return events

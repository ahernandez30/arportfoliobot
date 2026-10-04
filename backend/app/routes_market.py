"""Market data for the signed-in user (candles, watchlist, quotes) and saved screen layouts.

Every call uses the signed-in user's own key; nothing is shared between users.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import layouts, user_settings
from app.auth import current_user
from app.db import get_db
from app.errors import first_error
from app.marketdata import service
from app.marketdata.base import TIMEFRAMES, MarketData, MarketDataError
from app.models import User
from app.user_settings import SYMBOL_RE

router = APIRouter()

providers = service.ProviderCache()
cache = service.TTLCache()


def _provider(db: Session, user: User) -> MarketData:
    md = providers.get(db, user.id)
    if md is None:
        raise HTTPException(409, service.NO_KEY)
    return md


def _symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if not SYMBOL_RE.match(s):
        raise HTTPException(422, "That is not a valid symbol.")
    return s


async def _call(coro):
    try:
        return await coro
    except MarketDataError as exc:
        raise HTTPException(exc.status, str(exc))


@router.get("/api/market/status")
async def market_status(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    info = service.feed_info(db, user.id)
    out = {"provider": info.provider, "realtime": info.realtime, "clock": None, "detail": None}
    if info.provider is None:
        out["detail"] = service.NO_KEY
        return out
    md = _provider(db, user)
    key = (user.id, "clock")
    clock = cache.get(key)
    if clock is None:
        try:
            clock = await md.clock()
            cache.put(key, clock, 60)
        except MarketDataError as exc:
            out["detail"] = str(exc)
            return out
    out["clock"] = {"state": clock.state, "description": clock.description,
                    "next_change": clock.next_change, "next_state": clock.next_state}
    return out


@router.post("/api/market/test")
async def test_connection(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The Config 'Test connection' button. Asks for a real quote: Tradier answers the market
    clock even for a wrong key, so the clock alone proves nothing."""
    info = service.feed_info(db, user.id)
    md = _provider(db, user)
    quotes = await _call(md.quotes(["SPY"]))
    if "SPY" not in quotes:
        raise HTTPException(502, "Tradier answered but sent no price. Try again in a minute.")
    clock = await _call(md.clock())
    which = "live account key" if info.provider == "tradier" else "sandbox (practice) key"
    speed = "real-time prices" if info.realtime else "prices delayed 15 minutes"
    return {"ok": True, "message": f"Connected to Tradier with your {which} ({speed}). "
                                   f"SPY last price {quotes['SPY'].last}. {clock.description}."}


@router.get("/api/market/candles")
async def get_candles(
    symbol: str = Query(max_length=12),
    tf: str = Query(max_length=4),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    symbol = _symbol(symbol)
    if tf not in TIMEFRAMES:
        raise HTTPException(422, "Unknown timeframe.")
    md = _provider(db, user)
    bars = await _call(service.candles(cache, user.id, md, symbol, tf))
    return {
        "symbol": symbol,
        "timeframe": tf,
        "realtime": md.realtime,
        "bars": [{"time": b.time, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
                 for b in bars],
    }


@router.get("/api/market/quote")
async def get_quote(symbol: str = Query(max_length=12), user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    symbol = _symbol(symbol)
    md = _provider(db, user)
    q = (await _call(md.quotes([symbol]))).get(symbol)
    if q is None:
        raise HTTPException(404, f"{symbol} was not found.")
    return {"symbol": q.symbol, "description": q.description, "last": q.last, "prev_close": q.prev_close,
            "change_pct": q.change_pct, "bid": q.bid, "ask": q.ask}


@router.get("/api/market/watchlist")
async def get_watchlist(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    symbols = user_settings.load(db, user.id).watchlist.symbols
    md = _provider(db, user)
    rows = await _call(service.watchlist(cache, user.id, md, symbols))
    return {"realtime": md.realtime, "rows": rows}


# ---------- layouts ----------


def _ticker(db: Session, user: User) -> str:
    return user_settings.load(db, user.id).watchlist.default_ticker


@router.get("/api/layouts/charts")
def get_chart_layout(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return layouts.load_charts(db, user.id, _ticker(db, user)).model_dump()


@router.put("/api/layouts/charts")
def put_chart_layout(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    try:
        layout = layouts.ChartsLayout.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, first_error(exc.errors()))
    layouts.save_charts(db, user.id, layout)
    db.commit()
    return layout.model_dump()


@router.get("/api/layouts/dashboard")
def get_dashboard_layout(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return layouts.load_dashboard(db, user.id, _ticker(db, user)).model_dump()


@router.put("/api/layouts/dashboard")
def put_dashboard_layout(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    try:
        layout = layouts.DashboardModel.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, first_error(exc.errors()))
    layouts.save_dashboard(db, user.id, layout)
    db.commit()
    return layout.model_dump()


@router.delete("/api/layouts/dashboard")
def reset_dashboard_layout(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    layout = layouts.default_dashboard(_ticker(db, user))
    layouts.save_dashboard(db, user.id, layout)
    db.commit()
    return layout.model_dump()

"""Live Trader: option chains, the manual paper order ticket, open positions, and the
Stop all trading / reset switches. Only the paper broker exists until Stage 8."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy.orm import Session

from app import paper, paper_rules, paper_view, routes_market, user_settings
from app.auth import current_user
from app.broker.base import OptionOrder
from app.broker.paper import PaperBroker
from app.db import get_db
from app.inputs import Strict
from app.marketdata import service
from app.marketdata.bars import NY
from app.marketdata.base import MarketData, MarketDataError
from app.models import User
from app.paper import PaperError
from app.paper_rules import Book, order_value
from app.routes_market import _call, _provider, _symbol

router = APIRouter()


def _md(db: Session, user: User) -> MarketData | None:
    return routes_market.providers.get(db, user.id)


# ---------- option chain ----------


@router.get("/api/market/expirations")
async def expirations(symbol: str = Query(max_length=12), user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict:
    symbol = _symbol(symbol)
    md = _provider(db, user)
    key = (user.id, "expirations", symbol)
    days = routes_market.cache.get(key)
    if days is None:
        days = await _call(md.option_expirations(symbol))
        routes_market.cache.put(key, days, 600)
    today = datetime.now(NY).date()
    return {"symbol": symbol, "expirations": [d.isoformat() for d in days if d >= today]}


@router.get("/api/market/chain")
async def chain(symbol: str = Query(max_length=12), expiration: date = Query(), user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    """Calls and puts side by side by strike, with the stock's own price."""
    symbol = _symbol(symbol)
    md = _provider(db, user)
    key = (user.id, "chain", symbol, expiration)
    options = routes_market.cache.get(key)
    if options is None:
        options = await _call(md.option_chain(symbol, expiration))
        routes_market.cache.put(key, options, 4)
    under = (await _call(md.quotes([symbol]))).get(symbol)
    rows: dict[float, dict] = {}
    for o in options:
        if o.strike is None or o.option_type not in ("call", "put"):
            continue
        row = rows.setdefault(o.strike, {"strike": o.strike, "call": None, "put": None})
        row[o.option_type] = {"symbol": o.symbol, "bid": o.bid, "ask": o.ask, "last": o.last,
                              "volume": o.volume, "open_interest": o.open_interest}
    return {"symbol": symbol, "expiration": expiration.isoformat(), "realtime": md.realtime,
            "underlying": {"last": under.last, "change_pct": under.change_pct, "description": under.description}
            if under else None,
            "rows": [rows[k] for k in sorted(rows)]}


# ---------- the paper account ----------


AccountName = Literal["main", "directional", "credit_spread", "debit_spread"]


async def _summary(db: Session, user: User, account: str = paper.MAIN) -> dict:
    out = await paper_view.summary(db, user.id, _md(db, user), account)
    if _md(db, user) is None:
        out["prices"]["detail"] = service.NO_KEY
    return out


@router.get("/api/paper")
async def get_paper(account: AccountName = "main", user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    return await _summary(db, user, account)


@router.get("/api/paper/events")
def get_events(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [paper_view.event_out(e) for e in paper.events(db, user.id, 100)]


class TicketIn(Strict):
    symbol: str = Field(max_length=12)
    option_type: Literal["call", "put"]
    strike: Decimal = Field(gt=0, le=Decimal("1000000"), decimal_places=3)
    expiration: date
    quantity: int = Field(ge=1, le=10000)
    limit_price: Decimal = Field(gt=0, le=Decimal("100000"), decimal_places=2)
    take_profit_pct: Decimal | None = Field(None, gt=0, le=1000, decimal_places=3)
    stop_loss_pct: Decimal | None = Field(None, gt=0, le=100, decimal_places=3)


def _contract(body: TicketIn) -> paper.Contract:
    return paper.Contract(_symbol(body.symbol), body.option_type, body.strike, body.expiration)


async def _clock_open(db: Session, user: User, md: MarketData) -> bool:
    key = (user.id, "clock")
    clock = routes_market.cache.get(key)
    if clock is None:
        try:
            clock = await md.clock()
        except MarketDataError:
            return False
        routes_market.cache.put(key, clock, 60)
    return paper_rules.session_open(clock.state, datetime.now(NY))


async def _book(md: MarketData, occ: str) -> Book | None:
    try:
        q = (await md.quotes([occ])).get(occ)
    except MarketDataError:
        return None
    return Book.of(q.bid, q.ask) if q else None


@router.post("/api/paper/orders/review")
async def review(body: TicketIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The review step before every order: cost, target and stop prices, whether it would fill
    now, and any reason it would be refused."""
    c = _contract(body)
    md = _provider(db, user)
    book = await _book(md, c.occ)
    if book is None:
        raise HTTPException(404, f"No quote for {c.label}. Check the strike and expiration.")
    rule = user_settings.load(db, user.id).paper.fill_rule
    settings = user_settings.load(db, user.id)
    acct = paper.account(db, user.id)
    db.commit()
    problem = paper_rules.opening_order_problem(
        halted=paper.controls(db, user.id).halted, quantity=body.quantity, limit=body.limit_price,
        available_cash=acct.cash - paper.reserved_cash(db, acct.id), realized_today=paper.realized_today(db, user.id),
        limits=paper_rules.Limits(Decimal(str(settings.trading.max_order_usd)), Decimal(str(settings.trading.max_daily_loss_usd))),
        expiration=c.expiration, today=datetime.now(NY).date())
    db.commit()
    market_open = await _clock_open(db, user, md)
    fill = paper_rules.fill_price("buy", book, rule, body.limit_price)
    est = fill if fill is not None else body.limit_price
    tp, sl = paper_rules.exit_prices(est, body.take_profit_pct, body.stop_loss_pct)
    return {
        "label": c.label, "occ_symbol": c.occ, "bid": float(book.bid) if book.bid is not None else None,
        "ask": float(book.ask) if book.ask is not None else None,
        "max_cost": float(order_value(body.quantity, body.limit_price)),
        "fill_now": market_open and fill is not None, "fill_price": float(fill) if fill is not None else None,
        "market_open": market_open, "take_profit_price": float(tp) if tp is not None else None,
        "stop_loss_price": float(sl) if sl is not None else None, "problem": problem, "fill_rule": rule,
    }


async def _fill_now(db: Session, user: User, order_id: int, occ: str) -> None:
    """Tries to fill a new order straight away; otherwise the worker keeps trying."""
    md = _md(db, user)
    if md is None or not await _clock_open(db, user, md):
        return
    book = await _book(md, occ)
    if book is not None:
        paper.try_fill(db, order_id, book, user_settings.load(db, user.id).paper.fill_rule)
        db.commit()


@router.post("/api/paper/orders")
async def place_order(body: TicketIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    c = _contract(body)
    try:
        order_id = PaperBroker(db, user.id).place_order(OptionOrder(
            symbol=c.symbol, option_type=c.option_type, strike=c.strike, expiration=c.expiration,
            quantity=body.quantity, limit_price=body.limit_price, take_profit_pct=body.take_profit_pct,
            stop_loss_pct=body.stop_loss_pct))
    except PaperError as exc:
        raise HTTPException(exc.status, str(exc))
    db.commit()
    await _fill_now(db, user, order_id, c.occ)
    out = await _summary(db, user)
    out["order"] = next((o for o in out["orders"] if o["id"] == order_id), None) or \
        paper_view.order_out(db.get(paper.PaperOrder, order_id))
    return out


class CloseIn(Strict):
    quantity: int | None = Field(None, ge=1, le=10000)
    # None: sell at the market (the current bid, or mid under the mid rule).
    limit_price: Decimal | None = Field(None, gt=0, le=Decimal("100000"), decimal_places=2)


@router.post("/api/paper/positions/{position_id}/close")
async def close_position(position_id: int, body: CloseIn, user: User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict:
    """The Close button (all contracts, at the market) or a sell ticket for part of a position.
    A spread always closes whole, at the market, both legs together."""
    pos = paper.own_position(db, user.id, position_id)
    if pos is not None and pos.status == "open" and pos.structure != "single":
        return await _close_spread(db, user, pos)
    try:
        order = paper.place_close(db, user.id, position_id, body.quantity, body.limit_price, "manual",
                                  replace_working=body.quantity is None)
    except PaperError as exc:
        raise HTTPException(exc.status, str(exc))
    order_id, occ = order.id, order.occ_symbol
    db.commit()
    await _fill_now(db, user, order_id, occ)
    return await _summary(db, user)


async def _close_spread(db: Session, user: User, pos: paper.PaperPosition) -> dict:
    md = _provider(db, user)
    if not await _clock_open(db, user, md):
        raise HTTPException(409, "The options market is closed; a spread can only be closed while it is open.")
    try:
        quotes = await md.quotes([pos.occ_symbol, pos.occ_symbol2])
    except MarketDataError as exc:
        raise HTTPException(exc.status, str(exc))
    books = {s: Book.of(q.bid, q.ask) for s, q in quotes.items()}
    rule = user_settings.load(db, user.id).paper.fill_rule
    account = db.get(paper.PaperAccount, pos.account_id).name
    locked = paper.lock_open(db, pos.id)
    if locked is None:
        raise HTTPException(404, "That position is not open any more.")
    price = paper.closing_price(locked, books, rule)
    if price is None:
        raise HTTPException(409, "No two-sided quote on both legs right now; try again in a moment.")
    paper.close_whole_at(db, locked, price, "manual", source="manual", detail="Closed by you.")
    db.commit()
    return await _summary(db, user, account)


class ExitsIn(Strict):
    take_profit_price: Decimal | None = Field(None, gt=0, le=Decimal("100000"), decimal_places=2)
    stop_loss_price: Decimal | None = Field(None, ge=0, le=Decimal("100000"), decimal_places=2)


@router.patch("/api/paper/positions/{position_id}")
async def edit_exits(position_id: int, body: ExitsIn, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    """Change a position's target or stop price (empty removes it)."""
    pos = paper.own_position(db, user.id, position_id, lock=True)
    if pos is None or pos.status != "open":
        raise HTTPException(404, "That position is not open any more.")
    if body.take_profit_price is not None and body.stop_loss_price is not None \
            and body.stop_loss_price >= body.take_profit_price:
        raise HTTPException(422, "The stop must be below the target.")
    pos.take_profit_price = body.take_profit_price
    pos.stop_loss_price = body.stop_loss_price
    paper.log(db, user.id, pos.account_id, "exits_changed",
              f"{paper.contract_of(pos).label}: target {body.take_profit_price or 'none'}, stop {body.stop_loss_price or 'none'}.",
              position_id=pos.id)
    db.commit()
    return await _summary(db, user)


@router.delete("/api/paper/orders/{order_id}")
async def cancel_order(order_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    try:
        PaperBroker(db, user.id).cancel_order(order_id)
    except PaperError as exc:
        raise HTTPException(exc.status, str(exc))
    db.commit()
    return await _summary(db, user)


@router.post("/api/paper/stop-all")
async def stop_all(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    paper.stop_all(db, user.id)
    db.commit()
    return await _summary(db, user)


@router.post("/api/paper/resume")
async def resume(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    paper.resume(db, user.id)
    db.commit()
    return await _summary(db, user)


class PauseIn(Strict):
    paused: bool


@router.post("/api/paper/auto-pause")
async def auto_pause(body: PauseIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    paper.set_auto_paused(db, user.id, body.paused)
    db.commit()
    return await _summary(db, user)


@router.post("/api/paper/reset")
async def reset(account: AccountName = "main", user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    paper.reset(db, user.id, account)
    db.commit()
    return await _summary(db, user, account)

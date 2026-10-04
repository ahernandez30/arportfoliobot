"""Capital Tracking and Account Manager for the signed-in user.

No handler takes a user id from the request; every lookup is by the session's user, so
another user's record answers 404 exactly like one that does not exist.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app import capital, ledger, paper_view, routes_market, trade_log, user_settings
from app.auth import current_user
from app.db import get_db
from app.inputs import FlowIn, ManualTrade, NewPosition, NotesEdit, PositionEdit, PositionTrade
from app.ledger import Fill
from app.marketdata import service
from app.models import CapitalFlow, ClosedTrade, LongTermPosition, LongTermTrade, User
from app.routes_market import _symbol

router = APIRouter()

NOT_FOUND = "That record was not found. It may have been deleted."


def _ledger_problem(exc: ledger.LedgerError) -> HTTPException:
    return HTTPException(409, str(exc))


async def _summary(db: Session, user: User) -> dict:
    # Same per-user provider objects as the market screens, so they share one rate limit.
    md = routes_market.providers.get(db, user.id)
    out, problem = await capital.build_summary(db, user.id, md)
    out["prices"] = {"available": md is not None and problem is None,
                     "detail": problem or (None if md else service.NO_KEY)}
    return out


# ---------- Capital Tracking ----------


@router.get("/api/capital")
async def get_capital(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return await _summary(db, user)


@router.post("/api/capital/positions")
async def add_position(body: NewPosition, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Records a purchase. Buying more of something already held adds to that position
    (its average cost is updated) instead of making a second row."""
    p = capital.find_open(db, user.id, body.kind, body.symbol, body.option_type, body.strike, body.expiration)
    note = body.note
    if p is None:
        # A new position keeps the note itself; a buy added to an existing one keeps it on the buy.
        p = LongTermPosition(user_id=user.id, kind=body.kind, symbol=body.symbol, option_type=body.option_type,
                             strike=body.strike, expiration=body.expiration, note=body.note)
        db.add(p)
        db.flush()
        note = ""
    trade = LongTermTrade(user_id=user.id, position_id=p.id, side="buy", quantity=body.quantity, price=body.price,
                          fees=body.fees, day=body.day, note=note)
    existing = capital.trades_by_position(db, user.id).get(p.id, [])
    try:
        capital.check_trades(p, [*existing, Fill("buy", body.quantity, body.price, body.fees, body.day)])
    except ledger.LedgerError as exc:
        raise _ledger_problem(exc)
    db.add(trade)
    db.commit()
    return await _summary(db, user)


def _own_position(db: Session, user: User, position_id: int) -> LongTermPosition:
    p = capital.position(db, user.id, position_id)
    if p is None:
        raise HTTPException(404, NOT_FOUND)
    return p


@router.post("/api/capital/positions/{position_id}/trades")
async def add_position_trade(position_id: int, body: PositionTrade, user: User = Depends(current_user),
                             db: Session = Depends(get_db)) -> dict:
    """Buy more of a position, or sell some or all of it."""
    p = _own_position(db, user, position_id)
    existing = capital.trades_by_position(db, user.id).get(p.id, [])
    try:
        capital.check_trades(p, [*existing, Fill(body.side, body.quantity, body.price, body.fees, body.day,
                                                 id=2**62)])
    except ledger.LedgerError as exc:
        raise _ledger_problem(exc)
    db.add(LongTermTrade(user_id=user.id, position_id=p.id, side=body.side, quantity=body.quantity,
                         price=body.price, fees=body.fees, day=body.day, note=body.note))
    db.commit()
    return await _summary(db, user)


@router.patch("/api/capital/positions/{position_id}")
async def edit_position(position_id: int, body: PositionEdit, user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> dict:
    p = _own_position(db, user, position_id)
    p.note = body.note
    db.commit()
    return await _summary(db, user)


@router.delete("/api/capital/positions/{position_id}")
async def delete_position(position_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Removes a position and all its buys and sells (for one entered by mistake)."""
    if not capital.delete_position(db, user.id, position_id):
        raise HTTPException(404, NOT_FOUND)
    db.commit()
    return await _summary(db, user)


def _own_trade(db: Session, user: User, trade_id: int) -> tuple[LongTermTrade, LongTermPosition]:
    t = db.get(LongTermTrade, trade_id)
    if t is None or t.user_id != user.id:
        raise HTTPException(404, NOT_FOUND)
    return t, _own_position(db, user, t.position_id)


@router.put("/api/capital/trades/{trade_id}")
async def edit_position_trade(trade_id: int, body: PositionTrade, user: User = Depends(current_user),
                              db: Session = Depends(get_db)) -> dict:
    t, p = _own_trade(db, user, trade_id)
    others = [x for x in capital.trades_by_position(db, user.id).get(p.id, []) if x.id != t.id]
    try:
        capital.check_trades(p, [*others, Fill(body.side, body.quantity, body.price, body.fees, body.day, id=t.id)])
    except ledger.LedgerError as exc:
        raise _ledger_problem(exc)
    for k in ("side", "quantity", "price", "fees", "day", "note"):
        setattr(t, k, getattr(body, k))
    db.commit()
    return await _summary(db, user)


@router.delete("/api/capital/trades/{trade_id}")
async def delete_position_trade(trade_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Removes one buy or sell. Removing the last one removes the position too."""
    t, p = _own_trade(db, user, trade_id)
    others = [x for x in capital.trades_by_position(db, user.id).get(p.id, []) if x.id != t.id]
    try:
        capital.check_trades(p, others)
    except ledger.LedgerError as exc:
        raise HTTPException(409, f"Cannot remove it: the sales after it would no longer add up. {exc}")
    db.delete(t)
    db.flush()
    if not others:
        db.delete(p)
    db.commit()
    return await _summary(db, user)


@router.get("/api/capital/history")
async def capital_history(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Daily end-of-day values (taken by the worker after each close) and every deposit or
    withdrawal, for the capital-over-time chart."""
    return {
        "snapshots": [{"day": s.day.isoformat(), "total": capital.money(s.total), "put_in": capital.money(s.put_in),
                       "estimated": s.estimated} for s in capital.snapshots(db, user.id)],
        "flows": [capital.flow_out(f) for f in capital.flows(db, user.id, "long_term")],
    }


# ---------- deposits and withdrawals (both accounts) ----------


@router.get("/api/flows")
def list_flows(account: Literal["long_term", "short_term"], user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> list[dict]:
    return [capital.flow_out(f) for f in capital.flows(db, user.id, account)]


@router.post("/api/flows")
def add_flow(body: FlowIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    f = CapitalFlow(user_id=user.id, account=body.account, kind=body.kind, amount=body.amount, day=body.day,
                    note=body.note)
    db.add(f)
    db.commit()
    return capital.flow_out(f)


@router.delete("/api/flows/{flow_id}")
def delete_flow(flow_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    f = db.get(CapitalFlow, flow_id)
    if f is None or f.user_id != user.id:
        raise HTTPException(404, NOT_FOUND)
    db.delete(f)
    db.commit()
    return {"ok": True}


# ---------- Account Manager ----------


def _tz(db: Session, user: User) -> ZoneInfo:
    return ZoneInfo(user_settings.load(db, user.id).display.timezone)


def _filtered(db: Session, user: User, mode: str, period: str, start: date | None, end: date | None,
              symbol: str | None) -> list[ClosedTrade]:
    window = trade_log.period_window(period, _tz(db, user), datetime.now(timezone.utc), start, end)
    return trade_log.query(db, user.id, mode, window, _symbol(symbol) if symbol else None)


Period = Literal["today", "week", "month", "year", "all", "custom"]


@router.get("/api/trades")
async def list_trades(
    mode: Literal["paper", "real"] = "real",
    period: Period = "month",
    start: date | None = None,
    end: date | None = None,
    symbol: str | None = Query(None, max_length=12),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    trades = _filtered(db, user, mode, period, start, end, symbol)
    if mode == "real":
        value, estimated = trade_log.real_account_value(db, user.id), False
    else:
        # The paper account itself: cash plus open positions at live prices.
        acct = (await paper_view.summary(db, user.id, routes_market.providers.get(db, user.id)))["account"]
        value, estimated = Decimal(str(acct["total"])), acct["estimated"]
    return {
        "mode": mode,
        "account_value": capital.money(value),
        "account_value_estimated": estimated,
        "stats": trade_log.stats_out(trades),
        "trades": [trade_log.trade_out(t) for t in trades],
        "symbols": sorted({t.symbol for t in trade_log.query(db, user.id, mode)}),
    }


@router.get("/api/trades.csv")
def export_trades(
    mode: Literal["paper", "real"] = "real",
    period: Period = "month",
    start: date | None = None,
    end: date | None = None,
    symbol: str | None = Query(None, max_length=12),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    trades = _filtered(db, user, mode, period, start, end, symbol)
    name = f"trades-{mode}-{datetime.now(_tz(db, user)):%Y-%m-%d}.csv"
    return Response(trade_log.csv_text(trades, _tz(db, user)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _apply_manual(t: ClosedTrade, body: ManualTrade, tz: ZoneInfo) -> None:
    for k in ("kind", "symbol", "option_type", "strike", "expiration", "direction", "quantity", "entry_price",
              "exit_price", "fees", "close_reason", "notes"):
        setattr(t, k, getattr(body, k))
    t.opened_at = trade_log.to_utc(body.opened_at, tz)
    t.closed_at = trade_log.to_utc(body.closed_at, tz)
    if t.closed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(422, "The close time cannot be in the future.")


@router.post("/api/trades")
def add_trade(body: ManualTrade, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """A real trade typed in by hand. Paper trades are only written by the paper engine."""
    t = ClosedTrade(user_id=user.id, mode="real", source="manual")
    _apply_manual(t, body, _tz(db, user))
    db.add(t)
    db.commit()
    return trade_log.trade_out(t)


def _own_closed(db: Session, user: User, trade_id: int) -> ClosedTrade:
    t = trade_log.get(db, user.id, trade_id)
    if t is None:
        raise HTTPException(404, NOT_FOUND)
    return t


@router.put("/api/trades/{trade_id}")
def edit_trade(trade_id: int, body: ManualTrade, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    t = _own_closed(db, user, trade_id)
    if not trade_log.editable(t):
        raise HTTPException(409, "Trades placed by the site cannot be changed, only their notes.")
    _apply_manual(t, body, _tz(db, user))
    db.commit()
    return trade_log.trade_out(t)


@router.patch("/api/trades/{trade_id}")
def edit_trade_notes(trade_id: int, body: NotesEdit, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    t = _own_closed(db, user, trade_id)
    t.notes = body.notes
    db.commit()
    return trade_log.trade_out(t)


@router.delete("/api/trades/{trade_id}")
def delete_trade(trade_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    t = _own_closed(db, user, trade_id)
    if not trade_log.editable(t):
        raise HTTPException(409, "Trades placed by the site are a permanent record and cannot be deleted.")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ---------- dashboard ----------


@router.get("/api/totals")
async def totals(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The dashboard's account totals tile."""
    summary = await _summary(db, user)
    p = await paper_view.summary(db, user.id, routes_market.providers.get(db, user.id))
    return {
        "long_term": {**summary["totals"], "has_records": capital.has_records(db, user.id)},
        "short_term": {
            "value": capital.money(trade_log.real_account_value(db, user.id)),
            "has_records": bool(capital.flows(db, user.id, "short_term") or trade_log.query(db, user.id, "real")),
        },
        "paper": {**p["account"], "open_positions": len(p["positions"])},
        "prices": summary["prices"],
    }

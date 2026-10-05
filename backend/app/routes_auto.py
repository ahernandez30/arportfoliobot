"""Automatic paper trading (Stage 6): “What to trade on a signal” per pegged symbol and timeframe,
a preview of the trade a signal would lead to right now, the Live Trader status strip, and the
list of signals with the paper trades they caused. Only the signed-in user's own records."""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auto_plan, auto_trader, paper, routes_market, user_settings
from app.auth import current_user
from app.db import get_db
from app.inputs import Strict
from app.marketdata.bars import NY
from app.marketdata.base import MarketDataError
from app.models import PaperPosition, StrategyPreset, StrategyTrade, User
from app.routes_market import _provider, _symbol
from app.routes_strategy import _inputs, _strategy, _timeframe
from app.strategy import service

router = APIRouter(prefix="/api/auto")


def _preset(db: Session, user: User, strategy: str, symbol: str, timeframe: str, *, lock: bool = False
            ) -> StrategyPreset | None:
    stmt = select(StrategyPreset).where(StrategyPreset.user_id == user.id, StrategyPreset.strategy == strategy,
                                        StrategyPreset.symbol == symbol, StrategyPreset.timeframe == timeframe)
    return db.scalar(stmt.with_for_update() if lock else stmt)


def _plan_out(p: StrategyPreset | None, settings: user_settings.SettingsModel) -> dict:
    trade = auto_plan.load_settings(p.trade if p else None)
    return {"pegged": p is not None, "trade": trade.model_dump(), "auto": bool(p and p.auto),
            "auto_since": p.auto_since.isoformat() if p and p.auto_since else None,
            "auto_trading": settings.trading.auto_trading}


@router.get("/plan")
def get_plan(strategy: str, symbol: str = Query(max_length=12), timeframe: str = Query(max_length=4),
             user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    s = _strategy(strategy)
    p = _preset(db, user, s.id, _symbol(symbol), _timeframe(timeframe))
    return _plan_out(p, user_settings.load(db, user.id))


class PlanIn(Strict):
    strategy: str = Field(max_length=40)
    symbol: str = Field(max_length=12)
    timeframe: str = Field(max_length=4)
    trade: dict[str, Any]
    auto: bool


def _trade_settings(raw: dict) -> auto_plan.TradeSettings:
    try:
        return auto_plan.TradeSettings.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise HTTPException(422, f"{'.'.join(str(x) for x in first['loc'])}: {first['msg']}")


@router.put("/plan")
def put_plan(body: PlanIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Saves “What to trade on a signal” with the pegged settings, and switches placing paper trades
    from this symbol and timeframe's signals on or off. Automatic trades always use the pegged inputs."""
    s = _strategy(body.strategy)
    symbol, tf = _symbol(body.symbol), _timeframe(body.timeframe)
    trade = _trade_settings(body.trade)
    p = _preset(db, user, s.id, symbol, tf, lock=True)
    if p is None:
        raise HTTPException(409, f"Peg settings to {symbol} {tf} first: automatic trades use the pegged settings.")
    old = auto_plan.load_settings(p.trade)
    p.trade = trade.model_dump()
    src = auto_trader.source_name(s.id, symbol, tf)
    if body.auto and not p.auto:
        p.auto, p.auto_since = True, datetime.now(timezone.utc)
        paper.log(db, user.id, None, "auto_on", f"Paper trades from {symbol} {tf} signals switched on "
                  f"({auto_plan.STRUCTURE_LABELS.get(trade.structure, 'all three structures, compared')}). "
                  "Only signals on candles closing from now on count.", source=src)
    elif not body.auto and p.auto:
        p.auto = False
        paper.log(db, user.id, None, "auto_off", f"Paper trades from {symbol} {tf} signals switched off. Trades "
                  "already open still close when the strategy exits.", source=src)
    elif old != trade:
        paper.log(db, user.id, None, "auto_settings", f"“What to trade on a signal” changed for {symbol} {tf}.",
                  source=src)
    db.commit()
    return _plan_out(p, user_settings.load(db, user.id))


class PreviewIn(Strict):
    strategy: str = Field(max_length=40)
    symbol: str = Field(max_length=12)
    timeframe: str = Field(max_length=4)
    # The inputs to take the history from (the screen's current ones); automatic trades use the pegged set.
    inputs: dict[str, Any] = Field(default_factory=dict)
    trade: dict[str, Any]


@router.post("/preview")
async def preview(body: PreviewIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """What a BUY and a SELL signal would trade right now under these settings, for each structure:
    the strike distance and expiration rule worked out from the strategy's history, the contracts,
    size, most it can lose and make, and the payout ratio (shown before any trade, plan 7.6)."""
    s = _strategy(body.strategy)
    symbol, tf = _symbol(body.symbol), _timeframe(body.timeframe)
    trade = _trade_settings(body.trade)
    inputs = _inputs(s, body.inputs)
    md = _provider(db, user)
    rule = user_settings.load(db, user.id).paper.fill_rule
    try:
        data = await service.load(routes_market.cache, user.id, md, s, symbol, tf, inputs)
        run = await service.run(s, data, inputs)
        quote = (await md.quotes([symbol])).get(symbol)
    except MarketDataError as exc:
        raise HTTPException(exc.status, str(exc))
    if quote is None or quote.last is None:
        raise HTTPException(404, f"No price for {symbol}.")
    history = auto_plan.history_of(run)
    rules = auto_plan.rules_for(trade, history)
    price = auto_trader.to_dec(quote.last)
    today = datetime.now(NY).date()
    rows = []
    for structure in auto_plan.STRUCTURES if trade.structure == "compare" else (trade.structure,):
        for direction in (1, -1):
            try:
                out = await auto_trader.choose_contract(md, trade, structure, symbol, direction, price, history, rule,
                                                        today)
            except MarketDataError as exc:
                out = str(exc)
            row = {"structure": structure, "label": auto_plan.STRUCTURE_LABELS[structure],
                   "signal": "BUY" if direction == 1 else "SELL"}
            if isinstance(out, str):
                row["problem"] = out
            else:
                row.update(out[1])
            rows.append(row)
    return {"stock_price": quote.last, "history": {"winners": history.winners, "avg_win_move": history.avg_win_move,
                                                  "avg_days": history.avg_days, "avg_loss_days": history.avg_loss_days},
            "rules": {"distance_pct": rules.distance_pct, "distance_note": rules.distance_note,
                      "hold_days": rules.hold_days, "hold_note": rules.hold_note},
            "fill_rule": rule, "rows": rows}


def _trade_out(t: StrategyTrade, pos: PaperPosition | None) -> dict:
    return {
        "id": t.id, "strategy": t.strategy, "symbol": t.symbol, "timeframe": t.timeframe, "structure": t.structure,
        "structure_label": auto_plan.STRUCTURE_LABELS[t.structure], "account": t.account_name,
        "account_label": paper.ACCOUNT_LABELS.get(t.account_name, t.account_name),
        "signal_time": t.signal_time, "signal": "BUY" if t.direction == 1 else "SELL", "candle_type": t.candle_type,
        "signal_price": float(t.signal_price), "status": t.status, "detail": t.detail, "plan": t.plan,
        "position_id": t.position_id, "position_label": paper.label_of(pos) if pos else None,
        "under_entry": float(t.under_entry) if t.under_entry is not None else None,
        "under_target": float(t.under_target) if t.under_target is not None else None,
        "under_stop": float(t.under_stop) if t.under_stop is not None else None,
        "under_exit": float(t.under_exit) if t.under_exit is not None else None,
        "exit_reason": t.exit_reason, "created_at": t.created_at.isoformat(),
        "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
    }


@router.get("/trades")
def trades(strategy: str | None = None, symbol: str | None = Query(None, max_length=12),
           timeframe: str | None = Query(None, max_length=4), limit: int = Query(50, ge=1, le=500),
           user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """Signals the worker acted on, newest first, each with the paper trade it caused."""
    stmt = select(StrategyTrade).where(StrategyTrade.user_id == user.id)
    if strategy:
        stmt = stmt.where(StrategyTrade.strategy == _strategy(strategy).id)
    if symbol:
        stmt = stmt.where(StrategyTrade.symbol == _symbol(symbol))
    if timeframe:
        stmt = stmt.where(StrategyTrade.timeframe == _timeframe(timeframe))
    rows = list(db.scalars(stmt.order_by(StrategyTrade.signal_time.desc(), StrategyTrade.id.desc()).limit(limit)))
    ids = [t.position_id for t in rows if t.position_id]
    positions = {p.id: p for p in db.scalars(select(PaperPosition).where(PaperPosition.id.in_(ids)))} if ids else {}
    return [_trade_out(t, positions.get(t.position_id)) for t in rows]


@router.get("/status")
def status(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The Live Trader status strip: is automatic trading on, what drives it, and any warning."""
    settings = user_settings.load(db, user.id)
    ctl = paper.controls(db, user.id)
    db.commit()
    runs = list(db.scalars(select(StrategyPreset).where(StrategyPreset.user_id == user.id, StrategyPreset.auto.is_(True))
                           .order_by(StrategyPreset.symbol, StrategyPreset.timeframe)))
    active = list(db.scalars(select(StrategyTrade).where(StrategyTrade.user_id == user.id,
                                                         StrategyTrade.status.in_(auto_trader.ACTIVE))))
    if settings.trading.auto_trading == "off":
        state = "off"
    elif ctl.halted:
        state = "stopped"
    elif ctl.auto_paused:
        state = "paused"
    elif not runs:
        state = "nothing"
    elif ctl.auto_problem:
        state = "problem"
    else:
        state = "on"
    return {
        "state": state, "auto_trading": settings.trading.auto_trading, "halted": ctl.halted,
        "paused": ctl.auto_paused, "problem": ctl.auto_problem,
        "problem_at": ctl.auto_problem_at.isoformat() if ctl.auto_problem_at else None,
        "checked_at": ctl.auto_checked_at.isoformat() if ctl.auto_checked_at else None,
        "runs": [{"strategy": p.strategy, "name": auto_trader.source_name(p.strategy, p.symbol, p.timeframe),
                  "symbol": p.symbol, "timeframe": p.timeframe,
                  "structure": auto_plan.load_settings(p.trade).structure,
                  "open": sum(1 for t in active if (t.strategy, t.symbol, t.timeframe) == (p.strategy, p.symbol, p.timeframe)
                              and t.status != "waiting"),
                  "waiting": sum(1 for t in active if (t.strategy, t.symbol, t.timeframe) == (p.strategy, p.symbol, p.timeframe)
                                 and t.status == "waiting")} for p in runs],
        "active": len(active),
    }

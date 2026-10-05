"""Backtest tab (Stage 7): run the strategy over history, keep the runs, reopen them.
Only the signed-in user's own runs and key."""
import asyncio
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auto_plan, routes_market, user_settings
from app.auth import current_user
from app.backtest import run as bt
from app.backtest.option_history import ModelOptionHistory
from app.db import get_db
from app.inputs import Strict
from app.marketdata import service as md_service
from app.marketdata.base import MarketDataError
from app.models import BacktestRun, StrategyPreset, User
from app.routes_auto import _trade_settings
from app.routes_market import _provider, _symbol
from app.routes_strategy import _inputs, _strategy, _timeframe
from app.strategy import service

router = APIRouter(prefix="/api/backtest")

KEEP_RUNS = 30


class RunIn(Strict):
    strategy: str = Field(max_length=40)
    symbol: str = Field(max_length=12)
    timeframe: str = Field(max_length=4)
    # pegged: the set pegged to this symbol and timeframe; given: `inputs` (e.g. sent from Master Chart).
    inputs_source: Literal["pegged", "given", "defaults"] = "pegged"
    inputs: dict[str, Any] = Field(default_factory=dict)
    start: date | None = None
    end: date | None = None
    starting_cash: float = Field(100_000.0, gt=0, le=1_000_000_000)
    stock_dollars: float = Field(10_000.0, gt=0, le=1_000_000_000)
    # off: stock price only; plan: the structure(s) in “What to trade on a signal”; compare: all three.
    options: Literal["off", "plan", "compare"] = "compare"
    # “What to trade on a signal” to use; empty: the one saved for this symbol and timeframe.
    trade: dict[str, Any] | None = None


def _summary(result: dict) -> dict:
    return {name: {k: col.get(k) for k in ("trades", "total", "total_pct", "win_rate", "max_drop_pct")}
            for name, col in result["columns"].items()}


def _out(r: BacktestRun, full: bool) -> dict:
    out = {"id": r.id, "strategy": r.strategy, "symbol": r.symbol, "timeframe": r.timeframe, "setup": r.setup,
           "summary": r.summary, "created_at": r.created_at.isoformat()}
    if full:
        out["result"] = r.result
    return out


@router.post("/run")
async def run(body: RunIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    s = _strategy(body.strategy)
    symbol, tf = _symbol(body.symbol), _timeframe(body.timeframe)
    if body.start and body.end and body.start > body.end:
        raise HTTPException(422, "The start date is after the end date.")
    preset = db.scalar(select(StrategyPreset).where(StrategyPreset.user_id == user.id, StrategyPreset.strategy == s.id,
                                                    StrategyPreset.symbol == symbol, StrategyPreset.timeframe == tf))
    if body.inputs_source == "pegged":
        if preset is None:
            raise HTTPException(409, f"Nothing is pegged to {symbol} {tf}. Peg settings in Master Chart, or use other inputs.")
        inputs = _inputs(s, preset.inputs)
    elif body.inputs_source == "given":
        inputs = _inputs(s, body.inputs)
    else:
        inputs = s.defaults()
    trade = _trade_settings(body.trade) if body.trade is not None else auto_plan.load_settings(preset.trade if preset else None)
    structures = {"off": (), "plan": trade.structures(), "compare": auto_plan.STRUCTURES}[body.options]
    rule = user_settings.load(db, user.id).paper.fill_rule
    md = _provider(db, user)
    try:
        data = await service.load(routes_market.cache, user.id, md, s, symbol, tf, inputs)
        daily_bars = data.bars if tf == "1D" else await md_service.candles(
            routes_market.cache, user.id, md, symbol, "1D", service.HISTORY_START["1D"])
    except MarketDataError as exc:
        raise HTTPException(exc.status, str(exc))
    if not data.bars:
        raise HTTPException(404, f"No {tf} candles for {symbol}.")
    daily = bt.daily_closes(daily_bars)
    setup = bt.Setup(body.start, body.end, body.starting_cash, body.stock_dollars, tuple(structures), trade, rule)
    history = ModelOptionHistory({symbol: daily}) if structures else None
    result = await asyncio.to_thread(bt.run, s, data, inputs, setup, history, daily)
    if not result["range"]["first"]:
        raise HTTPException(422, "No closed candles in that date range.")
    saved_setup = {"strategy": s.id, "symbol": symbol, "timeframe": tf, "inputs_source": body.inputs_source,
                   "inputs": inputs, "start": body.start.isoformat() if body.start else None,
                   "end": body.end.isoformat() if body.end else None, "starting_cash": body.starting_cash,
                   "stock_dollars": body.stock_dollars, "options": body.options, "structures": list(structures),
                   "trade": trade.model_dump(), "fill_rule": rule}
    row = BacktestRun(user_id=user.id, strategy=s.id, symbol=symbol, timeframe=tf, setup=saved_setup,
                      summary=_summary(result), result=result)
    db.add(row)
    db.flush()
    old = db.scalars(select(BacktestRun).where(BacktestRun.user_id == user.id)
                     .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc()).offset(KEEP_RUNS))
    for r in old:
        db.delete(r)
    db.commit()
    return _out(row, True)


@router.get("/runs")
def runs(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(BacktestRun).where(BacktestRun.user_id == user.id)
                      .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc()))
    return [_out(r, False) for r in rows]


def _own(db: Session, user: User, run_id: int) -> BacktestRun:
    r = db.get(BacktestRun, run_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "That backtest was not found.")
    return r


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return _out(_own(db, user, run_id), True)


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    db.delete(_own(db, user, run_id))
    db.commit()
    return {"ok": True}

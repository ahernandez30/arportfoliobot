"""Master Chart: run a strategy on a chart, peg settings per symbol and timeframe, and check parity
with TradingView. Every handler works only on the signed-in user's own records and key."""
import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import routes_market
from app.auth import current_user
from app.db import get_db
from app.inputs import Strict
from app.marketdata.base import TIMEFRAMES, MarketDataError
from app.models import MasterChartState, ParityCheck, StrategyPreset, User
from app.routes_market import _provider, _symbol
from app.strategy import parity, service
from app.strategy.base import InputError, Strategy, check_inputs
from app.strategy.swing import STRATEGIES

router = APIRouter(prefix="/api/strategy")


def _strategy(strategy_id: str) -> Strategy:
    s = STRATEGIES.get(strategy_id)
    if s is None:
        raise HTTPException(404, "Unknown strategy.")
    return s


def _timeframe(tf: str) -> str:
    if tf not in TIMEFRAMES:
        raise HTTPException(422, "Unknown timeframe.")
    return tf


def _inputs(s: Strategy, raw: dict | None) -> dict:
    try:
        return check_inputs(s.inputs, raw)
    except InputError as exc:
        raise HTTPException(422, str(exc))


@router.get("/strategies")
def strategies(user: User = Depends(current_user)) -> list[dict]:
    return [{"id": s.id, "name": s.name, "version": s.version, "inputs": [d.as_dict() for d in s.inputs]}
            for s in STRATEGIES.values()]


class RunIn(Strict):
    strategy: str = Field(max_length=40)
    symbol: str = Field(max_length=12)
    timeframe: str = Field(max_length=4)
    inputs: dict[str, Any] = Field(default_factory=dict)
    luck: bool = False


@router.post("/run")
async def run(body: RunIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Signals, trades and results of a strategy on a chart, with the candles it used."""
    s = _strategy(body.strategy)
    tf = _timeframe(body.timeframe)
    symbol = _symbol(body.symbol)
    inputs = _inputs(s, body.inputs)
    md = _provider(db, user)
    try:
        data = await service.load(routes_market.cache, user.id, md, s, symbol, tf, inputs, db=db)
    except MarketDataError as exc:
        raise HTTPException(exc.status, str(exc))
    if not data.bars:
        raise HTTPException(404, f"No {tf} candles for {symbol}.")
    out = await service.run(s, data, inputs, luck=body.luck)
    out["bars"] = [{"time": b.time, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                    "volume": b.volume} for b in data.bars]
    out["closed"] = data.closed
    out["inputs"] = inputs
    out["realtime"] = md.realtime
    return out


# ---------- Master Chart state ----------


class StateIn(Strict):
    strategy: str = Field(max_length=40)
    symbol: str = Field(max_length=12)
    timeframe: str = Field(max_length=4)
    inputs: dict[str, Any] = Field(default_factory=dict)


@router.get("/state")
def get_state(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = db.get(MasterChartState, user.id)
    data = dict(row.data) if row and row.data else {}
    s = STRATEGIES.get(data.get("strategy", "")) or next(iter(STRATEGIES.values()))
    try:
        inputs = check_inputs(s.inputs, data.get("inputs"))
    except InputError:
        inputs = s.defaults()
    return {"strategy": s.id, "symbol": data.get("symbol", "TSLA"),
            "timeframe": data.get("timeframe") if data.get("timeframe") in TIMEFRAMES else "1D", "inputs": inputs}


@router.put("/state")
def put_state(body: StateIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    s = _strategy(body.strategy)
    data = {"strategy": s.id, "symbol": _symbol(body.symbol), "timeframe": _timeframe(body.timeframe),
            "inputs": _inputs(s, body.inputs)}
    stmt = insert(MasterChartState).values(user_id=user.id, data=data)
    db.execute(stmt.on_conflict_do_update(index_elements=["user_id"], set_={"data": data,
                                                                            "updated_at": datetime.now(timezone.utc)}))
    db.commit()
    return data


# ---------- pegged settings: one set per symbol and timeframe ----------


def _preset_out(p: StrategyPreset) -> dict:
    return {"id": p.id, "strategy": p.strategy, "symbol": p.symbol, "timeframe": p.timeframe, "inputs": p.inputs,
            "pegged_at": p.pegged_at.isoformat()}


@router.get("/presets")
def list_presets(strategy: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    s = _strategy(strategy)
    rows = db.scalars(select(StrategyPreset).where(StrategyPreset.user_id == user.id, StrategyPreset.strategy == s.id)
                      .order_by(StrategyPreset.symbol, StrategyPreset.timeframe))
    out = []
    for p in rows:
        d = _preset_out(p)
        try:
            d["inputs"] = check_inputs(s.inputs, p.inputs)  # older saves get any new inputs at their defaults
        except InputError:
            d["inputs"] = s.defaults()
        out.append(d)
    return out


@router.put("/presets")
def peg(body: StateIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """“Peg these settings to <symbol> <timeframe>”: saves them as that pair's default."""
    s = _strategy(body.strategy)
    values = {"user_id": user.id, "strategy": s.id, "symbol": _symbol(body.symbol),
              "timeframe": _timeframe(body.timeframe), "inputs": _inputs(s, body.inputs)}
    stmt = insert(StrategyPreset).values(**values)
    db.execute(stmt.on_conflict_do_update(constraint="strategy_presets_key",
                                          set_={"inputs": values["inputs"], "pegged_at": datetime.now(timezone.utc)}))
    db.commit()
    row = db.scalar(select(StrategyPreset).where(
        StrategyPreset.user_id == user.id, StrategyPreset.strategy == s.id,
        StrategyPreset.symbol == values["symbol"], StrategyPreset.timeframe == values["timeframe"]))
    return _preset_out(row)


@router.delete("/presets/{preset_id}")
def unpeg(preset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = db.get(StrategyPreset, preset_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Those pegged settings were not found.")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------- parity with TradingView ----------


class ParityIn(Strict):
    strategy: str = Field(max_length=40)
    symbol: str = Field(max_length=12)
    timeframe: str = Field(max_length=4)
    filename: str = Field("", max_length=200)
    csv: str = Field(max_length=20_000_000)


def _parity_out(p: ParityCheck, detail: bool = False) -> dict:
    out = {"id": p.id, "strategy": p.strategy, "symbol": p.symbol, "timeframe": p.timeframe,
           "filename": p.filename, "summary": p.summary, "created_at": p.created_at.isoformat(),
           "signed_off_at": p.signed_off_at.isoformat() if p.signed_off_at else None,
           "sign_off_note": p.sign_off_note}
    if detail:
        out["detail"] = p.detail
    return out


@router.post("/parity")
async def run_parity(body: ParityIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Compares the engine (script defaults, as the plan asks) with a TradingView export."""
    s = _strategy(body.strategy)
    tf = _timeframe(body.timeframe)
    symbol = _symbol(body.symbol)
    try:
        tv = parity.parse_export(body.csv, tf)
    except parity.ParityError as exc:
        raise HTTPException(422, str(exc))
    inputs = s.defaults()
    md = _provider(db, user)
    try:
        data = await service.load(routes_market.cache, user.id, md, s, symbol, tf, inputs)
    except MarketDataError as exc:
        raise HTTPException(exc.status, str(exc))
    ours = data.bars[:data.closed]
    summary, detail = await asyncio.to_thread(parity.compare, s, inputs, tv, ours, tf)
    row = ParityCheck(user_id=user.id, strategy=s.id, symbol=symbol, timeframe=tf, filename=body.filename,
                      summary=summary, detail=detail)
    db.add(row)
    db.commit()
    return _parity_out(row, detail=True)


@router.get("/parity")
def list_parity(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ParityCheck).where(ParityCheck.user_id == user.id)
                      .order_by(ParityCheck.created_at.desc()).limit(50))
    return [_parity_out(p) for p in rows]


def _own_parity(db: Session, user: User, check_id: int) -> ParityCheck:
    row = db.get(ParityCheck, check_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "That parity check was not found.")
    return row


@router.get("/parity/{check_id}")
def get_parity(check_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return _parity_out(_own_parity(db, user, check_id), detail=True)


class SignOffIn(Strict):
    note: str = Field("", max_length=2000)


@router.post("/parity/{check_id}/sign-off")
def sign_off(check_id: int, body: SignOffIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Rafa's sign-off that the differences are explained (required before automatic trading)."""
    row = _own_parity(db, user, check_id)
    row.signed_off_at = datetime.now(timezone.utc)
    row.sign_off_note = body.note
    db.commit()
    return _parity_out(row, detail=True)

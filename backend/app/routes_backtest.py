"""Backtest tab (Stage 7): run the strategy over history, keep the runs, reopen them.
Only the signed-in user's own runs and key."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import data_jobs, history, keys
from app.auth import current_user
from app.backtest.prepare import RunError, RunIn, plan_missing, prepare, run_prepared
from app.db import get_db
from app.models import BacktestRun, DataJob, User
from app.routes_market import _symbol
from app.routes_strategy import _strategy, _timeframe

router = APIRouter(prefix="/api/backtest")

KEEP_RUNS = 30


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
async def run(body: RunIn, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Runs and saves a backtest. With real option prices, if some are not downloaded yet, nothing is
    saved: a download job is started instead (202), and the screen asks before anything is spent."""
    body = body.model_copy(update={"symbol": _symbol(body.symbol), "timeframe": _timeframe(body.timeframe)})
    _strategy(body.strategy)
    try:
        p = await prepare(db, user, body)
    except RunError as exc:
        raise HTTPException(exc.status, str(exc))
    real = body.option_prices == "real" and p.option is not None
    if real:
        if not keys.get_secret(db, user.id, "databento"):
            raise HTTPException(409, "Real option prices come from Databento: add your Databento key in Config → Keys first.")
        missing, positions = await asyncio.to_thread(plan_missing, db, user.id, p)
        if not missing.empty():
            job = DataJob(user_id=user.id, status="estimating",
                          request={"run": body.model_dump(mode="json"), "missing": missing.as_dict(), "positions": positions})
            db.add(job)
            db.commit()
            response.status_code = 202
            return {"job": data_jobs.job_out(job)}
    result = await run_prepared(db, user.id, p, "real" if real else "estimate")
    if not result["range"]["first"]:
        raise HTTPException(422, "No closed candles in that date range.")
    saved_setup = {"strategy": p.strategy.id, "symbol": p.symbol, "timeframe": p.timeframe, "inputs_source": body.inputs_source,
                   "inputs": p.inputs, "start": body.start.isoformat() if body.start else None,
                   "end": body.end.isoformat() if body.end else None, "starting_cash": body.starting_cash,
                   "stock_dollars": body.stock_dollars,
                   "option": p.option.model_dump() if p.option else None, "fill_rule": p.rule, "option_prices": "real" if real else "estimate"}
    row = BacktestRun(user_id=user.id, strategy=p.strategy.id, symbol=p.symbol, timeframe=p.timeframe, setup=saved_setup,
                      summary=_summary(result), result=result)
    db.add(row)
    db.flush()
    old = db.scalars(select(BacktestRun).where(BacktestRun.user_id == user.id)
                     .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc()).offset(KEEP_RUNS))
    for r in old:
        db.delete(r)
    db.commit()
    return _out(row, True)


# ---------- downloads of real option prices, and what is stored ----------


def _own_job(db: Session, user: User, job_id: int) -> DataJob:
    j = db.get(DataJob, job_id)
    if j is None or j.user_id != user.id:
        raise HTTPException(404, "That download was not found.")
    return j


@router.get("/data-jobs/{job_id}")
def get_job(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return data_jobs.job_out(_own_job(db, user, job_id))


@router.post("/data-jobs/{job_id}/confirm")
def confirm_job(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The user's yes to the estimated cost: the worker may now spend up to the job's limit."""
    j = _own_job(db, user, job_id)
    if j.status != "confirm":
        raise HTTPException(409, "This download is not waiting for a yes.")
    j.status = "queued"
    j.updated_at = datetime.now(timezone.utc)
    db.commit()
    return data_jobs.job_out(j)


@router.post("/data-jobs/{job_id}/cancel")
def cancel_job(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    j = _own_job(db, user, job_id)
    if j.status in ("done", "failed", "cancelled"):
        raise HTTPException(409, "This download has already finished.")
    j.status = "cancelled"
    j.updated_at = datetime.now(timezone.utc)
    db.commit()
    return data_jobs.job_out(j)


@router.get("/history")
def stored_history(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Candles and real option prices stored for this user."""
    return {"candles": history.coverage(db, user.id), "options": data_jobs.option_coverage(db, user.id),
            "databento_key": bool(keys.get_secret(db, user.id, "databento"))}


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

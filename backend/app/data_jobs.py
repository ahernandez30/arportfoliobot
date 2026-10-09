"""Worker job: downloads of real option prices from Databento for a backtest (Backtest plan, step 3).

A backtest with real prices that finds some missing starts a job (status "estimating"). Here the
worker prices the download with Databento's own cost calculator and asks the user ("confirm");
nothing is spent before the user says yes ("queued"). Then it downloads ("running"): first the
option chains of the days a trade opens, then the prices of the contracts the backtest picks at each
moment a trade opens or closes, re-planning the backtest after each round so it fetches exactly
what the run needs. Before every request it checks the exact cost against the limit the user agreed
to and stops rather than pass it. Every download is kept, so it is paid for only once.
"""
import asyncio
import logging
import random
from datetime import datetime, timezone
from decimal import ROUND_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import keys
from app.backtest import real_options
from app.backtest.options import NEAREST_STRIKES
from app.backtest.prepare import RunIn, plan_missing, prepare
from app.backtest.real_options import LOOKBACK, DatabentoClient, Missing
from app.models import DataJob, OptionChainDay, OptionQuote, User

log = logging.getLogger("arpb.data")

POLL_SECONDS = 5
ROUNDS = 3  # chains, then prices, then whatever the real prices change
MAX_SYMBOLS = 500  # contracts per Databento request
CONTRACTS_PER_DAY = 4000  # roughly how many TSLA option contracts are listed on a day (for estimates)
SAMPLES = 3  # Databento cost checks used for an estimate
MARGIN = Decimal("1.5")  # the limit the user agrees to: the estimate times this, plus a dollar

# Tests replace this with a stand-in; the live one talks to Databento.
make_client = DatabentoClient


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def job_out(j: DataJob) -> dict:
    return {"id": j.id, "status": j.status, "plan": j.plan, "estimate_usd": float(j.estimate_usd) if j.estimate_usd is not None else None,
            "limit_usd": j.plan.get("limit_usd"), "spent_usd": float(j.spent_usd or 0), "progress": j.progress, "error": j.error,
            "symbol": j.request.get("run", {}).get("symbol"), "timeframe": j.request.get("run", {}).get("timeframe"),
            "created_at": j.created_at.isoformat() if j.created_at else None}


def option_coverage(db: Session, user_id: int) -> dict:
    days = db.execute(select(OptionChainDay.underlying, func.count(), func.min(OptionChainDay.day), func.max(OptionChainDay.day))
                      .where(OptionChainDay.user_id == user_id).group_by(OptionChainDay.underlying)).all()
    quotes = db.scalar(select(func.count()).select_from(OptionQuote).where(OptionQuote.user_id == user_id)) or 0
    spent = db.scalar(select(func.coalesce(func.sum(DataJob.spent_usd), 0)).where(DataJob.user_id == user_id))
    return {"chains": [{"underlying": u, "days": n, "from": a.isoformat(), "to": b.isoformat()} for u, n, a, b in days],
            "quote_samples": quotes, "spent_usd": float(spent or 0), "available_from": real_options.OPRA_START.isoformat()}


def _usd(x: float | Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.0001"))


def estimate(client, underlying: str, missing: Missing, positions: int) -> tuple[Decimal, dict]:
    """The likely cost: Databento's price for a few of the chain days, times all of them; and for the
    prices, the cost of one minute of the whole option chain spread over its contracts, times the
    contract-minutes needed (when the chains are not known yet, the prices still to come are
    guessed from the number of option positions the backtest prices)."""
    days = sorted(missing.days)
    sample = random.Random(0).sample(days, min(SAMPLES, len(days)))
    chain_each = sum(client.chain_cost(underlying, d) for d in sample) / len(sample) if sample else 0.0
    moments = sorted(missing.moments)
    minutes_per_moment = (LOOKBACK + 60) // 60
    if moments:
        contract_minutes = sum(len(missing.moments[t]) for t in moments) * minutes_per_moment
        probe = moments[len(moments) // 2]
    else:
        contract_minutes = positions * (NEAREST_STRIKES + 2) * minutes_per_moment
        probe = None
    if contract_minutes:
        if probe is None:
            d = days[len(days) // 2] if days else real_options.OPRA_START
            probe = int(datetime(d.year, d.month, d.day, 15, 0, tzinfo=timezone.utc).timestamp())
        per_contract_minute = client.minute_cost_all(underlying, probe) / CONTRACTS_PER_DAY
    else:
        per_contract_minute = 0.0
    chains, prices = chain_each * len(days), per_contract_minute * contract_minutes
    total = _usd(chains + prices)
    limit = (total * MARGIN + 1).quantize(Decimal("1"), rounding=ROUND_UP)
    return total, {"chain_days": len(days), "moments": len(moments), "contract_minutes": contract_minutes,
                   "chains_usd": round(chains, 4), "prices_usd": round(prices, 4), "limit_usd": float(limit),
                   "positions": positions, "prices_known": bool(moments) or not days}


class Stop(Exception):
    """The job must stop: cancelled, or the next request would pass the agreed limit."""


class Runner:
    def __init__(self, sm: sessionmaker, job_id: int):
        self.sm = sm
        self.job_id = job_id

    def _job(self, db: Session) -> DataJob:
        return db.get(DataJob, self.job_id)

    def _check(self, db: Session, cost: float) -> None:
        """Before each paid request: still wanted, and within the limit."""
        db.expire_all()
        j = self._job(db)
        if j.status == "cancelled":
            raise Stop("cancelled")
        limit = Decimal(str(j.plan.get("limit_usd", 0)))
        if (j.spent_usd or 0) + _usd(cost) > limit:
            raise Stop(f"Stopped before passing the ${limit} you agreed to (spent ${float(j.spent_usd):.2f}). "
                       "Run the backtest again to see what is still missing and its cost.")

    def _spent(self, db: Session, cost: float, progress: dict) -> None:
        j = self._job(db)
        j.spent_usd = (j.spent_usd or 0) + _usd(cost)
        j.progress = progress
        j.updated_at = utcnow()
        db.commit()

    def download(self, db: Session, client, user_id: int, underlying: str, missing: Missing, rnd: int) -> None:
        days = sorted(missing.days)
        for i, d in enumerate(days):
            cost = client.chain_cost(underlying, d)
            self._check(db, cost)
            rows = client.chain(underlying, d)
            real_options.save_chain(db, user_id, underlying, d, rows)
            self._spent(db, cost, {"round": rnd, "phase": "option chains", "done": i + 1, "total": len(days)})
        moments = sorted(missing.moments)
        for i, t in enumerate(moments):
            contracts = sorted(missing.moments[t])
            for k in range(0, len(contracts), MAX_SYMBOLS):
                batch = contracts[k:k + MAX_SYMBOLS]
                start, end = t - LOOKBACK, t + 60
                cost = client.quotes_cost(batch, start, end)
                self._check(db, cost)
                # Only the last sample at or before the moment is ever read: keep just that one.
                last: dict[str, tuple] = {}
                for r in client.quotes(batch, start, end):
                    if r[1] <= t and (r[0] not in last or r[1] > last[r[0]][1]):
                        last[r[0]] = r
                rows = list(last.values())
                real_options.save_quotes(db, user_id, batch, start, t, rows)
                self._spent(db, cost, {"round": rnd, "phase": "option prices", "done": i + 1, "total": len(moments)})

    async def run(self) -> None:
        with self.sm() as db:
            j = self._job(db)
            user = db.get(User, j.user_id)
            body = RunIn.model_validate(j.request["run"])
            key = keys.get_secret(db, user.id, "databento")
            if not key:
                self._fail(db, "No Databento key: add it in Config → Keys.")
                return
            client = make_client(key)
            if j.status == "estimating":
                missing = Missing.from_dict(j.request["missing"])
                try:
                    total, plan = await asyncio.to_thread(estimate, client, body.symbol, missing, j.request.get("positions", 0))
                except Exception as exc:  # noqa: BLE001 (shown to the user)
                    self._fail(db, f"Databento could not price the download: {exc}")
                    return
                j.estimate_usd, j.plan, j.status, j.updated_at = total, plan, "confirm", utcnow()
                db.commit()
                return
            j.status, j.updated_at = "running", utcnow()
            db.commit()
            try:
                missing = Missing.from_dict(j.request["missing"])
                for rnd in range(1, ROUNDS + 1):
                    if missing.empty():
                        break
                    await asyncio.to_thread(self.download, db, client, user.id, body.symbol, missing, rnd)
                    p = await prepare(db, user, body)
                    missing, _ = await asyncio.to_thread(plan_missing, db, user.id, p)
                j = self._job(db)
                j.status = "done"
                j.progress = {**(j.progress or {}), "left": missing.as_dict() if not missing.empty() else None}
                j.updated_at = utcnow()
                db.commit()
            except Stop as exc:
                db.rollback()
                j = self._job(db)
                if j.status != "cancelled":
                    self._fail(db, str(exc))
            except Exception as exc:  # noqa: BLE001 (shown to the user)
                db.rollback()
                log.exception("data job %s failed", self.job_id)
                self._fail(db, f"The download stopped: {exc}")

    def _fail(self, db: Session, message: str) -> None:
        j = self._job(db)
        j.status, j.error, j.updated_at = "failed", message, utcnow()
        db.commit()


async def run(engine: Engine, stop: asyncio.Event) -> None:
    sm = sessionmaker(engine, expire_on_commit=False)
    while not stop.is_set():
        try:
            with sm() as db:
                ids = list(db.scalars(select(DataJob.id).where(DataJob.status.in_(("estimating", "queued")))
                                      .order_by(DataJob.created_at)))
            for job_id in ids:
                await Runner(sm, job_id).run()
        except Exception:
            log.exception("data jobs loop failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


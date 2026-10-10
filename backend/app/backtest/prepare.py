"""Everything a backtest needs before it runs, shared by the Backtest tab and the worker's download
jobs (which re-plan a run to find the real option prices still missing)."""
import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import routes_market, user_settings
from app.backtest import run as bt
from app.backtest.option_history import ModelOptionHistory, OptionHistory
from app.backtest.options import OptionSetup
from app.backtest.real_options import Missing, StoredOptionHistory
from app.inputs import Strict
from app.marketdata import service as md_service
from app.marketdata.base import MarketDataError
from app.models import StrategyPreset, User
from app.strategy import service
from app.strategy.base import InputError, Strategy, StrategyData, check_inputs
from app.strategy.swing import STRATEGIES


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
    # The option setup to replay on every trade; none: stock price only.
    option: OptionSetup | None = None
    # estimate: the pricing model; real: Databento prices the user has downloaded (or will).
    option_prices: Literal["estimate", "real"] = "estimate"


class RunError(Exception):
    """A backtest that cannot run, with an HTTP status and a message fit for the screen."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Prepared:
    strategy: Strategy
    symbol: str
    timeframe: str
    inputs: dict
    data: StrategyData
    daily: list
    setup: bt.Setup
    option: OptionSetup | None
    rule: str


async def prepare(db: Session, user: User, body: RunIn) -> Prepared:
    """Checks the request and gathers the candles (with the user's stored history)."""
    s = STRATEGIES.get(body.strategy)
    if s is None:
        raise RunError(404, "Unknown strategy.")
    symbol, tf = body.symbol, body.timeframe
    if body.start and body.end and body.start > body.end:
        raise RunError(422, "The start date is after the end date.")
    preset = db.scalar(select(StrategyPreset).where(StrategyPreset.user_id == user.id, StrategyPreset.strategy == s.id,
                                                    StrategyPreset.symbol == symbol, StrategyPreset.timeframe == tf))
    try:
        if body.inputs_source == "pegged":
            if preset is None:
                raise RunError(409, f"Nothing is pegged to {symbol} {tf}. Peg settings in Master Chart, or use other inputs.")
            inputs = check_inputs(s.inputs, preset.inputs)
        elif body.inputs_source == "given":
            inputs = check_inputs(s.inputs, body.inputs)
        else:
            inputs = s.defaults()
    except InputError as exc:
        raise RunError(422, str(exc))
    rule = user_settings.load(db, user.id).paper.fill_rule
    md = routes_market.providers.get(db, user.id)
    if md is None:
        raise RunError(409, md_service.NO_KEY)
    try:
        data = await service.load(routes_market.cache, user.id, md, s, symbol, tf, inputs, db=db)
        daily_bars = data.bars if tf == "1D" else await md_service.candles(
            routes_market.cache, user.id, md, symbol, "1D", service.HISTORY_START["1D"])
    except MarketDataError as exc:
        raise RunError(exc.status, str(exc))
    if not data.bars:
        raise RunError(404, f"No {tf} candles for {symbol}.")
    daily = bt.daily_closes(daily_bars)
    setup = bt.Setup(body.start, body.end, body.starting_cash, body.stock_dollars, body.option, rule)
    return Prepared(s, symbol, tf, inputs, data, daily, setup, body.option, rule)


def model_history(p: Prepared) -> ModelOptionHistory:
    return ModelOptionHistory({p.symbol: p.daily})


def plan_missing(db: Session, user_id: int, p: Prepared) -> tuple[Missing, int]:
    """Runs the backtest with the stored real prices, filling the gaps with the estimate, and returns
    what is missing and how many option positions the run prices."""
    hist = StoredOptionHistory(db, user_id, p.symbol, fallback=model_history(p))
    result = bt.run(p.strategy, p.data, p.inputs, p.setup, hist, p.daily)
    positions = sum(len(rows) for t in result["trades"] for rows in t["options"].values())
    return hist.missing, positions


def history_for(db: Session, user_id: int, p: Prepared, prices: str) -> OptionHistory | None:
    if p.option is None:
        return None
    if prices == "real":
        return StoredOptionHistory(db, user_id, p.symbol)
    return model_history(p)


async def run_prepared(db: Session, user_id: int, p: Prepared, prices: str) -> dict:
    history = history_for(db, user_id, p, prices)
    return await asyncio.to_thread(bt.run, p.strategy, p.data, p.inputs, p.setup, history, p.daily)

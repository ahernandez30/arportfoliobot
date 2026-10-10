"""Worker job: automatic paper trading from strategy signals (Stage 6, plan 7.6 and section 10).

For every symbol and timeframe a user has switched on (pegged settings with “Place paper trades
from signals”), and while Config → Trading → automatic trading is “paper”:

1. Runs the same engine as Master Chart on the pegged settings, on CLOSED candles only. A candle
   counts as closed a little after its close time (SETTLE), so the provider has its final prices.
2. A position the rules open on the latest closed candle becomes a waiting trade, one per
   structure. It is placed when the options market is open: the strike, expiration and size are
   chosen by app.auto_plan from the strategy's own history and the user's settings.
3. Exits follow the indicator on the stock chart, never the option's own price: the target or
   stop on the stock price (watched live), the candle count, the opposite signal, the forced close
   time or the basket rule. Exits keep working while automatic trading is paused or stopped, so
   nothing is left without its exit; only new trades stop.
4. A position still open the day before its expiration is closed (and the Live Trader warns in the
   days before), so nothing is left to expire.

The same signal never orders twice: one strategy_trades row per signal and structure (unique),
and the paper order's idempotency key. Prices that are stale or a provider that cannot be reached
pause automatic trading for that user and show a warning (TradingControls.auto_problem).
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import auto_plan, paper, paper_rules, user_settings
from app.marketdata.bars import NY
from app.marketdata.base import MarketData, MarketDataError, Quote
from app.marketdata.service import ProviderCache, TTLCache
from app.models import PaperPosition, StrategyPreset, StrategyTrade
from app.paper import PaperError
from app.paper_rules import Book
from app.strategy import service
from app.strategy.base import TIMEFRAME_SECONDS, InputError, check_inputs
from app.strategy.swing import STRATEGIES

log = logging.getLogger("arpb.auto")

CYCLE_SECONDS = 20
# Seconds after a candle's close before it is treated as closed: the provider's last prints settle.
SETTLE = {"1m": 20, "5m": 30, "15m": 30, "30m": 45, "1h": 60, "1D": 600, "1W": 600}
# During market hours, a stock price older than this means the feed is stale.
STALE_SECONDS = 300
# A waiting trade that cannot be placed within this many minutes of trying is given up.
ENTRY_TRY_MINUTES = 15
ACTIVE = ("waiting", "open", "floating")
# The engine's exit reasons (the script's own names) as Account Manager reasons.
REASONS = {"TP": "take_profit", "SL": "stop_loss", "SIG": "signal", "contra": "signal", "cambio W": "signal", "CESTA": "take_profit",
           "corte": "time", "HORA": "time"}
REASON_WORDS = {"take_profit": "the strategy's target on the stock price", "stop_loss": "the strategy's stop on the stock price",
                "signal": "the opposite signal", "time": "the strategy's time rule",
                "expiry": "its expiration being next (closed before expiring)"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def source_name(strategy_id: str, symbol: str, timeframe: str) -> str:
    s = STRATEGIES.get(strategy_id)
    return f"{s.name.split(',')[0] if s else strategy_id} {s.version if s else ''} · {symbol} {timeframe}".replace("  ", " ")


def idempotency_key(user_id: int, t: StrategyTrade) -> str:
    """Strategy, symbol, timeframe and candle time (plan section 10), per user and structure."""
    return f"auto:{user_id}:{t.strategy}:{t.symbol}:{t.timeframe}:{t.signal_time}:{t.structure}"


def candle_close_time(bar_time: int, tf: str) -> datetime:
    """When a candle that starts at `bar_time` closes."""
    if tf in ("1D", "1W"):
        d = datetime.fromtimestamp(bar_time, timezone.utc).date()
        last = d + timedelta(days=4) if tf == "1W" else d
        return datetime(last.year, last.month, last.day, 16, 0, tzinfo=NY)
    start = datetime.fromtimestamp(bar_time, NY)
    return min(start + timedelta(seconds=TIMEFRAME_SECONDS[tf]), start.replace(hour=16, minute=0, second=0))


# ---------- reading the engine's answer ----------


@dataclass(frozen=True)
class Exit:
    reason: str  # Account Manager reason, or "floating"
    price: float | None
    time: int


def engine_exit(run: dict, signal_time: int, direction: int) -> Exit | None:
    """How the engine finished the position opened at `signal_time`, if it has."""
    for t in run.get("trades", []):
        if t["dir"] != direction:
            continue
        if t["entry_time"] == signal_time or signal_time in t.get("entry_times", ()):
            if t["reason"] == "flot":
                return Exit("floating", t["exit_price"], t["exit_time"])
            return Exit(REASONS.get(t["reason"], "signal"), t["exit_price"], t["exit_time"])
    return None


def engine_levels(run: dict, signal_time: int, direction: int) -> tuple[float | None, float | None]:
    """The strategy's current target and stop on the stock price for an open position."""
    for t in run.get("open_trades", []):
        if t["entry_time"] == signal_time and t["dir"] == direction:
            return t.get("target"), t.get("stop")
    pos = run.get("position")
    if pos and pos["dir"] == direction and (pos["entry_time"] == signal_time or signal_time in pos.get("entry_times", ())):
        if run.get("results", {}).get("mode") != "target_stop":
            return pos.get("target"), pos.get("stop")
    return None, None


def level_hit(direction: int, price: float, target: float | None, stop: float | None) -> str | None:
    """Whether the live stock price has reached the strategy's stop or target. The stop is checked
    first, like the script does when a candle touches both."""
    if direction == 1:
        if stop is not None and price <= stop:
            return "stop_loss"
        if target is not None and price >= target:
            return "take_profit"
    else:
        if stop is not None and price >= stop:
            return "stop_loss"
        if target is not None and price <= target:
            return "take_profit"
    return None


def new_entries(run: dict, bars: list, closed: int, tf: str, since: datetime) -> list[tuple[dict, bool]]:
    """Positions the rules opened on candles that closed after `since`, each with whether it is on
    the latest closed candle (True: trade it; False: it was missed while the worker was not looking)."""
    out = []
    for e in run.get("entries", []):
        if e["i"] >= closed:
            continue
        if candle_close_time(bars[e["i"]].time, tf) < since:
            continue
        out.append((e, e["i"] == closed - 1))
    return out


def safety_close_due(expiration: date, today: date) -> bool:
    """Strategy positions are closed on the last market day before their expiration day."""
    last = expiration - timedelta(days=1)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    return today >= last


# ---------- the job ----------


class AutoTrader:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.providers = ProviderCache()
        self.cache = TTLCache()
        self.clocks: dict[int, tuple[float, str | None]] = {}

    def users(self) -> list[int]:
        with Session(self.engine) as db:
            on = select(StrategyPreset.user_id).where(StrategyPreset.auto.is_(True))
            active = select(StrategyTrade.user_id).where(StrategyTrade.status.in_(ACTIVE))
            return sorted(set(db.scalars(on)) | set(db.scalars(active)))

    async def clock(self, user_id: int, md: MarketData) -> str | None:
        hit = self.clocks.get(user_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        try:
            state = (await md.clock()).state
        except MarketDataError:
            state = None
        self.clocks[user_id] = (time.monotonic() + 60, state)
        return state

    def set_problem(self, user_id: int, problem: str, now: datetime) -> None:
        with Session(self.engine) as db:
            ctl = paper.controls(db, user_id, lock=True)
            if problem and problem != ctl.auto_problem:
                paper.log(db, user_id, None, "auto_warning", f"Automatic trading paused: {problem}")
                ctl.auto_problem_at = now
            elif not problem and ctl.auto_problem:
                paper.log(db, user_id, None, "auto_ok", "Automatic trading back to normal: prices are flowing again.")
                ctl.auto_problem_at = now
            ctl.auto_problem = problem
            ctl.auto_checked_at = now
            db.commit()

    async def run_once(self, now: datetime | None = None) -> None:
        for user_id in self.users():
            try:
                await self.run_user(user_id, now)
            except Exception:
                log.exception("automatic trading failed for user %s", user_id)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            started = time.monotonic()
            try:
                await self.run_once()
            except Exception:
                log.exception("automatic trading cycle failed")
            wait = max(1.0, CYCLE_SECONDS - (time.monotonic() - started))
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass

    async def run_user(self, user_id: int, now: datetime | None = None) -> None:
        now = now or utcnow()
        with Session(self.engine) as db:
            md = self.providers.get(db, user_id)
            settings = user_settings.load(db, user_id)
            ctl = paper.controls(db, user_id)
            db.commit()
            halted, paused = ctl.halted, ctl.auto_paused
            presets = list(db.scalars(select(StrategyPreset).where(StrategyPreset.user_id == user_id)))
            trades = list(db.scalars(select(StrategyTrade).where(StrategyTrade.user_id == user_id,
                                                                 StrategyTrade.status.in_(ACTIVE))))
            db.expunge_all()
        if md is None:
            self.set_problem(user_id, "No market data key. Add a Tradier key in Config → Keys & connections.", now)
            return
        may_open = settings.trading.auto_trading == "paper" and not halted and not paused
        rule = settings.paper.fill_rule
        market_open = paper_rules.session_open(await self.clock(user_id, md), now.astimezone(NY))

        by_key = {(p.strategy, p.symbol, p.timeframe): p for p in presets}
        wanted = {k for k, p in by_key.items() if p.auto} | {(t.strategy, t.symbol, t.timeframe) for t in trades}

        # The stock prices: for live targets and stops, entry prices, and the staleness check.
        symbols = sorted({k[1] for k in wanted})
        try:
            quotes = await md.quotes(symbols) if symbols else {}
        except MarketDataError as exc:
            self.set_problem(user_id, f"Cannot reach the market data provider ({exc}).", now)
            return
        if market_open:
            stale = [s for s in symbols if not fresh(quotes.get(s), now)]
            if stale:
                self.set_problem(user_id, f"Prices for {', '.join(stale)} have not updated for over "
                                          f"{STALE_SECONDS // 60} minutes.", now)
                return
        problem = ""
        for key in sorted(wanted):
            strategy_id, symbol, tf = key
            preset = by_key.get(key)
            try:
                await self.run_chart(user_id, md, preset, strategy_id, symbol, tf,
                                     [t for t in trades if (t.strategy, t.symbol, t.timeframe) == key],
                                     quotes.get(symbol), may_open, market_open, rule, now)
            except MarketDataError as exc:
                problem = f"Cannot reach the market data provider ({exc})."
        self.set_problem(user_id, problem, now)

    async def run_chart(self, user_id: int, md: MarketData, preset: StrategyPreset | None, strategy_id: str,
                        symbol: str, tf: str, trades: list[StrategyTrade], quote: Quote | None, may_open: bool,
                        market_open: bool, rule: str, now: datetime) -> None:
        strategy = STRATEGIES.get(strategy_id)
        if strategy is None:
            return
        if preset is None:
            # The pegged settings were removed: there is no rule left to follow, so close at once.
            for t in trades:
                await self.finish(user_id, md, t, "time", None, market_open, rule, now,
                                  "its pegged settings were removed, so the strategy no longer follows it")
            return
        try:
            inputs = check_inputs(strategy.inputs, preset.inputs)
        except InputError:
            inputs = strategy.defaults()
        settle_now = now - timedelta(seconds=SETTLE.get(tf, 60))
        data = await service.load(self.cache, user_id, md, strategy, symbol, tf, inputs, now=settle_now)
        if not data.bars:
            return
        run = await asyncio.to_thread(strategy.run, data, inputs)
        last = quote.last if quote else None

        # Exits first, for what is already held or waiting.
        for t in trades:
            ex = engine_exit(run, t.signal_time, t.direction)
            tgt, stp = engine_levels(run, t.signal_time, t.direction)
            if ex is None and (tgt is not None or stp is not None):
                self.update(t.id, under_target=_dec(tgt), under_stop=_dec(stp))
            if t.status == "waiting":
                if ex is not None and ex.reason != "floating":
                    self.update(t.id, status="missed", detail=f"The strategy exited ({REASON_WORDS[ex.reason]}) "
                                                              "before the order could go in.", closed_at=now)
                    t.status = "missed"
                continue
            if ex is not None and ex.reason == "floating" and t.status == "open":
                self.update(t.id, status="floating", under_target=None, under_stop=None,
                            detail="The strategy stopped following it (its “leave floating” rule). It stays open "
                                   "and is closed the market day before expiration, or by you.")
                continue
            reason, under_exit = None, None
            if ex is not None and ex.reason != "floating":
                reason, under_exit = ex.reason, ex.price
            elif t.status == "open" and last is not None and market_open:
                reason = level_hit(t.direction, last, tgt, stp)
                under_exit = last if reason else None
            if reason is None and t.position_id is not None:
                exp = self.expiration_of(t.position_id)
                if exp and safety_close_due(exp, now.astimezone(NY).date()):
                    reason = "expiry"
            if reason is not None:
                await self.finish(user_id, md, t, reason, under_exit, market_open, rule, now)

        # New positions the rules opened.
        if preset.auto and preset.auto_since is not None:
            settings = auto_plan.load_settings(preset.trade)
            for e, latest in new_entries(run, data.bars, data.closed, tf, preset.auto_since):
                for structure in settings.structures():
                    self.record_entry(user_id, preset, e, structure, settings.account_for(structure), latest, now)

        # Place waiting trades while the market is open.
        if market_open:
            with Session(self.engine) as db:
                waiting = list(db.scalars(select(StrategyTrade).where(
                    StrategyTrade.user_id == user_id, StrategyTrade.strategy == strategy_id,
                    StrategyTrade.symbol == symbol, StrategyTrade.timeframe == tf, StrategyTrade.status == "waiting")))
                db.expunge_all()
            for t in waiting:
                if not may_open:
                    self.update(t.id, status="refused", closed_at=now,
                                detail="Automatic trading was off, paused or stopped when the order was due.")
                    continue
                await self.place(user_id, md, preset, t, run, last, rule, now)

    # ----- small database steps, each its own transaction -----

    def update(self, trade_id: int, **values) -> None:
        with Session(self.engine) as db:
            t = db.get(StrategyTrade, trade_id, with_for_update=True)
            for k, v in values.items():
                setattr(t, k, v)
            db.commit()

    def expiration_of(self, position_id: int) -> date | None:
        with Session(self.engine) as db:
            pos = db.get(PaperPosition, position_id)
            return pos.expiration if pos and pos.status == "open" else None

    def record_entry(self, user_id: int, preset: StrategyPreset, e: dict, structure: str, account_name: str,
                     latest: bool, now: datetime) -> None:
        with Session(self.engine) as db:
            exists = db.scalar(select(StrategyTrade.id).where(
                StrategyTrade.user_id == user_id, StrategyTrade.strategy == preset.strategy,
                StrategyTrade.symbol == preset.symbol, StrategyTrade.timeframe == preset.timeframe,
                StrategyTrade.signal_time == e["time"], StrategyTrade.structure == structure))
            if exists:
                return
            t = StrategyTrade(user_id=user_id, preset_id=preset.id, strategy=preset.strategy, symbol=preset.symbol,
                              timeframe=preset.timeframe, structure=structure, account_name=account_name,
                              signal_time=e["time"], direction=e["dir"], candle_type=e["type"],
                              signal_price=_dec(e["price"]), status="waiting" if latest else "missed",
                              detail="" if latest else "This signal's candle closed while the worker was not "
                                                       "watching, and a newer candle has closed since.",
                              closed_at=None if latest else now)
            db.add(t)
            db.flush()
            word = "BUY" if e["dir"] == 1 else "SELL"
            paper.log(db, user_id, None, "signal", f"{word} {e['type']} on {preset.symbol} {preset.timeframe} at "
                      f"{e['price']:.2f} → {auto_plan.STRUCTURE_LABELS[structure]}"
                      + (" (waiting for the options market)." if latest else " (missed)."),
                      source=source_name(preset.strategy, preset.symbol, preset.timeframe))
            db.commit()
            log.info("user %s: %s signal %s %s %s (%s)", user_id, word, preset.symbol, preset.timeframe,
                     structure, "waiting" if latest else "missed")

    async def place(self, user_id: int, md: MarketData, preset: StrategyPreset, t: StrategyTrade, run: dict,
                    last: float | None, rule: str, now: datetime) -> None:
        first = t.plan.get("first_try")
        if first is None:
            self.update(t.id, plan={**t.plan, "first_try": now.isoformat()})
        elif now - datetime.fromisoformat(first) > timedelta(minutes=ENTRY_TRY_MINUTES):
            self.update(t.id, status="refused", closed_at=now, detail=(t.detail or "Could not be placed")
                        + f" Gave up after {ENTRY_TRY_MINUTES} minutes of trying.")
            return
        if last is None:
            self.update(t.id, detail="No stock price yet; trying again.")
            return
        try:
            outcome = await plan_trade(md, preset, t, run, Decimal(str(last)), rule, now)
        except MarketDataError as exc:
            self.update(t.id, detail=f"Could not get option prices ({exc}); trying again.")
            return
        if isinstance(outcome, str):
            self.update(t.id, status="refused", detail=outcome, closed_at=now)
            with Session(self.engine) as db:
                paper.log(db, user_id, None, "order_refused", f"{auto_plan.STRUCTURE_LABELS[t.structure]} for the "
                          f"{'BUY' if t.direction == 1 else 'SELL'} signal on {t.symbol} {t.timeframe}: {outcome}",
                          source=source_name(t.strategy, t.symbol, t.timeframe))
                db.commit()
            return
        spec, plan = outcome
        with Session(self.engine) as db:
            row = db.get(StrategyTrade, t.id, with_for_update=True)
            if row.status != "waiting":
                return
            try:
                pos = paper.open_at_market(db, user_id, spec, account_name=t.account_name,
                                           source=source_name(t.strategy, t.symbol, t.timeframe),
                                           idempotency_key=idempotency_key(user_id, t), now=now)
            except PaperError as exc:
                db.rollback()
                with Session(self.engine) as db2:
                    r2 = db2.get(StrategyTrade, t.id, with_for_update=True)
                    r2.status, r2.detail, r2.closed_at, r2.plan = "refused", str(exc), now, {**r2.plan, **plan}
                    paper.log(db2, user_id, None, "order_refused", f"{plan['description']}: {exc}",
                              source=source_name(t.strategy, t.symbol, t.timeframe))
                    db2.commit()
                return
            row.status, row.position_id, row.opened_at = "open", pos.id, now
            row.under_entry = Decimal(str(last))
            tgt, stp = engine_levels(run, t.signal_time, t.direction)
            row.under_target, row.under_stop = to_dec(tgt), to_dec(stp)
            row.plan = {**row.plan, **plan}
            row.detail = ""
            db.commit()
            log.info("user %s: opened %s for %s %s signal %s", user_id, plan["description"], t.symbol, t.timeframe,
                     t.signal_time)

    async def finish(self, user_id: int, md: MarketData, t: StrategyTrade, reason: str, under_exit: float | None,
                     market_open: bool, rule: str, now: datetime, why: str | None = None) -> None:
        """Closes a strategy trade's position at the market, now or at the next open."""
        why = why or f"Closed by {REASON_WORDS[reason]}."
        with Session(self.engine) as db:
            row = db.get(StrategyTrade, t.id, with_for_update=True)
            pos = db.get(PaperPosition, row.position_id) if row.position_id else None
            if row.status == "waiting" or pos is None or pos.status != "open":
                gone = "closed by hand" if pos is not None and pos.status == "closed" else "no longer open"
                row.status, row.closed_at = ("missed" if row.status == "waiting" else "closed"), now
                row.detail = why if row.status == "missed" else f"The position was {gone}."
                db.commit()
                return
            if row.exit_reason is None:
                row.exit_reason = "time" if reason == "expiry" else reason
                if under_exit is not None:
                    row.under_exit = Decimal(str(under_exit))
                row.detail = why + ("" if market_open else " The order goes in when the options market opens.")
                db.commit()
            legs = [pos.occ_symbol] + ([pos.occ_symbol2] if pos.occ_symbol2 else [])
        if not market_open:
            return
        quotes = await md.quotes(legs)
        books = {s: Book.of(q.bid, q.ask) for s, q in quotes.items()}
        with Session(self.engine) as db:
            row = db.get(StrategyTrade, t.id, with_for_update=True)
            pos = paper.lock_open(db, row.position_id)
            if pos is None:
                return
            price = paper.closing_price(pos, books, rule)
            if price is None:
                row.detail = why + " Waiting for a two-sided quote to close it."
                db.commit()
                return
            trade = paper.close_whole_at(db, pos, price, row.exit_reason, now=now, detail=why)
            trade.underlying_entry = row.under_entry
            trade.underlying_exit = row.under_exit
            row.status, row.closed_at = "closed", now
            db.commit()
            log.info("user %s: closed strategy trade %s (%s)", user_id, t.id, row.exit_reason)


def fresh(q: Quote | None, now: datetime) -> bool:
    if q is None or q.last is None:
        return False
    if q.trade_time is None:
        return True
    return (now - datetime.fromtimestamp(q.trade_time / 1000, timezone.utc)).total_seconds() <= STALE_SECONDS


def _dec(x: float | None) -> Decimal | None:
    return None if x is None else Decimal(str(round(x, 4)))


# ---------- choosing and sizing the contract ----------


async def option_legs(md: MarketData, symbol: str, expiration: date, option_type: str) -> list[auto_plan.Leg]:
    chain = await md.option_chain(symbol, expiration)
    return [auto_plan.Leg(Decimal(str(o.strike)), to_dec(o.bid), to_dec(o.ask), o.symbol)
            for o in chain if o.option_type == option_type and o.strike is not None]


def to_dec(x: float | None) -> Decimal | None:
    return None if x is None else Decimal(str(x))


async def choose_contract(md: MarketData, settings: auto_plan.TradeSettings, structure: str, symbol: str,
                          direction: int, price: Decimal, history: auto_plan.History, rule: str, today: date
                          ) -> tuple[paper.MarketOpen, dict] | str:
    """The trade a signal leads to right now, with how it was worked out, or why there is none."""
    rules = auto_plan.rules_for(settings, history)
    exps = await md.option_expirations(symbol)
    exp = auto_plan.pick_expiration(exps, today, rules.hold_days)
    if exp is None:
        return (f"No {symbol} expiration is {rules.hold_days} or more days away, so the option could expire "
                "before the strategy exits.")
    otype = auto_plan.option_type_for(structure, direction)
    legs = await option_legs(md, symbol, exp, otype)
    choice = auto_plan.choose(structure, direction, price, rules.distance_pct, settings.side, legs)
    if isinstance(choice, str):
        return choice
    sizing = auto_plan.size(choice, rule, Decimal(str(settings.risk_usd)))
    if isinstance(sizing, str):
        return sizing
    spec = paper.MarketOpen(
        structure="single" if structure == "directional" else structure, symbol=symbol, option_type=otype,
        expiration=exp, strike=(choice.sold or choice.bought).strike, occ=(choice.sold or choice.bought).occ,
        quantity=sizing.quantity, price=sizing.price,
        strike2=choice.bought.strike if choice.sold else None, occ2=choice.bought.occ if choice.sold else None)
    plan = {
        "description": auto_plan.describe(choice, exp, symbol), "stock_price": float(price),
        "distance_pct": rules.distance_pct, "distance_note": rules.distance_note, "side": settings.side,
        "target_strike": float(round(choice.target_strike, 2)), "hold_days": rules.hold_days,
        "hold_note": rules.hold_note, "expiration": exp.isoformat(), "quantity": sizing.quantity,
        "price": float(sizing.price), "unit_risk": float(sizing.unit_risk),
        "unit_reward": float(sizing.unit_reward) if sizing.unit_reward is not None else None,
        "max_loss": float(sizing.unit_risk * sizing.quantity),
        "max_gain": float(sizing.unit_reward * sizing.quantity) if sizing.unit_reward is not None else None,
        "payout": sizing.payout, "width": float(choice.width) if choice.sold else None, "risk_usd": settings.risk_usd,
    }
    return spec, plan


async def plan_trade(md: MarketData, preset: StrategyPreset, t: StrategyTrade, run: dict, price: Decimal, rule: str,
                     now: datetime) -> tuple[paper.MarketOpen, dict] | str:
    settings = auto_plan.load_settings(preset.trade)
    return await choose_contract(md, settings, t.structure, t.symbol, t.direction, price, auto_plan.history_of(run),
                                 rule, now.astimezone(NY).date())


async def run(engine: Engine, stop: asyncio.Event) -> None:
    await AutoTrader(engine).run(stop)

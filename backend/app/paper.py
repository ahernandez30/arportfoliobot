"""The paper trading engine (plan section 8). The only code that can place orders until Stage 8.

Both the web server (a new order may fill at once) and the worker (working orders, targets,
stops, expirations) change orders here. Every change locks the order or position row first
and re-checks its state, so the two can never fill or close the same thing twice.

Every order, fill, close and account change is written to paper_events, which is never edited.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auto_plan, ledger, paper_rules, user_settings
from app.ledger import ZERO
from app.marketdata.bars import NY
from app.models import ClosedTrade, PaperAccount, PaperEvent, PaperOrder, PaperPosition, TradingControls
from app.paper_rules import Book, order_value

MAIN = "main"
# Paper sub-accounts, one per trade structure, so structures run on the same signals never mix
# results (plan section 8). A single structure trades in the main account.
SUB_ACCOUNTS = ("directional", "credit_spread", "debit_spread")
ACCOUNT_LABELS = {MAIN: "Main", "directional": "Directional", "credit_spread": "Credit spread",
                  "debit_spread": "Debit spread"}


class PaperError(ValueError):
    """An order or action that cannot go ahead, with a message fit for the screen."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Contract:
    symbol: str
    option_type: str
    strike: Decimal
    expiration: date

    @property
    def occ(self) -> str:
        return ledger.occ_symbol(self.symbol, self.option_type, self.strike, self.expiration)

    @property
    def label(self) -> str:
        return f"{self.symbol} {ledger.fmt_qty(self.strike)} {self.option_type}, {self.expiration:%b %d %Y}"


def contract_of(row: PaperOrder | PaperPosition) -> Contract:
    return Contract(row.symbol, row.option_type, Decimal(row.strike), row.expiration)


SPREAD_NAMES = {("credit_spread", "put"): "bull put spread", ("credit_spread", "call"): "bear call spread",
                ("debit_spread", "call"): "bull call spread", ("debit_spread", "put"): "bear put spread"}


def label_of(row: PaperOrder | PaperPosition) -> str:
    """A position's name on screen: the contract, or both legs of a spread."""
    if row.structure == "single":
        return contract_of(row).label
    k = ledger.fmt_qty
    return (f"{row.symbol} {k(Decimal(row.strike))}/{k(Decimal(row.strike2))} {row.option_type} "
            f"{SPREAD_NAMES[(row.structure, row.option_type)]}, {row.expiration:%b %d %Y}")


def width_of(row: PaperOrder | PaperPosition) -> Decimal:
    return abs(Decimal(row.strike) - Decimal(row.strike2)) if row.structure != "single" else ZERO


def unit_risk(row: PaperOrder | PaperPosition, price: Decimal) -> Decimal:
    """Dollars one contract or spread opened at `price` can lose: the cash set aside for it."""
    if row.structure == "credit_spread":
        return (width_of(row) - price) * ledger.HUNDRED
    return price * ledger.HUNDRED


def position_value(pos: PaperPosition, close_price: Decimal) -> Decimal:
    """What the position gives back if closed at `close_price` (per share; for a spread, the net
    debit to buy a credit spread back or the net credit a debit spread sells for)."""
    if pos.structure == "credit_spread":
        return (width_of(pos) - close_price) * ledger.HUNDRED * pos.quantity
    return order_value(pos.quantity, close_price)


def position_cost(pos: PaperPosition) -> Decimal:
    return unit_risk(pos, Decimal(pos.entry_price)) * pos.quantity


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------- account, switches, log ----------


def account(db: Session, user_id: int, *, lock: bool = False, name: str = MAIN) -> PaperAccount:
    """A paper account of the user's (the main one unless named), created at the Config starting
    balance on first use."""
    stmt = select(PaperAccount).where(PaperAccount.user_id == user_id, PaperAccount.name == name)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        start = Decimal(str(user_settings.load(db, user_id).paper.starting_balance))
        row = PaperAccount(user_id=user_id, name=name, cash=start, starting_balance=start)
        db.add(row)
        db.flush()
        what = "Paper account" if name == MAIN else f"Paper sub-account “{ACCOUNT_LABELS.get(name, name)}”"
        log(db, user_id, row.id, "account_opened", f"{what} opened with ${start:,.2f}.")
        if lock:
            db.refresh(row, with_for_update=True)
    return row


def accounts(db: Session, user_id: int) -> list[PaperAccount]:
    """The main account first, then any sub-accounts."""
    rows = list(db.scalars(select(PaperAccount).where(PaperAccount.user_id == user_id)))
    order = (MAIN, *SUB_ACCOUNTS)
    return sorted(rows, key=lambda a: (order.index(a.name) if a.name in order else len(order), a.name))


def controls(db: Session, user_id: int, *, lock: bool = False) -> TradingControls:
    stmt = select(TradingControls).where(TradingControls.user_id == user_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        row = TradingControls(user_id=user_id, halted=False, auto_paused=False)
        db.add(row)
        db.flush()
    return row


def log(db: Session, user_id: int, account_id: int | None, event: str, detail: str, *, source: str = "manual",
        order_id: int | None = None, position_id: int | None = None) -> None:
    db.add(PaperEvent(user_id=user_id, account_id=account_id, event=event, detail=detail, source=source,
                      order_id=order_id, position_id=position_id))


def reserved_cash(db: Session, account_id: int) -> Decimal:
    """Cash set aside for working buy orders (at their limit price)."""
    rows = db.execute(select(PaperOrder.quantity, PaperOrder.limit_price).where(
        PaperOrder.account_id == account_id, PaperOrder.status == "working", PaperOrder.side == "buy"))
    return sum((order_value(q, p) for q, p in rows if p is not None), ZERO)


def realized_today(db: Session, user_id: int, now: datetime | None = None) -> Decimal:
    """Today's (New York date) result of finished paper trades, for the daily loss limit."""
    now = now or utcnow()
    start = datetime.combine(now.astimezone(NY).date(), datetime.min.time(), tzinfo=NY)
    rows = db.scalars(select(ClosedTrade).where(
        ClosedTrade.user_id == user_id, ClosedTrade.mode == "paper", ClosedTrade.closed_at >= start,
        ClosedTrade.closed_at < start + timedelta(days=1)))
    return sum((ledger.trade_result(t.direction, t.kind, t.quantity, t.entry_price, t.exit_price, t.fees)[0]
                for t in rows), ZERO)


def open_positions(db: Session, user_id: int, account_id: int | None = None) -> list[PaperPosition]:
    stmt = select(PaperPosition).where(PaperPosition.user_id == user_id, PaperPosition.status == "open")
    if account_id is not None:
        stmt = stmt.where(PaperPosition.account_id == account_id)
    return list(db.scalars(stmt.order_by(PaperPosition.opened_at)))


def working_orders(db: Session, user_id: int, account_id: int | None = None) -> list[PaperOrder]:
    stmt = select(PaperOrder).where(PaperOrder.user_id == user_id, PaperOrder.status == "working")
    if account_id is not None:
        stmt = stmt.where(PaperOrder.account_id == account_id)
    return list(db.scalars(stmt.order_by(PaperOrder.created_at)))


def recent_orders(db: Session, user_id: int, limit: int = 30, account_id: int | None = None) -> list[PaperOrder]:
    stmt = select(PaperOrder).where(PaperOrder.user_id == user_id, PaperOrder.status != "working")
    if account_id is not None:
        stmt = stmt.where(PaperOrder.account_id == account_id)
    return list(db.scalars(stmt.order_by(PaperOrder.done_at.desc(), PaperOrder.id.desc()).limit(limit)))


def closing_quantity(db: Session, position_id: int) -> int:
    """Contracts of a position already promised to working closing orders."""
    return int(db.scalar(select(func.coalesce(func.sum(PaperOrder.quantity), 0)).where(
        PaperOrder.position_id == position_id, PaperOrder.status == "working", PaperOrder.intent == "close")) or 0)


# ---------- placing and cancelling ----------


def place_open(db: Session, user_id: int, c: Contract, quantity: int, limit: Decimal,
               take_profit_pct: Decimal | None, stop_loss_pct: Decimal | None, *, source: str = "manual",
               now: datetime | None = None, idempotency_key: str | None = None) -> PaperOrder:
    """A buy-to-open limit order, after every check in plan section 10."""
    now = now or utcnow()
    if idempotency_key and db.scalar(select(PaperOrder.id).where(PaperOrder.idempotency_key == idempotency_key)):
        raise PaperError("This signal already placed its order.")
    acct = account(db, user_id, lock=True)
    ctl = controls(db, user_id)
    settings = user_settings.load(db, user_id)
    problem = paper_rules.opening_order_problem(
        halted=ctl.halted, quantity=quantity, limit=limit,
        available_cash=acct.cash - reserved_cash(db, acct.id),
        realized_today=realized_today(db, user_id, now),
        limits=paper_rules.Limits(Decimal(str(settings.trading.max_order_usd)),
                                  Decimal(str(settings.trading.max_daily_loss_usd))),
        expiration=c.expiration, today=now.astimezone(NY).date(),
    )
    if problem:
        log(db, user_id, acct.id, "order_refused", f"Buy {quantity} {c.label} at {limit}: {problem}", source=source)
        db.commit()
        raise PaperError(problem)
    order = PaperOrder(user_id=user_id, account_id=acct.id, source=source, side="buy", intent="open",
                       symbol=c.symbol, option_type=c.option_type, strike=c.strike, expiration=c.expiration,
                       occ_symbol=c.occ, structure="single", quantity=quantity, limit_price=limit, take_profit_pct=take_profit_pct,
                       stop_loss_pct=stop_loss_pct, status="working", idempotency_key=idempotency_key)
    db.add(order)
    db.flush()
    exits = []
    if take_profit_pct:
        exits.append(f"take profit {ledger.fmt_qty(take_profit_pct)}%")
    if stop_loss_pct:
        exits.append(f"stop loss {ledger.fmt_qty(stop_loss_pct)}%")
    log(db, user_id, acct.id, "order_placed",
        f"Buy {quantity} {c.label}, limit {limit}" + (f", {', '.join(exits)}" if exits else "") + ".",
        source=source, order_id=order.id)
    return order


def own_position(db: Session, user_id: int, position_id: int, *, lock: bool = False) -> PaperPosition | None:
    stmt = select(PaperPosition).where(PaperPosition.id == position_id, PaperPosition.user_id == user_id)
    return db.scalar(stmt.with_for_update() if lock else stmt)


def place_close(db: Session, user_id: int, position_id: int, quantity: int | None, limit: Decimal | None,
                reason: str = "manual", *, source: str = "manual", replace_working: bool = False) -> PaperOrder:
    """A sell-to-close order for all (quantity None) or part of a position. Closing is allowed
    even when trading is stopped, since it only reduces risk."""
    pos = own_position(db, user_id, position_id, lock=True)
    if pos is None or pos.status != "open":
        raise PaperError("That position is not open any more.", status=404)
    if pos.structure != "single":
        raise PaperError("A spread closes as a whole, at the market.")
    if replace_working:
        for o in db.scalars(select(PaperOrder).where(PaperOrder.position_id == pos.id, PaperOrder.status == "working",
                                                     PaperOrder.intent == "close").with_for_update()):
            _finish(db, o, "cancelled", "Replaced by a new closing order.")
    free = pos.quantity - closing_quantity(db, pos.id)
    quantity = free if quantity is None else quantity
    if quantity <= 0:
        raise PaperError("Every contract of this position already has a closing order working. Cancel it first.")
    if quantity > free:
        raise PaperError(f"Only {free} contract{'s' if free != 1 else ''} of this position can still be sold.")
    c = contract_of(pos)
    order = PaperOrder(user_id=user_id, account_id=pos.account_id, source=source, side="sell", intent="close",
                       position_id=pos.id, symbol=c.symbol, option_type=c.option_type, strike=c.strike,
                       expiration=c.expiration, occ_symbol=c.occ, structure="single", quantity=quantity, limit_price=limit,
                       close_reason=reason, status="working")
    db.add(order)
    db.flush()
    log(db, user_id, pos.account_id, "order_placed",
        f"Sell {quantity} {c.label}, {'limit ' + str(limit) if limit is not None else 'at the market'} ({reason}).",
        source=source, order_id=order.id, position_id=pos.id)
    return order


def _finish(db: Session, order: PaperOrder, status: str, detail: str) -> None:
    order.status = status
    order.status_detail = detail
    order.done_at = utcnow()
    log(db, order.user_id, order.account_id, f"order_{status}",
        f"{'Buy' if order.side == 'buy' else 'Sell'} {order.quantity} {contract_of(order).label}: {detail}",
        source=order.source, order_id=order.id, position_id=order.position_id)


def cancel(db: Session, user_id: int, order_id: int, detail: str = "Cancelled by you.") -> PaperOrder:
    order = db.scalar(select(PaperOrder).where(PaperOrder.id == order_id, PaperOrder.user_id == user_id)
                      .with_for_update())
    if order is None:
        raise PaperError("That order was not found.", status=404)
    if order.status != "working":
        raise PaperError("That order is no longer working (it has already filled or been cancelled).")
    _finish(db, order, "cancelled", detail)
    return order


# ---------- fills ----------


def _lock_working(db: Session, order_id: int) -> PaperOrder | None:
    return db.scalar(select(PaperOrder).where(PaperOrder.id == order_id, PaperOrder.status == "working")
                     .with_for_update(skip_locked=True))


def try_fill(db: Session, order_id: int, book: Book, rule: str, now: datetime | None = None) -> bool:
    """Fills a working order if the quote allows it. Safe to call from two processes at once."""
    order = _lock_working(db, order_id)
    if order is None:
        return False
    price = paper_rules.fill_price(order.side, book, rule, order.limit_price)
    if price is None:
        return False
    _apply_fill(db, order, price, now or utcnow())
    return True


def _apply_fill(db: Session, order: PaperOrder, price: Decimal, now: datetime, detail: str = "") -> PaperPosition:
    acct = db.scalar(select(PaperAccount).where(PaperAccount.id == order.account_id).with_for_update())
    value = order_value(order.quantity, price)
    order.status = "filled"
    order.fill_price = price
    order.done_at = now
    order.status_detail = detail
    c = contract_of(order)
    if order.intent == "open":
        tp, sl = paper_rules.exit_prices(price, order.take_profit_pct, order.stop_loss_pct)
        acct.cash -= value
        pos = PaperPosition(user_id=order.user_id, account_id=order.account_id, source=order.source,
                            symbol=c.symbol, option_type=c.option_type, strike=c.strike, expiration=c.expiration,
                            occ_symbol=c.occ, structure="single", quantity=order.quantity, entry_price=price, take_profit_price=tp,
                            stop_loss_price=sl, status="open", opened_at=now)
        db.add(pos)
        db.flush()
        order.position_id = pos.id
        log(db, order.user_id, acct.id, "order_filled",
            f"Bought {order.quantity} {c.label} at {price} (${value:,.2f})."
            + (f" Target {tp}." if tp is not None else "") + (f" Stop {sl}." if sl is not None else ""),
            source=order.source, order_id=order.id, position_id=pos.id)
        return pos
    pos = db.scalar(select(PaperPosition).where(PaperPosition.id == order.position_id).with_for_update())
    sold = min(order.quantity, pos.quantity)
    acct.cash += order_value(sold, price)
    pos.quantity -= sold
    trade = ClosedTrade(user_id=order.user_id, mode="paper", source=pos.source, kind="option", symbol=c.symbol,
                        option_type=c.option_type, strike=c.strike, expiration=c.expiration, direction="long",
                        quantity=Decimal(sold), entry_price=pos.entry_price, exit_price=price, fees=ZERO,
                        opened_at=pos.opened_at, closed_at=now, close_reason=order.close_reason or "manual",
                        paper_position_id=pos.id, account_name=acct.name)
    db.add(trade)
    result = order_value(sold, price - pos.entry_price)
    log(db, order.user_id, acct.id, "order_filled",
        f"Sold {sold} {c.label} at {price} (${order_value(sold, price):,.2f}), result "
        f"{'+' if result >= 0 else '−'}${abs(result):,.2f} ({order.close_reason}).",
        source=order.source, order_id=order.id, position_id=pos.id)
    if pos.quantity == 0:
        pos.status = "closed"
        pos.closed_at = now
        # Nothing left to sell: any other closing orders for it are pointless.
        for o in db.scalars(select(PaperOrder).where(PaperOrder.position_id == pos.id, PaperOrder.status == "working")
                            .with_for_update()):
            _finish(db, o, "cancelled", "The position is already closed.")
    return pos


def close_at_market(db: Session, position: PaperPosition, book: Book, rule: str, reason: str,
                    now: datetime | None = None) -> bool:
    """Closes a whole position now (target or stop reached). Replaces any working closing orders."""
    price = paper_rules.market_price("sell", book, rule)
    if price is None:
        return False
    order = place_close(db, position.user_id, position.id, None, None, reason, source=position.source,
                        replace_working=True)
    _apply_fill(db, order, price, now or utcnow())
    return True


def check_exits(db: Session, position_id: int, book: Book, rule: str, now: datetime | None = None) -> str | None:
    """Closes a position whose target or stop has been reached. Returns which one, if any."""
    pos = db.scalar(select(PaperPosition).where(PaperPosition.id == position_id, PaperPosition.status == "open")
                    .with_for_update(skip_locked=True))
    if pos is None or pos.structure != "single":
        return None
    hit = paper_rules.exit_trigger(book, rule, pos.take_profit_price, pos.stop_loss_price)
    if hit and close_at_market(db, pos, book, rule, hit, now):
        return hit
    return None


def settle(db: Session, position_id: int, underlying: Decimal, now: datetime | None = None) -> bool:
    """Settles an expired position: worthless if out of the money, else its in-the-money value."""
    pos = db.scalar(select(PaperPosition).where(PaperPosition.id == position_id, PaperPosition.status == "open")
                    .with_for_update(skip_locked=True))
    if pos is None:
        return False
    if pos.structure != "single":
        return _settle_spread(db, pos, underlying, now or utcnow())
    for o in db.scalars(select(PaperOrder).where(PaperOrder.position_id == pos.id, PaperOrder.status == "working")
                        .with_for_update()):
        _finish(db, o, "cancelled", "The option expired.")
    price = paper_rules.settlement_price(pos.option_type, Decimal(pos.strike), underlying)
    order = PaperOrder(user_id=pos.user_id, account_id=pos.account_id, source=pos.source, side="sell",
                       intent="close", position_id=pos.id, symbol=pos.symbol, option_type=pos.option_type,
                       strike=pos.strike, expiration=pos.expiration, occ_symbol=pos.occ_symbol, structure="single",
                       quantity=pos.quantity, limit_price=None, close_reason="expired", status="working")
    db.add(order)
    db.flush()
    _apply_fill(db, order, price, now or utcnow(),
                detail=f"Settled at expiration with {pos.symbol} at {underlying}.")
    return True


def expire_order(db: Session, order_id: int) -> bool:
    order = _lock_working(db, order_id)
    if order is None:
        return False
    _finish(db, order, "cancelled", "The option expired before the order filled.")
    return True


# ---------- switches ----------


def stop_all(db: Session, user_id: int) -> int:
    """"Stop all trading": cancels every working order, turns automatic trading off, and refuses
    new orders until resumed. Targets and stops on open positions stay active."""
    ctl = controls(db, user_id, lock=True)
    ctl.halted = True
    ctl.changed_at = utcnow()
    settings = user_settings.load(db, user_id)
    if settings.trading.auto_trading != "off":
        user_settings.save(db, user_id, user_settings.apply_changes(settings, {"trading": {"auto_trading": "off"}}))
    n = 0
    for o in db.scalars(select(PaperOrder).where(PaperOrder.user_id == user_id, PaperOrder.status == "working")
                        .with_for_update()):
        _finish(db, o, "cancelled", "Stop all trading.")
        n += 1
    log(db, user_id, None, "trading_stopped", f"Stop all trading: {n} working order(s) cancelled, automatic trading off.")
    return n


def resume(db: Session, user_id: int) -> None:
    ctl = controls(db, user_id, lock=True)
    ctl.halted = False
    ctl.changed_at = utcnow()
    log(db, user_id, None, "trading_resumed", "Trading resumed. Automatic trading stays off until you turn it on.")


def set_auto_paused(db: Session, user_id: int, paused: bool) -> None:
    ctl = controls(db, user_id, lock=True)
    ctl.auto_paused = paused
    ctl.changed_at = utcnow()
    log(db, user_id, None, "auto_paused" if paused else "auto_resumed",
        "Automatic trading paused." if paused else "Automatic trading un-paused.")


def reset(db: Session, user_id: int, name: str = MAIN) -> None:
    """Starts a paper account over at the Config starting balance. Working orders are
    cancelled and open positions are set aside (not counted as trades). Finished paper
    trades stay in Account Manager and the event log keeps everything."""
    acct = account(db, user_id, lock=True, name=name)
    for o in db.scalars(select(PaperOrder).where(PaperOrder.account_id == acct.id, PaperOrder.status == "working")
                        .with_for_update()):
        _finish(db, o, "cancelled", "Paper account reset.")
    voided = 0
    for p in db.scalars(select(PaperPosition).where(PaperPosition.account_id == acct.id,
                                                    PaperPosition.status == "open").with_for_update()):
        p.status = "voided"
        p.closed_at = utcnow()
        voided += 1
    start = Decimal(str(user_settings.load(db, user_id).paper.starting_balance))
    old = acct.cash
    acct.cash = acct.starting_balance = start
    acct.reset_at = utcnow()
    log(db, user_id, acct.id, "account_reset",
        f"{'Paper account' if name == MAIN else 'Paper sub-account ' + ACCOUNT_LABELS.get(name, name)} reset to ${start:,.2f} (cash was ${old:,.2f}; {voided} open position(s) set aside).")


def events(db: Session, user_id: int, limit: int = 50) -> list[PaperEvent]:
    return list(db.scalars(select(PaperEvent).where(PaperEvent.user_id == user_id)
                           .order_by(PaperEvent.at.desc(), PaperEvent.id.desc()).limit(limit)))


# ---------- automatic trades: open and close at the market, spreads ----------


@dataclass(frozen=True)
class MarketOpen:
    """An opening trade filled at once at prices just quoted (automatic trades, plan 7.6).
    For a spread, strike/occ are the leg sold and strike2/occ2 the leg bought; price is net per share."""

    structure: str
    symbol: str
    option_type: str
    expiration: date
    strike: Decimal
    occ: str
    quantity: int
    price: Decimal
    strike2: Decimal | None = None
    occ2: str | None = None


def open_at_market(db: Session, user_id: int, spec: MarketOpen, *, account_name: str = MAIN, source: str,
                   idempotency_key: str, now: datetime | None = None) -> PaperPosition:
    """Opens a position filled at `spec.price`, after every check in plan section 10. The size
    limit applies to the most it can lose (for a spread, width minus credit)."""
    now = now or utcnow()
    if db.scalar(select(PaperOrder.id).where(PaperOrder.idempotency_key == idempotency_key)):
        raise PaperError("This signal already placed its order.")
    acct = account(db, user_id, lock=True, name=account_name)
    ctl = controls(db, user_id)
    settings = user_settings.load(db, user_id)
    order = PaperOrder(user_id=user_id, account_id=acct.id, source=source, side="sell" if spec.structure == "credit_spread" else "buy",
                       intent="open", symbol=spec.symbol, option_type=spec.option_type, strike=spec.strike,
                       expiration=spec.expiration, occ_symbol=spec.occ, structure=spec.structure, strike2=spec.strike2,
                       occ_symbol2=spec.occ2, quantity=spec.quantity, limit_price=None, status="working",
                       idempotency_key=idempotency_key)
    risk_each = unit_risk(order, spec.price)
    problem = paper_rules.opening_order_problem(
        halted=ctl.halted, quantity=spec.quantity, limit=risk_each / ledger.HUNDRED,
        available_cash=acct.cash - reserved_cash(db, acct.id), realized_today=realized_today(db, user_id, now),
        limits=paper_rules.Limits(Decimal(str(settings.trading.max_order_usd)),
                                  Decimal(str(settings.trading.max_daily_loss_usd))),
        expiration=spec.expiration, today=now.astimezone(NY).date(),
        what="cost" if spec.structure != "credit_spread" else "risk")
    if problem:
        log(db, user_id, acct.id, "order_refused", f"{label_of(order)} x{spec.quantity}: {problem}", source=source)
        raise PaperError(problem)
    db.add(order)
    total = risk_each * spec.quantity
    acct.cash -= total
    order.status = "filled"
    order.fill_price = spec.price
    order.done_at = now
    pos = PaperPosition(user_id=user_id, account_id=acct.id, source=source, symbol=spec.symbol,
                        option_type=spec.option_type, strike=spec.strike, expiration=spec.expiration,
                        occ_symbol=spec.occ, structure=spec.structure, strike2=spec.strike2, occ_symbol2=spec.occ2,
                        quantity=spec.quantity, entry_price=spec.price, status="open", opened_at=now)
    db.add(pos)
    db.flush()
    order.position_id = pos.id
    if spec.structure == "credit_spread":
        what = f"Sold {spec.quantity} {label_of(pos)} for a credit of {spec.price} (${total:,.2f} set aside: most it can lose)."
    elif spec.structure == "debit_spread":
        what = f"Bought {spec.quantity} {label_of(pos)} for {spec.price} (${total:,.2f})."
    else:
        what = f"Bought {spec.quantity} {label_of(pos)} at {spec.price} (${total:,.2f})."
    log(db, user_id, acct.id, "order_filled", what, source=source, order_id=order.id, position_id=pos.id)
    return pos


def lock_open(db: Session, position_id: int) -> PaperPosition | None:
    return db.scalar(select(PaperPosition).where(PaperPosition.id == position_id, PaperPosition.status == "open")
                     .with_for_update(skip_locked=True))


def close_whole_at(db: Session, pos: PaperPosition, price: Decimal, reason: str, *, now: datetime | None = None,
                   source: str | None = None, detail: str = "") -> ClosedTrade:
    """Closes a whole locked open position (single option or spread) at `price` per share (net for a
    spread), cancelling any working orders on it, and writes the finished trade."""
    now = now or utcnow()
    for o in db.scalars(select(PaperOrder).where(PaperOrder.position_id == pos.id, PaperOrder.status == "working")
                        .with_for_update()):
        _finish(db, o, "cancelled", "Replaced by a closing order.")
    acct = db.scalar(select(PaperAccount).where(PaperAccount.id == pos.account_id).with_for_update())
    order = PaperOrder(user_id=pos.user_id, account_id=pos.account_id, source=source or pos.source,
                       side="buy" if pos.structure == "credit_spread" else "sell", intent="close", position_id=pos.id,
                       symbol=pos.symbol, option_type=pos.option_type, strike=pos.strike, expiration=pos.expiration,
                       occ_symbol=pos.occ_symbol, structure=pos.structure, strike2=pos.strike2,
                       occ_symbol2=pos.occ_symbol2, quantity=pos.quantity, limit_price=None, close_reason=reason,
                       status="filled", fill_price=price, done_at=now, status_detail=detail)
    db.add(order)
    db.flush()
    back = position_value(pos, price)
    risk = position_cost(pos)
    acct.cash += back
    trade = ClosedTrade(user_id=pos.user_id, mode="paper", source=pos.source, kind="option", symbol=pos.symbol,
                        option_type=pos.option_type, strike=pos.strike, expiration=pos.expiration,
                        direction="short" if pos.structure == "credit_spread" else "long",
                        quantity=Decimal(pos.quantity), entry_price=pos.entry_price, exit_price=price, fees=ZERO,
                        opened_at=pos.opened_at, closed_at=now, close_reason=reason, paper_position_id=pos.id,
                        structure=pos.structure, strike2=pos.strike2, account_name=acct.name,
                        risk=risk if pos.structure != "single" else None)
    db.add(trade)
    result = back - risk
    verb = "Bought back" if pos.structure == "credit_spread" else "Sold"
    log(db, pos.user_id, acct.id, "order_filled",
        f"{verb} {pos.quantity} {label_of(pos)} at {price} (${back:,.2f} back), result "
        f"{'+' if result >= 0 else '−'}${abs(result):,.2f} ({reason})." + (f" {detail}" if detail else ""),
        source=order.source, order_id=order.id, position_id=pos.id)
    pos.quantity = 0
    pos.status = "closed"
    pos.closed_at = now
    db.flush()
    return trade


def closing_price(pos: PaperPosition, books: dict[str, Book], rule: str) -> Decimal | None:
    """What closing the whole position would fill at now, per share (net for a spread)."""
    if pos.structure == "single":
        b = books.get(pos.occ_symbol)
        return paper_rules.market_price("sell", b, rule) if b else None
    sold, bought = books.get(pos.occ_symbol), books.get(pos.occ_symbol2)
    if sold is None or bought is None:
        return None
    return auto_plan.spread_price(pos.structure, sold, bought, rule, False, width_of(pos))


def _settle_spread(db: Session, pos: PaperPosition, underlying: Decimal, now: datetime) -> bool:
    """At expiration each leg is worth how far it is in the money; the pair settles at the difference."""
    sold = paper_rules.settlement_price(pos.option_type, Decimal(pos.strike), underlying)
    bought = paper_rules.settlement_price(pos.option_type, Decimal(pos.strike2), underlying)
    w = width_of(pos)
    net = sold - bought if pos.structure == "credit_spread" else bought - sold
    net = max(ZERO, min(w, net))
    close_whole_at(db, pos, net, "expired", now=now, detail=f"Settled at expiration with {pos.symbol} at {underlying}.")
    return True

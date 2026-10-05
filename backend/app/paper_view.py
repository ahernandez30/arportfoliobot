"""What the Live Trader, Account Manager and dashboard show about the paper account,
valued at live quotes fetched with the user's own key."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app import paper, paper_rules, user_settings
from app.capital import money, price_out
from app.ledger import ZERO
from app.marketdata.bars import NY
from app.marketdata.base import MarketData, MarketDataError, Quote
from app.models import PaperEvent, PaperOrder, PaperPosition
from app.paper_rules import Book

WARN_DAYS = 3


async def quotes_for(md: MarketData | None, symbols: list[str]) -> tuple[dict[str, Quote], str | None]:
    if md is None or not symbols:
        return {}, None
    try:
        return await md.quotes(symbols), None
    except MarketDataError as exc:
        return {}, str(exc)


def position_out(p: PaperPosition, quotes: dict[str, Quote], rule: str, today) -> dict:
    q = quotes.get(p.occ_symbol)
    if p.structure == "single":
        book = Book.of(q.bid, q.ask) if q else Book(None, None)
        price = paper_rules.market_price("sell", book, rule)
        if price is None and q is not None and q.last is not None:
            price = Decimal(str(q.last))
        bid, ask = (q.bid, q.ask) if q else (None, None)
    else:
        books = {s: Book.of(x.bid, x.ask) for s, x in quotes.items() if s in (p.occ_symbol, p.occ_symbol2)}
        price = paper.closing_price(p, books, rule)
        bid = ask = None
    cost = paper.position_cost(p)
    value = paper.position_value(p, price) if price is not None else None
    days_left = (p.expiration - today).days
    return {
        "id": p.id, "source": p.source, "label": paper.label_of(p), "symbol": p.symbol, "structure": p.structure,
        "option_type": p.option_type, "strike": float(p.strike), "expiration": p.expiration.isoformat(),
        "strike2": float(p.strike2) if p.strike2 is not None else None,
        "width": float(paper.width_of(p)) if p.structure != "single" else None,
        "quantity": p.quantity, "entry_price": price_out(p.entry_price),
        "bid": bid, "ask": ask, "price": price_out(price),
        "cost": money(cost), "value": money(value),
        "pl": money(value - cost) if value is not None else None,
        "pl_pct": float(round((value - cost) / cost * 100, 4)) if value is not None and cost else None,
        "take_profit_price": price_out(p.take_profit_price), "stop_loss_price": price_out(p.stop_loss_price),
        "opened_at": p.opened_at.isoformat(), "days_left": days_left,
        "expiring_soon": 0 <= days_left <= WARN_DAYS,
    }


def order_out(o: PaperOrder) -> dict:
    return {
        "id": o.id, "source": o.source, "side": o.side, "intent": o.intent, "label": paper.label_of(o),
        "structure": o.structure,
        "occ_symbol": o.occ_symbol, "quantity": o.quantity, "limit_price": price_out(o.limit_price),
        "take_profit_pct": float(o.take_profit_pct) if o.take_profit_pct is not None else None,
        "stop_loss_pct": float(o.stop_loss_pct) if o.stop_loss_pct is not None else None,
        "close_reason": o.close_reason, "status": o.status, "status_detail": o.status_detail,
        "fill_price": price_out(o.fill_price), "created_at": o.created_at.isoformat(),
        "done_at": o.done_at.isoformat() if o.done_at else None, "position_id": o.position_id,
    }


def event_out(e: PaperEvent) -> dict:
    return {"id": e.id, "at": e.at.isoformat(), "event": e.event, "source": e.source, "detail": e.detail}


async def summary(db: Session, user_id: int, md: MarketData | None, account_name: str = paper.MAIN) -> dict:
    """One paper account (the main one, or a sub-account per structure) at live prices."""
    acct = paper.account(db, user_id, name=account_name)
    db.commit()  # keep a newly opened account
    ctl = paper.controls(db, user_id)
    db.commit()
    rule = user_settings.load(db, user_id).paper.fill_rule
    positions = paper.open_positions(db, user_id, acct.id)
    orders = paper.working_orders(db, user_id, acct.id)
    legs = {p.occ_symbol for p in positions} | {p.occ_symbol2 for p in positions if p.occ_symbol2}
    quotes, problem = await quotes_for(md, sorted(legs | {o.occ_symbol for o in orders}))
    today = datetime.now(NY).date()
    rows = [position_out(p, quotes, rule, today) for p in positions]
    priced = all(r["value"] is not None for r in rows)
    positions_value = sum((Decimal(str(r["value"] if r["value"] is not None else r["cost"])) for r in rows), ZERO)
    open_pl = sum((Decimal(str(r["pl"])) for r in rows if r["pl"] is not None), ZERO)
    reserved = paper.reserved_cash(db, acct.id)
    working = []
    for o in orders:
        out = order_out(o)
        q = quotes.get(o.occ_symbol)
        out["bid"], out["ask"] = (q.bid, q.ask) if q else (None, None)
        working.append(out)
    settings = user_settings.load(db, user_id)
    return {
        "account": {
            "cash": money(acct.cash), "reserved": money(reserved), "free_cash": money(acct.cash - reserved),
            "positions_value": money(positions_value), "total": money(acct.cash + positions_value),
            "open_pl": money(open_pl), "realized_today": money(paper.realized_today(db, user_id)),
            "starting_balance": money(acct.starting_balance), "reset_at": acct.reset_at.isoformat() if acct.reset_at else None,
            "estimated": not priced,
        },
        "positions": rows,
        "orders": working,
        "recent": [order_out(o) for o in paper.recent_orders(db, user_id, 20, acct.id)],
        "account_name": acct.name,
        "accounts": [{"name": a.name, "label": paper.ACCOUNT_LABELS.get(a.name, a.name)}
                     for a in paper.accounts(db, user_id)],
        "controls": {"halted": ctl.halted, "auto_paused": ctl.auto_paused,
                     "auto_trading": settings.trading.auto_trading},
        "fill_rule": rule,
        "prices": {"available": md is not None and problem is None, "detail": problem},
    }

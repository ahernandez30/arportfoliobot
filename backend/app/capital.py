"""Capital Tracking: a user's long-term positions, deposits and withdrawals, valued at today's
prices, and the daily snapshots behind the capital-over-time chart.

Every query filters on the user id it is given; callers pass the signed-in user only.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import ledger
from app.ledger import ZERO, Fill, Holding, Valued
from app.marketdata.bars import NY
from app.marketdata.base import MarketData, MarketDataError, Quote
from app.models import CapitalFlow, CapitalSnapshot, LongTermPosition, LongTermTrade

# Days before expiration when an open option gets a warning.
EXPIRY_WARNING_DAYS = 7


def today_ny() -> date:
    return datetime.now(NY).date()


def contract_label(kind: str, symbol: str, option_type: str | None, strike: Decimal | None,
                   expiration: date | None) -> str:
    """'TSLA 450 call, Dec 18 2026' or 'TSLA shares'."""
    if kind == "stock":
        return f"{symbol} shares"
    return f"{symbol} {ledger.fmt_qty(Decimal(strike))} {option_type}, {expiration:%b %d %Y}"


# ---------- loading ----------


def positions(db: Session, user_id: int) -> list[LongTermPosition]:
    return list(db.scalars(
        select(LongTermPosition).where(LongTermPosition.user_id == user_id).order_by(LongTermPosition.id)
    ))


def position(db: Session, user_id: int, position_id: int) -> LongTermPosition | None:
    return db.scalar(select(LongTermPosition).where(
        LongTermPosition.id == position_id, LongTermPosition.user_id == user_id))


def trades_by_position(db: Session, user_id: int) -> dict[int, list[LongTermTrade]]:
    out: dict[int, list[LongTermTrade]] = {}
    rows = db.scalars(select(LongTermTrade).where(LongTermTrade.user_id == user_id)
                      .order_by(LongTermTrade.day, LongTermTrade.id))
    for t in rows:
        out.setdefault(t.position_id, []).append(t)
    return out


def flows(db: Session, user_id: int, account: str) -> list[CapitalFlow]:
    return list(db.scalars(
        select(CapitalFlow).where(CapitalFlow.user_id == user_id, CapitalFlow.account == account)
        .order_by(CapitalFlow.day, CapitalFlow.id)
    ))


def put_in(db: Session, user_id: int, account: str) -> Decimal:
    return ledger.net_flows((f.kind, f.amount) for f in flows(db, user_id, account))


def as_fill(t: LongTermTrade) -> Fill:
    return Fill(side=t.side, quantity=t.quantity, price=t.price, fees=t.fees, day=t.day, id=t.id)


def holding(p: LongTermPosition, trades: Sequence[LongTermTrade]) -> Holding:
    return ledger.replay((as_fill(t) for t in trades), p.kind)


def find_open(db: Session, user_id: int, kind: str, symbol: str, option_type: str | None,
              strike: Decimal | None, expiration: date | None) -> LongTermPosition | None:
    """An open position for the same stock or contract, so a new buy adds to it."""
    by_pos = trades_by_position(db, user_id)
    for p in positions(db, user_id):
        same = (p.kind == kind and p.symbol == symbol and p.option_type == option_type
                and p.expiration == expiration
                and (p.strike is None and strike is None or p.strike is not None and strike is not None
                     and Decimal(p.strike) == Decimal(strike)))
        if same and holding(p, by_pos.get(p.id, [])).quantity > 0:
            return p
    return None


def check_trades(p: LongTermPosition, trades: Sequence[LongTermTrade | Fill]) -> Holding:
    """Raises ledger.LedgerError if the buys and sells do not add up."""
    return ledger.replay((t if isinstance(t, Fill) else as_fill(t) for t in trades), p.kind)


# ---------- prices ----------


@dataclass(frozen=True)
class Mark:
    price: Decimal | None
    source: str  # "mid", "last", "intrinsic" or "none"
    underlying: Decimal | None = None


def quote_symbols(items: Sequence[LongTermPosition]) -> list[str]:
    out: list[str] = []
    for p in items:
        wanted = [p.symbol] if p.kind == "stock" else [p.symbol, ledger.occ_symbol(p.symbol, p.option_type, p.strike, p.expiration)]
        for s in wanted:
            if s not in out:
                out.append(s)
    return out


async def fetch_quotes(md: MarketData | None, items: Sequence[LongTermPosition]) -> tuple[dict[str, Quote], str | None]:
    """Today's quotes for the positions and their underlying stocks, plus a problem message if any."""
    symbols = quote_symbols(items)
    if md is None or not symbols:
        return {}, None
    try:
        return await md.quotes(symbols), None
    except MarketDataError as exc:
        return {}, str(exc)


def _dec(x: float | None) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def mark(p: LongTermPosition, quotes: dict[str, Quote], today: date) -> Mark:
    under_q = quotes.get(p.symbol)
    underlying = _dec(under_q.last) if under_q else None
    if p.kind == "stock":
        return Mark(underlying, "last" if underlying is not None else "none", underlying)
    if p.expiration < today:
        # Expired: worth whatever it was in the money. Uses today's stock price, which is
        # close enough until the user records how it actually settled.
        if underlying is None:
            return Mark(None, "none", None)
        return Mark(ledger.intrinsic(p.option_type, Decimal(p.strike), underlying), "intrinsic", underlying)
    q = quotes.get(ledger.occ_symbol(p.symbol, p.option_type, p.strike, p.expiration))
    if q is None:
        return Mark(None, "none", underlying)
    price = ledger.mid_price(q.bid, q.ask, q.last)
    usable_mid = q.bid is not None and q.ask is not None and q.ask > 0
    return Mark(price, "none" if price is None else ("mid" if usable_mid else "last"), underlying)


# ---------- summary ----------


def money(x: Decimal | None) -> float | None:
    return None if x is None else float(ledger.cents(x))


def price_out(x: Decimal | None) -> float | None:
    return None if x is None else float(x.quantize(Decimal("0.0001")))


def qty_out(x: Decimal) -> float:
    return float(x)


def trade_out(t: LongTermTrade, kind: str) -> dict:
    gross = t.quantity * t.price * ledger.multiplier(kind)
    return {
        "id": t.id, "side": t.side, "quantity": qty_out(t.quantity), "price": price_out(t.price),
        "fees": money(t.fees), "day": t.day.isoformat(), "note": t.note,
        "amount": money(gross + t.fees if t.side == "buy" else gross - t.fees),
    }


def summarize(items: Sequence[LongTermPosition], by_pos: dict[int, list[LongTermTrade]],
              account_flows: Sequence[CapitalFlow], quotes: dict[str, Quote], today: date) -> dict:
    """Everything the Capital Tracking screen shows, worked out from the stored records."""
    put_in_total = ledger.net_flows((f.kind, f.amount) for f in account_flows)
    holdings: list[Holding] = []
    valued: list[Valued] = []
    open_rows: list[dict] = []
    closed_rows: list[dict] = []
    for p in items:
        trades = by_pos.get(p.id, [])
        h = holding(p, trades)
        holdings.append(h)
        base = {
            "id": p.id, "kind": p.kind, "symbol": p.symbol, "option_type": p.option_type,
            "strike": float(p.strike) if p.strike is not None else None,
            "expiration": p.expiration.isoformat() if p.expiration else None,
            "label": contract_label(p.kind, p.symbol, p.option_type, p.strike, p.expiration),
            "note": p.note, "realized": money(h.realized),
            "opened": h.opened.isoformat() if h.opened else None,
            "trades": [trade_out(t, p.kind) for t in trades],
        }
        if h.quantity == 0:
            closed_rows.append({**base, "closed": h.closed.isoformat() if h.closed else None,
                                "invested": money(h.cash_out), "returned": money(h.cash_in)})
            continue
        m = mark(p, quotes, today)
        v = Valued(quantity=h.quantity, cost=h.cost, price=m.price, kind=p.kind)
        valued.append(v)
        days_left = (p.expiration - today).days if p.expiration else None
        open_rows.append({
            **base,
            "quantity": qty_out(h.quantity),
            "average_price": price_out(h.average_price),
            "cost": money(h.cost),
            "price": price_out(m.price),
            "price_source": m.source,
            "underlying_last": price_out(m.underlying),
            "value": money(v.value),
            "gain": money(v.gain) if m.price is not None else None,
            "gain_pct": float(round(v.gain_pct, 4)) if m.price is not None and v.gain_pct is not None else None,
            "days_left": days_left,
            "expired": days_left is not None and days_left < 0,
            "expiring_soon": days_left is not None and 0 <= days_left <= EXPIRY_WARNING_DAYS,
        })
    totals = ledger.capital_totals(put_in_total, holdings, valued)
    for row in open_rows:
        row["pct_of_capital"] = (float(round(Decimal(str(row["value"])) / totals.total * 100, 4))
                                 if totals.total > 0 else None)
    return {
        "totals": {
            "total": money(totals.total), "put_in": money(totals.put_in), "cash": money(totals.cash),
            "positions_value": money(totals.positions_value), "gain": money(totals.gain),
            "gain_pct": float(round(totals.gain_pct, 4)) if totals.gain_pct is not None else None,
            "realized": money(totals.realized), "unrealized": money(totals.unrealized),
            "estimated": totals.estimated,
        },
        "open": open_rows,
        "closed": closed_rows,
        "flows": [flow_out(f) for f in account_flows],
    }


def flow_out(f: CapitalFlow) -> dict:
    return {"id": f.id, "account": f.account, "kind": f.kind, "amount": money(f.amount),
            "day": f.day.isoformat(), "note": f.note}


async def build_summary(db: Session, user_id: int, md: MarketData | None) -> tuple[dict, str | None]:
    items = positions(db, user_id)
    by_pos = trades_by_position(db, user_id)
    held = [p for p in items if holding(p, by_pos.get(p.id, [])).quantity > 0]
    quotes, problem = await fetch_quotes(md, held)
    return summarize(items, by_pos, flows(db, user_id, "long_term"), quotes, today_ny()), problem


# ---------- daily snapshots ----------


def save_snapshot(db: Session, user_id: int, day: date, totals: dict) -> None:
    values = {
        "user_id": user_id, "day": day,
        "total": Decimal(str(totals["total"])), "put_in": Decimal(str(totals["put_in"])),
        "positions_value": Decimal(str(totals["positions_value"])), "cash": Decimal(str(totals["cash"])),
        "estimated": totals["estimated"],
    }
    stmt = insert(CapitalSnapshot).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "day"],
        set_={k: stmt.excluded[k] for k in ("total", "put_in", "positions_value", "cash", "estimated")}
        | {"taken_at": datetime.now(NY)},
    )
    db.execute(stmt)


def snapshots(db: Session, user_id: int) -> list[CapitalSnapshot]:
    return list(db.scalars(select(CapitalSnapshot).where(CapitalSnapshot.user_id == user_id)
                           .order_by(CapitalSnapshot.day)))


def has_records(db: Session, user_id: int) -> bool:
    return (db.scalar(select(LongTermPosition.id).where(LongTermPosition.user_id == user_id).limit(1)) is not None
            or db.scalar(select(CapitalFlow.id).where(CapitalFlow.user_id == user_id,
                                                      CapitalFlow.account == "long_term").limit(1)) is not None)


def delete_position(db: Session, user_id: int, position_id: int) -> bool:
    result = db.execute(delete(LongTermPosition).where(
        LongTermPosition.id == position_id, LongTermPosition.user_id == user_id))
    return result.rowcount > 0

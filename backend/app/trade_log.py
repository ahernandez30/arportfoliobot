"""Account Manager: the log of closed short-term trades, its filters, statistics and CSV export.

Paper and real trades are never added together: every query and total is for one mode.
"""
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import capital, ledger
from app.capital import money, price_out
from app.models import ClosedTrade

PERIODS = ("today", "week", "month", "year", "all", "custom")
REASON_LABELS = {
    "take_profit": "Take profit", "stop_loss": "Stop loss", "signal": "Signal", "manual": "Manual",
    "time": "Time", "expired": "Expired",
}


@dataclass(frozen=True)
class Window:
    start: datetime | None  # inclusive
    end: datetime | None  # exclusive


def period_window(period: str, tz: ZoneInfo, now: datetime, start: date | None = None,
                  end: date | None = None) -> Window:
    """The time span a period filter covers, in the user's own time zone."""
    today = now.astimezone(tz).date()

    def at(d: date) -> datetime:
        return datetime.combine(d, time(0), tzinfo=tz)

    if period == "today":
        return Window(at(today), at(today + timedelta(days=1)))
    if period == "week":
        monday = today - timedelta(days=today.weekday())
        return Window(at(monday), at(monday + timedelta(days=7)))
    if period == "month":
        first = today.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return Window(at(first), at(nxt))
    if period == "year":
        return Window(at(date(today.year, 1, 1)), at(date(today.year + 1, 1, 1)))
    if period == "custom":
        return Window(at(start) if start else None, at(end + timedelta(days=1)) if end else None)
    return Window(None, None)


def query(db: Session, user_id: int, mode: str, window: Window = Window(None, None),
          symbol: str | None = None) -> list[ClosedTrade]:
    stmt = select(ClosedTrade).where(ClosedTrade.user_id == user_id, ClosedTrade.mode == mode)
    if window.start is not None:
        stmt = stmt.where(ClosedTrade.closed_at >= window.start)
    if window.end is not None:
        stmt = stmt.where(ClosedTrade.closed_at < window.end)
    if symbol:
        stmt = stmt.where(ClosedTrade.symbol == symbol)
    return list(db.scalars(stmt.order_by(ClosedTrade.closed_at.desc(), ClosedTrade.id.desc())))


def get(db: Session, user_id: int, trade_id: int) -> ClosedTrade | None:
    return db.scalar(select(ClosedTrade).where(ClosedTrade.id == trade_id, ClosedTrade.user_id == user_id))


def result(t: ClosedTrade) -> tuple[Decimal, Decimal | None]:
    return ledger.trade_result(t.direction, t.kind, t.quantity, t.entry_price, t.exit_price, t.fees)


def editable(t: ClosedTrade) -> bool:
    """Only real trades typed in by hand can be changed. Paper trades are the engine's record."""
    return t.mode == "real" and t.source == "manual"


def trade_out(t: ClosedTrade) -> dict:
    dollars, pct = result(t)
    return {
        "id": t.id, "mode": t.mode, "source": t.source, "editable": editable(t),
        "kind": t.kind, "symbol": t.symbol, "option_type": t.option_type,
        "strike": float(t.strike) if t.strike is not None else None,
        "expiration": t.expiration.isoformat() if t.expiration else None,
        "label": capital.contract_label(t.kind, t.symbol, t.option_type, t.strike, t.expiration),
        "direction": t.direction, "quantity": float(t.quantity),
        "entry_price": price_out(t.entry_price), "exit_price": price_out(t.exit_price), "fees": money(t.fees),
        "opened_at": t.opened_at.isoformat(), "closed_at": t.closed_at.isoformat(),
        "close_reason": t.close_reason, "notes": t.notes,
        "result": money(dollars), "result_pct": float(round(pct, 4)) if pct is not None else None,
    }


def stats_out(trades: list[ClosedTrade]) -> dict:
    s = ledger.trade_stats((result(t)[0], t.close_reason) for t in trades)
    return {
        "count": s.count, "wins": s.wins, "losses": s.losses, "total": money(s.total),
        "win_rate": float(round(s.win_rate, 4)) if s.win_rate is not None else None,
        "average_win": money(s.average_win), "average_loss": money(s.average_loss),
        "by_reason": {k: {"count": v["count"], "total": money(v["total"])} for k, v in s.by_reason.items()},
    }


def real_account_value(db: Session, user_id: int) -> Decimal:
    """Money put into the short-term account plus every real trade's result."""
    return capital.put_in(db, user_id, "short_term") + sum((result(t)[0] for t in query(db, user_id, "real")),
                                                           ledger.ZERO)


def to_utc(local: datetime, tz: ZoneInfo) -> datetime:
    """A time typed in the user's time zone (no zone attached) as a UTC time."""
    if local.tzinfo is None:
        local = local.replace(tzinfo=tz)
    return local.astimezone(timezone.utc)


CSV_COLUMNS = ["Closed", "Opened", "Mode", "Placed by", "Contract", "Type", "Direction", "Quantity", "Entry",
               "Exit", "Fees", "Result $", "Result %", "How it closed", "Notes"]


def csv_text(trades: list[ClosedTrade], tz: ZoneInfo) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for t in trades:
        o = trade_out(t)
        w.writerow([
            t.closed_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"), t.opened_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
            t.mode, t.source, o["label"], t.kind, t.direction,
            ledger.fmt_qty(t.quantity), ledger.fmt_qty(t.entry_price), ledger.fmt_qty(t.exit_price),
            f"{o['fees']:.2f}", f"{o['result']:.2f}",
            "" if o["result_pct"] is None else f"{o['result_pct']:.2f}",
            REASON_LABELS[t.close_reason], _safe_cell(t.notes),
        ])
    return buf.getvalue()


def _safe_cell(text: str) -> str:
    """Stops a spreadsheet from running a note as a formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text

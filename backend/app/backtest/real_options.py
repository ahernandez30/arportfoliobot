"""Real option prices for backtests (Backtest plan, step 3).

`StoredOptionHistory` answers the backtest's questions (expirations, strikes, bid/ask at a moment)
from what the user has downloaded from Databento under their own account: the option chain listed
each day (schema "definition", kept as one row per expiration with its strikes) and the best bid and
ask across all exchanges, sampled each minute (OPRA "cbbo-1m"), for the minutes before each moment a
trade opens or closes. What is not stored yet is written down as missing, so a download job can
fetch exactly that and nothing more.

Databento's OPRA history starts on 28 March 2023: earlier trades have no real prices.
"""
import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.backtest.option_history import OptionHistory
from app.models import OptionChain, OptionChainDay, OptionQuote, OptionQuoteWindow

SOURCE = "Databento OPRA"
DATASET = "OPRA.PILLAR"
OPRA_START = date(2023, 3, 28)
LOOKBACK = 15 * 60  # seconds of quotes fetched before each moment; the last one at or before it is used


def occ(underlying: str, expiration: date, option_type: str, strike: Decimal) -> str:
    """The OCC option symbol Databento uses: root padded to 6, YYMMDD, C/P, strike x 1000 in 8 digits."""
    return (f"{underlying:<6}{expiration:%y%m%d}{'C' if option_type == 'call' else 'P'}"
            f"{int(round(Decimal(strike) * 1000)):08d}")


@dataclass
class Missing:
    """What a backtest asked for that is not stored: days without a chain, and per moment (Unix
    seconds) the contracts without quotes."""

    days: set[date] = field(default_factory=set)
    moments: dict[int, set[str]] = field(default_factory=lambda: defaultdict(set))

    def empty(self) -> bool:
        return not self.days and not self.moments

    def as_dict(self) -> dict:
        return {"days": sorted(d.isoformat() for d in self.days),
                "moments": {str(t): sorted(c) for t, c in sorted(self.moments.items())}}

    @staticmethod
    def from_dict(d: dict) -> "Missing":
        m = Missing({date.fromisoformat(x) for x in d.get("days", [])})
        for t, cs in d.get("moments", {}).items():
            m.moments[int(t)] = set(cs)
        return m


class StoredOptionHistory(OptionHistory):
    """Real prices from the user's stored downloads. With a `fallback` (the estimate), questions it
    cannot answer yet are answered by the fallback so a planning run can carry on and find every
    trade's contracts; without one, they get no price."""

    source = "real (Databento OPRA)"

    def __init__(self, db: Session, user_id: int, underlying: str, fallback: OptionHistory | None = None):
        self.underlying = underlying
        self.fallback = fallback
        self.missing = Missing()
        self.chains: dict[date, dict[date, tuple[frozenset, frozenset]]] = defaultdict(dict)
        self.days = set(db.scalars(select(OptionChainDay.day).where(OptionChainDay.user_id == user_id,
                                                                    OptionChainDay.underlying == underlying)))
        self.listed: dict[tuple[date, str], set[Decimal]] = defaultdict(set)  # any day's chain
        for row in db.execute(select(OptionChain.day, OptionChain.expiration, OptionChain.call_strikes,
                                     OptionChain.put_strikes)
                              .where(OptionChain.user_id == user_id, OptionChain.underlying == underlying)):
            calls, puts = frozenset(Decimal(k) for k in row[2]), frozenset(Decimal(k) for k in row[3])
            self.chains[row[0]][row[1]] = (calls, puts)
            self.listed[(row[1], "call")] |= calls
            self.listed[(row[1], "put")] |= puts
        prefix = f"{underlying:<6}"
        self.windows: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for c, s, e in db.execute(select(OptionQuoteWindow.contract, OptionQuoteWindow.start, OptionQuoteWindow.end)
                                  .where(OptionQuoteWindow.user_id == user_id,
                                         OptionQuoteWindow.contract.startswith(prefix))):
            self.windows[c].append((s, e))
        self.quotes: dict[str, tuple[list[int], list]] = {}
        rows = db.execute(select(OptionQuote.contract, OptionQuote.time, OptionQuote.bid, OptionQuote.ask)
                          .where(OptionQuote.user_id == user_id, OptionQuote.contract.startswith(prefix))
                          .order_by(OptionQuote.contract, OptionQuote.time))
        for c, t, b, a in rows:
            times, vals = self.quotes.setdefault(c, ([], []))
            times.append(t)
            vals.append((b, a))

    # ---- the chain ----

    def _chain(self, at: datetime) -> dict | None:
        d = at.date()
        if d in self.days:
            return self.chains.get(d, {})
        if d >= OPRA_START:
            self.missing.days.add(d)
        return None

    def expirations(self, symbol: str, at: datetime) -> list[date]:
        chain = self._chain(at)
        if chain is None:
            return self.fallback.expirations(symbol, at) if self.fallback and at.date() >= OPRA_START else []
        d = at.date()
        return sorted(e for e in chain if e > d or (e == d and at.hour < 16))

    def strikes(self, symbol: str, at: datetime, underlying: float) -> list[Decimal]:
        chain = self._chain(at)
        if chain is None:
            return self.fallback.strikes(symbol, at, underlying) if self.fallback and at.date() >= OPRA_START else []
        return sorted({k for calls, puts in chain.values() for k in calls | puts})

    # ---- quotes ----

    def quote(self, symbol: str, option_type: str, strike: Decimal, expiration: date, at: datetime,
              underlying: float) -> tuple[Decimal | None, Decimal | None]:
        if at.date() < OPRA_START:
            return None, None
        strike = Decimal(strike)
        d = at.date()
        if d in self.days:
            listed = self.chains.get(d, {}).get(expiration)
            if listed is None or strike not in listed[0 if option_type == "call" else 1]:
                return None, None  # not a listed contract that day
        elif strike not in self.listed.get((expiration, option_type), ()):
            # Neither that day's chain nor any other stored day lists it: it was picked from an
            # estimated chain, so its price is not worth fetching until the chain is known.
            return self.fallback.quote(symbol, option_type, strike, expiration, at, underlying) if self.fallback else (None, None)
        c = occ(self.underlying, expiration, option_type, strike)
        t = int(at.timestamp())
        if any(s <= t <= e for s, e in self.windows.get(c, ())):
            return self._latest(c, t)
        self.missing.moments[t].add(c)
        return self.fallback.quote(symbol, option_type, strike, expiration, at, underlying) if self.fallback else (None, None)

    def _latest(self, c: str, t: int) -> tuple[Decimal | None, Decimal | None]:
        times, vals = self.quotes.get(c, ([], []))
        i = bisect.bisect_right(times, t) - 1
        if i < 0 or times[i] < t - LOOKBACK:
            return None, None
        b, a = vals[i]
        return (Decimal(b) if b is not None else None), (Decimal(a) if a is not None else None)


# ---------- saving downloads ----------


def save_chain(db: Session, user_id: int, underlying: str, day: date, rows: list[tuple[str, date, str, Decimal]]) -> int:
    """rows: (root, expiration, call/put, strike) for every contract listed that day; only the
    standard root is kept (adjusted contracts after splits have other roots)."""
    by_exp: dict[date, tuple[set, set]] = defaultdict(lambda: (set(), set()))
    for root, exp, kind, strike in rows:
        if root == underlying:
            by_exp[exp][0 if kind == "call" else 1].add(Decimal(strike))
    for exp, (calls, puts) in by_exp.items():
        stmt = insert(OptionChain).values(user_id=user_id, underlying=underlying, day=day, expiration=exp,
                                          call_strikes=sorted(calls), put_strikes=sorted(puts))
        db.execute(stmt.on_conflict_do_update(index_elements=["user_id", "underlying", "day", "expiration"],
                                              set_={"call_strikes": stmt.excluded.call_strikes,
                                                    "put_strikes": stmt.excluded.put_strikes}))
    stmt = insert(OptionChainDay).values(user_id=user_id, underlying=underlying, day=day, source=SOURCE)
    db.execute(stmt.on_conflict_do_nothing())
    return len(by_exp)


def save_quotes(db: Session, user_id: int, contracts: list[str], start: int, end: int,
                rows: list[tuple[str, int, Decimal | None, Decimal | None]]) -> int:
    """Saves minute samples and marks [start, end] as fetched for each contract asked for, so a
    contract with no sample in it reads as having no quote then."""
    if rows:
        values = [{"user_id": user_id, "contract": c, "time": t, "bid": b, "ask": a} for c, t, b, a in rows]
        for i in range(0, len(values), 5000):
            stmt = insert(OptionQuote).values(values[i:i + 5000])
            db.execute(stmt.on_conflict_do_update(index_elements=["user_id", "contract", "time"],
                                                  set_={"bid": stmt.excluded.bid, "ask": stmt.excluded.ask}))
    db.add_all(OptionQuoteWindow(user_id=user_id, contract=c, start=start, end=end, source=SOURCE) for c in contracts)
    return len(rows)


def parse_occ(symbol: str) -> tuple[str, date, str, Decimal] | None:
    s = symbol.rstrip()
    if len(s) < 15:
        return None
    root, rest = s[:-15].strip(), s[-15:]
    try:
        exp = date(2000 + int(rest[0:2]), int(rest[2:4]), int(rest[4:6]))
        strike = Decimal(int(rest[7:])) / 1000
    except ValueError:
        return None
    kind = {"C": "call", "P": "put"}.get(rest[6])
    return (root, exp, kind, strike) if kind else None


# ---------- Databento ----------


class DatabentoClient:
    """The few Databento calls the download job makes. Kept small so tests can stand in for it."""

    def __init__(self, key: str):
        import databento

        self._c = databento.Historical(key)

    def chain_cost(self, underlying: str, day: date) -> float:
        return float(self._c.metadata.get_cost(dataset=DATASET, schema="definition", symbols=[f"{underlying}.OPT"],
                                               stype_in="parent", start=day.isoformat(),
                                               end=(day + timedelta(days=1)).isoformat()))

    def minute_cost_all(self, underlying: str, at: int) -> float:
        """Cost of one minute of quotes for every contract of the underlying (for estimates)."""
        return float(self._c.metadata.get_cost(dataset=DATASET, schema="cbbo-1m", symbols=[f"{underlying}.OPT"],
                                               stype_in="parent", start=_iso(at - 60), end=_iso(at)))

    def quotes_cost(self, contracts: list[str], start: int, end: int) -> float:
        return float(self._c.metadata.get_cost(dataset=DATASET, schema="cbbo-1m", symbols=contracts,
                                               stype_in="raw_symbol", start=_iso(start), end=_iso(end)))

    def chain(self, underlying: str, day: date) -> list[tuple[str, date, str, Decimal]]:
        df = self._c.timeseries.get_range(dataset=DATASET, schema="definition", symbols=[f"{underlying}.OPT"],
                                          stype_in="parent", start=day.isoformat(),
                                          end=(day + timedelta(days=1)).isoformat()).to_df()
        out = []
        for sym in df["raw_symbol"] if len(df) else []:
            p = parse_occ(str(sym))
            if p:
                out.append(p)
        return out

    def quotes(self, contracts: list[str], start: int, end: int) -> list[tuple[str, int, Decimal | None, Decimal | None]]:
        df = self._c.timeseries.get_range(dataset=DATASET, schema="cbbo-1m", symbols=contracts, stype_in="raw_symbol",
                                          start=_iso(start), end=_iso(end)).to_df()
        if not len(df):
            return []
        out = []
        for ts, sym, b, a in zip(df.index, df["symbol"], df["bid_px_00"], df["ask_px_00"]):
            out.append((str(sym), int(ts.timestamp()), _px(b), _px(a)))
        return out


def _iso(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _px(x) -> Decimal | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if f != f or f <= 0 else Decimal(str(round(f, 4)))

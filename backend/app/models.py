"""Database tables: accounts, login sessions, invites, settings, stored keys, layouts,
long-term capital and the short-term trade log.

Every table holding a user's own data has a user_id column, and every query for
it filters on the logged-in user (see the tests in test_isolation.py).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ROLES = ("admin", "user")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'user')", name="users_role_check"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Always stored trimmed and lower-case.
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Two-step login: the authenticator secret is encrypted with the master key.
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Last 30-second step a code was accepted for, so a code cannot be used twice.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginSession(Base):
    """A signed-in browser. The cookie holds a random token; only its SHA-256 is stored."""

    __tablename__ = "sessions"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # True between a correct password and a correct two-step code. Such a session
    # can only finish the two-step check; it cannot see anything.
    mfa_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    ip: Mapped[str | None] = mapped_column(INET)


class Invite(Base):
    """A one-time link: sign up for a new account, or (when for_user_id is set) choose a
    new password for an existing one. Only the SHA-256 of the link's token is stored."""

    __tablename__ = "invites"
    __table_args__ = (CheckConstraint("role IN ('admin', 'user')", name="invites_role_check"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    # If set, the new account must use this email; if empty, the invitee types one.
    email: Mapped[str | None] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(16), default="user")
    note: Mapped[str] = mapped_column(String(120), default="")
    for_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # NULL when created from the server command line rather than by an admin.
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginAttempt(Base):
    """Failed sign-in steps, counted for rate limiting. Old rows are pruned."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("login_attempts_email_at", "email", "at"),
        Index("login_attempts_ip_at", "ip", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    ip: Mapped[str | None] = mapped_column(INET)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSettings(Base):
    """All of a user's preferences, validated by app.user_settings.SettingsModel."""

    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApiKey(Base):
    """A broker or market-data key, encrypted with the master key. Never sent back to a browser."""

    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="api_keys_user_provider"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    secret_enc: Mapped[bytes] = mapped_column(LargeBinary)
    last4: Mapped[str] = mapped_column(String(4))
    # Not secret (for example a broker account number); shown in full.
    account_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    """Permanent record of security-relevant events. Never holds a secret."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    # Who did it (NULL for the server command line or an anonymous visitor).
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Whose account it affected.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str | None] = mapped_column(INET)



class DashboardLayout(Base):
    """A user's dashboard tiles and where they sit (validated by app.layouts)."""

    __tablename__ = "dashboard_layouts"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChartLayout(Base):
    """A user's four charts on the Charts tab: symbol and timeframe of each."""

    __tablename__ = "chart_layouts"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MarketWatch(Base):
    """Symbols a user currently has on screen. The web server refreshes seen_at while a
    browser shows them; the worker streams live prices for recent rows only."""

    __tablename__ = "market_watch"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class MarketFeedStatus(Base):
    """The worker's live price feed for each user: live, delayed, reconnecting, or a problem."""

    __tablename__ = "market_feed_status"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Last time a price actually arrived (for spotting a stale feed, plan section 10).
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------- Stage 3: long-term capital and the short-term trade log ----------

ACCOUNTS = ("long_term", "short_term")


class CapitalFlow(Base):
    """Money put into or taken out of an account, kept apart from gains (plan section 6)."""

    __tablename__ = "capital_flows"
    __table_args__ = (
        CheckConstraint("account IN ('long_term', 'short_term')", name="capital_flows_account_check"),
        CheckConstraint("kind IN ('deposit', 'withdrawal')", name="capital_flows_kind_check"),
        CheckConstraint("amount > 0", name="capital_flows_amount_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    day: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LongTermPosition(Base):
    """One stock or one option contract held long term. Its buys and sells are in
    long_term_trades; quantity, average cost and realized gain are worked out from them."""

    __tablename__ = "long_term_positions"
    __table_args__ = (
        CheckConstraint("kind IN ('option', 'stock')", name="lt_positions_kind_check"),
        CheckConstraint(
            "(kind = 'stock' AND option_type IS NULL AND strike IS NULL AND expiration IS NULL)"
            " OR (kind = 'option' AND option_type IN ('call', 'put') AND strike > 0 AND expiration IS NOT NULL)",
            name="lt_positions_contract_check",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(16))
    option_type: Mapped[str | None] = mapped_column(String(4))
    strike: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    expiration: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LongTermTrade(Base):
    """A buy or sell of a long-term position. Price is as quoted: per share for stock, per
    share of the contract for options (5.20 means $520 for one contract)."""

    __tablename__ = "long_term_trades"
    __table_args__ = (
        CheckConstraint("side IN ('buy', 'sell')", name="lt_trades_side_check"),
        CheckConstraint("quantity > 0", name="lt_trades_quantity_check"),
        CheckConstraint("price >= 0", name="lt_trades_price_check"),
        CheckConstraint("fees >= 0", name="lt_trades_fees_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("long_term_positions.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    fees: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    day: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapitalSnapshot(Base):
    """Long-term capital at the end of a market day, for the capital-over-time chart."""

    __tablename__ = "capital_snapshots"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    total: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    put_in: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    positions_value: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    cash: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    # True when some position had no price and was counted at its cost.
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClosedTrade(Base):
    """A finished short-term trade (Account Manager). Paper trades are written here by the
    paper engine (Stage 4 on); real ones are typed in by hand until a broker is connected."""

    __tablename__ = "closed_trades"
    __table_args__ = (
        CheckConstraint("mode IN ('paper', 'real')", name="closed_trades_mode_check"),
        CheckConstraint("kind IN ('option', 'stock')", name="closed_trades_kind_check"),
        CheckConstraint("direction IN ('long', 'short')", name="closed_trades_direction_check"),
        CheckConstraint("quantity > 0", name="closed_trades_quantity_check"),
        CheckConstraint("entry_price >= 0 AND exit_price >= 0 AND fees >= 0", name="closed_trades_prices_check"),
        CheckConstraint(
            "close_reason IN ('take_profit', 'stop_loss', 'signal', 'manual', 'time', 'expired')",
            name="closed_trades_reason_check",
        ),
        CheckConstraint(
            "(kind = 'stock' AND option_type IS NULL AND strike IS NULL AND expiration IS NULL)"
            " OR (kind = 'option' AND option_type IN ('call', 'put') AND strike > 0 AND expiration IS NOT NULL)",
            name="closed_trades_contract_check",
        ),
        Index("closed_trades_user_mode_closed", "user_id", "mode", "closed_at"),
        CheckConstraint("structure IN ('single', 'credit_spread', 'debit_spread')", name="closed_trades_structure_check"),
        CheckConstraint("(structure = 'single' AND strike2 IS NULL) OR (structure <> 'single' AND strike2 > 0 AND strike2 <> strike)",
                        name="closed_trades_legs_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(8))
    # "manual" for trades typed in here; otherwise what placed it (e.g. a strategy name).
    source: Mapped[str] = mapped_column(String(64), default="manual")
    kind: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(16))
    option_type: Mapped[str | None] = mapped_column(String(4))
    strike: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    expiration: Mapped[date | None] = mapped_column(Date)
    direction: Mapped[str] = mapped_column(String(8), default="long")
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    exit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    fees: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # For paper trades: the paper position it came from.
    paper_position_id: Mapped[int | None] = mapped_column(ForeignKey("paper_positions.id", ondelete="SET NULL"))
    # Stage 6: spreads (strike = leg sold, strike2 = leg bought; prices are net per share).
    structure: Mapped[str] = mapped_column(String(16), default="single")
    strike2: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    # Dollars that could be lost at entry, when that is not simply the price paid (a credit spread).
    risk: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    # Paper account it ran in: "main" or a sub-account per structure.
    account_name: Mapped[str | None] = mapped_column(String(32))
    # The stock's price at entry and exit, so the option's result can be set against the stock's move.
    underlying_entry: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    underlying_exit: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))


# ---------- Stage 4: paper trading ----------


class PaperAccount(Base):
    """A paper account. Each user has a "main" one; Stage 6 adds sub-accounts so two trade
    structures can run on the same signals without mixing results (plan section 8)."""

    __tablename__ = "paper_accounts"
    __table_args__ = (UniqueConstraint("user_id", "name", name="paper_accounts_user_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32), default="main")
    cash: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperOrder(Base):
    """A paper order for one option contract. Opening orders buy; closing orders sell all or
    part of a position. A working order fills when the live quote reaches its limit."""

    __tablename__ = "paper_orders"
    __table_args__ = (
        CheckConstraint("side IN ('buy', 'sell')", name="paper_orders_side_check"),
        CheckConstraint("intent IN ('open', 'close')", name="paper_orders_intent_check"),
        CheckConstraint("status IN ('working', 'filled', 'cancelled', 'rejected')", name="paper_orders_status_check"),
        CheckConstraint("quantity > 0", name="paper_orders_quantity_check"),
        CheckConstraint("option_type IN ('call', 'put')", name="paper_orders_type_check"),
        Index("paper_orders_working", "status", postgresql_where="status = 'working'"),
        CheckConstraint("structure IN ('single', 'credit_spread', 'debit_spread')", name="paper_orders_structure_check"),
        CheckConstraint("(structure = 'single' AND strike2 IS NULL) OR (structure <> 'single' AND strike2 > 0 AND strike2 <> strike)",
                        name="paper_orders_legs_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    # "manual", or the strategy that placed it (Stage 6).
    source: Mapped[str] = mapped_column(String(64), default="manual")
    side: Mapped[str] = mapped_column(String(4))
    intent: Mapped[str] = mapped_column(String(8))
    position_id: Mapped[int | None] = mapped_column(ForeignKey("paper_positions.id", ondelete="SET NULL"))
    symbol: Mapped[str] = mapped_column(String(16))
    option_type: Mapped[str] = mapped_column(String(4))
    strike: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    expiration: Mapped[date] = mapped_column(Date)
    occ_symbol: Mapped[str] = mapped_column(String(32))
    # "single" (one option), or a two-leg spread opened and closed together (plan 7.6). For a spread,
    # strike is the leg sold and strike2 the leg bought; prices are the net price of the pair.
    structure: Mapped[str] = mapped_column(String(16), default="single")
    strike2: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    occ_symbol2: Mapped[str | None] = mapped_column(String(32))
    quantity: Mapped[int] = mapped_column(BigInteger)
    # None means "at the market": fill at the current quote.
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    # For opening orders: the exits given to the position once filled (percent of the fill price).
    take_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    stop_loss_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    # Why a closing order was sent: take_profit, stop_loss, manual, ...
    close_reason: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), default="working")
    status_detail: Mapped[str] = mapped_column(Text, default="")
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Stage 6: strategy + symbol + timeframe + candle time, so a signal never orders twice.
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)


class PaperPosition(Base):
    """An open (or finished) paper option position."""

    __tablename__ = "paper_positions"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed', 'voided')", name="paper_positions_status_check"),
        CheckConstraint("quantity >= 0", name="paper_positions_quantity_check"),
        CheckConstraint("option_type IN ('call', 'put')", name="paper_positions_type_check"),
        CheckConstraint("structure IN ('single', 'credit_spread', 'debit_spread')", name="paper_positions_structure_check"),
        CheckConstraint("(structure = 'single' AND strike2 IS NULL) OR (structure <> 'single' AND strike2 > 0 AND strike2 <> strike)",
                        name="paper_positions_legs_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    symbol: Mapped[str] = mapped_column(String(16))
    option_type: Mapped[str] = mapped_column(String(4))
    strike: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    expiration: Mapped[date] = mapped_column(Date)
    occ_symbol: Mapped[str] = mapped_column(String(32))
    # "single" (one option), or a two-leg spread opened and closed together (plan 7.6). For a spread,
    # strike is the leg sold and strike2 the leg bought; prices are the net price of the pair.
    structure: Mapped[str] = mapped_column(String(16), default="single")
    strike2: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    occ_symbol2: Mapped[str | None] = mapped_column(String(32))
    # Contracts (or spreads) still held; goes down with partial closes.
    quantity: Mapped[int] = mapped_column(BigInteger)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    take_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    stop_loss_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    status: Mapped[str] = mapped_column(String(8), default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperEvent(Base):
    """The permanent log of every paper order, fill, close and account change (plan section 8)."""

    __tablename__ = "paper_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("paper_accounts.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    event: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(64), default="manual")
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    position_id: Mapped[int | None] = mapped_column(BigInteger)
    detail: Mapped[str] = mapped_column(Text, default="")


class TradingControls(Base):
    """Per-user switches that must take effect at once and survive a restart (plan section 10)."""

    __tablename__ = "trading_controls"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # "Stop all trading": open orders cancelled, no new orders until resumed.
    halted: Mapped[bool] = mapped_column(Boolean, default=False)
    # "Pause automatic trading": the strategy places nothing new (Stage 6).
    auto_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # Why automatic trading cannot act right now (stale prices, provider unreachable), or "".
    auto_problem: Mapped[str] = mapped_column(Text, default="")
    auto_problem_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last time the worker looked at this user's automatic trading.
    auto_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- Stage 5: strategies ----------


class StrategyPreset(Base):
    """Pegged strategy settings: one set per user, strategy, symbol and timeframe
    (Rafa's choice for plan open decision 3)."""

    __tablename__ = "strategy_presets"
    __table_args__ = (UniqueConstraint("user_id", "strategy", "symbol", "timeframe", name="strategy_presets_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(16))
    timeframe: Mapped[str] = mapped_column(String(4))
    inputs: Mapped[dict] = mapped_column(JSONB)
    pegged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # "What to trade on a signal" for this symbol and timeframe (validated by app.auto_plan).
    trade: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Place paper trades from these signals; only signals on candles closing after auto_since count.
    auto: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MasterChartState(Base):
    """What a user last had on Master Chart: strategy, symbol, timeframe and working inputs."""

    __tablename__ = "master_chart_state"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class ParityCheck(Base):
    """A comparison of the engine's signals with signal dates exported from TradingView (plan 7.5)."""

    __tablename__ = "parity_checks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(16))
    timeframe: Mapped[str] = mapped_column(String(4))
    filename: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[dict] = mapped_column(JSONB)
    detail: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    signed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sign_off_note: Mapped[str] = mapped_column(Text, default="")


# ---------- Stage 6: automatic paper trading ----------

STRATEGY_TRADE_STATUSES = ("waiting", "open", "floating", "closed", "refused", "missed")


class StrategyTrade(Base):
    """One signal turned into one paper trade for one structure (plan 7.6 and section 10).

    waiting: the signal's candle closed; the order goes in when the options market is open.
    open: the option position is held; it closes when the strategy exits on the stock chart.
    floating: the strategy stopped following it (its "leave floating" rule); closed before expiration.
    closed / refused / missed: finished, with the reason in detail."""

    __tablename__ = "strategy_trades"
    __table_args__ = (
        CheckConstraint("direction IN (1, -1)", name="strategy_trades_direction_check"),
        CheckConstraint("structure IN ('directional', 'credit_spread', 'debit_spread')",
                        name="strategy_trades_structure_check"),
        CheckConstraint("status IN ('waiting', 'open', 'floating', 'closed', 'refused', 'missed')",
                        name="strategy_trades_status_check"),
        UniqueConstraint("user_id", "strategy", "symbol", "timeframe", "signal_time", "structure",
                         name="strategy_trades_once"),
        Index("strategy_trades_user_created", "user_id", "created_at"),
        Index("strategy_trades_active", "status", postgresql_where="status IN ('waiting', 'open', 'floating')"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    preset_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_presets.id", ondelete="SET NULL"))
    strategy: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(16))
    timeframe: Mapped[str] = mapped_column(String(4))
    structure: Mapped[str] = mapped_column(String(16))
    account_name: Mapped[str] = mapped_column(String(32))
    # Start of the signal's candle (Unix seconds), as the engine reports it.
    signal_time: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[int] = mapped_column(SmallInteger)
    candle_type: Mapped[str] = mapped_column(String(16))
    signal_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    status: Mapped[str] = mapped_column(String(12))
    detail: Mapped[str] = mapped_column(Text, default="")
    # How the contract was chosen: strike distance, hold days, expiration, size, payout ratio.
    plan: Mapped[dict] = mapped_column(JSONB, default=dict)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("paper_positions.id", ondelete="SET NULL"))
    under_entry: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    # The strategy's target and stop on the stock price, when its mode has them.
    under_target: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    under_stop: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    under_exit: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    exit_reason: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------- Stage 7: backtests ----------


class BacktestRun(Base):
    """A saved backtest: its setup (strategy, symbol, timeframe, inputs, dates, money), a short summary
    for the list, and the full result for reopening it."""

    __tablename__ = "backtest_runs"
    __table_args__ = (Index("backtest_runs_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    strategy: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(16))
    timeframe: Mapped[str] = mapped_column(String(4))
    setup: Mapped[dict] = mapped_column(JSONB)
    summary: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

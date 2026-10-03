"""Database tables for accounts, login sessions, invites, settings and stored keys.

Every table holding a user's own data has a user_id column, and every query for
it filters on the logged-in user (see the tests in test_isolation.py).
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
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

"""accounts: users, sessions, invites, login attempts, settings, api keys, audit log

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _ts(name: str, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("totp_secret_enc", sa.LargeBinary),
        sa.Column("totp_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("totp_last_step", sa.BigInteger),
        _ts("created_at", nullable=False, server_default=sa.func.now()),
        _ts("password_changed_at", nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('admin', 'user')", name="users_role_check"),
    )

    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.LargeBinary(32), primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mfa_pending", sa.Boolean, nullable=False, server_default=sa.false()),
        _ts("created_at", nullable=False, server_default=sa.func.now()),
        _ts("last_seen_at", nullable=False, server_default=sa.func.now()),
        _ts("expires_at", nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=False, server_default=""),
        sa.Column("ip", postgresql.INET),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "invites",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column("email", sa.String(254)),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("note", sa.String(120), nullable=False, server_default=""),
        sa.Column("for_user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("created_by", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts("created_at", nullable=False, server_default=sa.func.now()),
        _ts("expires_at", nullable=False),
        _ts("used_at"),
        sa.Column("used_by", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts("revoked_at"),
        sa.CheckConstraint("role IN ('admin', 'user')", name="invites_role_check"),
    )

    op.create_table(
        "login_attempts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("ip", postgresql.INET),
        _ts("at", nullable=False, server_default=sa.func.now()),
    )
    op.create_index("login_attempts_email_at", "login_attempts", ["email", "at"])
    op.create_index("login_attempts_ip_at", "login_attempts", ["ip", "at"])

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("updated_at", nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("secret_enc", sa.LargeBinary, nullable=False),
        sa.Column("last4", sa.String(4), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False, server_default=""),
        _ts("created_at", nullable=False, server_default=sa.func.now()),
        _ts("updated_at", nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider", name="api_keys_user_provider"),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _ts("at", nullable=False, server_default=sa.func.now()),
        sa.Column("actor_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        sa.Column("ip", postgresql.INET),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])


def downgrade() -> None:
    for table in ("audit_log", "api_keys", "user_settings", "login_attempts", "invites", "sessions", "users"):
        op.drop_table(table)

"""history data: stored candles, option chains and option quotes, data download jobs

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Candles imported or downloaded by a user (their licence), older than what the live provider keeps.
    op.create_table(
        "candle_history",
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("timeframe", sa.String(4), primary_key=True),
        sa.Column("time", sa.BigInteger, primary_key=True),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.Float, nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
    )
    # The option chain listed on a day (from Databento "definition"): one row per expiration with its
    # call and put strikes, and which days are stored.
    op.create_table(
        "option_chain_days",
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("underlying", sa.String(16), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("source", sa.String(40), nullable=False),
    )
    op.create_table(
        "option_chains",
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("underlying", sa.String(16), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("expiration", sa.Date, primary_key=True),
        sa.Column("call_strikes", postgresql.ARRAY(sa.Numeric(12, 3)), nullable=False),
        sa.Column("put_strikes", postgresql.ARRAY(sa.Numeric(12, 3)), nullable=False),
    )
    # Minute bid/ask samples (OPRA consolidated best bid and offer), and the stretches of time already
    # fetched for a contract, so a missing sample inside one means "no quote", not "not downloaded".
    op.create_table(
        "option_quotes",
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("contract", sa.String(32), primary_key=True),
        sa.Column("time", sa.BigInteger, primary_key=True),
        sa.Column("bid", sa.Numeric(12, 4)),
        sa.Column("ask", sa.Numeric(12, 4)),
    )
    op.create_table(
        "option_quote_windows",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract", sa.String(32), nullable=False),
        sa.Column("start", sa.BigInteger, nullable=False),
        sa.Column("end", sa.BigInteger, nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
    )
    op.create_index("option_quote_windows_lookup", "option_quote_windows", ["user_id", "contract", "start"])
    # Downloads of real option prices for a backtest: estimated, confirmed by the user, then run by the worker.
    op.create_table(
        "data_jobs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request", postgresql.JSONB, nullable=False),
        sa.Column("plan", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("estimate_usd", sa.Numeric(12, 4)),
        sa.Column("spent_usd", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("progress", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('estimating', 'confirm', 'queued', 'running', 'done', 'failed', 'cancelled')",
                           name="data_jobs_status_check"),
    )
    op.create_index("data_jobs_user_created", "data_jobs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("data_jobs")
    op.drop_table("option_quote_windows")
    op.drop_table("option_quotes")
    op.drop_table("option_chains")
    op.drop_table("option_chain_days")
    op.drop_table("candle_history")

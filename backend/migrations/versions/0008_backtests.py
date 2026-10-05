"""backtests: saved backtest runs

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("setup", postgresql.JSONB, nullable=False),
        sa.Column("summary", postgresql.JSONB, nullable=False),
        sa.Column("result", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("backtest_runs_user_created", "backtest_runs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("backtest_runs")

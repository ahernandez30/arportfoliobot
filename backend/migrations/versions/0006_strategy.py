"""strategies: pegged settings per symbol and timeframe, Master Chart state, parity checks

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _user_fk(**kw) -> sa.Column:
    return sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, **kw)


def upgrade() -> None:
    op.create_table(
        "strategy_presets",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(index=True),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("inputs", postgresql.JSONB, nullable=False),
        sa.Column("pegged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "strategy", "symbol", "timeframe", name="strategy_presets_key"),
    )
    op.create_table(
        "master_chart_state",
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "parity_checks",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(index=True),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False, server_default=""),
        sa.Column("summary", postgresql.JSONB, nullable=False),
        sa.Column("detail", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("signed_off_at", sa.DateTime(timezone=True)),
        sa.Column("sign_off_note", sa.Text, nullable=False, server_default=""),
    )


def downgrade() -> None:
    for table in ("parity_checks", "master_chart_state", "strategy_presets"):
        op.drop_table(table)

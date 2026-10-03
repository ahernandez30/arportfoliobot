"""market data: dashboard and chart layouts, watched symbols, feed status

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _user_fk(**kw) -> sa.Column:
    return sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), **kw)


def upgrade() -> None:
    for table in ("dashboard_layouts", "chart_layouts"):
        op.create_table(
            table,
            _user_fk(primary_key=True),
            sa.Column("data", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    op.create_table(
        "market_watch",
        _user_fk(primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_market_watch_seen_at", "market_watch", ["seen_at"])
    op.create_table(
        "market_feed_status",
        _user_fk(primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    for table in ("market_feed_status", "market_watch", "chart_layouts", "dashboard_layouts"):
        op.drop_table(table)

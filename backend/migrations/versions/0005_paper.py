"""paper trading: accounts, orders, positions, the permanent event log, trading switches

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _user_fk(**kw) -> sa.Column:
    return sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, **kw)


def _account_fk() -> sa.Column:
    return sa.Column("account_id", sa.BigInteger, sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"),
                     nullable=False, index=True)


def _contract() -> list[sa.Column]:
    return [
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("option_type", sa.String(4), nullable=False),
        sa.Column("strike", sa.Numeric(12, 3), nullable=False),
        sa.Column("expiration", sa.Date, nullable=False),
        sa.Column("occ_symbol", sa.String(32), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(index=True),
        sa.Column("name", sa.String(32), nullable=False, server_default="main"),
        sa.Column("cash", sa.Numeric(16, 2), nullable=False),
        sa.Column("starting_balance", sa.Numeric(16, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reset_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "name", name="paper_accounts_user_name"),
    )
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(index=True),
        _account_fk(),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        *_contract(),
        sa.Column("quantity", sa.BigInteger, nullable=False),
        sa.Column("entry_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("take_profit_price", sa.Numeric(14, 4)),
        sa.Column("stop_loss_price", sa.Numeric(14, 4)),
        sa.Column("status", sa.String(8), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('open', 'closed', 'voided')", name="paper_positions_status_check"),
        sa.CheckConstraint("quantity >= 0", name="paper_positions_quantity_check"),
        sa.CheckConstraint("option_type IN ('call', 'put')", name="paper_positions_type_check"),
    )
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(index=True),
        _account_fk(),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("intent", sa.String(8), nullable=False),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("paper_positions.id", ondelete="SET NULL")),
        *_contract(),
        sa.Column("quantity", sa.BigInteger, nullable=False),
        sa.Column("limit_price", sa.Numeric(14, 4)),
        sa.Column("take_profit_pct", sa.Numeric(8, 3)),
        sa.Column("stop_loss_pct", sa.Numeric(8, 3)),
        sa.Column("close_reason", sa.String(16)),
        sa.Column("status", sa.String(12), nullable=False, server_default="working"),
        sa.Column("status_detail", sa.Text, nullable=False, server_default=""),
        sa.Column("fill_price", sa.Numeric(14, 4)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(200), unique=True),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="paper_orders_side_check"),
        sa.CheckConstraint("intent IN ('open', 'close')", name="paper_orders_intent_check"),
        sa.CheckConstraint("status IN ('working', 'filled', 'cancelled', 'rejected')", name="paper_orders_status_check"),
        sa.CheckConstraint("quantity > 0", name="paper_orders_quantity_check"),
        sa.CheckConstraint("option_type IN ('call', 'put')", name="paper_orders_type_check"),
    )
    op.create_index("paper_orders_working", "paper_orders", ["status"], postgresql_where=sa.text("status = 'working'"))
    op.create_table(
        "paper_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(index=True),
        sa.Column("account_id", sa.BigInteger, sa.ForeignKey("paper_accounts.id", ondelete="SET NULL")),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("order_id", sa.BigInteger),
        sa.Column("position_id", sa.BigInteger),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
    )
    op.create_table(
        "trading_controls",
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("halted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("auto_paused", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    # Paper trades in Account Manager point back to the position they came from.
    op.add_column("closed_trades", sa.Column("paper_position_id", sa.BigInteger,
                                             sa.ForeignKey("paper_positions.id", ondelete="SET NULL")))


def downgrade() -> None:
    op.drop_column("closed_trades", "paper_position_id")
    op.drop_index("paper_orders_working", table_name="paper_orders")
    for table in ("trading_controls", "paper_events", "paper_orders", "paper_positions", "paper_accounts"):
        op.drop_table(table)

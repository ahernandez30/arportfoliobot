"""long-term capital (positions, buys and sells, deposits, daily snapshots) and the closed-trade log

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CONTRACT_CHECK = (
    "(kind = 'stock' AND option_type IS NULL AND strike IS NULL AND expiration IS NULL)"
    " OR (kind = 'option' AND option_type IN ('call', 'put') AND strike > 0 AND expiration IS NOT NULL)"
)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger, primary_key=True)


def _user_fk(**kw) -> sa.Column:
    return sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, **kw)


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _contract() -> list[sa.Column]:
    return [
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("option_type", sa.String(4)),
        sa.Column("strike", sa.Numeric(12, 3)),
        sa.Column("expiration", sa.Date),
    ]


def upgrade() -> None:
    op.create_table(
        "capital_flows",
        _id(),
        _user_fk(index=True),
        sa.Column("account", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        _created(),
        sa.CheckConstraint("account IN ('long_term', 'short_term')", name="capital_flows_account_check"),
        sa.CheckConstraint("kind IN ('deposit', 'withdrawal')", name="capital_flows_kind_check"),
        sa.CheckConstraint("amount > 0", name="capital_flows_amount_check"),
    )
    op.create_table(
        "long_term_positions",
        _id(),
        _user_fk(index=True),
        *_contract(),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        _created(),
        sa.CheckConstraint("kind IN ('option', 'stock')", name="lt_positions_kind_check"),
        sa.CheckConstraint(CONTRACT_CHECK, name="lt_positions_contract_check"),
    )
    op.create_table(
        "long_term_trades",
        _id(),
        _user_fk(index=True),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("long_term_positions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column("price", sa.Numeric(14, 4), nullable=False),
        sa.Column("fees", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        _created(),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="lt_trades_side_check"),
        sa.CheckConstraint("quantity > 0", name="lt_trades_quantity_check"),
        sa.CheckConstraint("price >= 0", name="lt_trades_price_check"),
        sa.CheckConstraint("fees >= 0", name="lt_trades_fees_check"),
    )
    op.create_table(
        "capital_snapshots",
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("total", sa.Numeric(16, 2), nullable=False),
        sa.Column("put_in", sa.Numeric(16, 2), nullable=False),
        sa.Column("positions_value", sa.Numeric(16, 2), nullable=False),
        sa.Column("cash", sa.Numeric(16, 2), nullable=False),
        sa.Column("estimated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "closed_trades",
        _id(),
        _user_fk(),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        *_contract(),
        sa.Column("direction", sa.String(8), nullable=False, server_default="long"),
        sa.Column("quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column("entry_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("exit_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("fees", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_reason", sa.String(16), nullable=False),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        _created(),
        sa.CheckConstraint("mode IN ('paper', 'real')", name="closed_trades_mode_check"),
        sa.CheckConstraint("kind IN ('option', 'stock')", name="closed_trades_kind_check"),
        sa.CheckConstraint("direction IN ('long', 'short')", name="closed_trades_direction_check"),
        sa.CheckConstraint("quantity > 0", name="closed_trades_quantity_check"),
        sa.CheckConstraint("entry_price >= 0 AND exit_price >= 0 AND fees >= 0", name="closed_trades_prices_check"),
        sa.CheckConstraint(
            "close_reason IN ('take_profit', 'stop_loss', 'signal', 'manual', 'time', 'expired')",
            name="closed_trades_reason_check",
        ),
        sa.CheckConstraint(CONTRACT_CHECK, name="closed_trades_contract_check"),
    )
    op.create_index("closed_trades_user_mode_closed", "closed_trades", ["user_id", "mode", "closed_at"])


def downgrade() -> None:
    op.drop_index("closed_trades_user_mode_closed", table_name="closed_trades")
    for table in ("closed_trades", "capital_snapshots", "long_term_trades", "long_term_positions", "capital_flows"):
        op.drop_table(table)

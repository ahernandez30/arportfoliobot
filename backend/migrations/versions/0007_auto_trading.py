"""automatic paper trading: two-leg spreads, paper sub-accounts, strategy trades

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

STRUCTURES = "structure IN ('single', 'credit_spread', 'debit_spread')"
# A spread's second leg is the same kind of option at another strike, same expiration.
LEGS = "(structure = 'single' AND strike2 IS NULL) OR (structure <> 'single' AND strike2 > 0 AND strike2 <> strike)"


def _legs(table: str, prefix: str) -> None:
    op.add_column(table, sa.Column("structure", sa.String(16), nullable=False, server_default="single"))
    op.add_column(table, sa.Column("strike2", sa.Numeric(12, 3)))
    op.add_column(table, sa.Column("occ_symbol2", sa.String(32)))
    op.create_check_constraint(f"{prefix}_structure_check", table, STRUCTURES)
    op.create_check_constraint(f"{prefix}_legs_check", table, LEGS)


def upgrade() -> None:
    _legs("paper_orders", "paper_orders")
    _legs("paper_positions", "paper_positions")

    op.add_column("closed_trades", sa.Column("structure", sa.String(16), nullable=False, server_default="single"))
    op.add_column("closed_trades", sa.Column("strike2", sa.Numeric(12, 3)))
    op.create_check_constraint("closed_trades_structure_check", "closed_trades", STRUCTURES)
    op.create_check_constraint("closed_trades_legs_check", "closed_trades", LEGS)
    # Dollars that could be lost at entry (a spread's most it can lose); percent results are of this.
    op.add_column("closed_trades", sa.Column("risk", sa.Numeric(16, 2)))
    # Which paper account it ran in ("main" or a sub-account per structure).
    op.add_column("closed_trades", sa.Column("account_name", sa.String(32)))
    op.execute("UPDATE closed_trades SET account_name = 'main' WHERE mode = 'paper'")
    # The stock's price when the trade opened and closed (plan 7.6: record both moves).
    op.add_column("closed_trades", sa.Column("underlying_entry", sa.Numeric(14, 4)))
    op.add_column("closed_trades", sa.Column("underlying_exit", sa.Numeric(14, 4)))

    op.add_column("strategy_presets", sa.Column("trade", postgresql.JSONB, nullable=False,
                                                server_default=sa.text("'{}'::jsonb")))
    op.add_column("strategy_presets", sa.Column("auto", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("strategy_presets", sa.Column("auto_since", sa.DateTime(timezone=True)))

    op.add_column("trading_controls", sa.Column("auto_problem", sa.Text, nullable=False, server_default=""))
    op.add_column("trading_controls", sa.Column("auto_problem_at", sa.DateTime(timezone=True)))
    op.add_column("trading_controls", sa.Column("auto_checked_at", sa.DateTime(timezone=True)))

    op.create_table(
        "strategy_trades",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("preset_id", sa.BigInteger, sa.ForeignKey("strategy_presets.id", ondelete="SET NULL")),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("structure", sa.String(16), nullable=False),
        sa.Column("account_name", sa.String(32), nullable=False),
        sa.Column("signal_time", sa.BigInteger, nullable=False),
        sa.Column("direction", sa.SmallInteger, nullable=False),
        sa.Column("candle_type", sa.String(16), nullable=False),
        sa.Column("signal_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        sa.Column("plan", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("paper_positions.id", ondelete="SET NULL")),
        sa.Column("under_entry", sa.Numeric(14, 4)),
        sa.Column("under_target", sa.Numeric(14, 4)),
        sa.Column("under_stop", sa.Numeric(14, 4)),
        sa.Column("under_exit", sa.Numeric(14, 4)),
        sa.Column("exit_reason", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("direction IN (1, -1)", name="strategy_trades_direction_check"),
        sa.CheckConstraint("structure IN ('directional', 'credit_spread', 'debit_spread')",
                           name="strategy_trades_structure_check"),
        sa.CheckConstraint("status IN ('waiting', 'open', 'floating', 'closed', 'refused', 'missed')",
                           name="strategy_trades_status_check"),
        sa.UniqueConstraint("user_id", "strategy", "symbol", "timeframe", "signal_time", "structure",
                            name="strategy_trades_once"),
    )
    op.create_index("strategy_trades_user_created", "strategy_trades", ["user_id", "created_at"])
    op.create_index("strategy_trades_active", "strategy_trades", ["status"],
                    postgresql_where=sa.text("status IN ('waiting', 'open', 'floating')"))


def downgrade() -> None:
    op.drop_table("strategy_trades")
    for c in ("auto_checked_at", "auto_problem_at", "auto_problem"):
        op.drop_column("trading_controls", c)
    for c in ("auto_since", "auto", "trade"):
        op.drop_column("strategy_presets", c)
    op.drop_constraint("closed_trades_legs_check", "closed_trades")
    op.drop_constraint("closed_trades_structure_check", "closed_trades")
    for c in ("underlying_exit", "underlying_entry", "account_name", "risk", "strike2", "structure"):
        op.drop_column("closed_trades", c)
    for table in ("paper_positions", "paper_orders"):
        op.drop_constraint(f"{table}_legs_check", table)
        op.drop_constraint(f"{table}_structure_check", table)
        for c in ("occ_symbol2", "strike2", "structure"):
            op.drop_column(table, c)

"""add account classification and net worth

Revision ID: 20260813_0009
Revises: 20260813_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0009"
down_revision: str | None = "20260813_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("financial_accounts", sa.Column("include_in_net_worth", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("financial_accounts", sa.Column("ownership_scope", sa.String(20), server_default="household", nullable=False))
    op.add_column("financial_accounts", sa.Column("balance_nature", sa.String(20), server_default="asset", nullable=False))
    op.add_column("financial_accounts", sa.Column("liquidity", sa.String(20), server_default="spendable", nullable=False))
    op.add_column("financial_accounts", sa.Column("tax_treatment", sa.String(24), server_default="none", nullable=False))
    op.add_column("financial_accounts", sa.Column("institution", sa.String(200)))
    op.add_column("financial_accounts", sa.Column("masked_identifier", sa.String(24)))
    op.execute("UPDATE financial_accounts SET balance_nature='liability', liquidity='liability', include_in_planner=false WHERE account_type IN ('credit_card','loan')")
    op.add_column("ledger_transactions", sa.Column("activity_type", sa.String(24), server_default="regular", nullable=False))
    op.create_table("account_valuations", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("account_id", sa.Uuid(), nullable=False), sa.Column("valuation_date", sa.Date(), nullable=False), sa.Column("value_minor", sa.Integer(), nullable=False), sa.Column("currency_code", sa.String(3), nullable=False), sa.Column("source_type", sa.String(20), nullable=False), sa.Column("note", sa.String(500)), sa.Column("created_by_user_id", sa.Uuid()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["account_id"], ["financial_accounts.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("account_id", "valuation_date", name="uq_account_valuation_date"))
    op.create_index("ix_account_valuations_household_id", "account_valuations", ["household_id"])
    op.create_index("ix_account_valuations_account_id", "account_valuations", ["account_id"])
    op.create_index("ix_account_valuations_valuation_date", "account_valuations", ["valuation_date"])


def downgrade() -> None:
    op.drop_table("account_valuations")
    op.drop_column("ledger_transactions", "activity_type")
    op.drop_column("financial_accounts", "masked_identifier")
    op.drop_column("financial_accounts", "institution")
    op.drop_column("financial_accounts", "tax_treatment")
    op.drop_column("financial_accounts", "liquidity")
    op.drop_column("financial_accounts", "balance_nature")
    op.drop_column("financial_accounts", "ownership_scope")
    op.drop_column("financial_accounts", "include_in_net_worth")

"""add ledger lifecycle and merchants

Revision ID: 20260813_0007
Revises: 20260813_0006
Create Date: 2026-08-13
"""
import sqlalchemy as sa

from alembic import op

revision = "20260813_0007"
down_revision = "20260813_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("household_id", "name", name="uq_merchant_household_name"),
    )
    op.create_index("ix_merchants_household_id", "merchants", ["household_id"])
    op.create_table(
        "merchant_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("household_id", "alias", name="uq_merchant_alias_household_alias"),
    )
    op.create_index("ix_merchant_aliases_household_id", "merchant_aliases", ["household_id"])
    op.create_index("ix_merchant_aliases_merchant_id", "merchant_aliases", ["merchant_id"])
    op.add_column("ledger_transactions", sa.Column("merchant_id", sa.Uuid(), sa.ForeignKey("merchants.id", ondelete="SET NULL")))
    op.add_column("ledger_transactions", sa.Column("reversal_of_transaction_id", sa.Uuid(), sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT")))
    op.add_column("ledger_transactions", sa.Column("corrected_from_transaction_id", sa.Uuid(), sa.ForeignKey("ledger_transactions.id", ondelete="SET NULL")))
    op.add_column("ledger_transactions", sa.Column("reconciled_at", sa.DateTime(timezone=True)))
    op.add_column("ledger_transactions", sa.Column("reconciled_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.add_column("ledger_transactions", sa.Column("voided_at", sa.DateTime(timezone=True)))
    op.add_column("ledger_transactions", sa.Column("voided_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.create_index("ix_ledger_transactions_merchant_id", "ledger_transactions", ["merchant_id"])
    op.create_unique_constraint("uq_ledger_transaction_reversal_of", "ledger_transactions", ["reversal_of_transaction_id"])
    op.create_table(
        "transaction_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), sa.ForeignKey("ledger_transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("before_snapshot", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_transaction_revisions_household_id", "transaction_revisions", ["household_id"])
    op.create_index("ix_transaction_revisions_transaction_id", "transaction_revisions", ["transaction_id"])


def downgrade() -> None:
    op.drop_table("transaction_revisions")
    op.drop_constraint("uq_ledger_transaction_reversal_of", "ledger_transactions", type_="unique")
    op.drop_index("ix_ledger_transactions_merchant_id", table_name="ledger_transactions")
    op.drop_column("ledger_transactions", "voided_by_user_id")
    op.drop_column("ledger_transactions", "voided_at")
    op.drop_column("ledger_transactions", "reconciled_by_user_id")
    op.drop_column("ledger_transactions", "reconciled_at")
    op.drop_column("ledger_transactions", "corrected_from_transaction_id")
    op.drop_column("ledger_transactions", "reversal_of_transaction_id")
    op.drop_column("ledger_transactions", "merchant_id")
    op.drop_table("merchant_aliases")
    op.drop_table("merchants")

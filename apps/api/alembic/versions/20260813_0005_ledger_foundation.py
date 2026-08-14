"""add ledger foundation

Revision ID: 20260813_0005
Revises: 20260813_0004
Create Date: 2026-08-13
"""
import sqlalchemy as sa

from alembic import op

revision = "20260813_0005"
down_revision = "20260813_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("account_type", sa.String(24), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("opening_balance_minor", sa.Integer(), nullable=False),
        sa.Column("include_in_planner", sa.Boolean(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(currency_code) = 3", name="ck_financial_account_currency_length"),
        sa.UniqueConstraint("household_id", "name", name="uq_financial_account_household_name"),
    )
    op.create_index("ix_financial_accounts_household_id", "financial_accounts", ["household_id"])
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category_type", sa.String(16), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("household_id", "name", name="uq_category_household_name"),
    )
    op.create_index("ix_categories_household_id", "categories", ["household_id"])
    op.create_table(
        "ledger_transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("financial_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payee", sa.String(200)),
        sa.Column("memo", sa.Text()),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_reference", sa.String(255)),
        sa.Column("raw_payee", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(currency_code) = 3", name="ck_ledger_transaction_currency_length"),
    )
    op.create_index("ix_ledger_transactions_household_id", "ledger_transactions", ["household_id"])
    op.create_index("ix_ledger_transactions_account_id", "ledger_transactions", ["account_id"])
    op.create_index("ix_ledger_transactions_transaction_date", "ledger_transactions", ["transaction_date"])
    op.create_table(
        "transaction_splits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("transaction_id", sa.Uuid(), sa.ForeignKey("ledger_transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("memo", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_transaction_splits_transaction_id", "transaction_splits", ["transaction_id"])
    op.create_index("ix_transaction_splits_category_id", "transaction_splits", ["category_id"])
    op.create_table(
        "transfer_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_transaction_id", sa.Uuid(), sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("to_transaction_id", sa.Uuid(), sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("from_transaction_id <> to_transaction_id", name="ck_transfer_distinct_legs"),
        sa.UniqueConstraint("from_transaction_id", name="uq_transfer_from_transaction"),
        sa.UniqueConstraint("to_transaction_id", name="uq_transfer_to_transaction"),
    )
    op.create_index("ix_transfer_links_household_id", "transfer_links", ["household_id"])


def downgrade() -> None:
    op.drop_table("transfer_links")
    op.drop_table("transaction_splits")
    op.drop_table("ledger_transactions")
    op.drop_table("categories")
    op.drop_table("financial_accounts")

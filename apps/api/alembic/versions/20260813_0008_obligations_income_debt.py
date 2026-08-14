"""add obligations income and debt

Revision ID: 20260813_0008
Revises: 20260813_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0008"
down_revision: str | None = "20260813_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("bill_profiles", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("payee", sa.String(200)), sa.Column("cadence", sa.String(20), nullable=False), sa.Column("next_due_date", sa.Date()), sa.Column("due_day", sa.Integer()), sa.Column("expected_amount_minor", sa.Integer(), nullable=False), sa.Column("minimum_amount_minor", sa.Integer()), sa.Column("maximum_amount_minor", sa.Integer()), sa.Column("currency_code", sa.String(3), nullable=False), sa.Column("is_essential", sa.Boolean(), nullable=False), sa.Column("priority", sa.Integer(), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("household_id", "name", name="uq_bill_profile_household_name"))
    op.create_index("ix_bill_profiles_household_id", "bill_profiles", ["household_id"])
    op.create_table("debts", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("account_id", sa.Uuid()), sa.Column("name", sa.String(160), nullable=False), sa.Column("lender", sa.String(200)), sa.Column("balance_minor", sa.Integer(), nullable=False), sa.Column("apr_basis_points", sa.Integer(), nullable=False), sa.Column("minimum_payment_minor", sa.Integer(), nullable=False), sa.Column("due_day", sa.Integer(), nullable=False), sa.Column("next_due_date", sa.Date(), nullable=False), sa.Column("currency_code", sa.String(3), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["account_id"], ["financial_accounts.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("household_id", "name", name="uq_debt_household_name"))
    op.create_index("ix_debts_household_id", "debts", ["household_id"])
    op.create_table("income_sources", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("payer", sa.String(200)), sa.Column("cadence", sa.String(20), nullable=False), sa.Column("next_expected_date", sa.Date()), sa.Column("expected_amount_minor", sa.Integer(), nullable=False), sa.Column("minimum_amount_minor", sa.Integer()), sa.Column("maximum_amount_minor", sa.Integer()), sa.Column("currency_code", sa.String(3), nullable=False), sa.Column("confidence_percent", sa.Integer(), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("household_id", "name", name="uq_income_source_household_name"))
    op.create_index("ix_income_sources_household_id", "income_sources", ["household_id"])
    op.create_table("bill_instances", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("bill_profile_id", sa.Uuid()), sa.Column("debt_id", sa.Uuid()), sa.Column("name", sa.String(160), nullable=False), sa.Column("due_date", sa.Date(), nullable=False), sa.Column("expected_amount_minor", sa.Integer(), nullable=False), sa.Column("minimum_amount_minor", sa.Integer()), sa.Column("maximum_amount_minor", sa.Integer()), sa.Column("currency_code", sa.String(3), nullable=False), sa.Column("is_essential", sa.Boolean(), nullable=False), sa.Column("priority", sa.Integer(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("note", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["bill_profile_id"], ["bill_profiles.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["debt_id"], ["debts.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("bill_profile_id", "due_date", name="uq_bill_instance_profile_date"), sa.UniqueConstraint("debt_id", "due_date", name="uq_bill_instance_debt_date"))
    op.create_index("ix_bill_instances_household_id", "bill_instances", ["household_id"])
    op.create_index("ix_bill_instances_bill_profile_id", "bill_instances", ["bill_profile_id"])
    op.create_index("ix_bill_instances_debt_id", "bill_instances", ["debt_id"])
    op.create_index("ix_bill_instances_due_date", "bill_instances", ["due_date"])
    op.create_table("income_events", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("income_source_id", sa.Uuid()), sa.Column("received_transaction_id", sa.Uuid()), sa.Column("name", sa.String(160), nullable=False), sa.Column("expected_date", sa.Date(), nullable=False), sa.Column("expected_amount_minor", sa.Integer(), nullable=False), sa.Column("minimum_amount_minor", sa.Integer()), sa.Column("maximum_amount_minor", sa.Integer()), sa.Column("currency_code", sa.String(3), nullable=False), sa.Column("confidence_percent", sa.Integer(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("note", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["income_source_id"], ["income_sources.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["received_transaction_id"], ["ledger_transactions.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("income_source_id", "expected_date", name="uq_income_event_source_date"), sa.UniqueConstraint("received_transaction_id"))
    op.create_index("ix_income_events_household_id", "income_events", ["household_id"])
    op.create_index("ix_income_events_income_source_id", "income_events", ["income_source_id"])
    op.create_index("ix_income_events_expected_date", "income_events", ["expected_date"])
    op.create_table("bill_payment_links", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("bill_instance_id", sa.Uuid(), nullable=False), sa.Column("transaction_id", sa.Uuid(), nullable=False), sa.Column("amount_minor", sa.Integer(), nullable=False), sa.Column("created_by_user_id", sa.Uuid()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["bill_instance_id"], ["bill_instances.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["transaction_id"], ["ledger_transactions.id"], ondelete="RESTRICT"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("bill_instance_id", "transaction_id", name="uq_bill_payment_instance_transaction"))
    op.create_index("ix_bill_payment_links_household_id", "bill_payment_links", ["household_id"])
    op.create_index("ix_bill_payment_links_bill_instance_id", "bill_payment_links", ["bill_instance_id"])
    op.create_index("ix_bill_payment_links_transaction_id", "bill_payment_links", ["transaction_id"])


def downgrade() -> None:
    op.drop_table("bill_payment_links")
    op.drop_table("income_events")
    op.drop_table("bill_instances")
    op.drop_table("income_sources")
    op.drop_table("debts")
    op.drop_table("bill_profiles")

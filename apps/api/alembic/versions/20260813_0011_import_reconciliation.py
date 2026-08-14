"""add import and reconciliation evidence

Revision ID: 20260813_0011
Revises: 20260813_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0011"
down_revision: str | None = "20260813_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("import_sources", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("account_id", sa.Uuid(), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("institution", sa.String(200)), sa.Column("format_type", sa.String(24), nullable=False), sa.Column("date_column", sa.String(80), nullable=False), sa.Column("payee_column", sa.String(80), nullable=False), sa.Column("amount_column", sa.String(80), nullable=False), sa.Column("date_format", sa.String(40), nullable=False), sa.Column("export_method", sa.String(200)), sa.Column("export_instructions", sa.Text()), sa.Column("notes", sa.Text()), sa.Column("reminder_interval_days", sa.Integer()), sa.Column("next_reminder_date", sa.Date()), sa.Column("reminders_enabled", sa.Boolean(), nullable=False), sa.Column("last_imported_at", sa.DateTime(timezone=True)), sa.Column("is_active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["account_id"], ["financial_accounts.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("household_id", "name", name="uq_import_source_household_name"))
    for column in ("household_id", "account_id", "next_reminder_date"): op.create_index(f"ix_import_sources_{column}", "import_sources", [column])
    op.create_table("import_batches", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("source_id", sa.Uuid(), nullable=False), sa.Column("created_by_user_id", sa.Uuid()), sa.Column("filename", sa.String(255), nullable=False), sa.Column("file_checksum", sa.String(64), nullable=False), sa.Column("parser_version", sa.String(32), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("raw_csv", sa.Text(), nullable=False), sa.Column("row_count", sa.Integer(), nullable=False), sa.Column("candidate_count", sa.Integer(), nullable=False), sa.Column("duplicate_count", sa.Integer(), nullable=False), sa.Column("invalid_count", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["source_id"], ["import_sources.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("source_id", "file_checksum", name="uq_import_batch_source_checksum"))
    for column in ("household_id", "source_id", "file_checksum"): op.create_index(f"ix_import_batches_{column}", "import_batches", [column])
    op.create_table("import_rows", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("source_id", sa.Uuid(), nullable=False), sa.Column("batch_id", sa.Uuid(), nullable=False), sa.Column("row_number", sa.Integer(), nullable=False), sa.Column("raw_json", sa.Text(), nullable=False), sa.Column("raw_text", sa.Text(), nullable=False), sa.Column("row_hash", sa.String(64), nullable=False), sa.Column("transaction_date", sa.Date()), sa.Column("amount_minor", sa.Integer()), sa.Column("currency_code", sa.String(3), nullable=False), sa.Column("raw_payee", sa.String(500)), sa.Column("normalized_payee", sa.String(300)), sa.Column("status", sa.String(24), nullable=False), sa.Column("exception_type", sa.String(40)), sa.Column("validation_error", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["source_id"], ["import_sources.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    for column in ("household_id", "source_id", "batch_id", "row_hash", "transaction_date", "status"): op.create_index(f"ix_import_rows_{column}", "import_rows", [column])
    op.create_table("reconciliation_matches", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("import_row_id", sa.Uuid(), nullable=False), sa.Column("transaction_id", sa.Uuid(), nullable=False), sa.Column("method", sa.String(40), nullable=False), sa.Column("confidence_percent", sa.Integer(), nullable=False), sa.Column("evidence", sa.Text(), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("reviewed_by_user_id", sa.Uuid()), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("review_note", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["import_row_id"], ["import_rows.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["transaction_id"], ["ledger_transactions.id"], ondelete="RESTRICT"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("import_row_id", "transaction_id", name="uq_reconciliation_row_transaction"))
    for column in ("household_id", "import_row_id", "transaction_id", "status"): op.create_index(f"ix_reconciliation_matches_{column}", "reconciliation_matches", [column])
    op.create_table("reconciliation_exceptions", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("batch_id", sa.Uuid(), nullable=False), sa.Column("exception_type", sa.String(40), nullable=False), sa.Column("related_type", sa.String(40)), sa.Column("related_id", sa.String(120)), sa.Column("event_date", sa.Date()), sa.Column("amount_minor", sa.Integer()), sa.Column("currency_code", sa.String(3)), sa.Column("detail", sa.Text(), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("reviewed_by_user_id", sa.Uuid()), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    for column in ("household_id", "batch_id", "exception_type", "status"): op.create_index(f"ix_reconciliation_exceptions_{column}", "reconciliation_exceptions", [column])


def downgrade() -> None:
    op.drop_table("reconciliation_exceptions")
    op.drop_table("reconciliation_matches")
    op.drop_table("import_rows")
    op.drop_table("import_batches")
    op.drop_table("import_sources")

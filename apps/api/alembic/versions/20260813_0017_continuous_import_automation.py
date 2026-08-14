"""add continuous import automation and household rules

Revision ID: 20260813_0017
Revises: 20260813_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0017"
down_revision: str | None = "20260813_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_category_rule_match", "category_rules", type_="unique")
    for name, column in (
        ("account_id", sa.Column("account_id", sa.Uuid(), sa.ForeignKey("financial_accounts.id", ondelete="CASCADE"), nullable=True)),
        ("source_id", sa.Column("source_id", sa.Uuid(), sa.ForeignKey("import_sources.id", ondelete="CASCADE"), nullable=True)),
        ("amount_min_minor", sa.Column("amount_min_minor", sa.Integer(), nullable=True)),
        ("amount_max_minor", sa.Column("amount_max_minor", sa.Integer(), nullable=True)),
        ("description_pattern", sa.Column("description_pattern", sa.String(300), nullable=True)),
        ("priority", sa.Column("priority", sa.Integer(), nullable=False, server_default="100")),
        ("auto_apply", sa.Column("auto_apply", sa.Boolean(), nullable=False, server_default=sa.true())),
        ("use_count", sa.Column("use_count", sa.Integer(), nullable=False, server_default="0")),
        ("last_applied_at", sa.Column("last_applied_at", sa.DateTime(timezone=True), nullable=True)),
        ("created_from_action", sa.Column("created_from_action", sa.String(40), nullable=False, server_default="apply_and_remember")),
    ):
        op.add_column("category_rules", column)
    op.create_index("ix_category_rules_account_id", "category_rules", ["account_id"])
    op.create_index("ix_category_rules_source_id", "category_rules", ["source_id"])

    op.create_table(
        "import_source_mapping_versions",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False), sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("mapping_hash", sa.String(64), nullable=False), sa.Column("mapping_json", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["import_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("source_id", "version_number", name="uq_import_source_mapping_version"),
    )
    op.create_index("ix_import_source_mapping_versions_household_id", "import_source_mapping_versions", ["household_id"])
    op.create_index("ix_import_source_mapping_versions_source_id", "import_source_mapping_versions", ["source_id"])
    op.create_index("ix_import_source_mapping_versions_mapping_hash", "import_source_mapping_versions", ["mapping_hash"])

    for name in ("ready_count", "transfer_count", "recurring_count", "review_count"):
        op.add_column("import_batches", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    op.add_column("import_batches", sa.Column("mapping_version_id", sa.Uuid(), sa.ForeignKey("import_source_mapping_versions.id", ondelete="SET NULL"), nullable=True))

    op.add_column("import_rows", sa.Column("automation_kind", sa.String(40), nullable=True))
    op.add_column("import_rows", sa.Column("applied_rule_id", sa.Uuid(), sa.ForeignKey("category_rules.id", ondelete="SET NULL"), nullable=True))
    op.add_column("import_rows", sa.Column("proposed_category_id", sa.Uuid(), sa.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True))
    op.add_column("import_rows", sa.Column("automation_confidence", sa.Integer(), nullable=True))
    op.add_column("import_rows", sa.Column("automation_evidence", sa.Text(), nullable=True))
    op.create_index("ix_import_rows_automation_kind", "import_rows", ["automation_kind"])
    op.create_index("ix_import_rows_applied_rule_id", "import_rows", ["applied_rule_id"])

    op.create_table(
        "automation_decisions",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False), sa.Column("entity_id", sa.String(120), nullable=False),
        sa.Column("decision_type", sa.String(40), nullable=False), sa.Column("rule_id", sa.Uuid()),
        sa.Column("provider", sa.String(40), nullable=False), sa.Column("confidence_percent", sa.Integer(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False), sa.Column("outcome_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("actor_user_id", sa.Uuid()),
        sa.Column("reversed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["category_rules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"),
    )
    for name in ("household_id", "entity_type", "entity_id", "decision_type", "status"):
        op.create_index(f"ix_automation_decisions_{name}", "automation_decisions", [name])

    op.create_table(
        "transfer_candidates",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("import_row_id", sa.Uuid(), nullable=False), sa.Column("counterparty_transaction_id", sa.Uuid(), nullable=False),
        sa.Column("confidence_percent", sa.Integer(), nullable=False), sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("reviewed_by_user_id", sa.Uuid()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_row_id"], ["import_rows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["counterparty_transaction_id"], ["ledger_transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("import_row_id", "counterparty_transaction_id", name="uq_transfer_candidate_pair"),
    )
    for name in ("household_id", "import_row_id", "counterparty_transaction_id", "status"):
        op.create_index(f"ix_transfer_candidates_{name}", "transfer_candidates", [name])

    op.create_table(
        "reimbursement_links",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("reimbursement_transaction_id", sa.Uuid(), nullable=False), sa.Column("original_transaction_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False), sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reimbursement_transaction_id"], ["ledger_transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["original_transaction_id"], ["ledger_transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("reimbursement_transaction_id", "original_transaction_id", name="uq_reimbursement_pair"),
    )
    for name in ("household_id", "reimbursement_transaction_id", "original_transaction_id", "category_id"):
        op.create_index(f"ix_reimbursement_links_{name}", "reimbursement_links", [name])

    op.create_table(
        "recurring_profile_links",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False), sa.Column("profile_type", sa.String(20), nullable=False),
        sa.Column("bill_profile_id", sa.Uuid()), sa.Column("income_source_id", sa.Uuid()),
        sa.Column("match_method", sa.String(40), nullable=False), sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["ledger_transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bill_profile_id"], ["bill_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["income_source_id"], ["income_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("transaction_id", name="uq_recurring_profile_transaction"),
    )
    for name in ("household_id", "transaction_id", "bill_profile_id", "income_source_id"):
        op.create_index(f"ix_recurring_profile_links_{name}", "recurring_profile_links", [name])


def downgrade() -> None:
    op.drop_table("recurring_profile_links")
    op.drop_table("reimbursement_links")
    op.drop_table("transfer_candidates")
    op.drop_table("automation_decisions")
    for name in ("automation_evidence", "automation_confidence", "proposed_category_id", "applied_rule_id", "automation_kind"):
        op.drop_column("import_rows", name)
    for name in ("mapping_version_id", "review_count", "recurring_count", "transfer_count", "ready_count"):
        op.drop_column("import_batches", name)
    op.drop_table("import_source_mapping_versions")
    for name in ("created_from_action", "last_applied_at", "use_count", "auto_apply", "priority", "description_pattern", "amount_max_minor", "amount_min_minor", "source_id", "account_id"):
        op.drop_column("category_rules", name)
    op.create_unique_constraint("uq_category_rule_match", "category_rules", ["household_id", "match_type", "match_value", "direction"])

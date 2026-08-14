"""add categorization and assistant records

Revision ID: 20260813_0015
Revises: 20260813_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0015"
down_revision: str | None = "20260813_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("category_rules", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("category_id", sa.Uuid(), nullable=False), sa.Column("match_type", sa.String(24), nullable=False), sa.Column("match_value", sa.String(300), nullable=False), sa.Column("direction", sa.String(12), nullable=False), sa.Column("source_suggestion_id", sa.Uuid()), sa.Column("is_active", sa.Boolean(), nullable=False), sa.Column("created_by_user_id", sa.Uuid()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("household_id", "match_type", "match_value", "direction", name="uq_category_rule_match"))
    op.create_index("ix_category_rules_household_id", "category_rules", ["household_id"])
    op.create_index("ix_category_rules_category_id", "category_rules", ["category_id"])
    op.create_table("category_suggestions", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("transaction_id", sa.Uuid(), nullable=False), sa.Column("provider", sa.String(40), nullable=False), sa.Column("model_version", sa.String(255), nullable=False), sa.Column("rule_version", sa.String(80), nullable=False), sa.Column("confidence_percent", sa.Integer(), nullable=False), sa.Column("proposed_splits_json", sa.Text(), nullable=False), sa.Column("evidence_json", sa.Text(), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("reviewed_by_user_id", sa.Uuid()), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["transaction_id"], ["ledger_transactions.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_category_suggestions_household_id", "category_suggestions", ["household_id"])
    op.create_index("ix_category_suggestions_transaction_id", "category_suggestions", ["transaction_id"])
    op.create_index("ix_category_suggestions_status", "category_suggestions", ["status"])
    op.create_table("assistant_conversations", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("user_id", sa.Uuid(), nullable=False), sa.Column("title", sa.String(160), nullable=False), sa.Column("currency_code", sa.String(3), nullable=False), sa.Column("ownership_scope", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_assistant_conversations_household_id", "assistant_conversations", ["household_id"])
    op.create_index("ix_assistant_conversations_user_id", "assistant_conversations", ["user_id"])
    op.create_table("assistant_messages", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("conversation_id", sa.Uuid(), nullable=False), sa.Column("role", sa.String(16), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("citations_json", sa.Text(), nullable=False), sa.Column("provider", sa.String(40)), sa.Column("model_version", sa.String(255)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["conversation_id"], ["assistant_conversations.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_assistant_messages_conversation_id", "assistant_messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("assistant_messages")
    op.drop_table("assistant_conversations")
    op.drop_table("category_suggestions")
    op.drop_table("category_rules")

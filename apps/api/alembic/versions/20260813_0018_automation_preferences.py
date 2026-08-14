"""add automation preferences and ingestion provenance

Revision ID: 20260813_0018
Revises: 20260813_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0018"
down_revision: str | None = "20260813_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "import_batches",
        sa.Column("ingestion_channel", sa.String(32), nullable=False, server_default="csv_upload"),
    )
    op.add_column("import_batches", sa.Column("upstream_reference", sa.String(500), nullable=True))
    op.create_table(
        "household_automation_preferences",
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("transfer_window_days", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("reimbursement_window_days", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("household_id"),
    )


def downgrade() -> None:
    op.drop_table("household_automation_preferences")
    op.drop_column("import_batches", "upstream_reference")
    op.drop_column("import_batches", "ingestion_channel")

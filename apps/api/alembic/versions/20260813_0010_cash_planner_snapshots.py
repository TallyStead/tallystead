"""add cash planner snapshots

Revision ID: 20260813_0010
Revises: 20260813_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0010"
down_revision: str | None = "20260813_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planner_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid()),
        sa.Column("rule_version", sa.String(24), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("horizon_date", sa.Date(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planner_snapshots_household_id", "planner_snapshots", ["household_id"])
    op.create_index("ix_planner_snapshots_as_of_date", "planner_snapshots", ["as_of_date"])
    op.create_index("ix_planner_snapshots_input_hash", "planner_snapshots", ["input_hash"])


def downgrade() -> None:
    op.drop_table("planner_snapshots")

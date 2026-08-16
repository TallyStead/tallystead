"""Add existing-member Pangolin external identity links.

Revision ID: 20260815_0025
Revises: 20260814_0024
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0025"
down_revision: str | None = "20260814_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=320), nullable=False),
        sa.Column("email_at_link", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "subject", name="uq_external_identity_provider_subject"),
        sa.UniqueConstraint("provider", "user_id", name="uq_external_identity_provider_user"),
    )
    op.create_index(op.f("ix_external_identities_user_id"), "external_identities", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_external_identities_user_id"), table_name="external_identities")
    op.drop_table("external_identities")

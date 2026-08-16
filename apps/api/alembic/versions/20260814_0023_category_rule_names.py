"""Add editable category rule names.

Revision ID: 20260814_0023
Revises: 20260814_0022
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0023"
down_revision: str | None = "20260814_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("category_rules", sa.Column("rule_name", sa.String(160), nullable=True))
    op.execute("UPDATE category_rules SET rule_name = match_value WHERE rule_name IS NULL")
    op.alter_column("category_rules", "rule_name", nullable=False)


def downgrade() -> None:
    op.drop_column("category_rules", "rule_name")

"""Add dated debt balance anchors and principal allocations.

Revision ID: 20260814_0024
Revises: 20260814_0023
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0024"
down_revision: str | None = "20260814_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("debts", sa.Column("balance_anchor_minor", sa.Integer(), nullable=True))
    op.add_column("debts", sa.Column("balance_as_of_date", sa.Date(), nullable=True))
    op.add_column("bill_payment_links", sa.Column("principal_amount_minor", sa.Integer(), nullable=True))
    op.execute("UPDATE debts SET balance_anchor_minor = balance_minor")
    op.execute("UPDATE bill_payment_links SET principal_amount_minor = amount_minor")


def downgrade() -> None:
    op.drop_column("bill_payment_links", "principal_amount_minor")
    op.drop_column("debts", "balance_as_of_date")
    op.drop_column("debts", "balance_anchor_minor")

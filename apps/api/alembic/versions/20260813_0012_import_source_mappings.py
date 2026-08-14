"""add reusable csv source mappings

Revision ID: 20260813_0012
Revises: 20260813_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0012"
down_revision: str | None = "20260813_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("import_sources", "amount_column", existing_type=sa.String(80), nullable=True)
    for name in ("original_payee_column", "debit_column", "credit_column", "status_column", "category_column", "memo_column"):
        op.add_column("import_sources", sa.Column(name, sa.String(80), nullable=True))
    op.add_column("import_sources", sa.Column("amount_sign", sa.String(24), nullable=False, server_default="positive_in"))
    op.alter_column("import_sources", "amount_sign", server_default=None)


def downgrade() -> None:
    op.drop_column("import_sources", "amount_sign")
    for name in reversed(("original_payee_column", "debit_column", "credit_column", "status_column", "category_column", "memo_column")):
        op.drop_column("import_sources", name)
    op.execute("UPDATE import_sources SET amount_column = 'amount' WHERE amount_column IS NULL")
    op.alter_column("import_sources", "amount_column", existing_type=sa.String(80), nullable=False)

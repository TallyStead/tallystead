"""support transfer candidates between pending import rows

Revision ID: 20260813_0019
Revises: 20260813_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0019"
down_revision: str | None = "20260813_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("transfer_candidates", "counterparty_transaction_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("transfer_candidates", sa.Column("counterparty_import_row_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_transfer_candidate_import_row", "transfer_candidates", "import_rows", ["counterparty_import_row_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_transfer_candidates_counterparty_import_row_id", "transfer_candidates", ["counterparty_import_row_id"])
    op.create_unique_constraint("uq_transfer_candidate_import_pair", "transfer_candidates", ["import_row_id", "counterparty_import_row_id"])


def downgrade() -> None:
    op.drop_constraint("uq_transfer_candidate_import_pair", "transfer_candidates", type_="unique")
    op.drop_index("ix_transfer_candidates_counterparty_import_row_id", table_name="transfer_candidates")
    op.drop_constraint("fk_transfer_candidate_import_row", "transfer_candidates", type_="foreignkey")
    op.drop_column("transfer_candidates", "counterparty_import_row_id")
    op.alter_column("transfer_candidates", "counterparty_transaction_id", existing_type=sa.Uuid(), nullable=False)

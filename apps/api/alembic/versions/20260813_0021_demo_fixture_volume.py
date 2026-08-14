"""demo fixture volume

Revision ID: 20260813_0021
Revises: 20260813_0020
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0021"
down_revision: str | None = "20260813_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("household_data_states", sa.Column("demo_volume", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("household_data_states", "demo_volume")

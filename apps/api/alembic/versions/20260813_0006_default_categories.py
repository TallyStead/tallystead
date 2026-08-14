"""seed household default categories

Revision ID: 20260813_0006
Revises: 20260813_0005
Create Date: 2026-08-13
"""
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "20260813_0006"
down_revision = "20260813_0005"
branch_labels = None
depends_on = None

DEFAULT_CATEGORIES = (
    ("Paycheck", "income"),
    ("Freelance & side income", "income"),
    ("Benefits", "income"),
    ("Interest income", "income"),
    ("Other income", "income"),
    ("Housing", "expense"),
    ("Utilities", "expense"),
    ("Groceries", "expense"),
    ("Dining out", "expense"),
    ("Transportation", "expense"),
    ("Fuel", "expense"),
    ("Insurance", "expense"),
    ("Healthcare", "expense"),
    ("Childcare", "expense"),
    ("Education", "expense"),
    ("Household supplies", "expense"),
    ("Personal care", "expense"),
    ("Clothing", "expense"),
    ("Entertainment", "expense"),
    ("Subscriptions", "expense"),
    ("Gifts & donations", "expense"),
    ("Travel", "expense"),
    ("Taxes", "expense"),
    ("Bank fees", "expense"),
    ("Interest & finance charges", "expense"),
    ("Miscellaneous", "expense"),
)


def upgrade() -> None:
    op.add_column("categories", sa.Column("is_system_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    connection = op.get_bind()
    category_table = sa.table(
        "categories",
        sa.column("id", sa.Uuid()),
        sa.column("household_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("category_type", sa.String()),
        sa.column("is_system_default", sa.Boolean()),
        sa.column("is_archived", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    for household_id in connection.execute(sa.text("SELECT id FROM households")).scalars():
        existing = set(connection.execute(sa.text("SELECT name FROM categories WHERE household_id = :household_id"), {"household_id": household_id}).scalars())
        rows = [{"id": uuid.uuid4(), "household_id": household_id, "name": name, "category_type": category_type, "is_system_default": True, "is_archived": False, "created_at": datetime.now(UTC)} for name, category_type in DEFAULT_CATEGORIES if name not in existing]
        if rows:
            connection.execute(category_table.insert(), rows)
    op.alter_column("categories", "is_system_default", server_default=None)


def downgrade() -> None:
    op.drop_column("categories", "is_system_default")

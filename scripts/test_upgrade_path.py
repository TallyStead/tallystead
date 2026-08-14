#!/usr/bin/env python3
"""Exercise a supported PostgreSQL migration path without household data."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

ROOT = Path(__file__).resolve().parents[1]
START_REVISION = "20260813_0016"


def main() -> None:
    database_url = os.environ.get("TALLYSTEAD_DATABASE_URL")
    if not database_url or "sqlite" in database_url:
        raise SystemExit("TALLYSTEAD_DATABASE_URL must name a disposable PostgreSQL database")

    engine = sa.create_engine(database_url)
    with engine.connect() as connection:
        if sa.inspect(connection).get_table_names():
            raise SystemExit("Upgrade test database is not empty; refusing to modify it")

    config = Config(str(ROOT / "apps/api/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, START_REVISION)

    household_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO households (id, name, created_at) "
                "VALUES (:id, :name, CURRENT_TIMESTAMP)"
            ),
            {"id": household_id, "name": "Upgrade path fixture"},
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        name = connection.execute(
            sa.text("SELECT name FROM households WHERE id = :id"), {"id": household_id}
        ).scalar_one()
        current = MigrationContext.configure(connection).get_current_revision()
        session_columns = {
            item["name"] for item in sa.inspect(connection).get_columns("session_tokens")
        }

    if name != "Upgrade path fixture":
        raise SystemExit("Seeded household did not survive the migration")
    if current != "20260814_0022":
        raise SystemExit(f"Expected migration head 20260814_0022, found {current}")
    if "last_seen_at" not in session_columns:
        raise SystemExit("Current session idle-tracking schema is missing")
    print(f"Upgrade path passed: {START_REVISION} -> {current}; seeded household preserved.")


if __name__ == "__main__":
    main()

"""add operational foundation

Revision ID: 20260813_0003
Revises: 20260813_0002
Create Date: 2026-08-13
"""
import sqlalchemy as sa

from alembic import op

revision = "20260813_0003"
down_revision = "20260813_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "service_heartbeats",
        sa.Column("service_name", sa.String(length=80), primary_key=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_attempts_email", "login_attempts", ["email"])
    op.create_index("ix_login_attempts_attempted_at", "login_attempts", ["attempted_at"])
    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("archive_name", sa.String(length=255)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("detail", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("backup_runs")
    op.drop_table("login_attempts")
    op.drop_table("service_heartbeats")
    op.drop_table("system_settings")

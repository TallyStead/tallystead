"""add financial plans and goals

Revision ID: 20260813_0016
Revises: 20260813_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0016"
down_revision: str | None = "20260813_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("financial_plans", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("created_by_user_id", sa.Uuid()), sa.Column("name", sa.String(160), nullable=False), sa.Column("template_key", sa.String(40)), sa.Column("currency_code", sa.String(3), nullable=False), sa.Column("debt_strategy", sa.String(24), nullable=False), sa.Column("effective_date", sa.Date(), nullable=False), sa.Column("end_date", sa.Date()), sa.Column("assumptions_json", sa.Text(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_financial_plans_household_id", "financial_plans", ["household_id"])
    op.create_index("ix_financial_plans_status", "financial_plans", ["status"])
    op.create_table("plan_versions", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("plan_id", sa.Uuid(), nullable=False), sa.Column("version_number", sa.Integer(), nullable=False), sa.Column("reason", sa.String(500), nullable=False), sa.Column("snapshot_json", sa.Text(), nullable=False), sa.Column("created_by_user_id", sa.Uuid()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["plan_id"], ["financial_plans.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("plan_id", "version_number", name="uq_plan_version_number"))
    op.create_index("ix_plan_versions_plan_id", "plan_versions", ["plan_id"])
    op.create_table("plan_steps", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("plan_id", sa.Uuid(), nullable=False), sa.Column("step_key", sa.String(40), nullable=False), sa.Column("position", sa.Integer(), nullable=False), sa.Column("title", sa.String(200), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("step_type", sa.String(32), nullable=False), sa.Column("target_minor", sa.Integer()), sa.Column("target_months", sa.Integer()), sa.Column("percentage_basis_points", sa.Integer()), sa.Column("status", sa.String(20), nullable=False), sa.Column("is_paused", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["plan_id"], ["financial_plans.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("plan_id", "step_key", name="uq_plan_step_key"))
    op.create_index("ix_plan_steps_plan_id", "plan_steps", ["plan_id"])
    op.create_table("financial_goals", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("plan_id", sa.Uuid(), nullable=False), sa.Column("step_id", sa.Uuid(), nullable=False), sa.Column("name", sa.String(200), nullable=False), sa.Column("goal_type", sa.String(32), nullable=False), sa.Column("target_minor", sa.Integer()), sa.Column("target_date", sa.Date()), sa.Column("linked_account_id", sa.Uuid()), sa.Column("status", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["plan_id"], ["financial_plans.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["step_id"], ["plan_steps.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["linked_account_id"], ["financial_accounts.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_financial_goals_household_id", "financial_goals", ["household_id"])
    op.create_index("ix_financial_goals_plan_id", "financial_goals", ["plan_id"])
    op.create_index("ix_financial_goals_step_id", "financial_goals", ["step_id"])
    op.create_table("goal_allocations", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("goal_id", sa.Uuid(), nullable=False), sa.Column("transaction_id", sa.Uuid()), sa.Column("allocation_type", sa.String(24), nullable=False), sa.Column("amount_minor", sa.Integer(), nullable=False), sa.Column("allocation_date", sa.Date(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("note", sa.String(500)), sa.Column("created_by_user_id", sa.Uuid()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["goal_id"], ["financial_goals.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["transaction_id"], ["ledger_transactions.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_goal_allocations_household_id", "goal_allocations", ["household_id"])
    op.create_index("ix_goal_allocations_goal_id", "goal_allocations", ["goal_id"])
    op.create_table("goal_reserves", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("household_id", sa.Uuid(), nullable=False), sa.Column("plan_id", sa.Uuid(), nullable=False), sa.Column("goal_id", sa.Uuid(), nullable=False), sa.Column("planner_snapshot_id", sa.Uuid()), sa.Column("as_of_date", sa.Date(), nullable=False), sa.Column("requested_minor", sa.Integer(), nullable=False), sa.Column("allocated_minor", sa.Integer(), nullable=False), sa.Column("shortfall_minor", sa.Integer(), nullable=False), sa.Column("explanation", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["plan_id"], ["financial_plans.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["goal_id"], ["financial_goals.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["planner_snapshot_id"], ["planner_snapshots.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_goal_reserves_household_id", "goal_reserves", ["household_id"])
    op.create_index("ix_goal_reserves_plan_id", "goal_reserves", ["plan_id"])
    op.create_index("ix_goal_reserves_goal_id", "goal_reserves", ["goal_id"])


def downgrade() -> None:
    op.drop_table("goal_reserves")
    op.drop_table("goal_allocations")
    op.drop_table("financial_goals")
    op.drop_table("plan_steps")
    op.drop_table("plan_versions")
    op.drop_table("financial_plans")

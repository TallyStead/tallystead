import json
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.dependencies import DbSession, current_membership, current_user, require_roles
from app.models import (
    AuditEvent,
    Debt,
    FinancialAccount,
    FinancialGoal,
    FinancialPlan,
    GoalAllocation,
    GoalReserve,
    LedgerTransaction,
    Membership,
    PlanStep,
    PlanVersion,
    Role,
    User,
    utc_now,
)
from app.plans import BABY_STEPS, PLAN_RULE_VERSION, is_mortgage, plan_projection

router = APIRouter(prefix="/v1", tags=["plans"])
writer = require_roles(Role.OWNER, Role.MANAGER)


class PlanCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    currency_code: str = Field(default="USD", pattern="^(USD|CAD|MXN)$")
    effective_date: date
    debt_strategy: str = Field(default="smallest_balance", pattern="^(smallest_balance|highest_rate|custom)$")


class PlanUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    debt_strategy: str | None = Field(default=None, pattern="^(smallest_balance|highest_rate|custom)$")
    status: str | None = Field(default=None, pattern="^(active|paused|archived)$")
    custom_debt_order: list[UUID] | None = Field(default=None, max_length=100)
    reason: str = Field(min_length=3, max_length=500)


class StepUpdateRequest(BaseModel):
    position: int | None = Field(default=None, ge=1, le=100)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    target_minor: int | None = Field(default=None, ge=0)
    target_months: int | None = Field(default=None, ge=3, le=6)
    percentage_basis_points: int | None = Field(default=None, ge=0, le=10000)
    status: str | None = Field(default=None, pattern="^(pending|active|complete|skipped)$")
    is_paused: bool | None = None
    reason: str = Field(min_length=3, max_length=500)


class GoalUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_minor: int | None = Field(default=None, ge=0)
    target_date: date | None = None
    linked_account_id: UUID | None = None
    status: str | None = Field(default=None, pattern="^(active|complete|paused|cancelled)$")
    reason: str = Field(min_length=3, max_length=500)


class AllocationCreateRequest(BaseModel):
    allocation_type: Literal["recurring", "one_time", "transfer"]
    amount_minor: int = Field(gt=0)
    allocation_date: date
    status: Literal["planned", "confirmed"] = "planned"
    transaction_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class ProjectionRequest(BaseModel):
    as_of_date: date
    horizon_days: int = Field(default=30, ge=1, le=365)
    cash_buffer_minor: int = Field(default=0, ge=0)
    include_pending: bool = True
    save_reserves: bool = False


def owned_plan(db: DbSession, household_id: UUID, plan_id: UUID) -> FinancialPlan:
    item = db.scalar(select(FinancialPlan).where(FinancialPlan.id == plan_id, FinancialPlan.household_id == household_id))
    if not item:
        raise HTTPException(status_code=404, detail="Financial plan not found")
    return item


def plan_snapshot(db: DbSession, plan: FinancialPlan) -> dict:
    steps = db.scalars(select(PlanStep).where(PlanStep.plan_id == plan.id).order_by(PlanStep.position, PlanStep.id)).all()
    goals = db.scalars(select(FinancialGoal).where(FinancialGoal.plan_id == plan.id)).all()
    return {
        "plan": {"name": plan.name, "currency_code": plan.currency_code, "debt_strategy": plan.debt_strategy, "effective_date": plan.effective_date.isoformat(), "end_date": plan.end_date.isoformat() if plan.end_date else None, "status": plan.status, "assumptions": json.loads(plan.assumptions_json or "{}")},
        "steps": [{"step_id": str(item.id), "step_key": item.step_key, "position": item.position, "title": item.title, "description": item.description, "step_type": item.step_type, "target_minor": item.target_minor, "target_months": item.target_months, "percentage_basis_points": item.percentage_basis_points, "status": item.status, "is_paused": item.is_paused} for item in steps],
        "goals": [{"goal_id": str(item.id), "step_id": str(item.step_id), "name": item.name, "goal_type": item.goal_type, "target_minor": item.target_minor, "target_date": item.target_date.isoformat() if item.target_date else None, "linked_account_id": str(item.linked_account_id) if item.linked_account_id else None, "status": item.status} for item in goals],
    }


def version_plan(db: DbSession, plan: FinancialPlan, actor: User, reason: str) -> None:
    number = (db.scalar(select(func.max(PlanVersion.version_number)).where(PlanVersion.plan_id == plan.id)) or 0) + 1
    db.add(PlanVersion(plan_id=plan.id, version_number=number, reason=reason, snapshot_json=json.dumps(plan_snapshot(db, plan), sort_keys=True), created_by_user_id=actor.id))


def plan_response(db: DbSession, plan: FinancialPlan, detail: bool = False) -> dict:
    version_count = db.scalar(select(func.count()).select_from(PlanVersion).where(PlanVersion.plan_id == plan.id)) or 0
    result = {"plan_id": str(plan.id), "name": plan.name, "template_key": plan.template_key, "currency_code": plan.currency_code, "debt_strategy": plan.debt_strategy, "effective_date": plan.effective_date.isoformat(), "end_date": plan.end_date.isoformat() if plan.end_date else None, "status": plan.status, "version_count": version_count, "rule_version": PLAN_RULE_VERSION, "created_at": plan.created_at.isoformat(), "updated_at": plan.updated_at.isoformat()}
    if detail:
        snapshot = plan_snapshot(db, plan)
        allocations = db.execute(select(GoalAllocation, FinancialGoal).join(FinancialGoal, FinancialGoal.id == GoalAllocation.goal_id).where(FinancialGoal.plan_id == plan.id).order_by(GoalAllocation.allocation_date.desc())).all()
        versions = db.scalars(select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version_number.desc())).all()
        result.update(snapshot)
        result["allocations"] = [{"allocation_id": str(item.id), "goal_id": str(item.goal_id), "goal_name": goal.name, "allocation_type": item.allocation_type, "amount_minor": item.amount_minor, "allocation_date": item.allocation_date.isoformat(), "status": item.status, "transaction_id": str(item.transaction_id) if item.transaction_id else None, "note": item.note} for item, goal in allocations]
        result["versions"] = [{"version_id": str(item.id), "version_number": item.version_number, "reason": item.reason, "created_at": item.created_at.isoformat()} for item in versions]
    return result


def create_steps(db: DbSession, plan: FinancialPlan) -> None:
    debts = db.scalars(select(Debt).where(Debt.household_id == plan.household_id, Debt.currency_code == plan.currency_code, Debt.is_active.is_(True))).all()
    for position, definition in enumerate(BABY_STEPS, start=1):
        step = PlanStep(plan_id=plan.id, step_key=definition["key"], position=position, title=definition["title"], description=definition["description"], step_type=definition["type"], target_minor=definition.get("target_minor"), target_months=definition.get("target_months"), percentage_basis_points=definition.get("percentage_basis_points"), status="active" if position == 1 else "pending")
        db.add(step)
        db.flush()
        goal_target = definition.get("target_minor")
        if definition["type"] == "debt":
            total = sum(item.balance_minor for item in debts if not is_mortgage(db, item))
            goal_target = total or None
        elif definition["type"] == "mortgage":
            total = sum(item.balance_minor for item in debts if is_mortgage(db, item))
            goal_target = total or None
        db.add(FinancialGoal(household_id=plan.household_id, plan_id=plan.id, step_id=step.id, name=definition["title"], goal_type=definition["type"], target_minor=goal_target))


@router.get("/plans/templates")
def list_templates() -> list[dict]:
    return [{"template_key": "baby_steps", "name": "Seven baby steps", "description": "A configurable seven-step household plan.", "steps": BABY_STEPS}]


@router.get("/plans")
def list_plans(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[dict]:
    items = db.scalars(select(FinancialPlan).where(FinancialPlan.household_id == membership.household_id).order_by(FinancialPlan.status, FinancialPlan.updated_at.desc())).all()
    return [plan_response(db, item) for item in items]


@router.post("/plans", status_code=201)
def create_plan(request: PlanCreateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    active = db.scalar(select(FinancialPlan).where(FinancialPlan.household_id == membership.household_id, FinancialPlan.status == "active"))
    if active:
        active.status = "paused"
    plan = FinancialPlan(household_id=membership.household_id, created_by_user_id=actor.id, name=request.name.strip(), template_key="baby_steps", currency_code=request.currency_code, debt_strategy=request.debt_strategy, effective_date=request.effective_date, assumptions_json="{}")
    db.add(plan)
    db.flush()
    create_steps(db, plan)
    db.flush()
    version_plan(db, plan, actor, "Created from the configurable seven baby steps template")
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="plan.created", resource_type="financial_plan", resource_id=str(plan.id), detail="template:baby_steps;previous_active_paused:" + str(bool(active)).lower()))
    db.commit()
    return plan_response(db, plan, True)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: UUID, db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> dict:
    return plan_response(db, owned_plan(db, membership.household_id, plan_id), True)


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: UUID, request: PlanUpdateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    plan = owned_plan(db, membership.household_id, plan_id)
    values = request.model_dump(exclude_unset=True, exclude={"reason", "custom_debt_order"})
    if values.get("status") == "active":
        db.execute(FinancialPlan.__table__.update().where(FinancialPlan.household_id == membership.household_id, FinancialPlan.id != plan.id, FinancialPlan.status == "active").values(status="paused"))
    for key, value in values.items():
        setattr(plan, key, value.strip() if key == "name" else value)
    if request.custom_debt_order is not None:
        if len(set(request.custom_debt_order)) != len(request.custom_debt_order):
            raise HTTPException(status_code=422, detail="Custom debt order cannot contain duplicates")
        valid_debts = set(db.scalars(select(Debt.id).where(Debt.household_id == membership.household_id, Debt.currency_code == plan.currency_code, Debt.id.in_(request.custom_debt_order))).all()) if request.custom_debt_order else set()
        if valid_debts != set(request.custom_debt_order):
            raise HTTPException(status_code=422, detail="Custom debt order contains an unavailable debt")
        assumptions = json.loads(plan.assumptions_json or "{}")
        assumptions["custom_debt_order"] = [str(item) for item in request.custom_debt_order]
        plan.assumptions_json = json.dumps(assumptions, sort_keys=True)
    plan.updated_at = utc_now()
    db.flush()
    version_plan(db, plan, actor, request.reason)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="plan.updated", resource_type="financial_plan", resource_id=str(plan.id), detail=request.reason))
    db.commit()
    return plan_response(db, plan, True)


@router.patch("/plans/{plan_id}/steps/{step_id}")
def update_step(plan_id: UUID, step_id: UUID, request: StepUpdateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    plan = owned_plan(db, membership.household_id, plan_id)
    step = db.scalar(select(PlanStep).where(PlanStep.id == step_id, PlanStep.plan_id == plan.id))
    if not step:
        raise HTTPException(status_code=404, detail="Plan step not found")
    values = request.model_dump(exclude_unset=True, exclude={"reason"})
    new_position = values.get("position")
    if new_position is not None and new_position != step.position:
        old_position = step.position
        if new_position < old_position:
            db.execute(PlanStep.__table__.update().where(PlanStep.plan_id == plan.id, PlanStep.id != step.id, PlanStep.position >= new_position, PlanStep.position < old_position).values(position=PlanStep.position + 1))
        else:
            db.execute(PlanStep.__table__.update().where(PlanStep.plan_id == plan.id, PlanStep.id != step.id, PlanStep.position <= new_position, PlanStep.position > old_position).values(position=PlanStep.position - 1))
    for key, value in values.items():
        setattr(step, key, value)
    step.updated_at = utc_now()
    db.flush()
    version_plan(db, plan, actor, request.reason)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="plan.step_updated", resource_type="plan_step", resource_id=str(step.id), detail=request.reason))
    db.commit()
    return plan_response(db, plan, True)


@router.patch("/plans/{plan_id}/goals/{goal_id}")
def update_goal(plan_id: UUID, goal_id: UUID, request: GoalUpdateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    plan = owned_plan(db, membership.household_id, plan_id)
    goal = db.scalar(select(FinancialGoal).where(FinancialGoal.id == goal_id, FinancialGoal.plan_id == plan.id))
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    values = request.model_dump(exclude_unset=True, exclude={"reason"})
    if values.get("linked_account_id"):
        account = db.scalar(select(FinancialAccount).where(FinancialAccount.id == values["linked_account_id"], FinancialAccount.household_id == membership.household_id, FinancialAccount.currency_code == plan.currency_code))
        if not account:
            raise HTTPException(status_code=422, detail="Linked goal account must belong to this household and use the plan currency")
    for key, value in values.items():
        setattr(goal, key, value.strip() if key == "name" else value)
    goal.updated_at = utc_now()
    db.flush()
    version_plan(db, plan, actor, request.reason)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="goal.updated", resource_type="financial_goal", resource_id=str(goal.id), detail=request.reason))
    db.commit()
    return plan_response(db, plan, True)


@router.post("/plans/{plan_id}/goals/{goal_id}/allocations", status_code=201)
def create_allocation(plan_id: UUID, goal_id: UUID, request: AllocationCreateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    plan = owned_plan(db, membership.household_id, plan_id)
    goal = db.scalar(select(FinancialGoal).where(FinancialGoal.id == goal_id, FinancialGoal.plan_id == plan.id))
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    if request.status == "confirmed" and not request.transaction_id:
        raise HTTPException(status_code=422, detail="Confirmed progress requires a ledger transaction")
    if request.transaction_id:
        transaction = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == request.transaction_id, LedgerTransaction.household_id == membership.household_id, LedgerTransaction.currency_code == plan.currency_code, LedgerTransaction.status == "posted"))
        already_applied = db.scalar(select(func.coalesce(func.sum(GoalAllocation.amount_minor), 0)).where(GoalAllocation.household_id == membership.household_id, GoalAllocation.transaction_id == request.transaction_id, GoalAllocation.status == "confirmed")) or 0
        if not transaction or already_applied + request.amount_minor > abs(transaction.amount_minor):
            raise HTTPException(status_code=422, detail="Allocation evidence must be a posted household transaction with enough value in the plan currency")
    item = GoalAllocation(household_id=membership.household_id, goal_id=goal.id, transaction_id=request.transaction_id, allocation_type=request.allocation_type, amount_minor=request.amount_minor, allocation_date=request.allocation_date, status=request.status, note=request.note, created_by_user_id=actor.id)
    db.add(item)
    db.flush()
    version_plan(db, plan, actor, f"Added {request.status} {request.allocation_type} allocation")
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="goal.allocation_created", resource_type="goal_allocation", resource_id=str(item.id), detail=f"status:{item.status};amount:{item.amount_minor};ledger_unchanged:true"))
    db.commit()
    return {"allocation_id": str(item.id), "status": item.status}


@router.delete("/plans/{plan_id}/allocations/{allocation_id}", status_code=204)
def delete_allocation(plan_id: UUID, allocation_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> Response:
    plan = owned_plan(db, membership.household_id, plan_id)
    item = db.scalar(select(GoalAllocation).join(FinancialGoal, FinancialGoal.id == GoalAllocation.goal_id).where(GoalAllocation.id == allocation_id, FinancialGoal.plan_id == plan.id))
    if not item:
        raise HTTPException(status_code=404, detail="Goal allocation not found")
    db.delete(item)
    db.flush()
    version_plan(db, plan, actor, "Removed a goal allocation")
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="goal.allocation_deleted", resource_type="goal_allocation", resource_id=str(item.id), detail="ledger_unchanged:true"))
    db.commit()
    return Response(status_code=204)


@router.post("/plans/{plan_id}/projection")
def project_plan(plan_id: UUID, request: ProjectionRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(current_membership)]) -> dict:
    plan = owned_plan(db, membership.household_id, plan_id)
    result = plan_projection(db, plan, as_of_date=request.as_of_date, horizon_days=request.horizon_days, cash_buffer_minor=request.cash_buffer_minor, include_pending=request.include_pending)
    if request.save_reserves:
        if membership.role not in {Role.OWNER.value, Role.MANAGER.value}:
            raise HTTPException(status_code=403, detail="Only an Owner or Manager can save goal reserves")
        db.execute(delete(GoalReserve).where(GoalReserve.plan_id == plan.id, GoalReserve.as_of_date == request.as_of_date))
        for step in result["steps"]:
            db.add(GoalReserve(household_id=membership.household_id, plan_id=plan.id, goal_id=UUID(step["goal_id"]), as_of_date=request.as_of_date, requested_minor=(step["recommended_reserve_minor"] + step["shortfall_minor"]), allocated_minor=step["recommended_reserve_minor"], shortfall_minor=step["shortfall_minor"], explanation="Allocated only after required Cash Planner obligations and the protected buffer."))
        db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="plan.reserves_saved", resource_type="financial_plan", resource_id=str(plan.id), detail=f"as_of:{request.as_of_date};allocated:{result['allocated_to_goals_minor']};shortfall:{result['overcommitment_minor']};ledger_unchanged:true"))
        db.commit()
    return result

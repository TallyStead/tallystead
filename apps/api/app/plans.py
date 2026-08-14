import json
import math
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ledger import account_balance
from app.models import (
    BillInstance,
    BillPaymentLink,
    Debt,
    FinancialAccount,
    FinancialGoal,
    FinancialPlan,
    GoalAllocation,
    PlanStep,
)
from app.planner import calculate_forecast, collect_planner_input
from app.reporting import ReportFilters, spending_report

PLAN_RULE_VERSION = "financial-plan-v1"

BABY_STEPS = [
    {"key": "starter_emergency_fund", "title": "Save $1,000", "description": "Build a starter emergency fund.", "type": "savings", "target_minor": 100_000},
    {"key": "debt_snowball", "title": "Pay off all non-mortgage debt", "description": "Protect every minimum payment, then apply extra payoff from smallest balance to largest.", "type": "debt", "target_minor": None},
    {"key": "full_emergency_fund", "title": "Save 3–6 months of expenses", "description": "Build a fully funded emergency fund using the household's observed monthly spending.", "type": "emergency_months", "target_minor": None, "target_months": 3},
    {"key": "retirement_15_percent", "title": "Invest 15% for retirement", "description": "Track a retirement contribution target based on observed household income.", "type": "income_percentage", "target_minor": None, "percentage_basis_points": 1500},
    {"key": "childrens_college", "title": "Save for children's college", "description": "Set household-defined college goals, including 529 plans when applicable.", "type": "college", "target_minor": None},
    {"key": "pay_off_home", "title": "Pay off the home early", "description": "Track additional progress toward the household mortgage balance.", "type": "mortgage", "target_minor": None},
    {"key": "build_wealth_and_give", "title": "Build wealth and give", "description": "Invest further, give generously, and define the legacy the household wants to leave.", "type": "legacy", "target_minor": None},
]


def is_mortgage(db: Session, debt: Debt) -> bool:
    if not debt.account_id:
        return "mortgage" in debt.name.casefold() or "home" in debt.name.casefold()
    account = db.get(FinancialAccount, debt.account_id)
    return bool(account and account.account_type in {"mortgage", "home_loan"})


def current_debt_balance(db: Session, debt: Debt) -> int:
    paid = db.scalar(select(func.coalesce(func.sum(BillPaymentLink.amount_minor), 0)).join(BillInstance, BillInstance.id == BillPaymentLink.bill_instance_id).where(BillInstance.debt_id == debt.id)) or 0
    return max(0, debt.balance_minor - paid)


def observed_monthly(db: Session, household_id, currency_code: str, as_of: date) -> tuple[int, int]:
    start = as_of - timedelta(days=89)
    report = spending_report(db, household_id, ReportFilters(date_from=start, date_to=as_of, currency_code=currency_code, ownership_scope="household"))
    return report["totals"]["spending_minor"] // 3, report["totals"]["income_minor"] // 3


def actual_goal_funding(db: Session, goal: FinancialGoal, as_of: date) -> tuple[int, list[str]]:
    evidence = []
    funded = 0
    if goal.linked_account_id:
        account = db.get(FinancialAccount, goal.linked_account_id)
        if account and account.household_id == goal.household_id:
            funded = max(0, account_balance(db, account, as_of, include_pending=False))
            evidence.append(f"Linked account balance as of {as_of}")
    allocations = db.scalars(select(GoalAllocation).where(GoalAllocation.goal_id == goal.id, GoalAllocation.status == "confirmed", GoalAllocation.allocation_date <= as_of)).all()
    if allocations and not goal.linked_account_id:
        funded = sum(item.amount_minor for item in allocations)
        evidence.append(f"{len(allocations)} confirmed allocation(s)")
    return funded, evidence


def resolved_target(db: Session, step: PlanStep, goal: FinancialGoal, monthly_spending: int, monthly_income: int, debts: list[Debt]) -> tuple[int | None, str]:
    if goal.target_minor is not None:
        return goal.target_minor, "Household-configured target"
    if step.target_minor is not None:
        return step.target_minor, "Plan-step target"
    if step.step_type == "debt":
        return sum(current_debt_balance(db, item) for item in debts if not is_mortgage(db, item)), "Current active non-mortgage debt balances"
    if step.step_type == "mortgage":
        return sum(current_debt_balance(db, item) for item in debts if is_mortgage(db, item)), "Current active mortgage balances"
    if step.step_type == "emergency_months":
        return monthly_spending * (step.target_months or 3), f"{step.target_months or 3} × observed average monthly spending"
    if step.step_type == "income_percentage":
        return monthly_income * (step.percentage_basis_points or 1500) // 10_000, f"{(step.percentage_basis_points or 1500) / 100:g}% of observed average monthly income"
    return None, "Household target required"


def debt_order(db: Session, debts: list[Debt], strategy: str, custom_order: list[str]) -> list[Debt]:
    non_mortgage = [item for item in debts if not is_mortgage(db, item)]
    if strategy == "highest_rate":
        return sorted(non_mortgage, key=lambda item: (-item.apr_basis_points, current_debt_balance(db, item), item.name.casefold()))
    if strategy == "custom":
        rank = {value: index for index, value in enumerate(custom_order)}
        return sorted(non_mortgage, key=lambda item: (rank.get(str(item.id), len(rank)), item.name.casefold()))
    return sorted(non_mortgage, key=lambda item: (current_debt_balance(db, item), item.name.casefold()))


def plan_projection(db: Session, plan: FinancialPlan, *, as_of_date: date, horizon_days: int, cash_buffer_minor: int, include_pending: bool) -> dict[str, Any]:
    planner_input = collect_planner_input(db, plan.household_id, as_of_date=as_of_date, horizon_days=horizon_days, currency_code=plan.currency_code, cash_buffer_minor=cash_buffer_minor, include_pending=include_pending)
    planner = calculate_forecast(planner_input)
    available = planner["safe_to_spend_minor"]
    monthly_spending, monthly_income = observed_monthly(db, plan.household_id, plan.currency_code, as_of_date)
    debts = db.scalars(select(Debt).where(Debt.household_id == plan.household_id, Debt.currency_code == plan.currency_code, Debt.is_active.is_(True))).all()
    assumptions = json.loads(plan.assumptions_json or "{}")
    steps = db.scalars(select(PlanStep).where(PlanStep.plan_id == plan.id).order_by(PlanStep.position, PlanStep.id)).all()
    output = []
    total_allocated = 0
    total_shortfall = 0
    for step in steps:
        goal = db.scalar(select(FinancialGoal).where(FinancialGoal.step_id == step.id))
        if not goal:
            continue
        target, target_basis = resolved_target(db, step, goal, monthly_spending, monthly_income, debts)
        actual, evidence = actual_goal_funding(db, goal, as_of_date)
        if step.step_type in {"debt", "mortgage"} and target is not None:
            current_balance = sum(current_debt_balance(db, item) for item in debts if is_mortgage(db, item) == (step.step_type == "mortgage"))
            actual = max(0, target - current_balance)
            evidence = ["Original target less current active debt balances"]
        remaining = max(0, (target or 0) - actual) if target is not None else None
        requested = 0 if step.is_paused or step.status in {"complete", "skipped"} or target is None else remaining or 0
        allocated = min(requested, available)
        shortfall = requested - allocated
        available -= allocated
        total_allocated += allocated
        total_shortfall += shortfall
        progress = 100 if target == 0 and step.step_type in {"debt", "mortgage"} else (min(100, actual * 100 // target) if target else 0)
        planned_rows = db.scalars(select(GoalAllocation).where(GoalAllocation.goal_id == goal.id, GoalAllocation.status == "planned").order_by(GoalAllocation.allocation_date)).all()
        forecast_allocations = sum(item.amount_minor for item in planned_rows)
        recurring_minor = sum(item.amount_minor for item in planned_rows if item.allocation_type == "recurring")
        one_time_minor = sum(item.amount_minor for item in planned_rows if item.allocation_type != "recurring")
        forecast_completion_date = None
        if remaining == 0:
            forecast_completion_date = as_of_date.isoformat()
        elif remaining is not None and one_time_minor >= remaining and planned_rows:
            forecast_completion_date = max(item.allocation_date for item in planned_rows).isoformat()
        elif remaining is not None and recurring_minor > 0:
            months = math.ceil(max(0, remaining - one_time_minor) / recurring_minor)
            forecast_completion_date = (as_of_date + timedelta(days=30 * months)).isoformat()
        output.append({
            "step_id": str(step.id), "goal_id": str(goal.id), "step_key": step.step_key, "position": step.position,
            "title": step.title, "description": step.description, "step_type": step.step_type, "status": step.status,
            "is_paused": step.is_paused, "target_minor": target, "target_basis": target_basis,
            "actual_funded_minor": actual, "actual_evidence": evidence, "planned_allocation_minor": forecast_allocations,
            "remaining_minor": remaining, "recommended_reserve_minor": allocated, "shortfall_minor": shortfall,
            "progress_percent": progress, "target_months": step.target_months, "percentage_basis_points": step.percentage_basis_points,
            "forecast_completion_date": forecast_completion_date, "needs_configuration": target is None,
        })
    ordered_debts = debt_order(db, debts, plan.debt_strategy, assumptions.get("custom_debt_order", []))
    return {
        "rule_version": PLAN_RULE_VERSION, "plan_id": str(plan.id), "as_of_date": as_of_date.isoformat(),
        "currency_code": plan.currency_code, "planner_input_hash": planner["input_hash"],
        "safe_to_spend_before_goals_minor": planner["safe_to_spend_minor"], "allocated_to_goals_minor": total_allocated,
        "safe_to_spend_after_goals_minor": available, "overcommitment_minor": total_shortfall,
        "planner_shortfalls": planner["shortfalls"], "steps": output,
        "debt_strategy": plan.debt_strategy,
        "debt_order": [{"debt_id": str(item.id), "name": item.name, "balance_minor": current_debt_balance(db, item), "apr_basis_points": item.apr_basis_points, "minimum_payment_minor": item.minimum_payment_minor} for item in ordered_debts],
        "assumptions": [
            "Required bills and debt minimums are protected by the Cash Planner before goal reserves.",
            "Goal reserves are allocations only; they do not create cash or post ledger transactions.",
            "Actual progress comes from linked account balances, confirmed transaction evidence, or current debt balances.",
            "Forecast allocations remain separate from actual progress.",
        ],
    }

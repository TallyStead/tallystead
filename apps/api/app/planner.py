import hashlib
import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ledger import account_balance
from app.models import BillInstance, BillPaymentLink, FinancialAccount, IncomeEvent

RULE_VERSION = "cash-planner-v1"


def cautious_income_amount(event: IncomeEvent) -> int:
    if event.minimum_amount_minor is not None:
        return event.minimum_amount_minor
    return event.expected_amount_minor * event.confidence_percent // 100


def cautious_bill_amount(instance: BillInstance) -> int:
    return instance.maximum_amount_minor or instance.expected_amount_minor


def collect_planner_input(
    db: Session,
    household_id,
    *,
    as_of_date: date,
    horizon_days: int,
    currency_code: str,
    cash_buffer_minor: int,
    include_pending: bool,
) -> dict[str, Any]:
    horizon_date = as_of_date + timedelta(days=horizon_days)
    all_accounts = db.scalars(
        select(FinancialAccount).where(
            FinancialAccount.household_id == household_id,
            FinancialAccount.is_archived.is_(False),
        ).order_by(FinancialAccount.name, FinancialAccount.id)
    ).all()
    accounts = []
    excluded = []
    for account in all_accounts:
        eligible = (
            account.currency_code == currency_code
            and account.include_in_planner
            and account.ownership_scope == "household"
            and account.balance_nature == "asset"
            and account.liquidity == "spendable"
        )
        if eligible:
            accounts.append({
                "account_id": str(account.id),
                "name": account.name,
                "balance_minor": account_balance(db, account, as_of_date, include_pending),
            })
        else:
            reason = "different currency" if account.currency_code != currency_code else "not eligible for household spendable planning cash"
            excluded.append(f"{account.name}: {reason}")

    income_rows = db.scalars(
        select(IncomeEvent).where(
            IncomeEvent.household_id == household_id,
            IncomeEvent.currency_code == currency_code,
            IncomeEvent.expected_date >= as_of_date,
            IncomeEvent.expected_date <= horizon_date,
            IncomeEvent.status == "expected",
        ).order_by(IncomeEvent.expected_date, IncomeEvent.name, IncomeEvent.id)
    ).all()
    income = [{
        "item_id": str(item.id),
        "name": item.name,
        "event_date": item.expected_date.isoformat(),
        "amount_minor": cautious_income_amount(item),
        "expected_amount_minor": item.expected_amount_minor,
        "confidence_percent": item.confidence_percent,
        "is_variable": item.minimum_amount_minor is not None or item.maximum_amount_minor is not None or item.confidence_percent < 100,
    } for item in income_rows]
    missed_income_rows = db.scalars(
        select(IncomeEvent).where(
            IncomeEvent.household_id == household_id,
            IncomeEvent.currency_code == currency_code,
            IncomeEvent.expected_date < as_of_date,
            IncomeEvent.status == "expected",
        ).order_by(IncomeEvent.expected_date, IncomeEvent.name, IncomeEvent.id)
    ).all()

    bill_rows = db.scalars(
        select(BillInstance).where(
            BillInstance.household_id == household_id,
            BillInstance.currency_code == currency_code,
            BillInstance.due_date <= horizon_date,
            BillInstance.status != "skipped",
        ).order_by(BillInstance.due_date, BillInstance.priority, BillInstance.name, BillInstance.id)
    ).all()
    bills = []
    for item in bill_rows:
        paid = db.scalar(select(func.coalesce(func.sum(BillPaymentLink.amount_minor), 0)).where(BillPaymentLink.bill_instance_id == item.id)) or 0
        required = max(0, cautious_bill_amount(item) - paid)
        if required == 0:
            continue
        bills.append({
            "item_id": str(item.id),
            "name": item.name,
            "due_date": item.due_date.isoformat(),
            "event_date": max(item.due_date, as_of_date).isoformat(),
            "amount_minor": required,
            "expected_amount_minor": max(0, item.expected_amount_minor - paid),
            "is_variable": item.maximum_amount_minor is not None or item.minimum_amount_minor is not None,
            "is_essential": item.is_essential,
            "is_debt": item.debt_id is not None,
            "priority": item.priority,
            "is_overdue": item.due_date < as_of_date,
        })

    return {
        "rule_version": RULE_VERSION,
        "as_of_date": as_of_date.isoformat(),
        "horizon_date": horizon_date.isoformat(),
        "currency_code": currency_code,
        "cash_buffer_minor": cash_buffer_minor,
        "include_pending": include_pending,
        "accounts": accounts,
        "excluded_accounts": sorted(excluded),
        "income": income,
        "missed_income": [{"item_id": str(item.id), "name": item.name, "expected_date": item.expected_date.isoformat()} for item in missed_income_rows],
        "bills": bills,
    }


def input_hash(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def calculate_forecast(snapshot: dict[str, Any]) -> dict[str, Any]:
    planning_balance = sum(item["balance_minor"] for item in snapshot["accounts"])
    buffer = snapshot["cash_buffer_minor"]
    available = max(0, planning_balance - buffer)
    events = []
    warnings = []
    for item in snapshot.get("missed_income", []):
        warnings.append(f"{item['name']} was expected on {item['expected_date']} but is still missing; it is not counted as cash.")
    for item in snapshot["income"]:
        events.append({**item, "item_type": "income"})
        if item["is_variable"]:
            warnings.append(f"{item['name']} uses a cautious income estimate at {item['confidence_percent']}% confidence.")
    for item in snapshot["bills"]:
        events.append({**item, "item_type": "debt" if item["is_debt"] else "bill"})
        if item["is_variable"]:
            warnings.append(f"{item['name']} uses its configured maximum amount.")
        if item["is_overdue"]:
            warnings.append(f"{item['name']} is overdue and is reserved before future obligations.")

    def order(item: dict[str, Any]) -> tuple:
        if item["item_type"] == "income":
            rank = 0
        elif item.get("is_overdue") and item.get("is_essential"):
            rank = 1
        elif item["item_type"] == "debt":
            rank = 2
        elif item.get("is_essential"):
            rank = 3
        else:
            rank = 4
        return (item["event_date"], rank, item.get("priority", 9), item["name"], item["item_id"])

    balance = planning_balance
    minimum_balance = balance
    timeline = []
    reserves = []
    shortfalls = []
    for item in sorted(events, key=order):
        event_date = item["event_date"]
        if item["item_type"] == "income":
            amount = item["amount_minor"]
            balance += amount
            explanation = f"Cautious expected income ({item['confidence_percent']}% confidence); it is forecast cash, not a confirmed balance."
            confidence = item["confidence_percent"]
        else:
            required = item["amount_minor"]
            spendable_at_date = max(0, balance - buffer)
            funded = min(required, spendable_at_date)
            shortage = required - funded
            balance -= required
            amount = -required
            confidence = None
            status = "shortfall" if shortage else "funded"
            explanation = "Uses the variable maximum estimate." if item["is_variable"] else "Uses the unpaid expected amount."
            if item["is_overdue"]:
                explanation += " Overdue items are applied at the as-of date."
            reserves.append({
                "bill_instance_id": item["item_id"],
                "name": item["name"],
                "due_date": item["due_date"],
                "required_minor": required,
                "funded_minor": funded,
                "shortfall_minor": shortage,
                "status": status,
                "explanation": f"{explanation} Reserve is an allocation, not a separate bank balance.",
            })
            if shortage:
                shortfalls.append({
                    "event_date": event_date,
                    "amount_minor": shortage,
                    "obligation_name": item["name"],
                    "explanation": f"Projected spendable cash cannot cover {item['name']} while preserving the configured buffer.",
                })
        minimum_balance = min(minimum_balance, balance)
        timeline.append({
            "item_type": item["item_type"],
            "item_id": item["item_id"],
            "name": item["name"],
            "event_date": event_date,
            "amount_minor": amount,
            "projected_balance_minor": balance,
            "confidence_percent": confidence,
            "explanation": explanation,
        })

    safe_to_spend = max(0, minimum_balance - buffer)
    reserved_now = max(0, min(planning_balance, planning_balance - buffer - safe_to_spend))
    return {
        "snapshot_id": None,
        "rule_version": snapshot["rule_version"],
        "input_hash": input_hash(snapshot),
        "as_of_date": snapshot["as_of_date"],
        "horizon_date": snapshot["horizon_date"],
        "currency_code": snapshot["currency_code"],
        "include_pending": snapshot["include_pending"],
        "cash_buffer_minor": buffer,
        "planning_balance_minor": planning_balance,
        "available_to_plan_minor": available,
        "safe_to_spend_minor": safe_to_spend,
        "reserved_now_minor": reserved_now,
        "expected_income_minor": sum(item["amount_minor"] for item in snapshot["income"]),
        "required_outflow_minor": sum(item["amount_minor"] for item in snapshot["bills"]),
        "ending_balance_minor": balance,
        "accounts": snapshot["accounts"],
        "excluded_accounts": snapshot["excluded_accounts"],
        "timeline": timeline,
        "reserves": reserves,
        "shortfalls": shortfalls,
        "warnings": sorted(set(warnings)),
        "assumptions": [
            "Only active household-owned spendable asset accounts explicitly enabled for planning are included.",
            "Credit availability and non-planning currencies are excluded.",
            "Pending transactions reduce planning cash when the pending policy is enabled.",
            "Expected income never changes a confirmed account balance.",
            "Safe to spend is the lowest projected balance through the horizon minus the protected cash buffer.",
        ],
    }

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BillInstance,
    BillPaymentLink,
    Category,
    FinancialAccount,
    LedgerTransaction,
    Merchant,
    TransactionSplit,
    TransferLink,
)

REPORT_RULE_VERSION = "spending-report-v1"
INVESTMENT_TYPES = {"contribution", "employer_match", "purchase", "sale", "dividend", "interest", "fee", "withdrawal", "market_adjustment"}


@dataclass(frozen=True)
class ReportFilters:
    date_from: date
    date_to: date
    currency_code: str
    ownership_scope: str = "household"
    include_pending: bool = False
    account_id: UUID | None = None
    category_id: UUID | None = None
    merchant_id: UUID | None = None


def _period(db: Session, household_id: UUID, filters: ReportFilters) -> dict:
    accounts = db.scalars(select(FinancialAccount).where(FinancialAccount.household_id == household_id)).all()
    account_map = {item.id: item for item in accounts}
    allowed_accounts = {
        item.id for item in accounts
        if item.currency_code == filters.currency_code
        and (filters.ownership_scope == "all" or item.ownership_scope == filters.ownership_scope)
        and (filters.account_id is None or item.id == filters.account_id)
    }
    statuses = ["posted", "reversed"] + (["pending"] if filters.include_pending else [])
    transactions = db.scalars(
        select(LedgerTransaction).where(
            LedgerTransaction.household_id == household_id,
            LedgerTransaction.account_id.in_(allowed_accounts),
            LedgerTransaction.currency_code == filters.currency_code,
            LedgerTransaction.transaction_date >= filters.date_from,
            LedgerTransaction.transaction_date <= filters.date_to,
            LedgerTransaction.status.in_(statuses),
            LedgerTransaction.voided_at.is_(None),
        ).order_by(LedgerTransaction.transaction_date, LedgerTransaction.created_at)
    ).all() if allowed_accounts else []
    ids = {item.id for item in transactions}
    transfer_ids: set[UUID] = set()
    if ids:
        for link in db.scalars(select(TransferLink).where((TransferLink.from_transaction_id.in_(ids)) | (TransferLink.to_transaction_id.in_(ids)))):
            transfer_ids.update((link.from_transaction_id, link.to_transaction_id))
    debt_ids = set(db.scalars(
        select(BillPaymentLink.transaction_id)
        .join(BillInstance, BillInstance.id == BillPaymentLink.bill_instance_id)
        .where(BillPaymentLink.household_id == household_id, BillInstance.debt_id.is_not(None), BillPaymentLink.transaction_id.in_(ids))
    ).all()) if ids else set()
    split_rows = db.execute(
        select(TransactionSplit, Category).join(Category, Category.id == TransactionSplit.category_id).where(TransactionSplit.transaction_id.in_(ids))
    ).all() if ids else []
    splits_by_transaction: dict[UUID, list[tuple[TransactionSplit, Category]]] = defaultdict(list)
    for split, category in split_rows:
        splits_by_transaction[split.transaction_id].append((split, category))
    merchants = {item.id: item.name for item in db.scalars(select(Merchant).where(Merchant.household_id == household_id)).all()}
    rows: list[dict] = []
    category_totals: dict[tuple[str, str], int] = defaultdict(int)
    merchant_totals: dict[tuple[str, str], int] = defaultdict(int)
    account_totals: dict[tuple[str, str], int] = defaultdict(int)
    month_totals: dict[str, int] = defaultdict(int)
    totals = {"spending_minor": 0, "income_minor": 0, "refunds_minor": 0, "debt_payments_minor": 0, "investment_activity_minor": 0, "net_cash_flow_minor": 0}
    counts = {"included": 0, "pending": 0, "uncategorized": 0, "transfers_excluded": 0, "reversals_excluded": 0}
    for item in transactions:
        item_splits = splits_by_transaction.get(item.id, [])
        if filters.merchant_id and item.merchant_id != filters.merchant_id:
            continue
        if filters.category_id and not any(split.category_id == filters.category_id for split, _ in item_splits):
            continue
        if item.id in transfer_ids or item.activity_type == "external_owned_transfer":
            counts["transfers_excluded"] += 1
            continue
        if item.reversal_of_transaction_id or item.status == "reversed":
            counts["reversals_excluded"] += 1
            continue
        if item.status == "pending":
            counts["pending"] += 1
        expense_splits = [(split, category) for split, category in item_splits if category.category_type == "expense"]
        filtered_amount = (
            sum(split.amount_minor for split, _ in item_splits if split.category_id == filters.category_id)
            if filters.category_id
            else item.amount_minor
        )
        classification = "cash_activity"
        report_amount = filtered_amount
        if item.id in debt_ids:
            classification = "debt_payment"
            report_amount = abs(filtered_amount)
            totals["debt_payments_minor"] += report_amount
            totals["spending_minor"] += report_amount
            month_totals[item.transaction_date.strftime("%Y-%m")] += report_amount
        elif item.activity_type in INVESTMENT_TYPES:
            classification = "investment_activity"
            totals["investment_activity_minor"] += filtered_amount
        elif item.amount_minor < 0:
            classification = "spending"
            report_amount = abs(filtered_amount)
            totals["spending_minor"] += report_amount
            month_totals[item.transaction_date.strftime("%Y-%m")] += report_amount
        elif item.amount_minor > 0 and expense_splits:
            classification = "refund"
            report_amount = abs(filtered_amount)
            totals["refunds_minor"] += report_amount
            totals["spending_minor"] -= report_amount
            month_totals[item.transaction_date.strftime("%Y-%m")] -= report_amount
        elif item.amount_minor > 0:
            classification = "income"
            report_amount = abs(filtered_amount)
            totals["income_minor"] += report_amount
        totals["net_cash_flow_minor"] += filtered_amount
        counts["included"] += 1
        if not item_splits and classification in {"spending", "refund", "income"}:
            counts["uncategorized"] += 1
        if classification in {"spending", "refund", "debt_payment"}:
            sign = -1 if classification == "refund" else 1
            relevant = expense_splits
            if filters.category_id:
                relevant = [(split, category) for split, category in relevant if split.category_id == filters.category_id]
            if relevant:
                for split, category in relevant:
                    category_totals[(str(category.id), category.name)] += sign * abs(split.amount_minor)
            else:
                category_totals[("uncategorized", "Uncategorized")] += sign * report_amount
            merchant_name = merchants.get(item.merchant_id) or item.payee or item.raw_payee or "No merchant"
            merchant_totals[(str(item.merchant_id) if item.merchant_id else "unlinked", merchant_name)] += sign * report_amount
            account = account_map[item.account_id]
            account_totals[(str(account.id), account.name)] += sign * report_amount
        rows.append({
            "transaction_id": str(item.id),
            "transaction_date": item.transaction_date.isoformat(),
            "account_id": str(item.account_id),
            "account_name": account_map[item.account_id].name,
            "ownership_scope": account_map[item.account_id].ownership_scope,
            "payee": item.payee or item.raw_payee,
            "merchant_id": str(item.merchant_id) if item.merchant_id else None,
            "merchant_name": merchants.get(item.merchant_id),
            "amount_minor": item.amount_minor,
            "report_amount_minor": report_amount,
            "currency_code": item.currency_code,
            "status": item.status,
            "activity_type": item.activity_type,
            "classification": classification,
            "categories": [{"category_id": str(category.id), "name": category.name, "amount_minor": split.amount_minor} for split, category in item_splits],
        })
    def breakdown(values: dict[tuple[str, str], int]) -> list[dict]:
        return [
            {"id": item_id, "name": name, "amount_minor": amount}
            for (item_id, name), amount in sorted(values.items(), key=lambda pair: (-pair[1], pair[0][1].lower()))
        ]
    return {
        "totals": totals,
        "counts": counts,
        "transactions": rows,
        "by_category": breakdown(category_totals),
        "by_merchant": breakdown(merchant_totals),
        "by_account": breakdown(account_totals),
        "monthly_spending": [{"month": month, "amount_minor": amount} for month, amount in sorted(month_totals.items())],
    }


def spending_report(db: Session, household_id: UUID, filters: ReportFilters) -> dict:
    current = _period(db, household_id, filters)
    days = (filters.date_to - filters.date_from).days + 1
    prior_to = filters.date_from - timedelta(days=1)
    prior_filters = ReportFilters(
        date_from=prior_to - timedelta(days=days - 1),
        date_to=prior_to,
        currency_code=filters.currency_code,
        ownership_scope=filters.ownership_scope,
        include_pending=filters.include_pending,
        account_id=filters.account_id,
        category_id=filters.category_id,
        merchant_id=filters.merchant_id,
    )
    prior = _period(db, household_id, prior_filters)
    spending_values = [row["report_amount_minor"] for row in current["transactions"] if row["classification"] == "spending"]
    threshold = max(10_000, int(median(spending_values) * 3) if spending_values else 10_000)
    unusual = [row for row in current["transactions"] if row["classification"] == "spending" and row["report_amount_minor"] >= threshold]
    prior_merchants = {item["name"]: item["amount_minor"] for item in prior["by_merchant"]}
    recurring_changes = []
    for item in current["by_merchant"]:
        previous = prior_merchants.get(item["name"])
        if previous and abs(item["amount_minor"] - previous) >= 1_000:
            percent = round((item["amount_minor"] - previous) * 100 / abs(previous))
            if abs(percent) >= 20:
                recurring_changes.append({"name": item["name"], "current_minor": item["amount_minor"], "prior_minor": previous, "change_percent": percent})
    warnings = []
    if current["counts"]["pending"]:
        warnings.append(f"{current['counts']['pending']} pending transaction(s) are included and may change.")
    if current["counts"]["uncategorized"]:
        warnings.append(f"{current['counts']['uncategorized']} transaction(s) are uncategorized.")
    if filters.date_to >= datetime.now(UTC).date():
        warnings.append("The selected period is still in progress, so comparisons are incomplete.")
    return {
        "rule_version": REPORT_RULE_VERSION,
        "filters": {
            "date_from": filters.date_from.isoformat(), "date_to": filters.date_to.isoformat(),
            "currency_code": filters.currency_code, "ownership_scope": filters.ownership_scope,
            "include_pending": filters.include_pending, "account_id": str(filters.account_id) if filters.account_id else None,
            "category_id": str(filters.category_id) if filters.category_id else None,
            "merchant_id": str(filters.merchant_id) if filters.merchant_id else None,
        },
        "prior_period": {"date_from": prior_filters.date_from.isoformat(), "date_to": prior_filters.date_to.isoformat(), "totals": prior["totals"]},
        **current,
        "signals": {"unusual_transactions": unusual, "recurring_changes": recurring_changes, "warnings": warnings},
        "semantics": [
            "Transfers, voids, reversed originals, and reversal legs are excluded.",
            "Refunds with expense-category evidence reduce spending.",
            "Debt payments count as categorized household spending and are also shown as debt activity; only confirmed principal reduces the tracked debt balance.",
            "Every result is limited to one currency; no exchange rate is implied.",
        ],
    }

import json
from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.automation import (
    AUTOMATION_RULE_VERSION,
    automation_preferences,
    merchant_for_payee,
    recompute_pending_rows,
    record_applied_decision,
    recurring_proposal,
)
from app.dependencies import DbSession, current_membership, current_user, require_roles
from app.imports import raw_value
from app.models import (
    AuditEvent,
    AutomationDecision,
    BillInstance,
    BillPaymentLink,
    BillProfile,
    Category,
    CategoryRule,
    Debt,
    FinancialAccount,
    ImportBatch,
    ImportRow,
    ImportSource,
    ImportSourceMappingVersion,
    IncomeSource,
    LedgerTransaction,
    Membership,
    Merchant,
    ReconciliationMatch,
    RecurringProfileLink,
    ReimbursementLink,
    Role,
    TransactionSplit,
    TransferCandidate,
    TransferLink,
    User,
    utc_now,
)
from app.obligations import recalculate_debt_balance

router = APIRouter(prefix="/v1/automation", tags=["automation"])
writer = require_roles(Role.OWNER, Role.MANAGER)


class TransferDecision(BaseModel):
    action: Literal["confirm", "reject"]


class TransferResolutionRequest(BaseModel):
    row_id: UUID
    resolution_type: Literal[
        "tracked_account",
        "external_owned_account",
        "payment",
        "gift",
        "loan_or_repayment",
        "income_or_spending",
        "reimbursement",
        "not_transfer",
    ]
    counterparty_account_id: UUID | None = None
    counterparty_transaction_id: UUID | None = None
    counterparty_import_row_id: UUID | None = None
    category_id: UUID | None = None
    original_transaction_id: UUID | None = None
    debt_id: UUID | None = None
    amount_minor: int | None = Field(default=None, gt=0)
    principal_amount_minor: int | None = Field(default=None, ge=0)


class ReimbursementCreate(BaseModel):
    reimbursement_transaction_id: UUID
    original_transaction_id: UUID
    amount_minor: int = Field(gt=0)
    category_id: UUID | None = None


class RecurringCreate(BaseModel):
    transaction_id: UUID
    transaction_ids: list[UUID] = Field(default_factory=list, max_length=250)
    name: str | None = Field(default=None, max_length=160)
    cadence: Literal["weekly", "biweekly", "monthly", "quarterly", "yearly", "irregular"] | None = None
    force: bool = False


class AutomationRuleUpdate(BaseModel):
    rule_name: str | None = Field(default=None, min_length=1, max_length=160)
    priority: int | None = Field(default=None, ge=1, le=10000)
    is_active: bool | None = None
    auto_apply: bool | None = None
    account_id: UUID | None = None
    source_id: UUID | None = None
    amount_min_minor: int | None = Field(default=None, ge=0)
    amount_max_minor: int | None = Field(default=None, ge=0)
    description_pattern: str | None = Field(default=None, max_length=300)


class AutomationPreferenceUpdate(BaseModel):
    transfer_window_days: int = Field(ge=1, le=14)
    reimbursement_window_days: int = Field(ge=7, le=730)


def _row_transaction(db: DbSession, row: ImportRow, actor: User, category_id: UUID | None = None) -> LedgerTransaction:
    source = db.get(ImportSource, row.source_id)
    if not source or row.transaction_date is None or row.amount_minor is None or row.status in {"invalid", "duplicate", "matched"}:
        raise HTTPException(status_code=409, detail="This import row cannot create a transaction")
    raw = json.loads(row.raw_json)
    imported_status = ((raw_value(raw, source.status_column) if source.status_column else raw_value(raw, "Status")) or "posted").strip().casefold()
    merchant = merchant_for_payee(db, row.household_id, row.raw_payee or row.normalized_payee)
    transaction = LedgerTransaction(household_id=row.household_id, account_id=source.account_id, merchant_id=merchant.id if merchant else None, created_by_user_id=actor.id, transaction_date=row.transaction_date, amount_minor=row.amount_minor, currency_code=row.currency_code, status=imported_status if imported_status in {"posted", "pending"} else "posted", payee=merchant.name if merchant else row.normalized_payee or row.raw_payee, raw_payee=row.raw_payee, source_type="imported", source_reference=str(row.id), reconciled_at=utc_now(), reconciled_by_user_id=actor.id)
    db.add(transaction)
    db.flush()
    selected_category = category_id or row.proposed_category_id
    if selected_category:
        category = db.scalar(select(Category).where(Category.id == selected_category, Category.household_id == row.household_id, Category.is_archived.is_(False)))
        if not category or category.category_type != ("income" if row.amount_minor > 0 else "expense"):
            raise HTTPException(status_code=422, detail="The proposed category does not match the transaction direction")
        db.add(TransactionSplit(transaction_id=transaction.id, category_id=category.id, amount_minor=row.amount_minor))
    db.add(ReconciliationMatch(household_id=row.household_id, import_row_id=row.id, transaction_id=transaction.id, method="approved_household_automation" if row.applied_rule_id else "created_from_import", confidence_percent=100, evidence=row.automation_evidence or "User approved preserved import evidence", status="confirmed", reviewed_by_user_id=actor.id, reviewed_at=utc_now()))
    row.status = "matched"
    if row.applied_rule_id:
        rule = db.get(CategoryRule, row.applied_rule_id)
        if rule:
            rule.use_count += 1
            rule.last_applied_at = utc_now()
    record_applied_decision(db, row, actor.id, transaction.id)
    return transaction


def _rule_response(db: DbSession, rule: CategoryRule) -> dict:
    category = db.get(Category, rule.category_id)
    source = db.get(ImportSource, rule.source_id) if rule.source_id else None
    merchant = db.get(Merchant, UUID(rule.match_value)) if rule.match_type == "merchant" else None
    affected = db.scalar(select(func.count()).select_from(ImportRow).where(ImportRow.household_id == rule.household_id, ImportRow.status.in_(("unmatched", "ready", "deferred")), ImportRow.applied_rule_id == rule.id)) or 0
    return {"rule_id": str(rule.id), "rule_name": rule.rule_name, "match_type": rule.match_type, "match_value": rule.match_value, "match_label": merchant.name if merchant else rule.match_value, "direction": rule.direction, "category_id": str(rule.category_id), "category_name": category.name if category else "Unavailable", "account_id": str(rule.account_id) if rule.account_id else None, "source_id": str(rule.source_id) if rule.source_id else None, "source_name": source.name if source else None, "amount_min_minor": rule.amount_min_minor, "amount_max_minor": rule.amount_max_minor, "description_pattern": rule.description_pattern, "priority": rule.priority, "auto_apply": rule.auto_apply, "is_active": rule.is_active, "use_count": rule.use_count, "last_applied_at": rule.last_applied_at.isoformat() if rule.last_applied_at else None, "created_from_action": rule.created_from_action, "preview_unconfirmed_count": affected}


def _resolution_row(db: DbSession, household_id: UUID, row_id: UUID) -> tuple[ImportRow, ImportSource]:
    row = db.scalar(
        select(ImportRow).where(
            ImportRow.id == row_id,
            ImportRow.household_id == household_id,
        )
    )
    if not row or row.status not in {"unmatched", "ready", "deferred"} or row.amount_minor is None or row.transaction_date is None:
        raise HTTPException(status_code=409, detail="This pending import row cannot be resolved")
    source = db.get(ImportSource, row.source_id)
    if not source:
        raise HTTPException(status_code=409, detail="The import source is unavailable")
    return row, source


def _tracked_counterparty(
    db: DbSession,
    household_id: UUID,
    row: ImportRow,
    source: ImportSource,
    request: TransferResolutionRequest,
) -> tuple[LedgerTransaction | ImportRow | FinancialAccount, str]:
    selected = sum(bool(value) for value in (request.counterparty_transaction_id, request.counterparty_import_row_id, request.counterparty_account_id))
    if selected != 1:
        raise HTTPException(status_code=422, detail="Choose one opposite transaction, pending import row, or tracked account")
    if request.counterparty_transaction_id:
        item = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == request.counterparty_transaction_id, LedgerTransaction.household_id == household_id, LedgerTransaction.voided_at.is_(None)))
        if not item:
            raise HTTPException(status_code=404, detail="Opposite transaction not found")
        account_id, amount_minor, currency_code = item.account_id, item.amount_minor, item.currency_code
        label = item.payee or "Existing transaction"
    elif request.counterparty_import_row_id:
        item = db.scalar(select(ImportRow).where(ImportRow.id == request.counterparty_import_row_id, ImportRow.household_id == household_id, ImportRow.status.in_(("unmatched", "ready", "deferred"))))
        if not item:
            raise HTTPException(status_code=404, detail="Opposite pending import row not found")
        other_source = db.get(ImportSource, item.source_id)
        account_id, amount_minor, currency_code = other_source.account_id, item.amount_minor, item.currency_code
        label = item.raw_payee or "Pending imported transaction"
    else:
        item = db.scalar(select(FinancialAccount).where(FinancialAccount.id == request.counterparty_account_id, FinancialAccount.household_id == household_id, FinancialAccount.is_archived.is_(False)))
        if not item:
            raise HTTPException(status_code=404, detail="Tracked account not found")
        account_id, amount_minor, currency_code = item.id, -row.amount_minor, item.currency_code
        label = item.name
    if account_id == source.account_id or currency_code != row.currency_code or amount_minor != -row.amount_minor:
        raise HTTPException(status_code=422, detail="Tracked transfer sides must use different accounts with equal currency and opposite amounts")
    return item, label


def _resolution_debt(db: DbSession, household_id: UUID, row: ImportRow, debt_id: UUID | None) -> Debt:
    if row.amount_minor is None or row.amount_minor >= 0:
        raise HTTPException(status_code=422, detail="A payment linked to a tracked loan must be money out")
    debt = db.scalar(
        select(Debt).where(
            Debt.id == debt_id,
            Debt.household_id == household_id,
            Debt.currency_code == row.currency_code,
            Debt.is_active.is_(True),
        )
    ) if debt_id else None
    if not debt:
        raise HTTPException(status_code=422, detail="Choose an active loan or debt in the same currency")
    return debt


def _debt_payment_instance(db: DbSession, debt: Debt, row: ImportRow) -> BillInstance:
    instances = db.scalars(
        select(BillInstance).where(
            BillInstance.debt_id == debt.id,
            BillInstance.currency_code == row.currency_code,
        )
    ).all()
    if instances:
        return min(instances, key=lambda item: abs((item.due_date - row.transaction_date).days))
    amount = abs(row.amount_minor or 0)
    item = BillInstance(
        household_id=debt.household_id,
        debt_id=debt.id,
        name=f"{debt.name} payment",
        due_date=row.transaction_date,
        expected_amount_minor=amount,
        minimum_amount_minor=min(debt.minimum_payment_minor, amount),
        currency_code=row.currency_code,
        is_essential=True,
        priority=1,
    )
    db.add(item)
    db.flush()
    return item


@router.get("/summary")
def summary(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> dict:
    household = membership.household_id
    rules = db.scalars(select(CategoryRule).where(CategoryRule.household_id == household)).all()
    rows = db.scalars(select(ImportRow).where(ImportRow.household_id == household)).all()
    return {"rule_version": AUTOMATION_RULE_VERSION, "rules": {"active": sum(item.is_active for item in rules), "total": len(rules), "applications": sum(item.use_count for item in rules)}, "rows": {status: sum(item.status == status for item in rows) for status in ("ready", "unmatched", "deferred", "duplicate", "invalid", "matched")}, "transfer_candidates": db.scalar(select(func.count()).select_from(TransferCandidate).where(TransferCandidate.household_id == household, TransferCandidate.status == "pending")) or 0, "reimbursements": db.scalar(select(func.count()).select_from(ReimbursementLink).where(ReimbursementLink.household_id == household)) or 0, "recurring_links": db.scalar(select(func.count()).select_from(RecurringProfileLink).where(RecurringProfileLink.household_id == household)) or 0, "local_only": True}


@router.get("/preferences")
def get_preferences(
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
) -> dict:
    item = automation_preferences(db, membership.household_id)
    response = {"transfer_window_days": item.transfer_window_days, "reimbursement_window_days": item.reimbursement_window_days}
    db.commit()
    return response


@router.put("/preferences")
def update_preferences(
    request: AutomationPreferenceUpdate,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(writer)],
) -> dict:
    item = automation_preferences(db, membership.household_id)
    item.transfer_window_days = request.transfer_window_days
    item.reimbursement_window_days = request.reimbursement_window_days
    recompute_pending_rows(db, membership.household_id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="automation.preferences_updated", resource_type="household_automation_preference", resource_id=str(membership.household_id), detail=f"transfer_window_days={item.transfer_window_days};reimbursement_window_days={item.reimbursement_window_days}"))
    db.commit()
    return {"transfer_window_days": item.transfer_window_days, "reimbursement_window_days": item.reimbursement_window_days}


@router.get("/rules")
def rules(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[dict]:
    items = db.scalars(select(CategoryRule).where(CategoryRule.household_id == membership.household_id).order_by(CategoryRule.priority, CategoryRule.created_at)).all()
    return [_rule_response(db, item) for item in items]


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: UUID, request: AutomationRuleUpdate, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    rule = db.scalar(select(CategoryRule).where(CategoryRule.id == rule_id, CategoryRule.household_id == membership.household_id))
    if not rule: raise HTTPException(status_code=404, detail="Household rule not found")
    values = request.model_dump(exclude_unset=True)
    if "source_id" in values and values["source_id"] and not db.scalar(select(ImportSource.id).where(ImportSource.id == values["source_id"], ImportSource.household_id == membership.household_id)): raise HTTPException(status_code=422, detail="Import source is unavailable")
    if values.get("amount_min_minor") is not None and values.get("amount_max_minor") is not None and values["amount_min_minor"] > values["amount_max_minor"]: raise HTTPException(status_code=422, detail="Minimum amount cannot exceed maximum amount")
    for field, value in values.items(): setattr(rule, field, value)
    recompute_pending_rows(db, membership.household_id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="automation.rule_updated", resource_type="category_rule", resource_id=str(rule.id), detail=json.dumps(values, default=str, sort_keys=True)))
    db.commit()
    return _rule_response(db, rule)


@router.post("/rules/{rule_id}/run")
def run_rule(
    rule_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(writer)],
) -> dict:
    rule = db.scalar(
        select(CategoryRule).where(
            CategoryRule.id == rule_id,
            CategoryRule.household_id == membership.household_id,
        )
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Household rule not found")
    if not rule.is_active:
        raise HTTPException(status_code=409, detail="Enable this rule before running it")
    recompute_pending_rows(db, membership.household_id)
    response = _rule_response(db, rule)
    db.add(
        AuditEvent(
            household_id=membership.household_id,
            actor_user_id=actor.id,
            action="automation.rule_run",
            resource_type="category_rule",
            resource_id=str(rule.id),
            detail=f"ready_matches={response['preview_unconfirmed_count']};confirmed_history_unchanged=true",
        )
    )
    db.commit()
    return response


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(writer)],
) -> Response:
    rule = db.scalar(
        select(CategoryRule).where(
            CategoryRule.id == rule_id,
            CategoryRule.household_id == membership.household_id,
        )
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Household rule not found")
    db.execute(
        AutomationDecision.__table__.update()
        .where(AutomationDecision.rule_id == rule.id)
        .values(rule_id=None)
    )
    db.delete(rule)
    db.flush()
    recompute_pending_rows(db, membership.household_id)
    db.add(
        AuditEvent(
            household_id=membership.household_id,
            actor_user_id=actor.id,
            action="automation.rule_deleted",
            resource_type="category_rule",
            resource_id=str(rule_id),
            detail="Pending previews recomputed; confirmed history unchanged",
        )
    )
    db.commit()
    return Response(status_code=204)


@router.get("/sources/{source_id}/mappings")
def mapping_versions(source_id: UUID, db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[dict]:
    if not db.scalar(select(ImportSource.id).where(ImportSource.id == source_id, ImportSource.household_id == membership.household_id)): raise HTTPException(status_code=404, detail="Import source not found")
    rows = db.scalars(select(ImportSourceMappingVersion).where(ImportSourceMappingVersion.source_id == source_id).order_by(ImportSourceMappingVersion.version_number.desc())).all()
    return [{"mapping_version_id": str(item.id), "version_number": item.version_number, "mapping_hash": item.mapping_hash, "mapping": json.loads(item.mapping_json), "created_at": item.created_at.isoformat()} for item in rows]


@router.post("/batches/{batch_id}/approve-ready")
def approve_ready(batch_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.household_id == membership.household_id))
    if not batch: raise HTTPException(status_code=404, detail="Import batch not found")
    rows = db.scalars(select(ImportRow).where(ImportRow.batch_id == batch.id, ImportRow.status == "ready").order_by(ImportRow.row_number)).all()
    created = [_row_transaction(db, row, actor) for row in rows]
    batch.ready_count = 0
    if not db.scalar(select(ImportRow.id).where(ImportRow.batch_id == batch.id, ImportRow.status.in_(("unmatched", "deferred", "invalid", "duplicate"))).limit(1)): batch.status = "approved"
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="automation.ready_batch_approved", resource_type="import_batch", resource_id=str(batch.id), detail=f"created={len(created)}"))
    db.commit()
    return {"batch_id": str(batch.id), "approved_count": len(created), "transaction_ids": [str(item.id) for item in created]}


@router.post("/batches/{batch_id}/undo-ready")
def undo_ready_batch(
    batch_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(writer)],
) -> dict:
    batch = db.scalar(
        select(ImportBatch).where(
            ImportBatch.id == batch_id,
            ImportBatch.household_id == membership.household_id,
        )
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Import batch not found")
    rows = db.scalars(select(ImportRow).where(ImportRow.batch_id == batch.id)).all()
    undone = 0
    for row in rows:
        transaction = db.scalar(
            select(LedgerTransaction).where(
                LedgerTransaction.household_id == membership.household_id,
                LedgerTransaction.source_type == "imported",
                LedgerTransaction.source_reference == str(row.id),
                LedgerTransaction.voided_at.is_(None),
            )
        )
        if not transaction:
            continue
        match = db.scalar(
            select(ReconciliationMatch).where(
                ReconciliationMatch.import_row_id == row.id,
                ReconciliationMatch.transaction_id == transaction.id,
                ReconciliationMatch.method == "approved_household_automation",
                ReconciliationMatch.status == "confirmed",
            )
        )
        if not match:
            continue
        transaction.voided_at = utc_now()
        transaction.voided_by_user_id = actor.id
        match.status = "reversed"
        match.reviewed_by_user_id = actor.id
        match.reviewed_at = utc_now()
        row.status = "ready"
        decision = db.scalar(
            select(AutomationDecision).where(
                AutomationDecision.entity_type == "import_row",
                AutomationDecision.entity_id == str(row.id),
                AutomationDecision.status == "applied",
            )
        )
        if decision:
            decision.status = "reversed"
        undone += 1
    batch.ready_count = undone
    batch.status = "ready" if undone else batch.status
    db.add(
        AuditEvent(
            household_id=membership.household_id,
            actor_user_id=actor.id,
            action="automation.ready_batch_undone",
            resource_type="import_batch",
            resource_id=str(batch.id),
            detail=f"voided={undone};preserved_import_evidence=true",
        )
    )
    db.commit()
    return {"batch_id": str(batch.id), "undone_count": undone}


@router.get("/transfer-candidates")
def transfer_candidates(db: DbSession, membership: Annotated[Membership, Depends(current_membership)], include_confirmed: bool = False) -> list[dict]:
    statuses = ("pending", "confirmed") if include_confirmed else ("pending",)
    rows = db.scalars(select(TransferCandidate).where(TransferCandidate.household_id == membership.household_id, TransferCandidate.status.in_(statuses)).order_by(TransferCandidate.created_at)).all()
    result=[]
    for item in rows:
        row = db.get(ImportRow, item.import_row_id)
        transaction = db.get(LedgerTransaction, item.counterparty_transaction_id) if item.counterparty_transaction_id else None
        counterparty_row = db.get(ImportRow, item.counterparty_import_row_id) if item.counterparty_import_row_id else None
        counterparty = transaction or counterparty_row
        result.append({"candidate_id": str(item.id), "status": item.status, "row_id": str(row.id), "row_payee": row.raw_payee, "row_date": row.transaction_date.isoformat(), "row_amount_minor": row.amount_minor, "currency_code": row.currency_code, "counterparty_transaction_id": str(transaction.id) if transaction else None, "counterparty_import_row_id": str(counterparty_row.id) if counterparty_row else None, "counterparty_payee": transaction.payee if transaction else counterparty_row.raw_payee, "counterparty_date": counterparty.transaction_date.isoformat(), "counterparty_amount_minor": counterparty.amount_minor, "confidence_percent": item.confidence_percent, "evidence": item.evidence})
    return result


@router.post("/transfer-resolutions/preview")
def preview_transfer_resolution(
    request: TransferResolutionRequest,
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
) -> dict:
    row, source = _resolution_row(db, membership.household_id, request.row_id)
    category = None
    if request.resolution_type == "tracked_account":
        _, label = _tracked_counterparty(db, membership.household_id, row, source, request)
        report_effect = "Both tracked-account legs will be excluded from income and spending."
        reconciliation_effect = f"The pending row will be linked to {label} as an owned-account transfer."
        balance_change = 0
    elif request.resolution_type == "external_owned_account":
        report_effect = "The movement will be excluded from income and spending while the tracked account balance still changes."
        reconciliation_effect = "The pending row will be marked as movement to or from an owned account outside Tallystead."
        balance_change = row.amount_minor
    elif request.resolution_type == "reimbursement":
        if row.amount_minor <= 0 or not request.original_transaction_id:
            raise HTTPException(status_code=422, detail="Choose an original expense for a received reimbursement")
        original = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == request.original_transaction_id, LedgerTransaction.household_id == membership.household_id, LedgerTransaction.amount_minor < 0, LedgerTransaction.currency_code == row.currency_code, LedgerTransaction.voided_at.is_(None)))
        if not original:
            raise HTTPException(status_code=404, detail="Original expense not found")
        amount = request.amount_minor or row.amount_minor
        linked = db.scalar(select(func.sum(ReimbursementLink.amount_minor)).where(ReimbursementLink.original_transaction_id == original.id)) or 0
        if linked + amount > abs(original.amount_minor) or amount > row.amount_minor:
            raise HTTPException(status_code=422, detail="The reimbursement exceeds the eligible original expense or received amount")
        category_id = request.category_id or db.scalar(select(TransactionSplit.category_id).where(TransactionSplit.transaction_id == original.id).limit(1))
        category = db.scalar(select(Category).where(Category.id == category_id, Category.household_id == membership.household_id, Category.category_type == "expense")) if category_id else None
        if not category:
            raise HTTPException(status_code=422, detail="Choose the expense category this reimbursement offsets")
        report_effect = f"{category.name} spending will decrease by {amount} minor units; the receipt will not count as income."
        reconciliation_effect = "The imported receipt and original expense remain intact with an audited reimbursement link."
        balance_change = row.amount_minor
    elif request.resolution_type == "loan_or_repayment":
        debt = _resolution_debt(db, membership.household_id, row, request.debt_id)
        category = db.scalar(select(Category).where(Category.id == request.category_id, Category.household_id == membership.household_id, Category.category_type == "expense", Category.is_archived.is_(False))) if request.category_id else None
        if not category:
            raise HTTPException(status_code=422, detail="Choose an expense category for this loan payment")
        principal = request.principal_amount_minor if request.principal_amount_minor is not None else abs(row.amount_minor)
        if principal > abs(row.amount_minor):
            raise HTTPException(status_code=422, detail="Principal cannot exceed the total loan payment")
        resulting_balance = max(0, debt.balance_minor - principal)
        report_effect = f"The full payment will count as {category.name} spending and debt-payment activity. {principal} minor units of principal will reduce {debt.name} from {debt.balance_minor} to {resulting_balance}."
        reconciliation_effect = "The imported payment will be linked to the nearest obligation for this tracked debt."
        balance_change = row.amount_minor
    else:
        category = db.scalar(select(Category).where(Category.id == request.category_id, Category.household_id == membership.household_id, Category.is_archived.is_(False))) if request.category_id else None
        expected_type = "income" if row.amount_minor > 0 else "expense"
        if not category or category.category_type != expected_type:
            raise HTTPException(status_code=422, detail=f"Choose an {expected_type} category for this transaction")
        report_effect = f"The row will count as {expected_type} in {category.name}; it will not be excluded as an owned-account transfer."
        reconciliation_effect = f"The pending row will become a categorized ledger transaction identified as {request.resolution_type.replace('_', ' ')}."
        balance_change = row.amount_minor
    return {"row_id": str(row.id), "resolution_type": request.resolution_type, "currency_code": row.currency_code, "account_balances_change_minor": balance_change, "spending_removed_minor": abs(row.amount_minor) if row.amount_minor < 0 and request.resolution_type in {"tracked_account", "external_owned_account"} else 0, "income_removed_minor": row.amount_minor if row.amount_minor > 0 and request.resolution_type in {"tracked_account", "external_owned_account", "reimbursement"} else 0, "report_effect": report_effect, "reconciliation_effect": reconciliation_effect, "category_name": category.name if category else None}


@router.post("/transfer-resolutions", status_code=201)
def resolve_transfer(
    request: TransferResolutionRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(writer)],
) -> dict:
    row, source = _resolution_row(db, membership.household_id, request.row_id)
    preview = preview_transfer_resolution(request, db, membership)
    linked_id = None
    if request.resolution_type == "tracked_account":
        counterparty, _ = _tracked_counterparty(db, membership.household_id, row, source, request)
        transaction = _row_transaction(db, row, actor)
        if isinstance(counterparty, ImportRow):
            other_transaction = _row_transaction(db, counterparty, actor)
        elif isinstance(counterparty, LedgerTransaction):
            other_transaction = counterparty
        else:
            other_transaction = LedgerTransaction(household_id=membership.household_id, account_id=counterparty.id, created_by_user_id=actor.id, transaction_date=row.transaction_date, amount_minor=-row.amount_minor, currency_code=row.currency_code, status="posted", payee=f"Transfer counterpart for {row.raw_payee or source.name}", raw_payee=row.raw_payee, source_type="transfer_resolution", source_reference=str(row.id))
            db.add(other_transaction)
            db.flush()
        already_linked = db.scalar(select(TransferLink.id).where((TransferLink.from_transaction_id.in_((transaction.id, other_transaction.id))) | (TransferLink.to_transaction_id.in_((transaction.id, other_transaction.id)))))
        if already_linked:
            raise HTTPException(status_code=409, detail="One of these transactions is already linked as a transfer")
        outgoing, incoming = (transaction, other_transaction) if transaction.amount_minor < 0 else (other_transaction, transaction)
        link = TransferLink(household_id=membership.household_id, from_transaction_id=outgoing.id, to_transaction_id=incoming.id, created_by_user_id=actor.id)
        db.add(link)
        db.flush()
        linked_id = link.id
    elif request.resolution_type == "external_owned_account":
        transaction = _row_transaction(db, row, actor)
        transaction.activity_type = "external_owned_transfer"
    elif request.resolution_type == "reimbursement":
        transaction = _row_transaction(db, row, actor)
        original = db.get(LedgerTransaction, request.original_transaction_id)
        category_id = request.category_id or db.scalar(select(TransactionSplit.category_id).where(TransactionSplit.transaction_id == original.id).limit(1))
        amount = request.amount_minor or row.amount_minor
        link = ReimbursementLink(household_id=membership.household_id, reimbursement_transaction_id=transaction.id, original_transaction_id=original.id, category_id=category_id, amount_minor=amount, created_by_user_id=actor.id)
        db.add(link)
        db.add(TransactionSplit(transaction_id=transaction.id, category_id=category_id, amount_minor=amount))
        db.flush()
        linked_id = link.id
    elif request.resolution_type == "loan_or_repayment":
        debt = _resolution_debt(db, membership.household_id, row, request.debt_id)
        if debt.balance_anchor_minor is None:
            debt.balance_anchor_minor = debt.balance_minor
        transaction = _row_transaction(db, row, actor, request.category_id)
        transaction.activity_type = "debt_payment"
        instance = _debt_payment_instance(db, debt, row)
        principal = request.principal_amount_minor if request.principal_amount_minor is not None else abs(row.amount_minor)
        link = BillPaymentLink(
            household_id=membership.household_id,
            bill_instance_id=instance.id,
            transaction_id=transaction.id,
            amount_minor=abs(row.amount_minor),
            principal_amount_minor=principal,
            created_by_user_id=actor.id,
        )
        db.add(link)
        db.flush()
        recalculate_debt_balance(db, debt)
        linked_id = link.id
    else:
        transaction = _row_transaction(db, row, actor, request.category_id)
    db.execute(TransferCandidate.__table__.update().where((TransferCandidate.import_row_id == row.id) | (TransferCandidate.counterparty_import_row_id == row.id)).values(status="rejected", reviewed_by_user_id=actor.id, reviewed_at=utc_now()))
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="automation.transfer_resolved", resource_type="import_row", resource_id=str(row.id), detail=f"resolution_type={request.resolution_type};transaction={transaction.id};linked={linked_id};preview={json.dumps(preview, sort_keys=True)}"))
    db.commit()
    return {"row_id": str(row.id), "resolution_type": request.resolution_type, "transaction_id": str(transaction.id), "linked_id": str(linked_id) if linked_id else None, "preview": preview}


@router.put("/transfer-candidates/{candidate_id}")
def decide_transfer(candidate_id: UUID, request: TransferDecision, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    candidate = db.scalar(select(TransferCandidate).where(TransferCandidate.id == candidate_id, TransferCandidate.household_id == membership.household_id))
    if not candidate or candidate.status != "pending": raise HTTPException(status_code=404, detail="Pending transfer candidate not found")
    candidate.status = "confirmed" if request.action == "confirm" else "rejected"; candidate.reviewed_by_user_id=actor.id; candidate.reviewed_at=utc_now()
    transaction_id = None
    if request.action == "confirm":
        row = db.get(ImportRow, candidate.import_row_id)
        counterparty = db.get(LedgerTransaction, candidate.counterparty_transaction_id) if candidate.counterparty_transaction_id else None
        counterparty_row = db.get(ImportRow, candidate.counterparty_import_row_id) if candidate.counterparty_import_row_id else None
        if counterparty_row:
            counterparty = _row_transaction(db, counterparty_row, actor)
        transaction = db.scalar(select(LedgerTransaction).where(LedgerTransaction.source_type == "imported", LedgerTransaction.source_reference == str(row.id), LedgerTransaction.voided_at.is_(None))) if row.status == "matched" else _row_transaction(db, row, actor)
        if not transaction or not counterparty:
            raise HTTPException(status_code=409, detail="The imported transfer transaction is unavailable")
        outgoing, incoming = (transaction, counterparty) if transaction.amount_minor < 0 else (counterparty, transaction)
        db.add(TransferLink(household_id=membership.household_id, from_transaction_id=outgoing.id, to_transaction_id=incoming.id, created_by_user_id=actor.id))
        db.execute(TransferCandidate.__table__.update().where(TransferCandidate.import_row_id == row.id, TransferCandidate.id != candidate.id).values(status="rejected", reviewed_by_user_id=actor.id, reviewed_at=utc_now()))
        transaction_id = transaction.id
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=f"automation.transfer_{candidate.status}", resource_type="transfer_candidate", resource_id=str(candidate.id), detail=candidate.evidence))
    db.commit()
    return {"candidate_id": str(candidate.id), "status": candidate.status, "created_transaction_id": str(transaction_id) if transaction_id else None}


@router.get("/transfer-candidates/{candidate_id}/preview")
def preview_transfer(
    candidate_id: UUID,
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
) -> dict:
    candidate = db.scalar(select(TransferCandidate).where(TransferCandidate.id == candidate_id, TransferCandidate.household_id == membership.household_id))
    if not candidate:
        raise HTTPException(status_code=404, detail="Transfer candidate not found")
    row = db.get(ImportRow, candidate.import_row_id)
    counterparty = db.get(LedgerTransaction, candidate.counterparty_transaction_id) if candidate.counterparty_transaction_id else db.get(ImportRow, candidate.counterparty_import_row_id)
    outgoing_minor = abs(row.amount_minor if row.amount_minor < 0 else counterparty.amount_minor)
    incoming_minor = row.amount_minor if row.amount_minor > 0 else counterparty.amount_minor
    return {"candidate_id": str(candidate.id), "account_balances_change_minor": 0, "spending_removed_minor": outgoing_minor, "income_removed_minor": max(incoming_minor, 0), "currency_code": row.currency_code, "reconciliation_effect": "The imported row becomes a confirmed ledger transaction and both owned-account legs are linked.", "report_effect": "Both legs are excluded from household income and spending; account balances remain unchanged.", "evidence": candidate.evidence}


@router.delete("/transfer-candidates/{candidate_id}/confirmation", status_code=204)
def undo_transfer(
    candidate_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(writer)],
) -> Response:
    candidate = db.scalar(select(TransferCandidate).where(TransferCandidate.id == candidate_id, TransferCandidate.household_id == membership.household_id, TransferCandidate.status == "confirmed"))
    if not candidate:
        raise HTTPException(status_code=404, detail="Confirmed transfer candidate not found")
    row = db.get(ImportRow, candidate.import_row_id)
    imported = db.scalar(select(LedgerTransaction).where(LedgerTransaction.source_type == "imported", LedgerTransaction.source_reference == str(row.id), LedgerTransaction.voided_at.is_(None)))
    link = db.scalar(select(TransferLink).where(TransferLink.household_id == membership.household_id, ((TransferLink.from_transaction_id == imported.id) | (TransferLink.to_transaction_id == imported.id)))) if imported else None
    if link:
        db.delete(link)
    candidate.status = "pending"
    candidate.reviewed_by_user_id = actor.id
    candidate.reviewed_at = utc_now()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="automation.transfer_unlinked", resource_type="transfer_candidate", resource_id=str(candidate.id), detail="Both ledger transactions preserved"))
    db.commit()
    return Response(status_code=204)


@router.post("/reimbursements", status_code=201)
def create_reimbursement(request: ReimbursementCreate, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    reimbursement = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == request.reimbursement_transaction_id, LedgerTransaction.household_id == membership.household_id))
    original = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == request.original_transaction_id, LedgerTransaction.household_id == membership.household_id))
    if not reimbursement or not original: raise HTTPException(status_code=404, detail="Transaction not found")
    if reimbursement.amount_minor <= 0 or original.amount_minor >= 0 or reimbursement.currency_code != original.currency_code: raise HTTPException(status_code=422, detail="A reimbursement must be a same-currency inflow linked to an expense")
    original_linked = db.scalar(select(func.sum(ReimbursementLink.amount_minor)).where(ReimbursementLink.original_transaction_id == original.id)) or 0
    reimbursement_linked = db.scalar(select(func.sum(ReimbursementLink.amount_minor)).where(ReimbursementLink.reimbursement_transaction_id == reimbursement.id)) or 0
    if original_linked + request.amount_minor > abs(original.amount_minor): raise HTTPException(status_code=422, detail="Linked reimbursements would exceed the original expense")
    if reimbursement_linked + request.amount_minor > reimbursement.amount_minor: raise HTTPException(status_code=422, detail="Linked offsets would exceed the reimbursement transaction")
    category_id = request.category_id or db.scalar(select(TransactionSplit.category_id).where(TransactionSplit.transaction_id == original.id).limit(1))
    category = db.scalar(select(Category).where(Category.id == category_id, Category.household_id == membership.household_id, Category.category_type == "expense")) if category_id else None
    if not category: raise HTTPException(status_code=422, detail="Choose an expense category for this reimbursement")
    if db.scalar(select(TransactionSplit.id).where(TransactionSplit.transaction_id == reimbursement.id).limit(1)): raise HTTPException(status_code=409, detail="Remove the reimbursement transaction's existing category before linking it")
    item = ReimbursementLink(household_id=membership.household_id, reimbursement_transaction_id=reimbursement.id, original_transaction_id=original.id, category_id=category.id, amount_minor=request.amount_minor, created_by_user_id=actor.id)
    db.add(item); db.add(TransactionSplit(transaction_id=reimbursement.id, category_id=category.id, amount_minor=request.amount_minor)); db.flush()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="automation.reimbursement_linked", resource_type="reimbursement_link", resource_id=str(item.id), detail=f"original={original.id};amount={request.amount_minor}")); db.commit()
    return {"reimbursement_link_id": str(item.id), "category_name": category.name, "amount_minor": item.amount_minor}


@router.post("/reimbursements/preview")
def preview_reimbursement(
    request: ReimbursementCreate,
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
) -> dict:
    reimbursement = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == request.reimbursement_transaction_id, LedgerTransaction.household_id == membership.household_id))
    original = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == request.original_transaction_id, LedgerTransaction.household_id == membership.household_id))
    category = db.scalar(select(Category).where(Category.id == request.category_id, Category.household_id == membership.household_id)) if request.category_id else None
    if not reimbursement or not original:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if reimbursement.amount_minor <= 0 or original.amount_minor >= 0 or reimbursement.currency_code != original.currency_code:
        raise HTTPException(status_code=422, detail="A reimbursement must be a same-currency inflow linked to an expense")
    original_linked = db.scalar(select(func.sum(ReimbursementLink.amount_minor)).where(ReimbursementLink.original_transaction_id == original.id)) or 0
    reimbursement_linked = db.scalar(select(func.sum(ReimbursementLink.amount_minor)).where(ReimbursementLink.reimbursement_transaction_id == reimbursement.id)) or 0
    if original_linked + request.amount_minor > abs(original.amount_minor):
        raise HTTPException(status_code=422, detail="Linked reimbursements would exceed the original expense")
    if reimbursement_linked + request.amount_minor > reimbursement.amount_minor:
        raise HTTPException(status_code=422, detail="Linked offsets would exceed the reimbursement transaction")
    if db.scalar(select(TransactionSplit.id).where(TransactionSplit.transaction_id == reimbursement.id).limit(1)):
        raise HTTPException(status_code=409, detail="Remove the reimbursement transaction's existing category before linking it")
    if not category or category.category_type != "expense":
        raise HTTPException(status_code=422, detail="Choose an expense category for this reimbursement")
    return {"currency_code": reimbursement.currency_code, "amount_minor": request.amount_minor, "category_name": category.name if category else None, "account_balances_change_minor": 0, "spending_change_minor": -request.amount_minor, "income_change_minor": 0, "reconciliation_effect": "Both original transactions remain intact and receive an audited relationship.", "report_effect": f"Net {category.name if category else 'expense'} spending decreases by {request.amount_minor} minor units; the reimbursement is not counted as income."}


@router.get("/reimbursement-candidates/{transaction_id}")
def reimbursement_candidates(
    transaction_id: UUID,
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
    date_from: date | None = None,
    date_to: date | None = None,
    amount_minor: int | None = None,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    payee: str | None = None,
) -> list[dict]:
    reimbursement = db.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.id == transaction_id,
            LedgerTransaction.household_id == membership.household_id,
            LedgerTransaction.amount_minor > 0,
            LedgerTransaction.voided_at.is_(None),
        )
    )
    if not reimbursement:
        raise HTTPException(status_code=404, detail="Money-in transaction not found")
    preferences = automation_preferences(db, membership.household_id)
    earliest = date_from or reimbursement.transaction_date - timedelta(days=preferences.reimbursement_window_days)
    latest = date_to or reimbursement.transaction_date
    query = (
        select(LedgerTransaction).where(
            LedgerTransaction.household_id == membership.household_id,
            LedgerTransaction.amount_minor < 0,
            LedgerTransaction.currency_code == reimbursement.currency_code,
            LedgerTransaction.transaction_date <= latest,
            LedgerTransaction.transaction_date >= earliest,
            LedgerTransaction.voided_at.is_(None),
        )
    )
    if account_id:
        query = query.where(LedgerTransaction.account_id == account_id)
    if amount_minor is not None:
        query = query.where(LedgerTransaction.amount_minor == -abs(amount_minor))
    if payee:
        query = query.where(func.lower(LedgerTransaction.payee).contains(payee.strip().casefold()))
    if category_id:
        query = query.join(TransactionSplit, TransactionSplit.transaction_id == LedgerTransaction.id).where(TransactionSplit.category_id == category_id)
    expenses = db.scalars(query).unique().all()
    result = []
    for expense in expenses:
        linked = db.scalar(select(func.sum(ReimbursementLink.amount_minor)).where(ReimbursementLink.original_transaction_id == expense.id)) or 0
        remaining = abs(expense.amount_minor) - linked
        if remaining <= 0:
            continue
        category_id = db.scalar(select(TransactionSplit.category_id).where(TransactionSplit.transaction_id == expense.id).limit(1))
        category = db.get(Category, category_id) if category_id else None
        days = (reimbursement.transaction_date - expense.transaction_date).days
        same_merchant = bool(reimbursement.merchant_id and reimbursement.merchant_id == expense.merchant_id)
        amount_fit = min(reimbursement.amount_minor, remaining) == reimbursement.amount_minor
        same_account = reimbursement.account_id == expense.account_id
        confidence = 50 + (20 if same_merchant else 0) + (15 if amount_fit else 0) + (10 if days <= 30 else 0) + (5 if same_account else 0)
        evidence = [f"same currency; {days} day(s) earlier", f"{remaining} minor units remain eligible", "same account" if same_account else "different account"]
        if same_merchant:
            evidence.append("same canonical merchant")
        if amount_fit:
            evidence.append("reimbursement fits remaining expense")
        account = db.get(FinancialAccount, expense.account_id)
        result.append({"transaction_id": str(expense.id), "payee": expense.payee, "transaction_date": expense.transaction_date.isoformat(), "amount_minor": expense.amount_minor, "remaining_minor": min(reimbursement.amount_minor, remaining), "original_remaining_minor": remaining, "proposed_amount_minor": min(reimbursement.amount_minor, remaining), "currency_code": expense.currency_code, "account_id": str(expense.account_id), "account_name": account.name, "category_id": str(category.id) if category else None, "category_name": category.name if category else None, "confidence_percent": min(confidence, 100), "evidence": "; ".join(evidence)})
    return sorted(result, key=lambda item: (-item["confidence_percent"], item["transaction_date"], item["transaction_id"]))[:25]


@router.get("/reimbursements")
def reimbursements(
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
) -> list[dict]:
    items = db.scalars(
        select(ReimbursementLink).where(
            ReimbursementLink.household_id == membership.household_id
        ).order_by(ReimbursementLink.created_at.desc())
    ).all()
    result = []
    for item in items:
        reimbursement = db.get(LedgerTransaction, item.reimbursement_transaction_id)
        original = db.get(LedgerTransaction, item.original_transaction_id)
        category = db.get(Category, item.category_id)
        result.append(
            {
                "reimbursement_link_id": str(item.id),
                "reimbursement_transaction_id": str(reimbursement.id),
                "reimbursement_payee": reimbursement.payee,
                "original_transaction_id": str(original.id),
                "original_payee": original.payee,
                "category_name": category.name,
                "amount_minor": item.amount_minor,
                "currency_code": reimbursement.currency_code,
                "created_at": item.created_at.isoformat(),
            }
        )
    return result


@router.delete("/reimbursements/{link_id}", status_code=204)
def delete_reimbursement(link_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> Response:
    item = db.scalar(select(ReimbursementLink).where(ReimbursementLink.id == link_id, ReimbursementLink.household_id == membership.household_id))
    if not item: raise HTTPException(status_code=404, detail="Reimbursement link not found")
    db.execute(delete(TransactionSplit).where(TransactionSplit.transaction_id == item.reimbursement_transaction_id, TransactionSplit.category_id == item.category_id, TransactionSplit.amount_minor == item.amount_minor))
    db.delete(item); db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="automation.reimbursement_unlinked", resource_type="reimbursement_link", resource_id=str(link_id))); db.commit(); return Response(status_code=204)


@router.get("/recurring/{transaction_id}/proposal")
def get_recurring_proposal(transaction_id: UUID, db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> dict:
    transaction = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == transaction_id, LedgerTransaction.household_id == membership.household_id))
    if not transaction: raise HTTPException(status_code=404, detail="Transaction not found")
    return recurring_proposal(db, transaction)


@router.get("/recurring-candidates")
def recurring_candidates(
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
) -> list[dict]:
    linked = set(
        db.scalars(
            select(RecurringProfileLink.transaction_id).where(
                RecurringProfileLink.household_id == membership.household_id
            )
        ).all()
    )
    transactions = db.scalars(
        select(LedgerTransaction).where(
            LedgerTransaction.household_id == membership.household_id,
            LedgerTransaction.status == "posted",
            LedgerTransaction.voided_at.is_(None),
        ).order_by(LedgerTransaction.transaction_date.desc()).limit(250)
    ).all()
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for transaction in transactions:
        if transaction.id in linked:
            continue
        proposal = recurring_proposal(db, transaction)
        key = (proposal["profile_type"], proposal["name"].strip().casefold())
        if proposal["eligible"] and key not in seen:
            result.append({"transaction_id": str(transaction.id), **proposal})
            seen.add(key)
    return result[:25]


@router.get("/recurring-links")
def recurring_links(
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
) -> list[dict]:
    links = db.scalars(select(RecurringProfileLink).where(RecurringProfileLink.household_id == membership.household_id).order_by(RecurringProfileLink.created_at.desc())).all()
    result = []
    for link in links:
        transaction = db.get(LedgerTransaction, link.transaction_id)
        profile = db.get(BillProfile, link.bill_profile_id) if link.bill_profile_id else db.get(IncomeSource, link.income_source_id)
        result.append({"recurring_link_id": str(link.id), "profile_type": link.profile_type, "profile_id": str(link.bill_profile_id or link.income_source_id), "profile_name": profile.name, "transaction_id": str(transaction.id), "transaction_date": transaction.transaction_date.isoformat(), "payee": transaction.payee, "amount_minor": transaction.amount_minor, "currency_code": transaction.currency_code, "evidence": link.evidence})
    return result


@router.delete("/recurring-links/{link_id}", status_code=204)
def delete_recurring_link(
    link_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(writer)],
) -> Response:
    link = db.scalar(select(RecurringProfileLink).where(RecurringProfileLink.id == link_id, RecurringProfileLink.household_id == membership.household_id))
    if not link:
        raise HTTPException(status_code=404, detail="Recurring link not found")
    db.delete(link)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="automation.recurring_unlinked", resource_type="recurring_profile_link", resource_id=str(link_id), detail="Transaction and recurring profile preserved"))
    db.commit()
    return Response(status_code=204)


@router.post("/recurring", status_code=201)
def create_recurring(request: RecurringCreate, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    transaction = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == request.transaction_id, LedgerTransaction.household_id == membership.household_id))
    if not transaction: raise HTTPException(status_code=404, detail="Transaction not found")
    proposal = recurring_proposal(db, transaction)
    if not proposal["eligible"] and not request.force: raise HTTPException(status_code=409, detail="Three related transactions are required for an automatic recurring proposal; use manual confirmation to continue")
    name, cadence = (request.name or proposal["name"]).strip(), request.cadence or proposal["cadence"]
    if proposal["profile_type"] == "bill":
        if db.scalar(select(BillProfile.id).where(BillProfile.household_id == membership.household_id, BillProfile.name == name)): raise HTTPException(status_code=409, detail="A bill profile with this name already exists")
        profile = BillProfile(household_id=membership.household_id, name=name, payee=transaction.payee or transaction.raw_payee, cadence=cadence, next_due_date=datetime.fromisoformat(proposal["next_expected_date"]).date(), due_day=datetime.fromisoformat(proposal["next_expected_date"]).day, expected_amount_minor=proposal["expected_amount_minor"], minimum_amount_minor=proposal["minimum_amount_minor"], maximum_amount_minor=proposal["maximum_amount_minor"], currency_code=transaction.currency_code)
        db.add(profile); db.flush(); profile_id, profile_type = profile.id, "bill"
    else:
        if db.scalar(select(IncomeSource.id).where(IncomeSource.household_id == membership.household_id, IncomeSource.name == name)): raise HTTPException(status_code=409, detail="An income profile with this name already exists")
        profile = IncomeSource(household_id=membership.household_id, name=name, payer=transaction.payee or transaction.raw_payee, cadence=cadence, next_expected_date=datetime.fromisoformat(proposal["next_expected_date"]).date(), expected_amount_minor=proposal["expected_amount_minor"], minimum_amount_minor=proposal["minimum_amount_minor"], maximum_amount_minor=proposal["maximum_amount_minor"], currency_code=transaction.currency_code, confidence_percent=100)
        db.add(profile); db.flush(); profile_id, profile_type = profile.id, "income"
    proposed_ids = {UUID(value) for value in proposal["transaction_ids"]}
    selected = set(request.transaction_ids or proposed_ids); selected.add(transaction.id)
    if not selected.issubset(proposed_ids):
        raise HTTPException(status_code=422, detail="Selected history must come from the proposed same-account recurring group")
    if len(selected) < 3 and not request.force:
        raise HTTPException(status_code=409, detail="Select at least three related transactions or use manual confirmation")
    transactions = db.scalars(select(LedgerTransaction).where(LedgerTransaction.household_id == membership.household_id, LedgerTransaction.id.in_(selected))).all()
    for item in transactions:
        if db.scalar(select(RecurringProfileLink.id).where(RecurringProfileLink.transaction_id == item.id)): continue
        db.add(RecurringProfileLink(household_id=membership.household_id, transaction_id=item.id, profile_type=profile_type, bill_profile_id=profile_id if profile_type == "bill" else None, income_source_id=profile_id if profile_type == "income" else None, match_method="user_confirmed", evidence=proposal["evidence"], created_by_user_id=actor.id))
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=f"automation.{profile_type}_profile_created", resource_type=f"{profile_type}_profile", resource_id=str(profile_id), detail=f"linked={len(transactions)};cadence={cadence}")); db.commit()
    return {"profile_type": profile_type, "profile_id": str(profile_id), "linked_transaction_count": len(transactions), "proposal": proposal}

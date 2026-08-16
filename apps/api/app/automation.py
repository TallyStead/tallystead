import fnmatch
import hashlib
import json
from datetime import timedelta
from itertools import pairwise
from statistics import median
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    AutomationDecision,
    BillProfile,
    Category,
    CategoryRule,
    FinancialAccount,
    HouseholdAutomationPreference,
    ImportRow,
    ImportSource,
    ImportSourceMappingVersion,
    IncomeSource,
    LedgerTransaction,
    Merchant,
    MerchantAlias,
    RecurringProfileLink,
    TransferCandidate,
    TransferLink,
)

AUTOMATION_RULE_VERSION = "continuous-import-v1"
MAPPING_FIELDS = (
    "date_column", "payee_column", "original_payee_column", "amount_column", "debit_column",
    "credit_column", "status_column", "category_column", "memo_column", "amount_sign", "date_format",
)


def automation_preferences(db: Session, household_id: UUID) -> HouseholdAutomationPreference:
    preferences = db.get(HouseholdAutomationPreference, household_id)
    if preferences is None:
        preferences = HouseholdAutomationPreference(household_id=household_id)
        db.add(preferences)
        db.flush()
    return preferences


def mapping_snapshot(source: ImportSource) -> dict:
    return {field: getattr(source, field) for field in MAPPING_FIELDS}


def ensure_mapping_version(db: Session, source: ImportSource, actor_user_id: UUID | None) -> ImportSourceMappingVersion:
    mapping = mapping_snapshot(source)
    raw = json.dumps(mapping, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    existing = db.scalar(select(ImportSourceMappingVersion).where(ImportSourceMappingVersion.source_id == source.id, ImportSourceMappingVersion.mapping_hash == digest))
    if existing:
        return existing
    latest = db.scalar(select(func.max(ImportSourceMappingVersion.version_number)).where(ImportSourceMappingVersion.source_id == source.id)) or 0
    item = ImportSourceMappingVersion(household_id=source.household_id, source_id=source.id, version_number=latest + 1, mapping_hash=digest, mapping_json=raw, created_by_user_id=actor_user_id)
    db.add(item)
    db.flush()
    return item


def _description_matches(pattern: str | None, value: str) -> bool:
    if not pattern:
        return True
    return fnmatch.fnmatch(value.casefold(), pattern.casefold())


def merchant_for_payee(db: Session, household_id: UUID, payee: str | None) -> Merchant | None:
    value = (payee or "").strip()
    if not value:
        return None
    merchant = db.scalar(select(Merchant).where(Merchant.household_id == household_id, func.lower(Merchant.name) == value.casefold(), Merchant.is_archived.is_(False)))
    if merchant:
        return merchant
    alias = db.scalar(select(MerchantAlias).where(MerchantAlias.household_id == household_id, func.lower(MerchantAlias.alias) == value.casefold()))
    return db.get(Merchant, alias.merchant_id) if alias else None


def matching_category_rule(db: Session, row: ImportRow, source: ImportSource) -> tuple[CategoryRule, Category, str] | None:
    if row.amount_minor is None:
        return None
    direction = "in" if row.amount_minor > 0 else "out"
    merchant = merchant_for_payee(db, row.household_id, row.raw_payee or row.normalized_payee)
    payee = (row.normalized_payee or row.raw_payee or "").strip().casefold()
    rules = db.scalars(select(CategoryRule).where(CategoryRule.household_id == row.household_id, CategoryRule.direction == direction, CategoryRule.is_active.is_(True), CategoryRule.auto_apply.is_(True))).all()
    matches: list[tuple[int, int, CategoryRule]] = []
    for rule in rules:
        exact_match = (rule.match_type == "merchant" and merchant is not None and rule.match_value == str(merchant.id)) or (rule.match_type == "payee" and payee and rule.match_value.casefold() == payee)
        base_match = _description_matches(rule.description_pattern, payee) if rule.description_pattern else exact_match
        if not base_match or (rule.account_id and rule.account_id != source.account_id) or (rule.source_id and rule.source_id != source.id):
            continue
        absolute = abs(row.amount_minor)
        if rule.amount_min_minor is not None and absolute < rule.amount_min_minor:
            continue
        if rule.amount_max_minor is not None and absolute > rule.amount_max_minor:
            continue
        specificity = sum((merchant is not None and rule.match_type == "merchant", rule.account_id is not None, rule.source_id is not None, rule.amount_min_minor is not None, rule.amount_max_minor is not None, rule.description_pattern is not None))
        matches.append((specificity, -rule.priority, rule))
    if not matches:
        return None
    rule = min(matches, key=lambda item: (-item[0], item[1], item[2].created_at, str(item[2].id)))[2]
    category = db.get(Category, rule.category_id)
    if not category or category.is_archived:
        return None
    scope = [f"confirmed {rule.match_type}={rule.match_value}", f"direction={direction}"]
    if rule.account_id: scope.append("account scoped")
    if rule.source_id: scope.append("source scoped")
    return rule, category, "; ".join(scope)


def propose_row_automation(db: Session, row: ImportRow, source: ImportSource) -> None:
    match = matching_category_rule(db, row, source)
    if match:
        rule, category, evidence = match
        row.status = "ready"
        row.automation_kind = "exact_rule"
        row.applied_rule_id = rule.id
        row.proposed_category_id = category.id
        row.automation_confidence = 100
        row.automation_evidence = evidence
        db.add(AutomationDecision(household_id=row.household_id, entity_type="import_row", entity_id=str(row.id), decision_type="categorization", rule_id=rule.id, provider="household_rule", confidence_percent=100, evidence_json=json.dumps([evidence]), outcome_json=json.dumps({"category_id": str(category.id), "category_name": category.name}), status="proposed"))
        return
    if row.transaction_date is None or row.amount_minor is None:
        return

    payee = (row.normalized_payee or row.raw_payee or "").strip().casefold()
    if payee:
        if row.amount_minor < 0:
            profiles = db.scalars(
                select(BillProfile).where(
                    BillProfile.household_id == row.household_id,
                    BillProfile.is_active.is_(True),
                    BillProfile.currency_code == row.currency_code,
                )
            ).all()
            recurring = next(
                (
                    item
                    for item in profiles
                    if item.payee
                    and item.payee.strip().casefold() == payee
                    and (item.minimum_amount_minor is None or abs(row.amount_minor) >= item.minimum_amount_minor)
                    and (item.maximum_amount_minor is None or abs(row.amount_minor) <= item.maximum_amount_minor)
                ),
                None,
            )
            profile_type = "bill"
        else:
            profiles = db.scalars(
                select(IncomeSource).where(
                    IncomeSource.household_id == row.household_id,
                    IncomeSource.is_active.is_(True),
                    IncomeSource.currency_code == row.currency_code,
                )
            ).all()
            recurring = next(
                (
                    item
                    for item in profiles
                    if item.payer
                    and item.payer.strip().casefold() == payee
                    and (item.minimum_amount_minor is None or abs(row.amount_minor) >= item.minimum_amount_minor)
                    and (item.maximum_amount_minor is None or abs(row.amount_minor) <= item.maximum_amount_minor)
                ),
                None,
            )
            profile_type = "income"
        if recurring:
            evidence = (
                f"Matches confirmed {profile_type} profile '{recurring.name}' by payee, "
                "currency, direction, and amount range; review is still required."
            )
            row.automation_kind = "recurring_match"
            row.automation_confidence = 95
            row.automation_evidence = evidence
            db.add(
                AutomationDecision(
                    household_id=row.household_id,
                    entity_type="import_row",
                    entity_id=str(row.id),
                    decision_type="recurring_match",
                    provider="confirmed_profile",
                    confidence_percent=95,
                    evidence_json=json.dumps([evidence]),
                    outcome_json=json.dumps(
                        {"profile_type": profile_type, "profile_id": str(recurring.id)}
                    ),
                    status="proposed",
                )
            )
            return
    linked_ids: set[UUID] = set()
    for link in db.scalars(select(TransferLink).where(TransferLink.household_id == row.household_id)).all():
        linked_ids.update((link.from_transaction_id, link.to_transaction_id))
    window_days = automation_preferences(db, row.household_id).transfer_window_days
    candidates = db.scalars(select(LedgerTransaction).join(FinancialAccount, FinancialAccount.id == LedgerTransaction.account_id).where(LedgerTransaction.household_id == row.household_id, LedgerTransaction.account_id != source.account_id, LedgerTransaction.currency_code == row.currency_code, LedgerTransaction.amount_minor == -row.amount_minor, LedgerTransaction.transaction_date >= row.transaction_date - timedelta(days=window_days), LedgerTransaction.transaction_date <= row.transaction_date + timedelta(days=window_days), LedgerTransaction.voided_at.is_(None))).all()
    candidates = [item for item in candidates if item.id not in linked_ids]
    for transaction in candidates:
        days = abs((transaction.transaction_date - row.transaction_date).days)
        confidence = 98 if days == 0 else 94 if days == 1 else 90
        evidence = f"Opposite {row.currency_code} amount in another owned account within the configured {window_days}-day window ({days} day(s) apart); confirmation required."
        existing = db.scalar(
            select(TransferCandidate).where(
                TransferCandidate.import_row_id == row.id,
                TransferCandidate.counterparty_transaction_id == transaction.id,
            )
        )
        if existing and existing.status != "pending":
            continue
        if not existing:
            db.add(TransferCandidate(household_id=row.household_id, import_row_id=row.id, counterparty_transaction_id=transaction.id, confidence_percent=confidence, evidence=evidence))
        row.automation_kind = "transfer_candidate"
        row.automation_confidence = max(row.automation_confidence or 0, confidence)
        row.automation_evidence = evidence
    pending_rows = db.scalars(
        select(ImportRow).where(
            ImportRow.household_id == row.household_id,
            ImportRow.id != row.id,
            ImportRow.currency_code == row.currency_code,
            ImportRow.amount_minor == -row.amount_minor,
            ImportRow.transaction_date >= row.transaction_date - timedelta(days=window_days),
            ImportRow.transaction_date <= row.transaction_date + timedelta(days=window_days),
            ImportRow.status.in_(("unmatched", "ready", "deferred")),
        )
    ).all()
    for counterpart_row in pending_rows:
        counterpart_source = db.get(ImportSource, counterpart_row.source_id)
        if not counterpart_source or counterpart_source.account_id == source.account_id:
            continue
        existing = db.scalar(
            select(TransferCandidate).where(
                (
                    (TransferCandidate.import_row_id == row.id)
                    & (TransferCandidate.counterparty_import_row_id == counterpart_row.id)
                )
                | (
                    (TransferCandidate.import_row_id == counterpart_row.id)
                    & (TransferCandidate.counterparty_import_row_id == row.id)
                )
            )
        )
        if existing:
            continue
        days = abs((counterpart_row.transaction_date - row.transaction_date).days)
        confidence = 98 if days == 0 else 94 if days == 1 else 90
        evidence = (
            f"Opposite pending {row.currency_code} import in another owned account within "
            f"the configured {window_days}-day window ({days} day(s) apart); confirmation required."
        )
        db.add(
            TransferCandidate(
                household_id=row.household_id,
                import_row_id=row.id,
                counterparty_import_row_id=counterpart_row.id,
                confidence_percent=confidence,
                evidence=evidence,
            )
        )
        row.automation_kind = "transfer_candidate"
        row.automation_confidence = max(row.automation_confidence or 0, confidence)
        row.automation_evidence = evidence
        counterpart_row.automation_kind = "transfer_candidate"
        counterpart_row.automation_confidence = max(counterpart_row.automation_confidence or 0, confidence)
        counterpart_row.automation_evidence = evidence


def recompute_pending_rows(db: Session, household_id: UUID) -> None:
    db.execute(
        delete(TransferCandidate).where(
            TransferCandidate.household_id == household_id,
            TransferCandidate.status == "pending",
        )
    )
    rows = db.scalars(
        select(ImportRow).where(
            ImportRow.household_id == household_id,
            ImportRow.status.in_(("unmatched", "ready", "deferred")),
        )
    ).all()
    for row in rows:
        source = db.get(ImportSource, row.source_id)
        if not source:
            continue
        if row.status == "ready":
            row.status = "unmatched"
        row.automation_kind = None
        row.applied_rule_id = None
        row.proposed_category_id = None
        row.automation_confidence = None
        row.automation_evidence = None
        db.execute(
            AutomationDecision.__table__.update()
            .where(
                AutomationDecision.entity_type == "import_row",
                AutomationDecision.entity_id == str(row.id),
                AutomationDecision.status == "proposed",
            )
            .values(status="superseded")
        )
        propose_row_automation(db, row, source)

    batches = db.scalars(
        select(ImportRow.batch_id).where(ImportRow.household_id == household_id).distinct()
    ).all()
    for batch_id in batches:
        from app.models import ImportBatch

        batch = db.get(ImportBatch, batch_id)
        if not batch:
            continue
        batch.ready_count = db.scalar(
            select(func.count()).select_from(ImportRow).where(
                ImportRow.batch_id == batch_id, ImportRow.status == "ready"
            )
        ) or 0
        batch.transfer_count = db.scalar(
            select(func.count()).select_from(ImportRow).where(
                ImportRow.batch_id == batch_id,
                ImportRow.automation_kind == "transfer_candidate",
                ImportRow.status != "matched",
            )
        ) or 0
        batch.recurring_count = db.scalar(
            select(func.count()).select_from(ImportRow).where(
                ImportRow.batch_id == batch_id,
                ImportRow.automation_kind == "recurring_match",
                ImportRow.status != "matched",
            )
        ) or 0
        batch.review_count = db.scalar(
            select(func.count()).select_from(ImportRow).where(
                ImportRow.batch_id == batch_id,
                ImportRow.status.in_(("unmatched", "deferred")),
                ImportRow.automation_kind.is_(None),
            )
        ) or 0


def record_applied_decision(db: Session, row: ImportRow, actor_user_id: UUID, transaction_id: UUID) -> None:
    decision = db.scalar(select(AutomationDecision).where(AutomationDecision.entity_type == "import_row", AutomationDecision.entity_id == str(row.id), AutomationDecision.status == "proposed").order_by(AutomationDecision.created_at.desc()))
    if decision:
        outcome = json.loads(decision.outcome_json)
        decision.status = "applied"
        decision.actor_user_id = actor_user_id
        decision.outcome_json = json.dumps({**outcome, "transaction_id": str(transaction_id)})
        if decision.decision_type == "recurring_match":
            profile_type = outcome.get("profile_type")
            profile_id = UUID(outcome["profile_id"])
            existing = db.scalar(
                select(RecurringProfileLink.id).where(
                    RecurringProfileLink.transaction_id == transaction_id
                )
            )
            if not existing:
                db.add(
                    RecurringProfileLink(
                        household_id=row.household_id,
                        transaction_id=transaction_id,
                        profile_type=profile_type,
                        bill_profile_id=profile_id if profile_type == "bill" else None,
                        income_source_id=profile_id if profile_type == "income" else None,
                        match_method="confirmed_import_match",
                        evidence=row.automation_evidence or "User confirmed recurring import match",
                        created_by_user_id=actor_user_id,
                    )
                )


def learn_rule_from_row(db: Session, row: ImportRow, source: ImportSource, category_id: UUID, actor_user_id: UUID) -> CategoryRule | None:
    value = (row.normalized_payee or row.raw_payee or "").strip().casefold()
    if not value or row.amount_minor is None:
        return None
    merchant = merchant_for_payee(db, row.household_id, row.raw_payee or row.normalized_payee)
    match_type, match_value = ("merchant", str(merchant.id)) if merchant else ("payee", value)
    direction = "in" if row.amount_minor > 0 else "out"
    rule = db.scalar(select(CategoryRule).where(CategoryRule.household_id == row.household_id, CategoryRule.match_type == match_type, CategoryRule.match_value == match_value, CategoryRule.direction == direction, CategoryRule.account_id == source.account_id, CategoryRule.source_id == source.id))
    if rule:
        rule.category_id = category_id
        rule.is_active = True
        rule.auto_apply = True
        return rule
    rule = CategoryRule(household_id=row.household_id, category_id=category_id, match_type=match_type, match_value=match_value, rule_name=f"{(row.normalized_payee or row.raw_payee or 'New rule').strip()} rule", direction=direction, account_id=source.account_id, source_id=source.id, priority=100, auto_apply=True, created_from_action="apply_and_remember", created_by_user_id=actor_user_id)
    db.add(rule)
    db.flush()
    return rule


def recurring_proposal(db: Session, transaction: LedgerTransaction) -> dict:
    payee = (transaction.payee or transaction.raw_payee or "").strip().casefold()
    direction_filter = LedgerTransaction.amount_minor > 0 if transaction.amount_minor > 0 else LedgerTransaction.amount_minor < 0
    related = db.scalars(select(LedgerTransaction).where(LedgerTransaction.household_id == transaction.household_id, LedgerTransaction.id != transaction.id, LedgerTransaction.currency_code == transaction.currency_code, direction_filter, LedgerTransaction.status == "posted", LedgerTransaction.voided_at.is_(None)).order_by(LedgerTransaction.transaction_date)).all()
    related = [item for item in related if item.account_id == transaction.account_id and ((transaction.merchant_id and item.merchant_id == transaction.merchant_id) or (payee and (item.payee or item.raw_payee or "").strip().casefold() == payee))]
    all_items = sorted([*related, transaction], key=lambda item: item.transaction_date)
    gaps = [(right.transaction_date - left.transaction_date).days for left, right in pairwise(all_items)]
    typical_gap = int(median(gaps)) if gaps else 0
    cadence = "weekly" if 5 <= typical_gap <= 9 else "biweekly" if 12 <= typical_gap <= 17 else "monthly" if 25 <= typical_gap <= 35 else "quarterly" if 75 <= typical_gap <= 100 else "yearly" if 330 <= typical_gap <= 400 else "irregular"
    amounts = [abs(item.amount_minor) for item in all_items]
    return {"eligible": len(all_items) >= 3, "transaction_ids": [str(item.id) for item in all_items], "transactions": [{"transaction_id": str(item.id), "transaction_date": item.transaction_date.isoformat(), "payee": item.payee or item.raw_payee, "amount_minor": item.amount_minor, "account_id": str(item.account_id)} for item in all_items], "occurrences": len(all_items), "cadence": cadence, "expected_amount_minor": int(median(amounts)), "minimum_amount_minor": min(amounts), "maximum_amount_minor": max(amounts), "currency_code": transaction.currency_code, "next_expected_date": (all_items[-1].transaction_date + timedelta(days=typical_gap or 30)).isoformat(), "name": transaction.payee or transaction.raw_payee or "Recurring activity", "profile_type": "income" if transaction.amount_minor > 0 else "bill", "evidence": f"{len(all_items)} same-direction transaction(s) in the same account; median interval {typical_gap or 'unknown'} days; amount range {min(amounts)}–{max(amounts)} minor units."}

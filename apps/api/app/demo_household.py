from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data_management import delete_household_data, household_object_keys
from app.ledger import DEFAULT_CATEGORIES
from app.models import (
    AccountValuation,
    BillInstance,
    BillProfile,
    Category,
    CategoryRule,
    Debt,
    Document,
    DocumentMatch,
    FinancialAccount,
    FinancialGoal,
    FinancialPlan,
    HouseholdDataState,
    ImportBatch,
    ImportRow,
    ImportSource,
    ImportSourceMappingVersion,
    IncomeEvent,
    IncomeSource,
    LedgerTransaction,
    Merchant,
    PlanStep,
    PlanVersion,
    ReconciliationException,
    ReimbursementLink,
    ReportPreset,
    TransactionSplit,
    TransferCandidate,
    TransferLink,
)
from app.object_store import put_object
from app.plans import BABY_STEPS

DEMO_SEED = "tallystead-fictional-dawson-v1"
DEMO_NAMESPACE = UUID("529e928e-b258-4ce5-8c20-c5635a82df78")


def demo_id(household_id: UUID, key: str) -> UUID:
    return uuid5(DEMO_NAMESPACE, f"{household_id}:{DEMO_SEED}:{key}")


def has_household_activity(db: Session, household_id: UUID) -> bool:
    return bool(db.scalar(select(func.count(FinancialAccount.id)).where(FinancialAccount.household_id == household_id)))


def _created(reference_date: date, offset: int = 0) -> datetime:
    return datetime.combine(reference_date - timedelta(days=offset), datetime.min.time(), tzinfo=UTC)


def create_demo_household(db: Session, household_id: UUID, actor_user_id: UUID, reference_date: date, volume: str = "realistic") -> dict:
    if has_household_activity(db, household_id):
        raise ValueError("Demo data can only be created in an empty household")

    existing_categories = db.scalars(select(Category).where(Category.household_id == household_id)).all()
    for item in existing_categories:
        db.delete(item)
    db.flush()

    categories: dict[str, Category] = {}
    for name, category_type in DEFAULT_CATEGORIES:
        item = Category(
            id=demo_id(household_id, f"category:{name}"),
            household_id=household_id,
            name=name,
            category_type=category_type,
            is_system_default=True,
            created_at=_created(reference_date, 300),
        )
        categories[name] = item
        db.add(item)

    account_specs = [
        ("checking", "Dawson Household Checking", "checking", 280_000, True, "asset", "spendable", "none"),
        ("savings", "Emergency Savings", "savings", 125_000, True, "asset", "spendable", "none"),
        ("credit", "Fictional Rewards Card", "credit_card", 0, False, "liability", "credit", "none"),
        ("mortgage", "Fictional Home Mortgage", "mortgage", 0, False, "liability", "restricted", "none"),
        ("retirement", "Workplace 401(k)", "401k", 0, False, "asset", "restricted", "traditional"),
        ("hsa", "Household HSA", "hsa", 0, False, "asset", "restricted", "hsa"),
    ]
    accounts: dict[str, FinancialAccount] = {}
    for key, name, account_type, opening, planner, nature, liquidity, tax in account_specs:
        item = FinancialAccount(
            id=demo_id(household_id, f"account:{key}"), household_id=household_id, name=name,
            account_type=account_type, currency_code="USD", opening_balance_minor=opening,
            include_in_planner=planner, include_in_net_worth=True, ownership_scope="household",
            balance_nature=nature, liquidity=liquidity, tax_treatment=tax,
            institution="Fictional Community Financial", masked_identifier=f"demo-{key}",
            created_at=_created(reference_date, 300),
        )
        accounts[key] = item
        db.add(item)
    db.flush()

    db.add_all([
        AccountValuation(id=demo_id(household_id, "valuation:mortgage"), household_id=household_id, account_id=accounts["mortgage"].id, valuation_date=reference_date, value_minor=21_850_000, currency_code="USD", source_type="demo", note="Fictional mortgage balance", created_by_user_id=actor_user_id),
        AccountValuation(id=demo_id(household_id, "valuation:retirement"), household_id=household_id, account_id=accounts["retirement"].id, valuation_date=reference_date, value_minor=4_875_000, currency_code="USD", source_type="demo", note="Fictional 401(k) value", created_by_user_id=actor_user_id),
        AccountValuation(id=demo_id(household_id, "valuation:hsa"), household_id=household_id, account_id=accounts["hsa"].id, valuation_date=reference_date, value_minor=385_000, currency_code="USD", source_type="demo", note="Fictional HSA value", created_by_user_id=actor_user_id),
    ])

    merchant_names = ["H-E-B Demo Market", "Northstar Energy", "Juniper Cafe", "StreamBox", "Cedar Health Clinic", "City Electric", "Neighborhood Cinema"]
    merchants = {}
    for name in merchant_names:
        item = Merchant(id=demo_id(household_id, f"merchant:{name}"), household_id=household_id, name=name, created_at=_created(reference_date, 300))
        merchants[name] = item
        db.add(item)
    db.flush()

    transactions: list[LedgerTransaction] = []
    original_healthcare: LedgerTransaction | None = None
    reimbursement: LedgerTransaction | None = None

    def add_transaction(key: str, account: str, event_date: date, amount: int, payee: str, category: str | None, *, source_type: str = "demo", status: str = "posted") -> LedgerTransaction:
        merchant = merchants.get(payee)
        transaction = LedgerTransaction(
            id=demo_id(household_id, f"transaction:{key}"), household_id=household_id,
            account_id=accounts[account].id, merchant_id=merchant.id if merchant else None,
            created_by_user_id=actor_user_id, transaction_date=event_date, amount_minor=amount,
            currency_code="USD", status=status, payee=payee, raw_payee=payee,
            memo="Clearly fictional Phase 6C demonstration data", source_type=source_type,
            source_reference=f"demo:{key}", created_at=_created(event_date), updated_at=_created(event_date),
        )
        db.add(transaction)
        db.flush()
        if category:
            db.add(TransactionSplit(id=demo_id(household_id, f"split:{key}"), transaction_id=transaction.id, category_id=categories[category].id, amount_minor=amount, created_at=_created(event_date)))
        transactions.append(transaction)
        return transaction

    month_count = 2 if volume == "smoke" else 9
    for month in range(month_count - 1, -1, -1):
        anchor = reference_date - timedelta(days=month * 30)
        add_transaction(f"payroll-a-{month}", "checking", anchor.replace(day=min(5, anchor.day)), 315_000, "Fictional Northstar Payroll", "Paycheck")
        add_transaction(f"payroll-b-{month}", "checking", anchor.replace(day=min(19, anchor.day)), 315_000, "Fictional Northstar Payroll", "Paycheck")
        add_transaction(f"partner-{month}", "checking", anchor.replace(day=min(12, anchor.day)), 245_000, "Fictional Juniper Studio Payroll", "Paycheck")
        add_transaction(f"housing-{month}", "checking", anchor.replace(day=min(2, anchor.day)), -168_000, "Fictional Home Mortgage", "Housing")
        add_transaction(f"utilities-{month}", "checking", anchor.replace(day=min(9, anchor.day)), -(13_000 + month * 275), "City Electric", "Utilities")
        add_transaction(f"groceries-{month}", "checking", anchor.replace(day=min(14, anchor.day)), -(18_500 + month * 137), "H-E-B Demo Market", "Groceries")
        add_transaction(f"fuel-{month}", "credit", anchor.replace(day=min(16, anchor.day)), -(6_200 + month * 53), "Northstar Energy", "Fuel")
        add_transaction(f"dining-{month}", "credit", anchor.replace(day=min(20, anchor.day)), -(7_800 + month * 41), "Juniper Cafe", "Dining out")
        add_transaction(f"subscription-{month}", "credit", anchor.replace(day=min(22, anchor.day)), -1_699, "StreamBox", "Subscriptions")
        add_transaction(f"retirement-{month}", "retirement", anchor.replace(day=min(19, anchor.day)), 47_250, "Fictional Northstar Payroll", None, source_type="investment")
        if month % 3 == 0:
            add_transaction(f"entertainment-{month}", "credit", anchor.replace(day=min(24, anchor.day)), -4_850, "Neighborhood Cinema", "Entertainment")

    original_healthcare = add_transaction("healthcare-original", "credit", reference_date - timedelta(days=24), -12_400, "Cedar Health Clinic", "Healthcare")
    reimbursement = add_transaction("healthcare-refund", "checking", reference_date - timedelta(days=15), 8_000, "Cedar Health Clinic reimbursement", "Healthcare")
    db.add(ReimbursementLink(id=demo_id(household_id, "reimbursement:healthcare"), household_id=household_id, reimbursement_transaction_id=reimbursement.id, original_transaction_id=original_healthcare.id, category_id=categories["Healthcare"].id, amount_minor=8_000, created_by_user_id=actor_user_id))

    savings_out = add_transaction("savings-out", "checking", reference_date - timedelta(days=10), -25_000, "Emergency Savings", None, source_type="transfer")
    savings_in = add_transaction("savings-in", "savings", reference_date - timedelta(days=10), 25_000, "Dawson Household Checking", None, source_type="transfer")
    db.add(TransferLink(id=demo_id(household_id, "transfer:savings"), household_id=household_id, from_transaction_id=savings_out.id, to_transaction_id=savings_in.id, created_by_user_id=actor_user_id))
    card_out = add_transaction("card-payment-out", "checking", reference_date - timedelta(days=7), -45_000, "Fictional Rewards Card", None, source_type="transfer")
    card_in = add_transaction("card-payment-in", "credit", reference_date - timedelta(days=7), 45_000, "Dawson Household Checking", None, source_type="transfer")
    db.add(TransferLink(id=demo_id(household_id, "transfer:card"), household_id=household_id, from_transaction_id=card_out.id, to_transaction_id=card_in.id, created_by_user_id=actor_user_id))

    bill_specs = [
        ("mortgage", "Fictional Home Mortgage", 168_000, True, 1),
        ("electric", "City Electric", 14_500, True, 2),
        ("streaming", "StreamBox", 1_699, False, 4),
    ]
    for key, name, amount, essential, priority in bill_specs:
        profile = BillProfile(id=demo_id(household_id, f"bill-profile:{key}"), household_id=household_id, name=name, payee=name, cadence="monthly", next_due_date=reference_date + timedelta(days=10 + priority), due_day=None, expected_amount_minor=amount, minimum_amount_minor=amount if essential else None, maximum_amount_minor=amount + (2_500 if key == "electric" else 0), currency_code="USD", is_essential=essential, priority=priority)
        db.add(profile)
        db.flush()
        db.add(BillInstance(id=demo_id(household_id, f"bill-instance:{key}"), household_id=household_id, bill_profile_id=profile.id, name=name, due_date=profile.next_due_date, expected_amount_minor=amount, minimum_amount_minor=profile.minimum_amount_minor, maximum_amount_minor=profile.maximum_amount_minor, currency_code="USD", is_essential=essential, priority=priority, status="upcoming"))

    income_specs = [("primary", "Northstar payroll", "biweekly", 315_000), ("partner", "Juniper Studio payroll", "monthly", 245_000)]
    for index, (key, name, cadence, amount) in enumerate(income_specs):
        expected_date = reference_date + timedelta(days=7 + index * 5)
        source = IncomeSource(id=demo_id(household_id, f"income-source:{key}"), household_id=household_id, name=name, payer=name, cadence=cadence, next_expected_date=expected_date, expected_amount_minor=amount, currency_code="USD", confidence_percent=100)
        db.add(source)
        db.flush()
        db.add(IncomeEvent(id=demo_id(household_id, f"income-event:{key}"), household_id=household_id, income_source_id=source.id, name=name, expected_date=expected_date, expected_amount_minor=amount, currency_code="USD", confidence_percent=100, status="expected"))

    debts = [
        Debt(id=demo_id(household_id, "debt:card"), household_id=household_id, account_id=accounts["credit"].id, name="Fictional Rewards Card", lender="Fictional Community Financial", balance_minor=186_500, apr_basis_points=2199, minimum_payment_minor=6_500, due_day=18, next_due_date=reference_date + timedelta(days=18), currency_code="USD"),
        Debt(id=demo_id(household_id, "debt:mortgage"), household_id=household_id, account_id=accounts["mortgage"].id, name="Fictional Home Mortgage", lender="Fictional Community Financial", balance_minor=21_850_000, apr_basis_points=425, minimum_payment_minor=168_000, due_day=2, next_due_date=reference_date + timedelta(days=12), currency_code="USD"),
    ]
    db.add_all(debts)

    source = ImportSource(id=demo_id(household_id, "import-source:checking"), household_id=household_id, account_id=accounts["checking"].id, name="Fictional checking CSV", institution="Fictional Community Financial", format_type="csv_mapped", date_column="date", payee_column="description", amount_column="amount", amount_sign="positive_in", date_format="%Y-%m-%d", export_method="Website → Transactions → Download CSV", export_instructions="Sign in to the fictional institution, select the date range, and export CSV.", reminder_interval_days=30, next_reminder_date=reference_date + timedelta(days=30), reminders_enabled=True, is_active=True)
    db.add(source)
    db.flush()
    mapping = ImportSourceMappingVersion(id=demo_id(household_id, "mapping:checking:1"), household_id=household_id, source_id=source.id, version_number=1, mapping_hash=hashlib.sha256(b"demo-mapping-v1").hexdigest(), mapping_json=json.dumps({"date_column": "date", "payee_column": "description", "amount_column": "amount", "date_format": "%Y-%m-%d", "amount_sign": "positive_in"}, sort_keys=True), created_by_user_id=actor_user_id)
    db.add(mapping)
    db.flush()
    raw_csv = f"date,description,amount\n{reference_date.isoformat()},H-E-B DEMO MARKET,-63.42\n{reference_date.isoformat()},UNCLEAR DEMO CHARGE,-27.19\n"
    batch = ImportBatch(id=demo_id(household_id, "import-batch:checking"), household_id=household_id, source_id=source.id, created_by_user_id=actor_user_id, filename="fictional-checking.csv", file_checksum=hashlib.sha256(raw_csv.encode()).hexdigest(), parser_version="csv-v2", status="review", raw_csv=raw_csv, row_count=8, candidate_count=5, duplicate_count=1, invalid_count=1, ready_count=1, transfer_count=2, recurring_count=1, review_count=5, mapping_version_id=mapping.id, ingestion_channel="csv_upload")
    db.add(batch)
    db.flush()
    rule = CategoryRule(id=demo_id(household_id, "rule:heb"), household_id=household_id, category_id=categories["Groceries"].id, match_type="exact", match_value="h-e-b demo market", rule_name="H-E-B groceries", direction="out", account_id=accounts["checking"].id, source_id=source.id, priority=100, auto_apply=True, use_count=4, created_from_action="apply_and_remember", is_active=True, created_by_user_id=actor_user_id)
    db.add(rule)
    db.flush()
    rows = [
        ImportRow(id=demo_id(household_id, "import-row:ready"), household_id=household_id, source_id=source.id, batch_id=batch.id, row_number=1, raw_json=json.dumps({"date": reference_date.isoformat(), "description": "H-E-B DEMO MARKET", "amount": "-63.42"}), raw_text="H-E-B DEMO MARKET", row_hash=hashlib.sha256(b"demo-ready").hexdigest(), transaction_date=reference_date, amount_minor=-6_342, currency_code="USD", raw_payee="H-E-B DEMO MARKET", normalized_payee="h-e-b demo market", status="ready", automation_kind="category_rule", applied_rule_id=rule.id, proposed_category_id=categories["Groceries"].id, automation_confidence=100, automation_evidence="Exact household rule"),
        ImportRow(id=demo_id(household_id, "import-row:review"), household_id=household_id, source_id=source.id, batch_id=batch.id, row_number=2, raw_json=json.dumps({"date": reference_date.isoformat(), "description": "UNCLEAR DEMO CHARGE", "amount": "-27.19"}), raw_text="UNCLEAR DEMO CHARGE", row_hash=hashlib.sha256(b"demo-review").hexdigest(), transaction_date=reference_date, amount_minor=-2_719, currency_code="USD", raw_payee="UNCLEAR DEMO CHARGE", normalized_payee="unclear demo charge", status="review", exception_type="ambiguous"),
        ImportRow(id=demo_id(household_id, "import-row:transfer-out"), household_id=household_id, source_id=source.id, batch_id=batch.id, row_number=3, raw_json="{}", raw_text="TRANSFER TO SAVINGS", row_hash=hashlib.sha256(b"demo-transfer-out").hexdigest(), transaction_date=reference_date - timedelta(days=2), amount_minor=-25_000, currency_code="USD", raw_payee="TRANSFER TO SAVINGS", normalized_payee="transfer to savings", status="review", automation_kind="transfer"),
        ImportRow(id=demo_id(household_id, "import-row:transfer-in"), household_id=household_id, source_id=source.id, batch_id=batch.id, row_number=4, raw_json="{}", raw_text="TRANSFER FROM CHECKING", row_hash=hashlib.sha256(b"demo-transfer-in").hexdigest(), transaction_date=reference_date - timedelta(days=2), amount_minor=25_000, currency_code="USD", raw_payee="TRANSFER FROM CHECKING", normalized_payee="transfer from checking", status="review", automation_kind="transfer"),
        ImportRow(id=demo_id(household_id, "import-row:recurring"), household_id=household_id, source_id=source.id, batch_id=batch.id, row_number=5, raw_json="{}", raw_text="CITY ELECTRIC", row_hash=hashlib.sha256(b"demo-recurring").hexdigest(), transaction_date=reference_date - timedelta(days=4), amount_minor=-14_250, currency_code="USD", raw_payee="CITY ELECTRIC", normalized_payee="city electric", status="review", automation_kind="recurring_profile", automation_confidence=92, automation_evidence="Similar to confirmed fictional monthly bill"),
        ImportRow(id=demo_id(household_id, "import-row:reimbursement"), household_id=household_id, source_id=source.id, batch_id=batch.id, row_number=6, raw_json="{}", raw_text="CEDAR HEALTH REIMBURSEMENT", row_hash=hashlib.sha256(b"demo-reimbursement").hexdigest(), transaction_date=reference_date - timedelta(days=3), amount_minor=8_000, currency_code="USD", raw_payee="CEDAR HEALTH REIMBURSEMENT", normalized_payee="cedar health reimbursement", status="review", automation_kind="reimbursement", automation_confidence=88, automation_evidence="Nearby compatible fictional healthcare expense"),
        ImportRow(id=demo_id(household_id, "import-row:duplicate"), household_id=household_id, source_id=source.id, batch_id=batch.id, row_number=7, raw_json="{}", raw_text="STREAMBOX", row_hash=hashlib.sha256(b"demo-duplicate").hexdigest(), transaction_date=reference_date - timedelta(days=5), amount_minor=-1_699, currency_code="USD", raw_payee="STREAMBOX", normalized_payee="streambox", status="duplicate", exception_type="duplicate"),
        ImportRow(id=demo_id(household_id, "import-row:invalid"), household_id=household_id, source_id=source.id, batch_id=batch.id, row_number=8, raw_json="{}", raw_text="INVALID DATE,DEMO", row_hash=hashlib.sha256(b"demo-invalid").hexdigest(), transaction_date=None, amount_minor=None, currency_code="USD", raw_payee="INVALID DEMO ROW", normalized_payee="invalid demo row", status="invalid", exception_type="validation", validation_error="Fictional invalid date for review demonstration"),
    ]
    db.add_all(rows)
    db.flush()
    db.add(ReconciliationException(id=demo_id(household_id, "exception:ambiguous"), household_id=household_id, batch_id=batch.id, exception_type="ambiguous", related_type="import_row", related_id=str(rows[1].id), event_date=reference_date, amount_minor=-2_719, currency_code="USD", detail="Fictional ambiguous charge intentionally left for review", status="open"))
    db.add_all([
        ReconciliationException(id=demo_id(household_id, "exception:duplicate"), household_id=household_id, batch_id=batch.id, exception_type="duplicate", related_type="import_row", related_id=str(rows[6].id), event_date=rows[6].transaction_date, amount_minor=rows[6].amount_minor, currency_code="USD", detail="Fictional suspected duplicate", status="open"),
        ReconciliationException(id=demo_id(household_id, "exception:invalid"), household_id=household_id, batch_id=batch.id, exception_type="validation", related_type="import_row", related_id=str(rows[7].id), currency_code="USD", detail="Fictional invalid row", status="open"),
        TransferCandidate(id=demo_id(household_id, "transfer-candidate:pending-pair"), household_id=household_id, import_row_id=rows[2].id, counterparty_import_row_id=rows[3].id, confidence_percent=100, evidence="Equal and opposite fictional amounts on the same date", status="pending"),
    ])

    plan = FinancialPlan(id=demo_id(household_id, "plan:baby-steps"), household_id=household_id, created_by_user_id=actor_user_id, name="Dawson seven-step plan", template_key="baby_steps", currency_code="USD", debt_strategy="smallest_balance", effective_date=reference_date, assumptions_json="{}", status="active")
    db.add(plan)
    db.flush()
    steps = []
    for position, definition in enumerate(BABY_STEPS, start=1):
        step = PlanStep(id=demo_id(household_id, f"plan-step:{definition['key']}"), plan_id=plan.id, step_key=definition["key"], position=position, title=definition["title"], description=definition["description"], step_type=definition["type"], target_minor=definition.get("target_minor"), target_months=definition.get("target_months"), percentage_basis_points=definition.get("percentage_basis_points"), status="active" if position == 1 else "pending")
        db.add(step)
        steps.append(step)
    db.flush()
    for step in steps:
        linked = accounts["savings"].id if step.step_key in {"starter_emergency_fund", "full_emergency_fund"} else None
        db.add(FinancialGoal(id=demo_id(household_id, f"goal:{step.step_key}"), household_id=household_id, plan_id=plan.id, step_id=step.id, name=step.title, goal_type=step.step_type, target_minor=step.target_minor, linked_account_id=linked, status="active"))
    db.add(PlanVersion(id=demo_id(household_id, "plan-version:1"), plan_id=plan.id, version_number=1, reason="Created with the fictional Phase 6C scenario", snapshot_json=json.dumps({"demo_seed": DEMO_SEED, "reference_date": reference_date.isoformat()}), created_by_user_id=actor_user_id))
    db.add(ReportPreset(id=demo_id(household_id, "report-preset:monthly"), household_id=household_id, created_by_user_id=actor_user_id, name="Demo monthly spending", report_type="spending", filters_json=json.dumps({"currency_code": "USD", "ownership_scope": "household"})))

    receipt_content = b"FICTIONAL DEMO RECEIPT\nH-E-B Demo Market\nGroceries 63.42 USD\nNot a real purchase or merchant document.\n"
    document_id = demo_id(household_id, "document:grocery-receipt")
    object_key = f"{household_id}/{document_id}/original"
    put_object(object_key, receipt_content, "text/plain")
    document = Document(id=document_id, household_id=household_id, uploaded_by_user_id=actor_user_id, account_id=accounts["checking"].id, kind="receipt", filename="FICTIONAL-demo-grocery-receipt.txt", content_type="text/plain", size_bytes=len(receipt_content), checksum_sha256=hashlib.sha256(receipt_content).hexdigest(), object_key=object_key, status="stored", document_date=reference_date, amount_minor=6_342, currency_code="USD", payee="H-E-B Demo Market", notes="Clearly fictional Phase 6C demonstration document")
    db.add(document)
    db.flush()
    grocery_transaction = next(item for item in reversed(transactions) if item.payee == "H-E-B Demo Market")
    db.add(DocumentMatch(id=demo_id(household_id, "document-match:grocery"), household_id=household_id, document_id=document.id, transaction_id=grocery_transaction.id, method="demo_exact", confidence_percent=100, evidence="Fictional amount and merchant match", status="accepted", reviewed_by_user_id=actor_user_id))

    state = db.get(HouseholdDataState, household_id)
    if state:
        state.mode = "demo"
        state.demo_seed = DEMO_SEED
        state.demo_volume = volume
        state.demo_reference_date = reference_date
    else:
        db.add(HouseholdDataState(household_id=household_id, mode="demo", demo_seed=DEMO_SEED, demo_volume=volume, demo_reference_date=reference_date))
    db.flush()
    return {"seed": DEMO_SEED, "volume": volume, "reference_date": reference_date.isoformat(), "transaction_count": len(transactions), "account_count": len(accounts), "review_count": 5}


def reset_demo_household(db: Session, household_id: UUID, actor_user_id: UUID, reference_date: date | None = None, volume: str | None = None) -> tuple[dict, list[str]]:
    state = db.get(HouseholdDataState, household_id)
    if not state or state.mode != "demo" or not state.demo_reference_date:
        raise ValueError("Only a marked demo household can be reset")
    chosen_date = reference_date or state.demo_reference_date
    chosen_volume = volume or state.demo_volume or "realistic"
    old_keys = delete_household_data(db, household_id)
    result = create_demo_household(db, household_id, actor_user_id, chosen_date, chosen_volume)
    current_keys = set(household_object_keys(db, household_id))
    return result, [key for key in old_keys if key not in current_keys]

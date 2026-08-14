"""Shared evidence ingestion used by every import transport.

Transports provide preserved text plus channel provenance. Parsing, normalization,
deduplication, automation, reconciliation candidates, and review behavior stay here.
"""

import hashlib
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.automation import ensure_mapping_version, propose_row_automation
from app.imports import PARSER_VERSION, candidates, parse_csv
from app.ledger import household_account
from app.models import (
    AuditEvent,
    BillInstance,
    ImportBatch,
    ImportRow,
    ImportSource,
    ReconciliationException,
    ReconciliationMatch,
    utc_now,
)

INGESTION_CHANNELS = {
    "csv_upload",
    "document_attachment",
    "imap_attachment",
    "financial_data_adapter",
}


def ingest_csv_evidence(
    db: Session,
    *,
    source: ImportSource,
    filename: str,
    csv_text: str,
    actor_user_id: UUID | None,
    channel: str = "csv_upload",
    upstream_reference: str | None = None,
) -> ImportBatch:
    if channel not in INGESTION_CHANNELS:
        raise ValueError("Unsupported ingestion channel")
    checksum = hashlib.sha256(csv_text.encode()).hexdigest()
    existing = db.scalar(
        select(ImportBatch).where(
            ImportBatch.source_id == source.id,
            ImportBatch.file_checksum == checksum,
        )
    )
    if existing:
        return existing

    parsed = parse_csv(source, csv_text)
    mapping_version = ensure_mapping_version(db, source, actor_user_id)
    batch = ImportBatch(
        household_id=source.household_id,
        source_id=source.id,
        created_by_user_id=actor_user_id,
        filename=filename,
        file_checksum=checksum,
        parser_version=PARSER_VERSION,
        status="processing",
        raw_csv=csv_text,
        mapping_version_id=mapping_version.id,
        ingestion_channel=channel,
        upstream_reference=upstream_reference,
    )
    db.add(batch)
    db.flush()
    duplicate_count = invalid_count = candidate_count = 0
    ready_count = transfer_count = recurring_count = review_count = 0
    prior_hashes = set(
        db.scalars(select(ImportRow.row_hash).where(ImportRow.source_id == source.id)).all()
    )
    account = household_account(db, source.household_id, source.account_id)
    for value in parsed:
        invalid = value.get("validation_error") is not None
        duplicate = value["row_hash"] in prior_hashes
        status_value = "invalid" if invalid else "duplicate" if duplicate else "unmatched"
        row = ImportRow(
            household_id=source.household_id,
            source_id=source.id,
            batch_id=batch.id,
            row_number=value["row_number"],
            raw_json=value["raw_json"],
            raw_text=value["raw_text"],
            row_hash=value["row_hash"],
            transaction_date=value.get("transaction_date"),
            amount_minor=value.get("amount_minor"),
            currency_code=account.currency_code,
            raw_payee=value.get("raw_payee"),
            normalized_payee=value.get("normalized_payee"),
            status=status_value,
            exception_type=status_value if status_value in {"invalid", "duplicate"} else None,
            validation_error=value.get("validation_error"),
        )
        db.add(row)
        db.flush()
        prior_hashes.add(value["row_hash"])
        if invalid:
            invalid_count += 1
            continue
        if duplicate:
            duplicate_count += 1
            continue
        for transaction, confidence, evidence in candidates(db, row, source.account_id):
            db.add(
                ReconciliationMatch(
                    household_id=source.household_id,
                    import_row_id=row.id,
                    transaction_id=transaction.id,
                    method="account_amount_date_payee",
                    confidence_percent=confidence,
                    evidence=evidence,
                )
            )
            candidate_count += 1
        propose_row_automation(db, row, source)
        if row.status == "ready":
            ready_count += 1
        elif row.automation_kind == "transfer_candidate":
            transfer_count += 1
        elif row.automation_kind == "recurring_match":
            recurring_count += 1
        else:
            review_count += 1

    valid_rows = [
        value
        for value in parsed
        if value.get("transaction_date") is not None and value.get("amount_minor") is not None
    ]
    if valid_rows:
        first_date = min(value["transaction_date"] for value in valid_rows)
        last_date = max(value["transaction_date"] for value in valid_rows)
        expected_bills = db.scalars(
            select(BillInstance).where(
                BillInstance.household_id == source.household_id,
                BillInstance.currency_code == account.currency_code,
                BillInstance.due_date >= first_date,
                BillInstance.due_date <= last_date,
                BillInstance.status != "skipped",
            )
        ).all()
        for bill in expected_bills:
            found = any(
                value["amount_minor"] == -bill.expected_amount_minor
                and abs((value["transaction_date"] - bill.due_date).days) <= 3
                for value in valid_rows
            )
            if not found:
                db.add(
                    ReconciliationException(
                        household_id=source.household_id,
                        batch_id=batch.id,
                        exception_type="missing_expected_bill",
                        related_type="bill_instance",
                        related_id=str(bill.id),
                        event_date=bill.due_date,
                        amount_minor=-bill.expected_amount_minor,
                        currency_code=bill.currency_code,
                        detail=f"No imported row matched expected obligation {bill.name} by amount and date tolerance",
                    )
                )

    batch.row_count = len(parsed)
    batch.candidate_count = candidate_count
    batch.duplicate_count = duplicate_count
    batch.invalid_count = invalid_count
    batch.ready_count = ready_count
    batch.transfer_count = transfer_count
    batch.recurring_count = recurring_count
    batch.review_count = review_count
    batch.status = (
        "complete_with_exceptions"
        if duplicate_count or invalid_count or review_count or transfer_count or recurring_count
        else "ready"
        if ready_count
        else "complete"
    )
    batch.completed_at = utc_now()
    source.last_imported_at = utc_now()
    if source.reminders_enabled and source.reminder_interval_days:
        source.next_reminder_date = utc_now().date() + timedelta(
            days=source.reminder_interval_days
        )
    db.add(
        AuditEvent(
            household_id=source.household_id,
            actor_user_id=actor_user_id,
            action="import.batch_ingested",
            resource_type="import_batch",
            resource_id=str(batch.id),
            detail=(
                f"channel:{channel};rows:{batch.row_count};ready:{ready_count};"
                f"transfers:{transfer_count};recurring:{recurring_count};review:{review_count};"
                f"duplicates:{duplicate_count};invalid:{invalid_count};candidates:{candidate_count};"
                f"mapping_version:{mapping_version.version_number}"
            ),
        )
    )
    return batch

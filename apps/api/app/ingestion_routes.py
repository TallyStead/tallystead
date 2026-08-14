import email
import imaplib
import ssl
from email.message import Message
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.dependencies import DbSession, current_user, require_roles
from app.ingestion import INGESTION_CHANNELS, ingest_csv_evidence
from app.models import AuditEvent, Document, ImportBatch, ImportSource, Membership, Role, User
from app.object_store import get_object
from app.settings_store import integration_status

router = APIRouter(prefix="/v1/imports", tags=["imports"])
writer = require_roles(Role.OWNER, Role.MANAGER)


class AdapterIngestionRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    csv_text: str = Field(min_length=1, max_length=20_000_000)
    upstream_reference: str = Field(min_length=1, max_length=500)


def _batch_response(item: ImportBatch) -> dict:
    return {
        "batch_id": str(item.id),
        "source_id": str(item.source_id),
        "filename": item.filename,
        "file_checksum": item.file_checksum,
        "parser_version": item.parser_version,
        "status": item.status,
        "row_count": item.row_count,
        "candidate_count": item.candidate_count,
        "duplicate_count": item.duplicate_count,
        "invalid_count": item.invalid_count,
        "ready_count": item.ready_count,
        "transfer_count": item.transfer_count,
        "recurring_count": item.recurring_count,
        "review_count": item.review_count,
        "mapping_version_id": str(item.mapping_version_id) if item.mapping_version_id else None,
        "ingestion_channel": item.ingestion_channel,
        "upstream_reference": item.upstream_reference,
        "created_at": item.created_at.isoformat(),
    }


def _source(db: DbSession, household_id: UUID, source_id: UUID) -> ImportSource:
    source = db.scalar(
        select(ImportSource).where(
            ImportSource.id == source_id,
            ImportSource.household_id == household_id,
            ImportSource.is_active.is_(True),
        )
    )
    if not source:
        raise HTTPException(status_code=404, detail="Import source not found")
    return source


@router.post("/sources/{source_id}/adapter", status_code=201)
def ingest_financial_adapter(
    source_id: UUID,
    request: AdapterIngestionRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(writer)],
) -> dict:
    source = _source(db, membership.household_id, source_id)
    batch = ingest_csv_evidence(
        db,
        source=source,
        filename=request.filename,
        csv_text=request.csv_text,
        actor_user_id=actor.id,
        channel="financial_data_adapter",
        upstream_reference=request.upstream_reference,
    )
    response = _batch_response(batch)
    db.commit()
    return response


@router.post("/sources/{source_id}/documents/{document_id}", status_code=201)
def ingest_document_attachment(
    source_id: UUID,
    document_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(writer)],
) -> dict:
    source = _source(db, membership.household_id, source_id)
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.household_id == membership.household_id,
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.content_type not in {"text/csv", "application/csv", "text/plain"} and not document.filename.casefold().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Only stored CSV documents can enter a mapped import source")
    try:
        content, _ = get_object(document.object_key)
        csv_text = content.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=422, detail="Stored document is not readable UTF-8 CSV evidence") from error
    batch = ingest_csv_evidence(
        db,
        source=source,
        filename=document.filename,
        csv_text=csv_text,
        actor_user_id=actor.id,
        channel="document_attachment",
        upstream_reference=f"document:{document.id}",
    )
    response = _batch_response(batch)
    db.commit()
    return response


def _csv_attachments(message: Message) -> list[tuple[str, str]]:
    attachments: list[tuple[str, str]] = []
    for part in message.walk():
        filename = part.get_filename()
        content_type = part.get_content_type()
        if not filename or (content_type not in {"text/csv", "application/csv", "text/plain"} and not filename.casefold().endswith(".csv")):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        try:
            attachments.append((filename[:255], payload.decode(part.get_content_charset() or "utf-8-sig")))
        except (LookupError, UnicodeDecodeError):
            continue
    return attachments


@router.post("/sources/{source_id}/imap", status_code=201)
def ingest_imap_attachments(
    source_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(writer)],
) -> dict:
    source = _source(db, membership.household_id, source_id)
    values, _ = integration_status(db)
    host, username, password = values.get("imap_host"), values.get("imap_username"), values.get("imap_password")
    if not host or not username or not password:
        raise HTTPException(status_code=409, detail="IMAP is not configured")
    port = int(values.get("imap_port") or 993)
    batches: list[ImportBatch] = []
    try:
        with imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=15) as mailbox:
            mailbox.login(username, password)
            mailbox.select("INBOX", readonly=not values.get("imap_archive_processed", False))
            status, ids = mailbox.uid("search", None, "UNSEEN")
            if status != "OK":
                raise OSError("IMAP search failed")
            for uid in ids[0].split():
                status, payload = mailbox.uid("fetch", uid, "(RFC822)")
                if status != "OK" or not payload or not isinstance(payload[0], tuple):
                    continue
                message = email.message_from_bytes(payload[0][1])
                imported = False
                for filename, csv_text in _csv_attachments(message):
                    batch = ingest_csv_evidence(
                        db,
                        source=source,
                        filename=filename,
                        csv_text=csv_text,
                        actor_user_id=actor.id,
                        channel="imap_attachment",
                        upstream_reference=f"imap:{uid.decode(errors='ignore')}:{filename}",
                    )
                    batches.append(batch)
                    imported = True
                if imported and values.get("imap_archive_processed", False):
                    mailbox.uid("store", uid, "+FLAGS", "(\\Seen)")
    except (OSError, imaplib.IMAP4.error) as error:
        raise HTTPException(status_code=503, detail="IMAP import failed; verify the local email configuration") from error
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="import.imap_checked", resource_type="import_source", resource_id=str(source.id), detail=f"attachments={len(batches)};shared_contract=true"))
    response = [_batch_response(item) for item in batches]
    db.commit()
    return {"imported_count": len(response), "batches": response, "supported_channels": sorted(INGESTION_CHANNELS)}

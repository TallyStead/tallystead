import base64
import binascii
import hashlib
import io
import json
from datetime import timedelta

from fastapi import HTTPException
from minio.error import MinioException
from PIL import Image, UnidentifiedImageError
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.local_ai import extract_document
from app.models import Document, DocumentExtraction, DocumentMatch, LedgerTransaction, utc_now
from app.object_store import get_object
from app.settings_store import load_integrations

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp", "text/plain"}


def decode_document(value: str, content_type: str) -> bytes:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail="Documents must be PDF, JPEG, PNG, WebP, or plain text")
    try:
        content = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail="Document content is not valid base64") from error
    if not content:
        raise HTTPException(status_code=422, detail="Document is empty")
    if len(content) > settings.max_document_bytes:
        raise HTTPException(status_code=413, detail=f"Document exceeds the {settings.max_document_bytes // 1_000_000} MB limit")
    return content


def checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def thumbnail(content: bytes, content_type: str) -> bytes | None:
    if not content_type.startswith("image/"):
        return None
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.thumbnail((480, 480))
            if image.mode != "RGB":
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image)
                image = background
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=82, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(status_code=422, detail="Image document could not be decoded") from error


def normalize(value: str | None) -> str:
    return " ".join((value or "").upper().split())


def refresh_document_matches(db: Session, document: Document) -> None:
    existing = {
        item.transaction_id: item
        for item in db.scalars(select(DocumentMatch).where(DocumentMatch.document_id == document.id)).all()
    }
    if document.amount_minor is None and document.document_date is None and not document.payee:
        return
    query = select(LedgerTransaction).where(
        LedgerTransaction.household_id == document.household_id,
        LedgerTransaction.status.in_(["posted", "pending"]),
        LedgerTransaction.voided_at.is_(None),
    )
    if document.account_id:
        query = query.where(LedgerTransaction.account_id == document.account_id)
    if document.amount_minor is not None:
        query = query.where(LedgerTransaction.amount_minor.in_([abs(document.amount_minor), -abs(document.amount_minor)]))
    if document.currency_code:
        query = query.where(LedgerTransaction.currency_code == document.currency_code)
    if document.document_date:
        query = query.where(
            LedgerTransaction.transaction_date >= document.document_date - timedelta(days=5),
            LedgerTransaction.transaction_date <= document.document_date + timedelta(days=5),
        )
    candidates = db.scalars(query.order_by(LedgerTransaction.transaction_date.desc()).limit(50)).all()
    candidate_ids = {item.id for item in candidates}
    for transaction_id, item in existing.items():
        if item.status == "suggested" and transaction_id not in candidate_ids:
            item.status = "superseded"
    for transaction in candidates:
        evidence: list[str] = []
        confidence = 0
        if document.amount_minor is not None and abs(transaction.amount_minor) == abs(document.amount_minor):
            confidence += 60
            evidence.append("exact amount")
        if document.document_date:
            days = abs((transaction.transaction_date - document.document_date).days)
            confidence += 25 if days == 0 else 15 if days <= 3 else 5
            evidence.append(f"date differs by {days} day(s)")
        if document.account_id and transaction.account_id == document.account_id:
            confidence += 10
            evidence.append("same account")
        if document.payee:
            payee_match = normalize(transaction.payee or transaction.raw_payee) == normalize(document.payee)
            if payee_match:
                confidence += 15
            evidence.append(f"payee {'matches' if payee_match else 'differs'}")
            if document.amount_minor is None and document.document_date is None and not document.account_id and not payee_match:
                continue
        confidence = min(confidence, 100)
        match = existing.get(transaction.id)
        if match is None:
            db.add(DocumentMatch(
                household_id=document.household_id,
                document_id=document.id,
                transaction_id=transaction.id,
                method="amount_date_account_payee",
                confidence_percent=confidence,
                evidence="; ".join(evidence) or "manual review candidate",
            ))
        elif match.status in {"suggested", "superseded"}:
            match.status = "suggested"
            match.confidence_percent = confidence
            match.evidence = "; ".join(evidence) or "manual review candidate"


def process_next_extraction(db: Session) -> str | None:
    values = load_integrations(db)
    if not (values.get("ai_enabled") and values.get("ai_extract_enabled")):
        return None
    extraction = db.scalar(
        select(DocumentExtraction).where(DocumentExtraction.status == "queued").order_by(DocumentExtraction.created_at).limit(1)
    )
    if extraction is None:
        return None
    document = db.get(Document, extraction.document_id)
    extraction.status = "processing"
    document.status = "extracting"
    db.commit()
    try:
        if values.get("ai_provider") != extraction.provider:
            raise ValueError("Local AI provider changed after this extraction was queued")
        content, _ = get_object(document.object_key)
        extraction_values = {**values, "ai_provider": extraction.provider, "ai_model": extraction.model_version, "document_kind": document.kind}
        result = extract_document(content, document.content_type, extraction_values)
        confidence = result.get("confidence", {}).get("overall_percent")
        extraction.output_json = json.dumps(result, sort_keys=True)
        extraction.confidence_percent = max(0, min(int(confidence or 0), 100))
        extraction.status = "complete"
        extraction.completed_at = utc_now()
        document.status = "suggestions_ready"
    except (OSError, ValueError, KeyError, TypeError, MinioException, PdfReadError):
        extraction.status = "failed"
        extraction.failure_detail = "Local extraction failed. Verify the runtime, model, and supported document type, then retry."
        extraction.completed_at = utc_now()
        document.status = "extraction_failed"
    db.commit()
    return str(extraction.id)

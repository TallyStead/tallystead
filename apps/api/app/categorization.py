import json
from collections import Counter
from urllib.request import Request, build_opener
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.local_ai import _json_content, _NoRedirect
from app.models import (
    Category,
    CategoryRule,
    CategorySuggestion,
    DocumentExtraction,
    DocumentMatch,
    LedgerTransaction,
    TransactionSplit,
    TransferLink,
)

CATEGORY_RULE_VERSION = "category-suggestion-v1"


def _direction(amount_minor: int) -> str:
    return "in" if amount_minor > 0 else "out"


def _normalized_payee(item: LedgerTransaction) -> str:
    return (item.payee or item.raw_payee or "").strip().casefold()


def _proposal(category: Category, amount_minor: int, confidence: int, evidence: list[str], provider: str = "rules", model: str = CATEGORY_RULE_VERSION) -> dict:
    return {
        "provider": provider,
        "model_version": model,
        "confidence_percent": confidence,
        "splits": [{"category_id": str(category.id), "category_name": category.name, "amount_minor": amount_minor}],
        "evidence": evidence,
    }


def deterministic_proposal(db: Session, household_id: UUID, item: LedgerTransaction) -> dict | None:
    direction = _direction(item.amount_minor)
    payee = _normalized_payee(item)
    rules = db.scalars(select(CategoryRule).where(CategoryRule.household_id == household_id, CategoryRule.direction == direction, CategoryRule.is_active.is_(True))).all()
    for rule in rules:
        matches = (rule.match_type == "merchant" and item.merchant_id and rule.match_value == str(item.merchant_id)) or (rule.match_type == "payee" and payee and rule.match_value == payee)
        if matches:
            category = db.get(Category, rule.category_id)
            if category and not category.is_archived:
                return _proposal(category, item.amount_minor, 100, [f"Accepted household {rule.match_type} rule matched {rule.match_value}."])

    candidates = db.scalars(select(LedgerTransaction).where(LedgerTransaction.household_id == household_id, LedgerTransaction.id != item.id, LedgerTransaction.amount_minor * item.amount_minor > 0, LedgerTransaction.status == "posted", LedgerTransaction.voided_at.is_(None)).order_by(LedgerTransaction.transaction_date.desc()).limit(500)).all()
    matched_ids = [candidate.id for candidate in candidates if (item.merchant_id and candidate.merchant_id == item.merchant_id) or (payee and _normalized_payee(candidate) == payee)]
    if matched_ids:
        split_rows = db.execute(select(TransactionSplit.category_id, Category).join(Category, Category.id == TransactionSplit.category_id).where(TransactionSplit.transaction_id.in_(matched_ids), Category.category_type == ("income" if item.amount_minor > 0 else "expense"))).all()
        counts = Counter(category_id for category_id, _ in split_rows)
        if counts:
            category_id, frequency = counts.most_common(1)[0]
            category = next(category for found_id, category in split_rows if found_id == category_id)
            confidence = min(95, 75 + frequency * 5)
            return _proposal(category, item.amount_minor, confidence, [f"{frequency} previously categorized transaction(s) matched this merchant or payee."])

    extraction = db.scalar(select(DocumentExtraction).join(DocumentMatch, DocumentMatch.document_id == DocumentExtraction.document_id).where(DocumentMatch.transaction_id == item.id, DocumentMatch.status == "confirmed", DocumentExtraction.user_disposition == "accepted", DocumentExtraction.output_json.is_not(None)).order_by(DocumentExtraction.created_at.desc()).limit(1))
    if extraction:
        output = json.loads(extraction.output_json or "{}")
        hints = [str(row.get("category_hint") or "") for row in output.get("line_items", []) if isinstance(row, dict)]
        hints.append(str(output.get("category_hint") or ""))
        categories = db.scalars(select(Category).where(Category.household_id == household_id, Category.category_type == ("income" if item.amount_minor > 0 else "expense"), Category.is_archived.is_(False))).all()
        for hint in hints:
            category = next((value for value in categories if value.name.casefold() == hint.strip().casefold()), None)
            if category:
                return _proposal(category, item.amount_minor, 70, [f"Accepted receipt evidence suggested {category.name}."])
    return None


def ai_proposal(db: Session, household_id: UUID, item: LedgerTransaction, values: dict) -> dict | None:
    categories = db.scalars(select(Category).where(Category.household_id == household_id, Category.category_type == ("income" if item.amount_minor > 0 else "expense"), Category.is_archived.is_(False)).order_by(Category.name)).all()
    if not categories:
        return None
    provider = values.get("ai_provider")
    base_url = str(values.get("ai_base_url") or "").rstrip("/")
    model = values.get("ai_model") or ("llama3.2" if provider == "ollama" else "local-model")
    prompt = f"""Suggest one category for this financial transaction. Return strict JSON only with category_name, confidence_percent (0-100), and explanation. Never propose a ledger action.
Transaction: payee={item.payee or item.raw_payee or 'unknown'}; signed_minor_units={item.amount_minor}; currency={item.currency_code}; direction={_direction(item.amount_minor)}.
Allowed categories: {', '.join(category.name for category in categories)}"""
    if provider == "ollama":
        endpoint = f"{base_url}/api/chat"
        body = {"model": model, "stream": False, "format": "json", "messages": [{"role": "user", "content": prompt}]}
    else:
        endpoint = f"{base_url}/v1/chat/completions"
        body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]}
    request = Request(endpoint, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with build_opener(_NoRedirect).open(request, timeout=45) as response:
        payload = json.loads(response.read().decode())
    content = payload.get("message", {}).get("content") if provider == "ollama" else payload.get("choices", [{}])[0].get("message", {}).get("content")
    result = _json_content(content or "{}")
    category = next((value for value in categories if value.name.casefold() == str(result.get("category_name") or "").strip().casefold()), None)
    if not category:
        return None
    confidence = max(0, min(100, int(result.get("confidence_percent") or 0)))
    return _proposal(category, item.amount_minor, confidence, [str(result.get("explanation") or "Local model suggestion; review required.")], provider, model)


def ai_import_proposals(db: Session, household_id: UUID, rows: list[object], values: dict) -> dict[str, dict]:
    """Ask the configured household-local model for reviewable import-row category proposals."""
    categories = db.scalars(select(Category).where(Category.household_id == household_id, Category.is_archived.is_(False)).order_by(Category.category_type, Category.name)).all()
    if not categories or not rows:
        return {}
    provider = values.get("ai_provider")
    base_url = str(values.get("ai_base_url") or "").rstrip("/")
    model = values.get("ai_model") or ("llama3.2" if provider == "ollama" else "local-model")
    allowed = {"income": [item.name for item in categories if item.category_type == "income"], "expense": [item.name for item in categories if item.category_type == "expense"]}
    row_payload = [{"row_id": str(row.id), "payee": row.normalized_payee or row.raw_payee or "unknown", "signed_minor_units": row.amount_minor, "currency": row.currency_code, "direction": "income" if row.amount_minor > 0 else "expense"} for row in rows]
    prompt = f"""Review these imported financial rows and suggest exactly one allowed category for each. Return strict JSON only as an object with a suggestions array. Each suggestion must contain row_id, category_name, confidence_percent (0-100), and explanation. Do not propose or perform ledger actions. Omit a row when evidence is insufficient.
Allowed categories by direction: {json.dumps(allowed, sort_keys=True)}
Rows: {json.dumps(row_payload, sort_keys=True)}"""
    if provider == "ollama":
        endpoint = f"{base_url}/api/chat"
        body = {"model": model, "stream": False, "format": "json", "messages": [{"role": "user", "content": prompt}]}
    else:
        endpoint = f"{base_url}/v1/chat/completions"
        body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]}
    request = Request(endpoint, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with build_opener(_NoRedirect).open(request, timeout=45) as response:
        payload = json.loads(response.read().decode())
    content = payload.get("message", {}).get("content") if provider == "ollama" else payload.get("choices", [{}])[0].get("message", {}).get("content")
    result = _json_content(content or "{}")
    row_map = {str(row.id): row for row in rows}
    category_map = {(item.category_type, item.name.casefold()): item for item in categories}
    proposals: dict[str, dict] = {}
    for suggestion in result.get("suggestions", []):
        if not isinstance(suggestion, dict):
            continue
        row_id = str(suggestion.get("row_id") or "")
        row = row_map.get(row_id)
        if row is None:
            continue
        direction = "income" if row.amount_minor > 0 else "expense"
        category = category_map.get((direction, str(suggestion.get("category_name") or "").strip().casefold()))
        if category is None:
            continue
        try:
            confidence = max(0, min(100, int(suggestion.get("confidence_percent") or 0)))
        except (TypeError, ValueError):
            confidence = 0
        proposals[row_id] = {"category_id": category.id, "category_name": category.name, "confidence_percent": confidence, "evidence": str(suggestion.get("explanation") or "Local model suggestion; review required.")[:2000], "provider": provider, "model_version": model}
    return proposals


def suggestion_candidates(db: Session, household_id: UUID) -> list[LedgerTransaction]:
    existing = set(db.scalars(select(CategorySuggestion.transaction_id).where(CategorySuggestion.household_id == household_id, CategorySuggestion.status == "pending")).all())
    split_ids = set(db.scalars(select(TransactionSplit.transaction_id)).all())
    transfer_ids: set[UUID] = set()
    for link in db.scalars(select(TransferLink).where(TransferLink.household_id == household_id)).all():
        transfer_ids.update((link.from_transaction_id, link.to_transaction_id))
    return [item for item in db.scalars(select(LedgerTransaction).where(LedgerTransaction.household_id == household_id, LedgerTransaction.status.in_(("posted", "pending")), LedgerTransaction.voided_at.is_(None), LedgerTransaction.reversal_of_transaction_id.is_(None), LedgerTransaction.activity_type == "regular").order_by(LedgerTransaction.transaction_date.desc()).limit(250)).all() if item.id not in existing | split_ids | transfer_ids]

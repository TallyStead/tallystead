import csv
import hashlib
import io
import json
import re
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ImportRow, LedgerTransaction

PARSER_VERSION = "csv-mapped-v2"
SECRET_PATTERN = re.compile(r"(?i)(password|passcode|one[- ]?time code|otp|session cookie|api key|secret)\s*[:=]")


def validate_safe_notes(*values: str | None) -> None:
    if any(value and SECRET_PATTERN.search(value) for value in values):
        raise HTTPException(status_code=422, detail="Import instructions and notes cannot contain credentials, codes, cookies, API keys, or secrets")


def parse_amount(value: str) -> int:
    cleaned = value.strip().replace(",", "").replace("$", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative: cleaned = cleaned[1:-1]
    try:
        amount = int((Decimal(cleaned) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation as error:
        raise ValueError(f"Invalid amount: {value}") from error
    return -abs(amount) if negative else amount


def normalize_payee(value: str) -> str:
    return " ".join(value.upper().split())


def matching_column(fieldnames: list[str], configured: str) -> str | None:
    wanted = configured.strip().casefold()
    return next((name for name in fieldnames if name.strip().casefold() == wanted), None)


def raw_value(raw: dict, column: str) -> str | None:
    matched = matching_column(list(raw), column)
    return raw.get(matched) if matched else None


COLUMN_ALIASES = {
    "date": ("date", "post date", "posting date", "posted date", "transaction date"),
    "payee": ("description", "payee", "merchant", "transaction description", "name"),
    "original_payee": ("original description", "original payee", "raw description"),
    "amount": ("amount", "signed amount", "transaction amount"),
    "debit": ("debit", "withdrawal", "withdrawals", "charge", "money out"),
    "credit": ("credit", "deposit", "deposits", "money in"),
    "status": ("status", "state", "transaction status"),
    "category": ("category", "classification", "type"),
    "memo": ("memo", "note", "notes"),
}
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%m-%d-%Y")


def _reader(text: str) -> csv.DictReader:
    try:
        sample = text[:8192]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return csv.DictReader(io.StringIO(text), dialect=dialect)
    except csv.Error:
        return csv.DictReader(io.StringIO(text))


def inspect_csv(text: str) -> dict:
    reader = _reader(text)
    try:
        rows = list(reader)
    except csv.Error as error:
        raise HTTPException(status_code=422, detail=f"CSV could not be parsed: {error}") from error
    headers = [header.strip() for header in (reader.fieldnames or []) if header and header.strip()]
    if not headers or not rows:
        raise HTTPException(status_code=422, detail="CSV must contain a header row and at least one data row")
    normalized = {header.casefold(): header for header in headers}
    mappings: dict[str, str | None] = {}
    confidence: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        match = next((normalized[alias] for alias in aliases if alias in normalized), None)
        mappings[field] = match
        confidence[field] = "high" if match else "none"

    detected_format = "%Y-%m-%d"
    date_column = mappings["date"]
    if date_column:
        values = [str(row.get(date_column) or "").strip() for row in rows[:25]]
        values = [value for value in values if value]
        for date_format in DATE_FORMATS:
            if values and all(_valid_date(value, date_format) for value in values):
                detected_format = date_format
                break
    return {
        "headers": headers,
        "row_count": len(rows),
        "mappings": mappings,
        "confidence": confidence,
        "date_format": detected_format,
        "preview_rows": [{header: str(row.get(header) or "") for header in headers} for row in rows[:3]],
    }


def _valid_date(value: str, date_format: str) -> bool:
    try:
        datetime.strptime(value, date_format)  # noqa: DTZ007
        return True
    except ValueError:
        return False


def parse_csv(source, text: str) -> list[dict]:
    reader = _reader(text)
    try:
        rows = list(reader)
    except csv.Error as error: raise HTTPException(status_code=422, detail=f"CSV could not be parsed: {error}") from error
    fieldnames = reader.fieldnames or []
    date_column = matching_column(fieldnames, source.date_column)
    payee_column = matching_column(fieldnames, source.payee_column)
    amount_column = matching_column(fieldnames, source.amount_column) if source.amount_column else None
    debit_column = matching_column(fieldnames, source.debit_column) if source.debit_column else None
    credit_column = matching_column(fieldnames, source.credit_column) if source.credit_column else None
    if not rows or not date_column or not payee_column or not (amount_column or debit_column or credit_column):
        raise HTTPException(status_code=422, detail="CSV does not match this Source. Review its date, description, and amount mappings.")
    original_payee_column = matching_column(fieldnames, source.original_payee_column) if source.original_payee_column else matching_column(fieldnames, "Original Description")
    result = []
    for number, raw in enumerate(rows, 2):
        raw_json = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        parsed = {"row_number": number, "raw": raw, "raw_json": raw_json, "raw_text": ",".join(raw.values()), "row_hash": hashlib.sha256(raw_json.encode()).hexdigest()}
        try:
            cleaned_payee = raw[payee_column].strip()
            original_payee = raw[original_payee_column].strip() if original_payee_column and raw.get(original_payee_column) else cleaned_payee
            if amount_column:
                amount_minor = parse_amount(raw[amount_column])
                if source.amount_sign == "positive_out": amount_minor *= -1
            else:
                debit = parse_amount(raw.get(debit_column) or "0") if debit_column and raw.get(debit_column, "").strip() else 0
                credit = parse_amount(raw.get(credit_column) or "0") if credit_column and raw.get(credit_column, "").strip() else 0
                amount_minor = abs(credit) - abs(debit)
            parsed.update(transaction_date=datetime.strptime(raw[date_column].strip(), source.date_format).date(), amount_minor=amount_minor, raw_payee=original_payee, normalized_payee=normalize_payee(cleaned_payee))  # noqa: DTZ007
        except (ValueError, TypeError) as error: parsed.update(transaction_date=None, amount_minor=None, raw_payee=raw.get(original_payee_column or payee_column), normalized_payee=None, validation_error=str(error))
        result.append(parsed)
    return result


def candidates(db: Session, row: ImportRow, account_id) -> list[tuple[LedgerTransaction, int, str]]:
    if row.transaction_date is None or row.amount_minor is None: return []
    items = db.scalars(select(LedgerTransaction).where(LedgerTransaction.household_id == row.household_id, LedgerTransaction.account_id == account_id, LedgerTransaction.amount_minor == row.amount_minor, LedgerTransaction.transaction_date >= row.transaction_date - timedelta(days=3), LedgerTransaction.transaction_date <= row.transaction_date + timedelta(days=3), LedgerTransaction.status.in_(["posted", "pending"]))).all()
    result = []
    for item in items:
        days = abs((item.transaction_date - row.transaction_date).days)
        payee_match = normalize_payee(item.raw_payee or item.payee or "") == row.normalized_payee
        confidence = 100 if days == 0 and payee_match else 90 if days == 0 else 75
        evidence = f"same account, direction, and amount; date difference {days} day(s); payee {'matches' if payee_match else 'differs'}"
        result.append((item, confidence, evidence))
    return sorted(result, key=lambda item: (-item[1], item[0].transaction_date, str(item[0].id)))

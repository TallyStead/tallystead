import calendar
from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BillInstance, BillPaymentLink, IncomeEvent, utc_now
from app.schemas import BillInstanceResponse, IncomeEventResponse, PaymentLinkResponse


def add_months(value: date, months: int, day: int | None = None) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    requested_day = day or value.day
    return date(year, month, min(requested_day, calendar.monthrange(year, month)[1]))


def next_occurrence(value: date, cadence: str, due_day: int | None = None) -> date | None:
    if cadence == "weekly":
        return value + timedelta(days=7)
    if cadence == "biweekly":
        return value + timedelta(days=14)
    if cadence == "monthly":
        return add_months(value, 1, due_day)
    if cadence == "quarterly":
        return add_months(value, 3, due_day)
    if cadence == "yearly":
        return add_months(value, 12, due_day)
    if cadence == "irregular":
        return None
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unsupported cadence")


def validate_range(expected: int, minimum: int | None, maximum: int | None) -> None:
    if minimum is not None and expected < minimum:
        raise HTTPException(status_code=422, detail="Expected amount cannot be below the minimum")
    if maximum is not None and expected > maximum:
        raise HTTPException(status_code=422, detail="Expected amount cannot exceed the maximum")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise HTTPException(status_code=422, detail="Minimum amount cannot exceed maximum amount")


def bill_instance_response(db: Session, item: BillInstance) -> BillInstanceResponse:
    links = db.scalars(select(BillPaymentLink).where(BillPaymentLink.bill_instance_id == item.id).order_by(BillPaymentLink.created_at)).all()
    paid = sum(link.amount_minor for link in links)
    if item.status == "skipped":
        effective_status = "skipped"
    elif paid >= item.expected_amount_minor:
        effective_status = "paid"
    elif paid > 0:
        effective_status = "partial"
    elif item.due_date < utc_now().date():
        effective_status = "overdue"
    else:
        effective_status = item.status
    return BillInstanceResponse(
        bill_instance_id=item.id, bill_profile_id=item.bill_profile_id, debt_id=item.debt_id,
        name=item.name, due_date=item.due_date, expected_amount_minor=item.expected_amount_minor,
        minimum_amount_minor=item.minimum_amount_minor, maximum_amount_minor=item.maximum_amount_minor,
        paid_amount_minor=paid, currency_code=item.currency_code, is_essential=item.is_essential,
        priority=item.priority, status=effective_status, note=item.note,
        payment_links=[PaymentLinkResponse(payment_link_id=link.id, transaction_id=link.transaction_id, amount_minor=link.amount_minor) for link in links],
    )


def income_event_response(item: IncomeEvent) -> IncomeEventResponse:
    return IncomeEventResponse(
        income_event_id=item.id, income_source_id=item.income_source_id,
        received_transaction_id=item.received_transaction_id, name=item.name,
        expected_date=item.expected_date, expected_amount_minor=item.expected_amount_minor,
        minimum_amount_minor=item.minimum_amount_minor, maximum_amount_minor=item.maximum_amount_minor,
        currency_code=item.currency_code, confidence_percent=item.confidence_percent,
        status=item.status, note=item.note,
    )


def transaction_linked_total(db: Session, transaction_id) -> int:
    return db.scalar(select(func.coalesce(func.sum(BillPaymentLink.amount_minor), 0)).where(BillPaymentLink.transaction_id == transaction_id)) or 0

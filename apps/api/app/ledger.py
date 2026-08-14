import json
from datetime import date
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    FinancialAccount,
    LedgerTransaction,
    Merchant,
    MerchantAlias,
    TransactionSplit,
    TransferLink,
)
from app.schemas import LedgerTransactionResponse, MerchantResponse, TransactionSplitResponse

DEFAULT_CATEGORIES = (
    ("Paycheck", "income"),
    ("Freelance & side income", "income"),
    ("Benefits", "income"),
    ("Interest income", "income"),
    ("Other income", "income"),
    ("Housing", "expense"),
    ("Utilities", "expense"),
    ("Groceries", "expense"),
    ("Dining out", "expense"),
    ("Transportation", "expense"),
    ("Fuel", "expense"),
    ("Insurance", "expense"),
    ("Healthcare", "expense"),
    ("Childcare", "expense"),
    ("Education", "expense"),
    ("Household supplies", "expense"),
    ("Personal care", "expense"),
    ("Clothing", "expense"),
    ("Entertainment", "expense"),
    ("Subscriptions", "expense"),
    ("Gifts & donations", "expense"),
    ("Travel", "expense"),
    ("Taxes", "expense"),
    ("Bank fees", "expense"),
    ("Interest & finance charges", "expense"),
    ("Miscellaneous", "expense"),
)


def seed_default_categories(db: Session, household_id: UUID) -> int:
    existing = set(db.scalars(select(Category.name).where(Category.household_id == household_id)).all())
    missing = [Category(household_id=household_id, name=name, category_type=category_type, is_system_default=True) for name, category_type in DEFAULT_CATEGORIES if name not in existing]
    db.add_all(missing)
    return len(missing)


def household_account(db: Session, household_id: UUID, account_id: UUID) -> FinancialAccount:
    account = db.scalar(select(FinancialAccount).where(FinancialAccount.id == account_id, FinancialAccount.household_id == household_id))
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Financial account not found")
    return account


def household_transaction(db: Session, household_id: UUID, transaction_id: UUID) -> LedgerTransaction:
    transaction = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == transaction_id, LedgerTransaction.household_id == household_id))
    if transaction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    return transaction


def household_merchant(db: Session, household_id: UUID, merchant_id: UUID) -> Merchant:
    merchant = db.scalar(select(Merchant).where(Merchant.id == merchant_id, Merchant.household_id == household_id))
    if merchant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchant not found")
    return merchant


def validate_splits(db: Session, household_id: UUID, amount_minor: int, splits: list) -> None:
    if not splits:
        return
    if sum(item.amount_minor for item in splits) != amount_minor:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Split amounts must equal the transaction amount")
    category_ids = {item.category_id for item in splits}
    found = set(db.scalars(select(Category.id).where(Category.household_id == household_id, Category.id.in_(category_ids))).all())
    if found != category_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more categories were not found")


def included_activity_query(account_id: UUID, as_of: date | None = None, include_pending: bool = True):
    statuses = ["posted", "reversed"] + (["pending"] if include_pending else [])
    query = select(LedgerTransaction).where(LedgerTransaction.account_id == account_id, LedgerTransaction.status.in_(statuses))
    if as_of is not None:
        query = query.where(LedgerTransaction.transaction_date <= as_of)
    return query


def account_balance(db: Session, account: FinancialAccount, as_of: date | None = None, include_pending: bool = True) -> int:
    statuses = ["posted", "reversed"] + (["pending"] if include_pending else [])
    query = select(func.coalesce(func.sum(LedgerTransaction.amount_minor), 0)).where(LedgerTransaction.account_id == account.id, LedgerTransaction.status.in_(statuses))
    if as_of is not None:
        query = query.where(LedgerTransaction.transaction_date <= as_of)
    activity = db.scalar(query)
    return account.opening_balance_minor + int(activity or 0)


def transaction_snapshot(db: Session, transaction: LedgerTransaction) -> str:
    splits = db.scalars(select(TransactionSplit).where(TransactionSplit.transaction_id == transaction.id).order_by(TransactionSplit.created_at)).all()
    return json.dumps({
        "transaction_id": str(transaction.id),
        "account_id": str(transaction.account_id),
        "transaction_date": transaction.transaction_date.isoformat(),
        "amount_minor": transaction.amount_minor,
        "currency_code": transaction.currency_code,
        "status": transaction.status,
        "payee": transaction.payee,
        "merchant_id": str(transaction.merchant_id) if transaction.merchant_id else None,
        "memo": transaction.memo,
        "source_type": transaction.source_type,
        "source_reference": transaction.source_reference,
        "activity_type": transaction.activity_type,
        "raw_payee": transaction.raw_payee,
        "reconciled_at": transaction.reconciled_at.isoformat() if transaction.reconciled_at else None,
        "splits": [{"category_id": str(split.category_id), "amount_minor": split.amount_minor, "memo": split.memo} for split in splits],
    }, sort_keys=True)


def merchant_response(db: Session, merchant: Merchant) -> MerchantResponse:
    aliases = list(db.scalars(select(MerchantAlias.alias).where(MerchantAlias.merchant_id == merchant.id).order_by(MerchantAlias.alias)).all())
    return MerchantResponse(merchant_id=merchant.id, name=merchant.name, aliases=aliases, is_archived=merchant.is_archived)


def transaction_response(db: Session, transaction: LedgerTransaction) -> LedgerTransactionResponse:
    account = db.get(FinancialAccount, transaction.account_id)
    merchant = db.get(Merchant, transaction.merchant_id) if transaction.merchant_id else None
    rows = db.execute(select(TransactionSplit, Category.name).join(Category, Category.id == TransactionSplit.category_id).where(TransactionSplit.transaction_id == transaction.id).order_by(TransactionSplit.created_at)).all()
    transfer = db.scalar(select(TransferLink).where((TransferLink.from_transaction_id == transaction.id) | (TransferLink.to_transaction_id == transaction.id)))
    return LedgerTransactionResponse(
        transaction_id=transaction.id,
        account_id=transaction.account_id,
        account_name=account.name if account else "Deleted account",
        transaction_date=transaction.transaction_date,
        amount_minor=transaction.amount_minor,
        currency_code=transaction.currency_code,
        status=transaction.status,
        payee=transaction.payee,
        raw_payee=transaction.raw_payee,
        merchant_id=transaction.merchant_id,
        merchant_name=merchant.name if merchant else None,
        memo=transaction.memo,
        source_type=transaction.source_type,
        source_reference=transaction.source_reference,
        activity_type=transaction.activity_type,
        transfer_id=transfer.id if transfer else None,
        reversal_of_transaction_id=transaction.reversal_of_transaction_id,
        corrected_from_transaction_id=transaction.corrected_from_transaction_id,
        reconciled_at=transaction.reconciled_at.isoformat() if transaction.reconciled_at else None,
        splits=[TransactionSplitResponse(split_id=split.id, category_id=split.category_id, category_name=name, amount_minor=split.amount_minor, memo=split.memo) for split, name in rows],
    )

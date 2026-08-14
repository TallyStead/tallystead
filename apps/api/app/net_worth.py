from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger import account_balance
from app.models import AccountValuation, FinancialAccount

LIABILITY_TYPES = {"credit_card", "loan", "mortgage", "line_of_credit", "business_credit_card", "business_loan"}
INVESTED_TYPES = {"brokerage", "investment", "401k", "403b", "traditional_ira", "roth_ira", "pension"}
RESTRICTED_TYPES = {"hsa", "fsa"}
NON_LIQUID_TYPES = {"property", "vehicle"}
BUSINESS_TYPES = {"business_checking", "business_savings", "business_credit_card", "business_loan"}


def account_defaults(account_type: str) -> dict[str, str | bool]:
    ownership = "business" if account_type in BUSINESS_TYPES else "household"
    if account_type in LIABILITY_TYPES:
        return {"ownership_scope": ownership, "balance_nature": "liability", "liquidity": "liability", "tax_treatment": "none", "include_in_planner": False}
    if account_type in INVESTED_TYPES:
        tax = "tax_deferred" if account_type in {"401k", "403b", "traditional_ira", "pension"} else "tax_free" if account_type == "roth_ira" else "taxable"
        return {"ownership_scope": ownership, "balance_nature": "asset", "liquidity": "invested", "tax_treatment": tax, "include_in_planner": False}
    if account_type in RESTRICTED_TYPES:
        return {"ownership_scope": ownership, "balance_nature": "asset", "liquidity": "restricted", "tax_treatment": "health_advantaged", "include_in_planner": False}
    if account_type in NON_LIQUID_TYPES:
        return {"ownership_scope": ownership, "balance_nature": "asset", "liquidity": "non_liquid", "tax_treatment": "none", "include_in_planner": False}
    return {"ownership_scope": ownership, "balance_nature": "asset", "liquidity": "spendable", "tax_treatment": "none", "include_in_planner": ownership == "household"}


def validate_planner_eligibility(account: FinancialAccount) -> None:
    if account.include_in_planner and (account.ownership_scope != "household" or account.balance_nature != "asset" or account.liquidity != "spendable"):
        raise HTTPException(status_code=422, detail="Cash Planner accounts must be household-owned, spendable assets")


def latest_valuation(db: Session, account_id, as_of: date) -> AccountValuation | None:
    return db.scalar(select(AccountValuation).where(AccountValuation.account_id == account_id, AccountValuation.valuation_date <= as_of).order_by(AccountValuation.valuation_date.desc()).limit(1))


def account_net_value(db: Session, account: FinancialAccount, as_of: date) -> tuple[int, AccountValuation | None]:
    valuation = latest_valuation(db, account.id, as_of)
    raw = valuation.value_minor if valuation else account_balance(db, account, as_of=as_of, include_pending=False)
    return (-abs(raw) if account.balance_nature == "liability" else raw), valuation

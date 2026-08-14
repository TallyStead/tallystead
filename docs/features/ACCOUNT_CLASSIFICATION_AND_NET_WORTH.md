# Account Classification and Net Worth

Tallystead separates what an account is from how the application may use it.

## Independent account attributes

- **Type:** checking, savings, money market, credit/debt, brokerage, retirement, HSA/FSA, property, vehicle, business, or other.
- **Ownership:** household or business.
- **Nature:** asset or liability.
- **Liquidity:** spendable, restricted, invested, non-liquid, or liability.
- **Tax treatment:** none, taxable, tax deferred, tax free, or health advantaged.
- **Cash Planner:** explicit inclusion, permitted only for household-owned spendable assets.
- **Net worth:** explicit inclusion, independent of planner eligibility.

Existing checking, savings, and cash accounts migrate as household spendable assets. Existing credit cards and loans migrate as liabilities and are removed from Cash Planner eligibility.

## Default treatment

- Checking, savings, cash, and money market accounts are household spendable assets unless they are business account types.
- Brokerage and investment accounts are invested assets and excluded from the Cash Planner.
- 401(k), 403(b), traditional IRA, and pension accounts are tax-deferred retirement assets.
- Roth IRA accounts are tax-free retirement assets.
- HSA and FSA accounts are health-advantaged, restricted-purpose assets.
- Property and vehicles are non-liquid assets.
- Credit cards, loans, mortgages, and lines of credit are liabilities.
- Business account types use business ownership and are excluded from household planning.

## Valuations and net worth

Ledger balances remain deterministic money-movement totals. Accounts may also have dated manual or imported valuation snapshots. The net-worth report uses the newest valuation on or before its as-of date; when none exists, it uses the ledger balance.

Net worth is calculated within one selected currency. Asset values add, liability values subtract by absolute value, and household and business subtotals remain visible separately. No implicit currency conversion occurs.

## Investment activity

Transactions may be labeled regular, contribution, employer match, purchase, sale, dividend, interest, fee, withdrawal, or market adjustment. These labels preserve meaning and provenance without changing signed ledger behavior. A valuation does not create a transaction, income, or spendable cash.

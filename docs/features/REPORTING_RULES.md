# Tallystead Reporting Rules

## Purpose

Phase 5A reports are deterministic read models over the authoritative household ledger. They do not create transactions, adjust balances, or copy financial results into a separate reporting ledger. The initial contract is versioned as `spending-report-v1`.

## Classification rules

All amounts use signed integer minor units and one explicitly requested ISO currency.

| Activity | Reporting treatment |
| --- | --- |
| Posted negative regular transaction | Spending, shown as a positive report amount. |
| Posted positive transaction with expense-category evidence | Refund; reduces spending. |
| Other posted positive regular transaction | Income. |
| Linked transfer legs | Excluded from spending, income, and net cash flow. |
| Debt-linked payment | Shown separately as debt payment; included in net cash flow, not spending. |
| Investment contribution, match, purchase, sale, dividend, interest, fee, withdrawal, or market adjustment | Shown separately as investment activity; included in net cash flow, not spending or income. |
| Pending transaction | Excluded by default and included only when the user enables the pending filter. |
| Voided transaction | Excluded. |
| Reversed original and its reversal leg | Both excluded. |

Net cash flow is the sum of the contributing signed ledger movements after the exclusions above. Spending after refunds equals classified spending minus classified refunds. A category filter uses only the matching split amounts, so the filtered total reconciles to its displayed category detail rather than the entire parent transaction.

## Filters and comparisons

Reports require a start date, end date, currency, and ownership scope. Optional filters are account, category, merchant, and pending status. Household and business scopes can be selected independently; the explicit `all` scope includes both. Currency is always singular. Tallystead does not infer an exchange rate or combine USD, CAD, and MXN.

The comparison period is the immediately preceding period with the same number of calendar days and the same filters. Current-period warnings identify pending activity, uncategorized activity, and periods that have not ended.

## Breakdowns and drill-down

Category totals derive from transaction splits. Merchant totals use the linked merchant and fall back to the reviewed/raw payee label when no merchant is linked. Account totals use the authoritative financial account. Each report returns the contributing transaction IDs, dates, classifications, accounts, payees, signed ledger amounts, report amounts, status, activity type, and category evidence.

## Explainable signals

- An unusual spending item is at least three times the median classified spending item for the selected period, with a minimum threshold of USD/CAD/MXN 100 in that currency's minor units.
- A recurring merchant change is shown when the merchant exists in both periods, the absolute change is at least 10 currency units, and the percentage change is at least 20 percent.
- These signals are deterministic review prompts, not fraud findings or financial advice.
- Local AI may later summarize these results, but it cannot calculate or replace totals.

## Saved reports and exports

Saved reports retain only a name, view type, and filter parameters. Results are recalculated from the live ledger whenever opened. CSV exports include summary totals, active filters, rule version, and transaction detail. The printable HTML layout can be printed or saved as PDF by the local browser. Exports use private no-store responses and do not require an external service.

## Access and performance

Every read is household-scoped. All household roles may view reports and saved parameters. Owner and Manager may save/delete shared report parameters and create CSV or printable exports; Contributor and Viewer are read-only. Export and saved-report changes are audited.

The initial implementation queries indexed household, account, date, status, split, merchant, and link records directly. No cache is required yet. If measured household-scale performance later requires caching, it must be rebuildable from the ledger and cannot become a second source of truth.

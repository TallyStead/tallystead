# Bills, Income, Debt, and Calendar

Tallystead represents future commitments separately from the observed ledger.

## Domain boundaries

- A bill profile is a recurrence template; a bill instance is one dated obligation.
- An income source is a recurrence template; an income event is one dated expectation.
- A debt balance is a tracked liability. Its generated minimum-payment instances are obligations, not additional expenses or balance changes.
- Expected bills and income never change financial-account balances.
- Posted, non-transfer ledger transactions are the evidence for paid bills and received income.

## Recurrence and generation

`POST /v1/obligations/generate?through=YYYY-MM-DD` materializes active bill, income, and debt-minimum events through an inclusive date. Repeating generation for the same horizon is safe and does not duplicate dated instances. Monthly, quarterly, and yearly schedules preserve their configured day and clamp invalid month-end dates.

Supported cadences are `weekly`, `biweekly`, `monthly`, `quarterly`, `yearly`, and `irregular`. Irregular templates materialize one event and require a new manual expectation or a future schedule update.

## Payment behavior

A bill instance may have multiple payment links. Applied amounts determine its effective state:

- No payment and not past due: `upcoming` or `changed`.
- No payment and past due: `overdue`.
- Applied amount below expected: `partial`.
- Applied amount equal to or above expected: `paid`.
- Explicitly omitted: `skipped`.

An applied amount cannot exceed the linked transaction outflow, including amounts already applied to other bills. Unlinking removes only the association; it never deletes or changes the ledger transaction.

## Access and operations

All `/v1/obligations/*` reads are authenticated and household-scoped. Owner and Manager may create, generate, change, link, or unlink records. Contributor and Viewer are read-only. Consequential changes create household audit events.

The Bills & calendar web workspace provides Upcoming, Bill profiles, Income, and Debts views. The Upcoming view combines dated bills, debt minimums, and expected income in due-date order.

## Bill-profile management

Owners and Managers can open any bill profile to view and edit its schedule, amount range, next due date, priority, essential flag, and active state. Edits affect future generation and do not rewrite already generated instances.

- **Remove upcoming only** deletes unlinked future instances and deactivates the profile. Past, paid, and linked history remains.
- **Delete profile and all** deletes the profile, every instance generated from it, and their payment-link associations after confirmation.

Neither operation deletes or modifies ledger transactions. A transaction that was linked to removed bill history remains in the household ledger with its original provenance.

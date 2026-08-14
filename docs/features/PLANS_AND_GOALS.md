# Financial plans and goals

Phase 6 implements versioned, deterministic household plans on top of the Cash Planner. A plan recommends allocations; it never moves money, posts a transaction, changes a balance, or hides a shortfall.

## Seven baby steps template

Tallystead includes the household-configurable template requested for this project:

1. Save $1,000 for a starter emergency fund.
2. Pay off all non-mortgage debt using the debt snowball: smallest balance first while every other minimum remains protected.
3. Save 3–6 months of observed expenses for a fully funded emergency fund.
4. Invest 15% of observed household income for retirement.
5. Save for children's college, including household-selected tax-advantaged accounts such as a 529 plan.
6. Pay off the home early.
7. Build wealth and give generously.

These steps are supported as a template, not imposed as advice. Owners and Managers can change targets, choose three through six emergency-fund months, change the retirement percentage, reorder or pause steps, mark completion, link evidence accounts, and use snowball, highest-rate, or custom debt ordering.

## Deterministic calculation boundary

Rule version `financial-plan-v1` first runs the existing `cash-planner-v1` contract. Required bills, debt minimum instances, cautious income, pending policy, eligible spendable accounts, and the protected cash buffer are resolved before any goal reserve.

Remaining safe-to-spend cash is allocated to active plan steps in visible priority order. Each step reports requested, allocated, remaining, and shortfall amounts. When targets exceed available cash, Tallystead records overcommitment; it never invents a reserve or treats credit as cash.

## Actual progress and forecasts

- Actual savings progress comes from a linked account balance or confirmed allocation with a posted ledger transaction.
- Debt progress compares the plan's original target with current active debt balances reduced by confirmed bill-payment links.
- Planned recurring, one-time, and transfer allocations remain forecast evidence and do not increase actual progress.
- A recurring allocation can produce an estimated completion date. The estimate is labeled and recalculates forward.
- Saved goal reserves are allocation records only. They do not create or move money.

## History, roles, and AI

Every plan, step, goal, allocation, and reserve change is household-scoped and audited. Consequential plan edits create an immutable numbered version with a reason and timestamp. Only one plan is active by default; activating or creating another pauses the former plan without deleting its history.

All members may read plans and calculate previews. Owners and Managers may change plans and save reserves. The local Assistant may read and cite the active plan through authorized context, but it has no plan-writing tools.

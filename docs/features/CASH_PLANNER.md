# Tallystead Cash Planner

The Cash Planner is a deterministic, read-only calculation over confirmed ledger balances and dated expectations. It answers how much is safe to spend through a selected horizon without treating forecasts, reserves, or credit limits as money.

## Inputs and scope

- Explicit as-of date, 1–365 day horizon, USD/CAD/MXN planning currency, protected cash buffer, and pending-transaction policy.
- Active household-owned spendable asset accounts explicitly enabled for planning.
- Unpaid bill instances and debt minimums due through the horizon, including overdue items.
- Expected income events inside the horizon. Received income is already represented by its linked ledger transaction and is not counted twice.
- A canonical serialized input and `cash-planner-v1` rule version used to produce the reproducibility hash.

Business money, credit availability, liability balances, investments, retirement assets, restricted HSA/FSA funds, property, archived accounts, and non-selected currencies are excluded from household planning cash.

## Cautious policy

Variable income uses the configured minimum; without one it uses the expected value multiplied by confidence. Variable bills use their configured maximum. Missing past-due income is warned about and not counted. Pending transactions reduce cash when the setting is enabled.

## Outputs

- Planning balance and available-to-plan after the protected buffer.
- Safe-to-spend based on the lowest projected balance through the horizon.
- Forecast event timeline, ending balance, and explanation for every input.
- Bill/debt reserve allocations that remain claims on cash rather than new balances.
- Explicit dated shortfalls when an obligation is infeasible.
- Included accounts, excluded-account reasons, assumptions, rule version, and input hash.

Owner and Manager can save snapshots. All household roles can preview and read the latest snapshot. Saved snapshots preserve historical calculation evidence and are never silently recalculated.

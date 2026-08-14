# Household Data Management and Deterministic Demo

## Owner-only data controls

The **Data & demo** workspace gives the household Owner three separate controls:

1. **Complete household archive** — downloads all household-scoped financial records and the original files stored for documents. The archive includes accounts, transactions and splits, bills, income, debts, imports and raw evidence, reconciliation work, automation rules and decisions, plans and goals, report presets, Assistant conversations, audit events, and document metadata/content.
2. **Validated restore** — accepts a Tallystead household archive, verifies its format, household identity, record/table shape, expanded size, and every stored-document checksum, then replaces the current household financial dataset. Household members, passwords, passkeys, active sessions, integration credentials, and server configuration remain in place so a restore cannot lock out the Owner.
3. **Permanent application deletion** — deletes household financial records and document objects after an exact household-name confirmation. It retains household membership/authentication, server and integration configuration, and backup archives stored outside the application. Those retained external backups must be removed separately when the Owner wants every recoverable copy gone.

The portable `.tallystead.zip` export is not encrypted by the application. Store it only on an encrypted device or inside an encrypted archive. It complements the operator-level PostgreSQL/MinIO backup; it does not replace the server recovery procedure in [Backup and Restore](../operations/BACKUP_AND_RESTORE.md).

## Restore safety contract

- Only an Owner can export, restore, or delete household data.
- An archive can restore only into the same household identity that created it.
- Compressed archives are limited to 100 MB, expanded content to 500 MB, 10,000 entries, and 500,000 database rows.
- Unsupported tables/columns, cross-household records, missing document objects, and checksum failures reject the complete restore.
- Restored documents receive new local object keys so validation and database replacement do not overwrite live objects in place.
- Every export, restore, deletion, demo creation/reset, and demo removal is audited.

## Phase 6C fictional scenario

The deterministic fixture uses seed `tallystead-fictional-dawson-v1`. Every name, institution, identifier, value, document, and import row is fictional. No credentials, network download, cloud AI, or external service are used.

The realistic fixture contains:

- nine months and 99 observed transactions across checking, savings, a credit card, mortgage, 401(k), and HSA;
- two recurring income schedules, fixed and variable bills, a credit-card debt, mortgage debt, internal savings and card-payment transfers, a partial healthcare reimbursement, retirement contributions, and the configurable seven-step plan;
- groceries, dining, subscriptions, fuel, healthcare, entertainment, utilities, and housing activity;
- a saved CSV source and versioned mapping, a confirmed direction-aware grocery rule, a ready row, a recurring candidate, a transfer pair, a reimbursement candidate, a suspected duplicate, an invalid row, and an intentionally ambiguous exception;
- one clearly marked fictional receipt stored locally and linked to a transaction;
- a saved spending report and populated report/forecast inputs.

The smoke fixture uses the same scenario definitions with two months and 27 observed transactions. Both variants generate identities and relationships from the household ID, seed, and chosen reference date. Recreating the same variant and reference date produces the same domain identities, amounts, transaction count, and review cases.

## Demo lifecycle

- Demo creation is available only when the selected household has no financial accounts. Existing real data is never mixed with demo data.
- A persistent yellow banner identifies the household as fictional in every workspace.
- Reset is enabled only for a household explicitly marked as demo and recreates only that household.
- Remove demo is enabled only for a marked demo household and leaves membership, authentication, and server settings intact.
- The Data & demo page links directly to every major workspace for a guided walkthrough.

The shared fixture builder is exercised by API/integration/report tests, migration deployment, and browser verification. It is the one source for both smoke and realistic fixtures.

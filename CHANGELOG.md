# Changelog

All notable changes to the Tallystead server, API contract, workers, and supported household deployment are recorded here.

## [0.2.0] - Unreleased

### Added

- Generated, platform-neutral OpenAPI document and typed `@tallystead/contracts` TypeScript transport package.
- Contract export, drift detection, compatibility checking, and coordinated semantic-version tooling.
- Safe deletion for empty financial accounts with reference checks, household authorization, conflict guidance, and audit history.
- Dated debt balance anchors, principal-aware payment links, and explicit debt-balance recalculation.
- Bulk creation of reviewed import transactions and household-local AI category proposals that remain reviewable and non-authoritative.
- User-facing names and match labels for household categorization rules.
- Focused migration and regression coverage for new contract and financial behavior.
- A Pangolin proxy-only Compose override and private Caddy ingress that avoid publishing Tallystead ports on the host.
- A field-by-field network configuration reference with deployment recipes, safe-change guidance, troubleshooting, and recovery.
- Optional Pangolin forwarded-identity sign-in for existing active members, with stable subject linking, trusted-source enforcement, local-role authority, session issuance, and audit history.
- An out-of-band network recovery script that resets saved proxy settings to `.env` and restores direct Caddy HTTPS publishing without requiring web access.
- Owner-only current-connection diagnostics with proxy trust decisions and sanitized request headers for deployment troubleshooting.
- Server-side transaction search, account/category/status/direction/reconciliation/date filters, total counts, and bounded pagination across the complete household ledger.
- Server-side standard and transfer review-queue pagination with complete-evidence search, source/account/category/status/direction/date filters, and accurate queue totals.
- Complete-ledger candidate filtering by currency, exact amount, excluded account or transaction, transfer state, and split state for bounded linking workflows.

### Changed

- Debt planning uses dated starting balances while current debt balances reflect linked principal payments.
- Release validation and documentation include the generated contract package and coordinated server/client versions.
- Server and contract package versions are aligned at `0.2.0`.

### Compatibility

- Database upgrades require forward migrations `20260814_0023_category_rule_names`, `20260814_0024_debt_balance_anchors`, and `20260815_0025_pangolin_external_identities`.
- Independently released clients should pin a compatible `@tallystead/contracts` version.
- Authentication continues to use bearer sessions; no refresh-token endpoint is part of this version.

## [0.1.0] - 2026-08-14

### Added

- Initial self-hosted FastAPI server and worker runtime with PostgreSQL, MinIO, Caddy, Docker Compose, migrations, backup, and recovery.
- Household authentication, passkeys, RBAC, sessions, audit history, and security-sensitive administration.
- Authoritative ledger, obligations, Cash Planner, imports, reconciliation, documents, local AI, reporting, Assistant, plans, goals, automation, demo data, and release/security tooling.

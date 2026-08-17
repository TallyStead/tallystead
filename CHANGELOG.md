# Changelog

All notable changes to the Tallystead server, API contract, workers, and supported household deployment are recorded here.

## [0.2.2] - Unreleased

### Fixed

- Production and local Compose now pass the same non-secret network configuration to the worker as the API, preventing startup validation failures when public and internal URLs are configured.
- The API accepts any hostname routed through Caddy instead of enforcing configured public/internal identities or requiring a browser-origin allowlist.
- Passkeys, password-recovery links, and server identity use the current trusted request origin rather than one deployment-wide canonical URL.
- Direct clients on the private HTTP ingress use normal Tallystead authentication; Pangolin identity headers remain trusted only from configured proxy addresses and are stripped for every other source.
- The versioned Caddy image embeds its tested private HTTP ingress configuration, eliminating the production host Caddyfile bind mount; the server bundle carries both Pangolin deployment overlays.
- The base Compose deployment publishes the configurable private HTTP ingress directly, so remote Pangolin deployments no longer require an extra host-port overlay.

### Compatibility

- This server patch pins `tallystead-web:0.2.2`, which automatically uses the origin from which it was loaded.
- Existing `0.2.1` databases require no additional migration for this patch.

## [0.2.1] - Unreleased

### Changed

- Network identity, origins, proxy trust, forwarded authentication, and certificate ownership now come only from `.env`; database staging and runtime Caddy rewriting were removed.
- The Server page is read-only and reports loaded configuration, certificate state, service health, and sanitized connection diagnostics.
- Pangolin/Newt may use either a private shared Docker network or a restricted private host IP and HTTP port while retaining direct local HTTPS recovery.
- Invalid network environments fail at startup, default HTTPS ports are normalized for identity matching, and host-console recovery no longer requires SQL.

### Compatibility

- The removed network stage/test/apply/rollback API endpoints are not used by the `0.2.1` web client. Deploy the `0.2.1` server and web images together.
- Existing encrypted `network_configuration` rows are ignored and do not need to be deleted.

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

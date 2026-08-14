# AGENTS.md

This file is the working agreement for coding agents contributing to the Tallystead server repository. It applies to the entire repository unless a more specific `AGENTS.md` exists below a subdirectory.

## Project purpose

Tallystead is a local-first, self-hosted household financial management server. The server owns financial truth, authorization, deterministic calculations, data storage, document processing, backup and recovery, and optional local-AI integrations. The web client is released separately from `tallystead/tallystead-web` and connects to this server through its versioned API.

Read these files before making a substantial change:

- `README.md` for repository layout and supported operation.
- `CONTRIBUTING.md` for contribution and pull-request expectations.
- `docs/README.md` for the public documentation index.
- `docs/security/TESTING.md` for required regression coverage.
- `docs/security/THREAT_MODEL.md` and `docs/security/PRIVACY_AND_DATA_FLOWS.md` for security and privacy boundaries.
- Relevant feature, architecture, or operations documents for the area being changed.

## Non-negotiable product rules

- Keep the server authoritative. Clients may present and request actions, but must not become the source of truth for balances, reports, planning rules, authorization, or ledger state.
- Preserve local-first operation. Core functionality must not require a hosted service or send household financial data outside the installation.
- Optional AI must remain local, reviewable, and non-authoritative. AI output must not directly mutate ledger entries, balances, reserves, reconciliation state, or other financial truth.
- Store money as integer minor units with an ISO currency code. Never introduce floating-point storage or implicit cross-currency aggregation.
- Make financial calculations deterministic, reproducible, and covered by tests. Keep rule or snapshot versions where the existing model requires them.
- Preserve provenance. Imported, extracted, suggested, corrected, and reconciled values must retain the evidence needed to explain their origin.
- Enforce household scoping and RBAC on the server for every read and write. Never rely on a hidden web control as authorization.
- Keep transfers neutral in household spending reports and keep transaction splits equal to their parent amount.
- Never hide planner shortfalls or let a reserve create money.
- Audit credential changes, role changes, session revocation, exports, destructive operations, and other security-sensitive actions consistently with existing behavior.

## Repository boundaries

- `apps/api` contains the FastAPI application, domain services, SQLAlchemy models, schemas, routes, workers, tests, and Alembic migrations.
- `apps/worker` contains worker and scheduler entry-point guidance. Worker behavior currently shares the API runtime.
- `infrastructure/compose` contains the supported Docker Compose deployment and Caddy configuration.
- `infrastructure/backup` contains backup and recovery tooling.
- `docs` contains supported public product, architecture, security, feature, and operations documentation.
- `tests/fixtures` contains fictional or fully anonymized cross-service fixtures.
- `scripts` contains repository, security, brand, and upgrade validation.
- The web interface does not belong in this repository. Coordinate API contract changes with `tallystead/tallystead-web`, but do not recreate `apps/web` here.
- `packages/contracts` is reserved for future shared, generated, or platform-neutral contracts. Do not move server authority or domain calculations into it.

Never read, copy, modify, print, or commit private runtime material unless the user explicitly places a specific sanitized file in scope. This includes:

- `.env` files and credentials;
- `data/`, database volumes, object-store contents, backups, and backup keys;
- `.local/` certificates or runtime state;
- real statements, receipts, exports, email contents, tokens, private logs, or household data;
- `_internal-repo-staging/` and local development caches.

Use fictional or sanitized fixtures in tests and examples. Do not expose secrets in command output, exceptions, API responses, screenshots, or documentation.

## How to work

1. Inspect the existing implementation, tests, documentation, and current Git status before editing.
2. Preserve unrelated user changes in a dirty worktree. Do not reset, discard, reformat, or overwrite work outside the requested scope.
3. Make the smallest coherent change that satisfies the requested behavior and its failure boundaries.
4. Add or update tests alongside implementation. A feature is not complete without coverage at the appropriate layer.
5. Update documentation in the same change when behavior, configuration, deployment, security, privacy, API compatibility, migration, backup, or recovery changes.
6. Run the focused checks first, then the broader relevant checks listed below.
7. Review the final diff for private data, accidental generated files, former-brand references, and unrelated churn.
8. Do not commit, push, open a pull request, publish an image, create a release, modify remote settings, or delete material unless the user explicitly asks for that action.

When requirements are unclear, inspect the documented product rules and existing behavior first. Ask the user only when a choice would materially change product behavior, security, data compatibility, or the requested scope.

## API and domain changes

- Keep HTTP concerns in routes and business rules in domain services where the current code follows that separation.
- Validate untrusted input at the API boundary and again where a domain invariant requires it.
- Use explicit schemas for requests and responses. Never serialize ORM objects or stored secret fields indiscriminately.
- Return only data authorized for the current user and household. Resource identifiers supplied by a client must not bypass household scoping.
- Keep errors useful without revealing credentials, internal network details, private data, or whether a cross-household resource exists.
- Maintain API compatibility for independently released clients. If a breaking change is truly necessary, document the compatibility and release order explicitly.
- Do not allow reports or the Assistant to invent totals. Totals must come from deterministic server calculations and authorized records.
- External AI or public runtime addresses remain outside the supported local-AI boundary unless the product documentation and security model are deliberately changed.

## Database migrations

- Every persistent schema change requires a new forward Alembic migration in `apps/api/alembic/versions`; do not rewrite an already released migration.
- Keep SQLAlchemy declarations, migration behavior, schemas, services, fixtures, and tests synchronized.
- Choose the next revision from the current Alembic head and verify there is only one intended head.
- Make upgrades safe for existing installations. Define defaults, backfills, constraints, and rollback/recovery implications deliberately.
- Test both a clean database and the supported earlier-database upgrade path when a migration can affect existing data.
- Never use a real household database for migration or destructive-operation testing.

## Authentication, authorization, and destructive actions

- Passwords, passkeys, recovery, sessions, network trust, and integration secrets are security boundaries. Preserve secure defaults and existing audit behavior.
- All roles must be handled intentionally. Owners may administer household-wide settings; each user must retain access to their own profile, security, and sessions as documented.
- Treat export, restore, demo reset, bulk replacement, deletion, and credential changes as high-risk operations. Require explicit authorization and preserve confirmations, validation, audit, and recovery behavior.
- Do not weaken authentication, authorization, cryptography, network enforcement, import validation, migration checks, backup verification, or CI security checks to make a test pass.
- If a narrowly scoped security exception is unavoidable, follow `docs/security/SECURITY_EXCEPTIONS.md` and include an owner, rationale, scope, and expiration.

## Imports, documents, and integrations

- Preserve raw import evidence, checksums, parser versions, source mappings, and row-level provenance.
- Expect CSV layouts to vary. Mapping guesses must remain reviewable and user-correctable.
- Imports must be idempotent and duplicates must not silently create duplicate ledger activity.
- Store documents through the protected object-storage boundary; do not expose raw object-store paths or unauthenticated downloads.
- OCR and local-AI extraction produce suggestions or document metadata until accepted through the configured review flow.
- Store integration secrets encrypted and never return the stored secret value to a client after save.
- Email, AI, object storage, and other integrations must fail safely without preventing the deterministic core from operating.

## Testing and validation

From `apps/api`:

```sh
ruff check .
pytest
```

From the repository root:

```sh
python3 scripts/check_brand_references.py
python3 scripts/check_repository_security.py
docker compose --env-file infrastructure/compose/.env.example \
  --file infrastructure/compose/compose.yaml config --quiet
docker compose --env-file infrastructure/compose/.env.release.example \
  --file infrastructure/compose/compose.release.yaml config --quiet
```

On Windows PowerShell, use `python` instead of `python3` when that is how Python is installed. The Compose commands can be entered on one line.

For migration changes, also run the upgrade-path check against a disposable PostgreSQL database as documented in `README.md` and `docs/security/TESTING.md`. For backup, networking, Caddy, MinIO, worker, or release changes, run the matching operational checks and document any manual verification that cannot be automated locally.

At minimum, test:

- the successful path;
- authorization and cross-household denial;
- invalid and boundary input;
- deterministic financial invariants;
- persistence and reload behavior;
- migration or compatibility behavior when applicable;
- safe failure of optional integrations.

Do not claim a check passed unless it was actually run. Report skipped checks and the reason.

## Documentation and naming

- Use the product name `Tallystead`. Former-name strings are allowed only where explicitly required for archive compatibility and documented by the brand checker.
- Keep public documentation focused on supported behavior and operations. Internal brainstorming, build history, AI transcripts, prototypes, and private planning belong outside this public repository.
- Use clear, direct language for household operators. Include Windows instructions when setup or operations differ materially from Unix-like systems.
- Keep configuration examples free of usable secrets and use documented example domains, addresses, and fictional household data.

## Completion checklist

A change is complete only when:

- the requested behavior works at the authoritative boundary;
- relevant security, privacy, household, currency, provenance, and audit invariants still hold;
- tests cover the behavior and important failures;
- migrations and upgrade behavior are handled when persistent data changes;
- public documentation and examples match the implementation;
- relevant lint, test, policy, Compose, and operational checks pass;
- the final diff contains no secrets, private data, generated caches, or unrelated changes;
- compatibility with the independently released web client is stated when affected.

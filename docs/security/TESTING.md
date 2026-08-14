# Tallystead Testing Strategy

## Principle

Tests are designed from the specification and acceptance criteria before or alongside implementation. A feature is incomplete without tests at the appropriate boundary.

## Test layers

| Layer | Location | Responsibility |
| --- | --- | --- |
| Unit | `apps/api/tests` and future domain packages | Money invariants, planner rules, bill generation, currency validation, role policy. |
| API | `apps/api/tests` | Request validation, versioned contracts, authentication, RBAC, household scoping. |
| Integration | `tests/integration` | PostgreSQL, MinIO, imports, background jobs, local-AI adapter behavior. |
| End-to-end | `tallystead-web` repository | Setup, server URL connection, transaction/bill/planner/review flows. |
| Visual acceptance | Live routed web application | Approved mockup alignment, desktop/mobile layout, navigation, empty states, and configuration-page behavior. |
| Operational | `infrastructure` test scripts | Docker startup, migrations, backup/restore, upgrade and health checks. |

## Required regression coverage

- A transaction split equals its parent transaction amount.
- Linked transfer legs are neutral in household reporting.
- A reserve cannot create money or hide a planner shortfall.
- Planner output is reproducible from its input snapshot and rule version.
- Cross-household reads/writes fail, regardless of resource identifiers supplied by a client.
- Role downgrade, session revocation, exports, credential changes, and destructive actions are audited.
- Imported/AI-derived data retains source/provenance and requires configured review behavior.
- Currency values are never stored as floats; cross-currency aggregation requires explicit conversion.
- Owner-only settings are absent for other roles and direct settings URLs return the user to the household workspace.
- Stored integration secrets are never rendered back into a browser field or API response.

## Commands

```bash
# API quality checks (after installing apps/api development dependencies)
cd apps/api
ruff check .
pytest

# Published-web integration verification
docker pull ghcr.io/tallystead/tallystead-web:0.1.0
TALLYSTEAD_WEB_IMAGE=ghcr.io/tallystead/tallystead-web:0.1.0 \
  docker compose --env-file .env -f infrastructure/compose/compose.yaml up -d web

# Compose configuration validation
docker compose --env-file infrastructure/compose/.env.example -f infrastructure/compose/compose.yaml config --quiet
```

## Scenario fixtures

Fixtures are anonymized and live in `tests/fixtures`. Add a fixture whenever a new financial edge case becomes an accepted product behavior.

## Phase 7 verification record

- API lint and 80 API/domain tests passed. Coverage includes absolute/idle session behavior, server logout revocation and audit, household roles, cross-household resource hiding, exports/deletion, documents, reports, Assistant no-write behavior, plans, automation, and the earlier phases' financial invariants.
- The realistic deterministic household creates at least 80 transactions. Its complete create/report/export/delete/restore/reset/remove scenario took 0.34 seconds in the local in-memory regression run; explicit guards require demo creation under 5 seconds and its nine-month spending report under 2 seconds.
- Before the repository split, web type checking and the 21-route production build passed. The independently released `ghcr.io/tallystead/tallystead-web:0.1.0` image later passed a same-origin Caddy integration smoke test covering session restoration, Overview, Transactions, Cash Planner, Review, Assistant, and Settings without browser warnings or errors.
- The `0.1.0` web manifest currently contains only `linux/amd64`. It ran successfully under arm64 Docker emulation, but a multi-architecture web release is required before arm64 is listed as a native supported production platform.
- PostgreSQL migration `20260813_0016` upgraded through `20260814_0022` in a disposable database while preserving a seeded household. The disposable database was removed afterward.
- A live encrypted backup was created and age-authenticated, all payload checksums passed, the object archive opened, and the database restored into a disposable 55-table verification database. The private identity and archive remain under ignored local data paths.
- Python advisory audits passed in this repository, while web dependency checks run in the `tallystead-web` repository. Repository forbidden-artifact/action-pin policy passed. Trivy source/IaC configuration passed with only expiring path-scoped SE-002 exceptions; API, web, backup, and remediated Caddy image scans passed with zero unresolved high/critical vulnerability or embedded-secret result.
- The deployed stack is healthy at migration `20260814_0022`. HTTPS returns HSTS, CSP, no-sniff, frame denial, no-referrer, and restricted browser permissions. The web runtime runs as the unprivileged `node` user.
- Manual screen-reader coverage, real Cloudflare/Proxmox validation, GitHub branch-protection enablement, trusted build CA replacement, and the external name review remain explicit release-operator gates in `RELEASE_CHECKLIST.md`; they are not silently treated as automated checks.

## Phase 1 verification record

- API lint: passed.
- API tests: 17 passed, covering household scoping, role enforcement, exact splits, linked-transfer neutrality, lifecycle transitions, reconciliation locks, audited revisions, reversal/correction evidence, historical replay, private export, and secret exclusion.
- Web production build: passed for all application-shell routes, including the ledger interface.
- Live deployment: migration `20260813_0007` is current and the HTTPS health endpoint reports healthy.

## Phase 2 verification record

- API lint: passed.
- API tests: 20 passed, adding recurrence idempotency, month-end clamping, partial-payment application, expected-income balance isolation, debt-minimum generation, calendar ordering, and read-only role coverage.
- Web production build: passed with the functional Bills & calendar workspace.
- Live deployment: migration `20260813_0008` is current and all Compose services report healthy.

## Phase 2B verification record

- API lint: passed.
- API tests: 24 passed, adding account default, planner eligibility, HSA/retirement/business exclusion, dated valuation, liability, net-worth, and investment activity coverage.
- Web production build: passed with expanded account management and the live net-worth overview.
- Live deployment: migration `20260813_0009` is current, all Compose services report healthy, and the HTTPS health and Overview routes respond successfully.

## Phase 3 verification record

- API lint: passed.
- API tests: 28 passed, adding deterministic paycheck-to-rent, cautious variable estimates, explicit shortfall, saved snapshot, role boundary, missing paycheck, pending-payment, and credit-exclusion coverage.
- Web production build: passed with the functional Cash Planner workspace.
- Live deployment: migration `20260813_0010` is current, all Compose services report healthy, and the HTTPS health and Planner routes respond successfully.

## Phase 4 verification record

- API lint: passed.
- API tests: 38 passed, covering idempotent file ingestion, raw evidence preservation, duplicate rows, non-secret source instructions, reminder advancement, deterministic candidate confidence, confirmation/unmatch recovery, explicit transaction creation, missing-expected-bill exceptions, title-cased real-export headers, original-description provenance, imported status handling, reviewable mapping guesses for different export shapes, signed and debit/credit amount layouts, Source editing/deletion with history preservation, account-reassignment protection, and read-only role coverage.
- Web production build: passed with the Imports & review workspace.
- Live deployment: migration `20260813_0012` is current, all Compose services report healthy, and the HTTPS health and Review routes respond successfully.

## Phase 5 verification record

- API lint: passed.
- API tests: 46 passed, adding authenticated object storage/download, protected image thumbnails, unlinked-document validity, deterministic candidate evidence, reversible match/unmatch behavior, read/write role boundaries, safe deletion, public-AI URL rejection, local runtime configuration, queued worker extraction, detailed receipt/line-item normalization, arithmetic inconsistency warnings, explicit suggestion acceptance, disable-AI preservation, and non-persistent/audited synthetic vision-test coverage.
- Web type check and production build: passed with the Documents workspace and authenticated application-shell route.
- Privacy invariants: extraction acceptance changes only document metadata; AI output cannot mutate ledger transactions, balances, reserves, or reconciliation state.
- Live deployment: migration `20260813_0013` is current, API and worker are healthy, the HTTPS Documents route responds successfully, and a real MinIO write/read/remove round trip passed without leaving a test object behind.
- Detailed receipt enhancement: the deployed API validated the `receipt-extraction-v1` normalization and arithmetic contract; the extraction worker is active. An end-to-end model accuracy check requires the household to configure a vision-capable Ollama or LM Studio model and provide representative receipt images.
- Settings form regression: AI, email, and access forms retain their form element before awaiting the API and no longer dereference React's cleared event target. Web type checking and the production build pass. Local in-app-browser submission remains unavailable until the Caddy local certificate authority is trusted by that browser.
- AI configuration persistence: API coverage verifies that saved non-secret provider, runtime URL, and model values are returned on both save and reload while password fields remain absent. The page uses those values as its reloaded form defaults.
- Live vision capability: LM Studio model `qwen/qwen2.5-vl-7b` processed the non-persistent synthetic receipt in 5.7 seconds. Model availability, structured contract, merchant, date, subtotal, tax, total, currency, two line items, and deterministic arithmetic all passed.

## Phase 5A verification record

- API lint: passed.
- API tests: 48 passed, adding spending/income/refund/transfer/reversal/pending/investment classification, exact split-filter totals, household/business scope, USD/CAD separation, drill-down reconciliation, local CSV and printable output, saved parameters, audit behavior, and Viewer export restrictions.
- Web type check and production build: passed with the Reports dashboard and category, merchant, account, cash-flow, debt, and net-worth views.
- Reporting invariants: all results are derived from the authoritative ledger under an explicit rule version; one currency is requested at a time; saved reports contain parameters rather than copied financial results; AI is not required and cannot calculate totals.

## Phase 5B verification record

- API lint: passed.
- API tests: 55 passed, adding deterministic history suggestions, pre-acceptance non-mutation, edited exact splits, rejection and batch review, explicit learned rules, reversible rule management, read/write role boundaries, disabled-AI behavior, private member conversations, streamed responses, authoritative report totals, persisted citations, and proof that assistant answers leave ledger records unchanged.
- Web type check and production build: passed with the Assistant route, shared bottom popup, conversation history, streaming stop/regenerate controls, source display, filter controls, categorization review, batch actions, and learned-rule management.
- Dependency review at the split boundary confirmed `ai` and `@ai-sdk/react` were used only as local client-side streaming utilities. Ongoing npm auditing and hosted-service boundary checks belong to the web repository.
- Currency presentation regression: assistant context contains display-ready USD, CAD, or MXN strings and no raw `*_minor` amount fields; the model never needs to convert stored cents into household-facing money.
- Live deployment: migration `20260813_0015` is current, all Compose services are healthy, and the HTTPS health and Assistant routes respond successfully.

## Phase 6 verification record

- API lint: passed.
- API tests: 59 passed, adding exact seven-step template order, editable/versioned steps, one-active-plan behavior, Cash Planner-first goal allocation, payment-evidence debt progress, debt snowball order, explicit overcommitment, ledger non-mutation, linked-account evidence, planned-versus-actual separation, confirmed transaction requirements, read/write role boundaries, and preserved plan versions.
- Live deployment: migration `20260813_0016` is current, all Compose services are healthy, and the HTTPS health and Goals routes respond successfully.
- Web type check and production build: passed with the Plans & goals route, responsive seven-step workflow, deterministic projection controls, goal/debt progress, completion estimates, reordering/pause/completion controls, allocation entry, plan history, and debt strategy controls.
- Planning invariants: goal reserves never exceed Cash Planner safe-to-spend cash; shortfalls remain explicit; allocation records do not move money; historical plan versions are not rewritten.
- AI boundary: the local Assistant may cite the active plan through authorized context but retains no plan mutation tools.

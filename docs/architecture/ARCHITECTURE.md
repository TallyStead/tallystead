# Tallystead Architecture Overview

## System boundary

Tallystead is a self-hosted household server, accessed by web clients now and mobile clients later. The server is authoritative for financial records, access control, calculations, documents, and background processing.

```text
Separately released Tallystead Web client / future iOS and Android clients
                 │ authenticated, versioned API
                 ▼
FastAPI server
├── API and RBAC boundary
├── Domain services: ledger, bills, planner, reconciliation, plans
├── PostgreSQL: relational records, provenance, audit history
├── MinIO: documents, attachments, exports, backup artifacts
└── Workers/scheduler: imports, email, OCR, local AI, notifications
                     └── Local AI gateway: Ollama / LM Studio
```

## Module rules

- The `tallystead-web` repository renders browser experiences and calls the API. It publishes an independently scanned, immutable container image and does not calculate balances, reserves, or authorization decisions.
- `apps/api` exposes versioned API endpoints and contains the server-side domain/application services.
- `apps/worker` contains asynchronous handlers. Each handler has an idempotency, retry, audit, and health contract.
- FastAPI's OpenAPI document is the canonical client/server contract source. `packages/contracts` contains its deterministic OpenAPI snapshot and generated TypeScript client; CI rejects drift and common breaking changes. Swift and Kotlin clients generate from the same released OpenAPI document.
- `infrastructure/compose` contains Docker deployment definitions only; it does not contain domain behavior.

## Data rules

- Store money as integer minor units and ISO 4217 codes.
- Initial currencies are USD, CAD, and MXN. Do not silently aggregate or convert currencies.
- Financial calculation code is pure, testable, and versioned.
- Raw imports and AI suggestions retain provenance and never replace source evidence.
- Document metadata and review history live in PostgreSQL; protected originals and thumbnails live in MinIO behind authenticated API routes.
- Deterministic document matching remains available without AI. AI extraction is asynchronous, suggestion-only, and can update document metadata only after explicit acceptance.
- Every database query and resource operation is household-scoped and authorization-checked server-side.

## Operational rules

- Docker on a household home server is the initial deployment target.
- Local network access only at launch; cloud account and internet access are not required.
- PostgreSQL and MinIO data use persistent volumes and must be included in backups.
- Local-AI endpoints are restricted to loopback, Docker-service, `.local`, `.home.arpa`, or private-network addresses and requests do not follow redirects.
- Environment files are local-only and excluded from Git. No secrets are logged or returned through the API.

## Verification boundary

- Unit tests verify domain invariants and pure calculations.
- Integration tests verify database, object storage, authorization, imports, and worker behavior.
- API tests verify contracts and household isolation.
- End-to-end tests in the web repository verify priority browser flows; this repository smoke-tests the selected published web image through Caddy against the server API.
- Docker deployment, backup/restore, and upgrade paths are supported and tested operational flows.

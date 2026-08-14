# Tallystead Self-Hosted Server Platform

**Status:** Foundational architecture  
**Related:** [Architecture Overview](ARCHITECTURE.md) · [RBAC and Access Control](RBAC_AND_ACCESS_CONTROL.md)

## Intent

Tallystead is a household-operated server with a web client, in the spirit of Home Assistant: the server runs locally, owns the household’s data and automations, and exposes a browser experience on the local network. Cloud services are neither required nor assumed.

This document defines the platform boundary required before feature implementation. It does not prescribe a particular language or framework.

## Operating model

```text
Browser / future native clients
            │ local authenticated API
            ▼
Tallystead server deployment
├── Separately released web-client container
├── API
├── Domain services (ledger, bills, planner, reconciliation, plans)
├── Background job runner
├── Integration adapters (CSV, IMAP/SMTP, OCR, local AI)
├── Local database and encrypted document store
├── Configuration, secrets, audit log, and health service
└── Backup and restore service
```

## Non-negotiable platform properties

- The core server works without internet access after installation.
- Financial records, documents, planner calculations, and AI processing remain local by default.
- The browser is a client; it must not become the source of financial truth.
- All consequential changes flow through authenticated server-side services and produce audit history.
- Integrations are adapters behind stable interfaces; they never directly write arbitrary ledger state.
- Configuration and credentials are protected separately from ordinary application data.

## Chosen implementation baseline

| Concern | Initial choice |
| --- | --- |
| Web client | Independently released React / Next.js image |
| API and domain services | Python / FastAPI |
| Primary database | PostgreSQL |
| Asynchronous work | Worker queue plus scheduler |
| Documents | Local persistent file/document storage with metadata in PostgreSQL |
| Document processing | Worker-based OCR and parsing |
| Local AI | Gateway adapters for Ollama and LM Studio |
| Deployment | Docker on a household home server |
| Launch network scope | Local network only |

## Server responsibilities

### Application and API

- Serve the browser application and versioned internal API.
- Authenticate sessions and enforce RBAC for every protected operation.
- Validate all money, date, account, and household-boundary inputs server-side.
- Keep UI-specific logic outside the financial domain services.

### Domain services

- Own ledger posting, balances, bill instances, planner forecasts, reconciliation, and plan allocation rules.
- Use pure, versioned calculation modules for financial computation.
- Record provenance and audit events for imports, corrections, configuration changes, and decisions.

### Background jobs

Jobs are server-owned and must be observable, retry-safe, and idempotent where possible.

- Import processing and duplicate detection.
- IMAP polling and document ingestion.
- Local OCR/AI extraction and suggestion generation.
- Bill-instance generation and forecast refresh.
- Notifications and backup scheduling.
- Cleanup only when governed by an explicit retention policy.

Each job needs a visible status, last run time, error message, retry behavior, and audit connection to the originating configuration or user action.

### Integrations

Begin with a narrow adapter contract:

- **Importer:** receives source content, emits raw records and parse warnings.
- **Email connector:** receives documents/messages and emits raw attachment/message metadata.
- **Document processor:** extracts suggestions with source and confidence.
- **Notifier:** sends a user-approved notification without accessing more data than necessary.

Adapters may propose data. Domain services validate, normalize, and apply reviewed changes.

Local AI uses a direct backend-to-runtime connection over the trusted household network. The browser must not act as the AI proxy. No relay or bridge service is part of the supported release.

## Installation, access, and local networking

### Initial installation

- The installer creates a local server identity, data directory, and first household owner.
- The first-run flow creates an owner account, encryption/key-storage configuration, backup recommendation, and local address.
- Installation must not require creating a cloud account.

### Local access

- Start with access from the host machine and trusted local network only.
- Use an explicit local address/hostname strategy; avoid assuming internet DNS.
- Require authenticated sessions even on a trusted local network.
- Keep remote access deferred until a separate security and threat-model decision is approved.
- Use Caddy as the initial local TLS reverse proxy. Its local certificate authority must be trusted on each household client before passkeys are enabled; HTTPS is a browser requirement for WebAuthn outside the special `localhost` development case.
- Use one database-backed canonical client URL and an optional internal HTTPS upstream URL. Stage and test changes before Caddy activation; keep the administration API on a permissioned Unix socket and retain a last known-good configuration.
- Support Cloudflare DNS-01 with a zone-scoped, encrypted, write-only token and a custom Caddy build; do not persist the token in Caddy's dynamic configuration state.
- Direct internet mode requires explicit Owner acknowledgment and the Phase 7 readiness controls; it is never inferred from DNS or proxy headers.

### Client connection model

- Web, iOS, and Android clients connect by entering or selecting the household server URL, rather than creating a Tallystead cloud account.
- The server setup screen provides the canonical local-network URL and a clear copy/share action.
- A client verifies server identity, signs the user in to that server, then stores only the URL and secure session/device credentials using platform-appropriate secure storage.
- Server switching, sign-out, and device revocation must be supported without deleting household data from the server.

### Updates

- Display installed version, update availability, migration requirements, and rollback/recovery guidance.
- Run database migrations transactionally where possible and create/verify a backup before a destructive migration.
- Never silently change financial calculation rules; record calculation-rule versioning and migration notes.

## Configuration and secrets

- Store application settings in structured local configuration with validation and change audit history.
- Store credentials and encryption material using platform-appropriate secure storage where available; never expose them in logs, exports, or browser responses.
- Email, AI, importer, and future integration credentials must be individually enabled/disabled and testable.
- Configuration UI must state the data boundary of every integration: what is read, what is sent, where it is stored, and how to revoke access.

## Data protection, backup, and recovery

- Use a local relational database for financial records and an encrypted local attachment/document store.
- Backups must cover database, attachments, configuration required for restoration, and version metadata.
- Backup success/failure must be visible in the application.
- Restore must be a guided, auditable operation with a preview of target household and data age.
- Export and deletion behavior must state whether backups remain affected.

## Health and observability

Provide a local health screen showing:

- Server version and uptime.
- Database status and most recent backup result.
- Background-job status, failures, and retries.
- Integration status without exposing credentials.
- Local AI runtime availability and resource limits.
- Security-relevant configuration warnings, such as no backup or weak/disabled local authentication.

Diagnostics must minimize sensitive content and remain local unless the user explicitly exports them.

## Documentation and verification requirements

- Every server component, integration, environment variable, backup/restore path, and migration procedure has maintained documentation in the repository.
- Worker jobs and integrations document their inputs, retries, idempotency behavior, failure states, health signal, and recovery path.
- Docker deployment, first-run setup, upgrades, backup/restore, and troubleshooting are tested as supported operational flows—not merely described.

## Build workstream

- [x] Define the Docker server runtime, persistent data locations, and home-server distribution baseline.
- [x] Define API/session model and connect it to [RBAC and Access Control](RBAC_AND_ACCESS_CONTROL.md).
- [x] Implement configuration, encrypted secret management, and audit-event foundation.
- [x] Implement worker heartbeat, cleanup jobs, and health reporting; feature-specific queue idempotency follows with each job type.
- [x] Implement local HTTPS access, canonical server identity, and first-run Owner setup.
- [x] Implement backup, restore, restore verification, and migration safeguards before broad household use.
- [ ] Define adapter SDK/contracts before adding a second importer or integration.

## Deferred decisions

- Remote access method and security posture.
- Multi-server or household-to-household data sharing.
- Public integration/plugin marketplace.
- Cloud relay, remote diagnostics, or any managed-service offering.

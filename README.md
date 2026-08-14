# Tallystead

Tallystead is a local-first, self-hosted household financial management server. It provides deterministic financial services, local document processing, optional local AI, and a deployment boundary for the separately released Tallystead Web client.

## Repository layout

- `apps/api` — FastAPI API and domain-service entry point.
- `apps/worker` — worker/scheduler entry point (scaffolded with the API runtime initially).
- `infrastructure/compose` — Docker Compose local/home-server stack.
- `docs` — product, architecture, access-control, and operational documentation.
- `tests` — cross-service scenario tests as they are introduced.

## Development principles

- The API/server is authoritative; clients do not contain financial calculation rules.
- Money uses integer minor units plus an ISO currency code.
- Tests and documentation are part of every completed task.
- Cloud services are optional and never required for core operation.

## Start the local stack

1. Create `.env` in the repository root from `infrastructure/compose/.env.example` and set local secrets. The local `.env` is intentionally ignored by Git. `TALLYSTEAD_WEB_IMAGE` selects the exact independently released web client.
2. Run `docker compose --env-file .env -f infrastructure/compose/compose.yaml up --build`.
3. Open Tallystead at `https://localhost:8443`. The web client and API are served through Caddy at one server URL.

### Local HTTPS and future passkeys

Caddy creates a local certificate authority for the configured server host. Before using passkeys from a browser, trust Caddy's local root certificate on each client device. For the Docker stack, copy the certificate from the Caddy container's `/data/caddy/pki/authorities/local/root.crt` and add it to the device trust store. For a home-server LAN deployment, set `TALLYSTEAD_SERVER_HOST` to a hostname that every client can resolve (for example `tallystead.home.arpa`), set the HTTPS port to `443`, and trust that local CA on household devices. Do not use an IP address as the long-term passkey server identity.

The self-hosted application now includes Caddy HTTPS, password/passkey authentication, household RBAC, encrypted integration configuration, ledger and account management, bills and income, Cash Planner, CSV imports/reconciliation, protected document storage, deterministic spending/cash-flow/debt/net-worth reports with local exports, configurable seven-step financial plans and goals, reviewable transaction categorization, a read-only local assistant, and optional household-local AI extraction. PostgreSQL and MinIO backup/restore are part of the supported operating model.

Owners can also use **Data & demo** to download a complete restorable household archive (including document originals), validate and restore that archive, permanently delete application-held financial data with retained-backup disclosure, or create/reset/remove a clearly fictional deterministic Phase 6C household.

Deployment references:

- [Documentation index](docs/README.md)
- [Network and certificate operations](docs/operations/NETWORK_AND_CERTIFICATES.md)
- [Proxmox deployment](docs/operations/PROXMOX_DEPLOYMENT.md)
- [Brand and compatibility](docs/architecture/BRAND_AND_COMPATIBILITY.md)
- [Backup and restore](docs/operations/BACKUP_AND_RESTORE.md)
- [Threat model](docs/security/THREAT_MODEL.md)
- [Privacy and data flows](docs/security/PRIVACY_AND_DATA_FLOWS.md)
- [GitHub security](docs/security/GITHUB_SECURITY.md)
- [Release checklist](docs/operations/RELEASE_CHECKLIST.md)
- [Release operation](docs/operations/RELEASING.md)
- [Release installation](docs/operations/RELEASE_INSTALL.md)

## Quality checks

- API: `cd apps/api && ruff check . && pytest`
- Deployment configuration: `docker compose --env-file infrastructure/compose/.env.example -f infrastructure/compose/compose.yaml config --quiet`
- Release deployment configuration: `docker compose --env-file infrastructure/compose/.env.release.example -f infrastructure/compose/compose.release.yaml config --quiet`
- Brand references: `python3 scripts/check_brand_references.py`
- Repository/secret policy: `python3 scripts/check_repository_security.py`
- Earlier-database upgrade test: `python3 scripts/test_upgrade_path.py` with a disposable PostgreSQL URL

## License

Tallystead is licensed under the [GNU Affero General Public License v3.0 only](LICENSE) (`AGPL-3.0-only`).

## Community

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change and follow the [Code of Conduct](CODE_OF_CONDUCT.md) in all project spaces. Report vulnerabilities privately according to [SECURITY.md](SECURITY.md).

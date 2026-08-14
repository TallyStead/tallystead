# Privacy and data flows

Tallystead is local-first. The browser connects to the household's selected server URL; the server, database, files, workers, scheduler, and optional AI runtime remain under household control. Core financial use does not require a vendor cloud account or application telemetry.

## Default flows

| Data | From | To | Trigger | Default and retained content |
| --- | --- | --- | --- | --- |
| API requests and session token | Household browser | Caddy and Tallystead API | User opens or uses the app | Local server only; responses are non-cacheable and the token is revocable. |
| Ledger, plans, rules, provenance, and audit events | API/worker | PostgreSQL | Normal use | Local relational volume; retained until household deletion or restore. |
| Receipts, statements, thumbnails, import evidence | API/worker | MinIO | Upload/import | Local object volume; original evidence is protected by household authorization. |
| Encrypted recovery archive | PostgreSQL/MinIO | Configured backup directory | Owner/operator runs backup | Age-encrypted, checksummed archive. Retained indefinitely by default or by explicit count. |
| Source and dependency metadata | GitHub Actions | GitHub security/artifact services | Repository CI | Source, findings, and container SBOMs only. No `.env`, runtime database, household file, backup, or log artifact is uploaded. |

## Opt-in connections

| Connection | Data that may leave the Tallystead service | Control |
| --- | --- | --- |
| SMTP | Notification recipient, subject, and message content | Disabled until an Owner configures and enables it. Credentials are encrypted and write-only. |
| IMAP | Mailbox requests and retrieved messages/attachments | Disabled until configured. Retrieved evidence enters the same review boundary as manual imports. |
| Ollama or LM Studio | Authorized prompt/context and document image for the selected local task | Disabled until configured; only private/loopback runtime addresses are accepted. Model output is advisory and reviewed. |
| Cloudflare/reverse proxy | Encrypted client traffic and normal connection metadata | Not required for local launch. Enabling it is an explicit deployment decision with trusted-proxy configuration. |

No hosted AI provider, advertising SDK, crash-reporting service, behavioral analytics, or Tallystead-operated telemetry endpoint is configured. Next.js telemetry is disabled in local containers and CI.

## Diagnostics and support

Health endpoints return service state, not financial records or secrets. Audit entries use identifiers and safe summaries; they must not include passwords, session tokens, secret values, raw mail bodies, full prompts, or document contents. Before sharing logs, screenshots, archives, or issue reports, replace household names, addresses, account identifiers, merchant details, and financial values with fictional data.

## Export, deletion, and retention

- The complete household archive is a portable, human-inspectable ZIP containing household records, a manifest, and checksummed document objects. Authentication and integration secrets are excluded.
- The infrastructure backup is a server recovery image and includes the database and object store. It is encrypted and is not a portable interchange format.
- Financial-data deletion preserves household authentication, members, server/integration settings, and external backup archives; the confirmation screen and API response disclose this.
- Deleted data can therefore remain in an older backup until that encrypted archive is removed under the household's retention policy.
- `TALLYSTEAD_BACKUP_RETENTION_COUNT=0` keeps every backup. A positive count is an explicit operator choice.

## Offline acceptance

With SMTP, IMAP, local AI, Cloudflare, and repository tooling disabled, the installed web UI, API, PostgreSQL, MinIO, imports, ledger, planner, reports, goals, exports, and manual document workflows continue to function on the local server. Browser-to-server LAN traffic is required; “offline” means no external internet or vendor service is required.

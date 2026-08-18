# Tallystead threat model

**Review date:** 2026-08-14  
**Scope:** The self-hosted web application, API, PostgreSQL, object storage, workers, Caddy, optional local integrations, encrypted backups, browser clients, and repository workflows. The separately planned public demo is excluded and requires its own review.

## Assets and security objectives

The primary assets are financial records, household documents, credentials and passkeys, session tokens, integration secrets, AI prompts/results, audit history, encryption identities, backups, and the integrity of deterministic calculations. Confidentiality matters, but integrity and recoverability are equally important: a quiet ledger mutation or unusable backup can be as harmful as disclosure.

## Trust boundaries

1. Browser to Caddy/API over HTTPS.
2. Authenticated household members to server-enforced role checks.
3. API/worker to PostgreSQL and MinIO on the private Compose network.
4. Server to explicitly enabled SMTP, IMAP, and local-AI endpoints.
5. Running services to mounted configuration, database, object, and backup volumes.
6. GitHub pull-request code to read-only CI runners; no household runtime data belongs in this boundary.
7. Backup recipient (public) to backup identity (offline secret). Possession of the server alone must not decrypt an off-server backup unless the identity was left with it.

## Threats and controls

| Threat | Impact | Implemented controls | Residual responsibility/status |
| --- | --- | --- | --- |
| Stolen password or browser session | Household disclosure or mutation | Argon2 password hashing, passkeys, bounded absolute and idle sessions, server-side revocation, current-session indicator, audited sign-in/sign-out/session administration | Owner must use strong credentials, remove stale sessions, and secure client devices. No high-severity open finding. |
| Member exceeds assigned role | Unauthorized export, configuration, or ledger change | Server-side household membership and role dependencies; Owner/Manager/Contributor/Viewer policy; Owner-only destructive data, membership, integration, and server operations; audited consequential actions | Every new route must receive an authorization test. No known cross-household bypass. |
| Cross-household object or record reference | Disclosure between households | Household-scoped queries and resource ownership checks, authenticated document delivery, non-public MinIO, API tests | Continue negative tests as resource types are added. |
| Malicious CSV, document, archive, or filename | Parser abuse, duplicate posting, path traversal, resource exhaustion | Preserved raw evidence, explicit review, checksums, duplicate detection, size/type limits, generated object keys, archive schema/member/checksum validation, safe-path checks | OCR/parser libraries require dependency updates; imports remain untrusted. |
| Local-AI or integration SSRF/exfiltration | Financial data leaves intended network | AI accepts private/loopback destinations only, integrations are disabled until configured, secrets encrypted at rest and write-only, manual non-AI path, AI has no ledger write authority | SMTP/IMAP necessarily sends/connects to the configured service. Owner must trust that endpoint. |
| Forwarded-header spoofing or incorrect public exposure | Identity-provider impersonation or incorrect client/source attribution | Host-neutral routing with normal Tallystead authentication for direct clients; explicit trusted-proxy CIDRs, Caddy-to-API shared secret, forwarded-header source validation, stripping of identity headers from untrusted clients, and Caddy security headers | A real reverse-proxy deployment validation remains a release gate before internet exposure. Hostnames are routing aliases, not authorization decisions. |
| Spoofed Pangolin forwarded identity | Account impersonation or role escalation | Feature disabled by default; Caddy transport secret and immediate proxy source validation; narrow trusted CIDRs; Caddy-owned internal identity headers; existing-active-member matching; stable external subject link; Pangolin role discarded; local roles remain authoritative; audited link and session creation | Compromise of the trusted Pangolin proxy remains equivalent to compromise of an identity provider and requires session revocation and identity review. |
| Database, object volume, or backup theft | Bulk disclosure | Private service network, authenticated API, encrypted age backup envelope, per-payload checksums, separate identity file, restore verification | Live PostgreSQL/MinIO volumes inherit host protection and are not application-level encrypted. Full-disk encryption and physical security are deployment responsibilities. |
| Backup corruption or malicious restore | Data loss or injected paths | Authenticated age decryption, exact archive member allowlist, SHA-256 verification, safe object paths, disposable database restore verification, explicit restore phrase | Keep multiple verified copies and protect the age identity separately. |
| Destructive operation or automation error | Irrecoverable loss or incorrect ledger | Typed confirmations, Owner-only controls, retained-backup disclosure, reversible review decisions, immutable source evidence, audit events, deterministic calculations | Backups are retained until an explicit household retention count is configured. |
| Sensitive diagnostics or telemetry | Unintended disclosure | No application analytics, Next.js telemetry disabled, no-store/private responses, audit details avoid bodies/prompts/secrets, repository artifact policy | Operators must sanitize container logs before sharing them. |
| Compromised dependency or CI action | Malicious build or source exfiltration | Read-only workflows, non-persisted checkout credentials, dependency audit, Trivy filesystem/IaC/secret/image scans, SBOMs, immutable action pins, Dependabot, forbidden-artifact policy, and an exact independently attested web-image selection | Repository branch protection must be enabled in GitHub for both server and web repositories. CodeQL remains unavailable unless GitHub Code Security is enabled for this private repository. Scanner advisories require ongoing review. |
| Physical host administrator | Total control of live installation | Not preventable by the application; encrypted off-server backups can remain protected when the identity is stored separately | Explicitly accepted deployment trust assumption. |

## Security decisions

- The default supported launch mode is a household local network. Internet exposure is not implied by configuring a reverse proxy.
- Tallystead does not claim protection from a malicious root/host administrator or an already-compromised browser.
- Financial calculations, posting, deletion, and matching remain deterministic and authorization checked. Local AI may suggest but cannot silently mutate the ledger.
- The live database and object volume rely on host controls. Application-managed field encryption protects integration secrets; encrypted backup protects exported recovery media.
- No high-severity application finding is silently deferred. The two reviewed deployment-image exceptions and their hard release boundaries are recorded in `SECURITY_EXCEPTIONS.md`. External deployment validation and GitHub branch-protection configuration are release operations, not hidden application exceptions.
- The development machine currently requires certificate-verification bypasses while building Python and backup-tool images because its package traffic is intercepted. This is an explicit release blocker: install the household CA in both build images and remove both bypasses before production deployment.

Re-review this document whenever public-demo mode, internet-facing support, a cloud service, mobile offline storage, a new parser, or a write-capable AI tool is introduced.

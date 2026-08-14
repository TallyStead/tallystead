# Server Foundation: First-Run Setup and Authentication

**Status:** Implemented foundation  
**Related:** [Self-Hosted Server Platform](SELF_HOSTED_SERVER_PLATFORM.md) · [RBAC and Access Control](RBAC_AND_ACCESS_CONTROL.md) · [Email System](../features/EMAIL_SYSTEM.md)

## Implemented behavior

On an unconfigured server, the first client uses the setup API to create:

- One household.
- One active User.
- One Owner membership for that user and household.
- One audit event recording setup completion.
- One opaque, server-stored session token.

The server allows setup only while no Owner membership exists. A second setup attempt returns a conflict response.

## Current API surface

| Endpoint | Purpose | Access |
| --- | --- | --- |
| `GET /health` | Server health/version metadata | Public/local network |
| `GET /v1/setup/status` | Whether first-run setup is required | Public/local network |
| `GET /v1/server/identity` | Canonical HTTPS server URL and API version | Public/local network |
| `POST /v1/setup` | Create first household Owner and session | Only while setup is required |
| `POST /v1/auth/login` | Password login and session creation | Public/local network |
| `POST /v1/auth/password-reset/request` | Send a non-enumerating reset request through configured SMTP | Public/local network |
| `POST /v1/auth/password-reset/finish` | Consume a 30-minute single-use reset token | Public/local network |
| `POST /v1/auth/passkeys/register/options` | Begin passkey enrollment | Authenticated user |
| `POST /v1/auth/passkeys/register/finish` | Verify and persist a passkey | Authenticated user |
| `POST /v1/auth/passkeys/login/options` | Begin passkey sign-in | Public/local network |
| `POST /v1/auth/passkeys/login/finish` | Verify a passkey and create a session | Public/local network |
| `GET /v1/auth/passkeys` | List current user's passkeys | Authenticated user |
| `DELETE /v1/auth/passkeys/{passkey_id}` | Remove current user's passkey | Authenticated user |
| `GET /v1/auth/me` | Current user, household, and role | Bearer session required |
| `GET /v1/household/members` | List household members | Owner only |
| `POST /v1/household/members` | Create a household member with a role | Owner only |
| `PATCH /v1/household/members/{membership_id}` | Change a member role | Owner only |
| `GET /v1/household/sessions` | List active household sessions | Owner only |
| `DELETE /v1/household/sessions/{session_id}` | Revoke a household member session | Owner only |
| `DELETE /v1/household/members/{membership_id}/passkeys` | Remove a member's passkeys and sessions during recovery | Owner only |
| `POST /v1/household/members/{membership_id}/reset-password` | Reset a member password and revoke all of their sessions | Owner only |
| `GET /v1/system/status` | Local operational readiness (without credentials) | Owner only |
| `GET /v1/system/integrations` | Read non-secret SMTP, IMAP, and local-AI readiness | Owner only |
| `PUT /v1/system/integrations` | Store write-only encrypted integration settings | Owner only |
| `POST /v1/system/integrations/test/{integration}` | Test a configured local integration | Owner only |

## Security properties in this slice

- Passwords use Argon2 through `pwdlib`; plaintext passwords are never persisted.
- Session tokens are opaque and only a SHA-256 hash is persisted server-side.
- Sessions expire after 30 days in this initial slice; device/session revocation is the next authentication increment.
- Setup, sessions, memberships, and audit events are relational database records created through Alembic migrations.
- Owners can create all accepted roles (Owner, Manager, Contributor, and Viewer), change a role while preserving at least one Owner, and revoke active sessions. These administrative actions write audit records.
- Until SMTP recovery exists, an Owner can reset a household member password. This invalidates every active session for that member and creates an audit event.
- The Owner screen can read database, document-store, SMTP, and passkey readiness without revealing credentials.
- WebAuthn passkeys use the configured canonical HTTPS origin. Challenges expire after five minutes, are single-use, and are cleaned by the worker.
- Password login is temporarily blocked after five failures in fifteen minutes. Owners can disable accounts, which revokes active sessions.
- SMTP, IMAP, and local-AI secrets are encrypted using a key derived from the server secret and are write-only through the API.
- The worker writes a durable heartbeat and cleans expired passkey challenges and old login-attempt records.
- Local database/object backups record health in PostgreSQL and support non-destructive restore verification; see [Backup and Restore](../operations/BACKUP_AND_RESTORE.md).
- All configuration uses local environment values; secrets are excluded from Git.

## Docker behavior

The API container runs `alembic upgrade head` before its application command. Workers wait for PostgreSQL health but do not run migrations, preventing concurrent first-start migration races. The database tracks the applied revision.

## Intentionally deferred

- General notifications beyond authentication recovery.
- Full request-level RBAC policy checks on financial resources (no financial resources exist yet).
- Production financial-resource authorization tests (no financial resources exist yet).

## Verification

The API test suite covers health metadata, setup/session behavior, role management, session revocation, Owner recovery, login throttling, account disablement, encrypted/write-only integration secrets, and passkey option/credential ownership. The Docker verification creates a real PostgreSQL/MinIO backup and restores it into a disposable database before deletion.

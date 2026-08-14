# Tallystead RBAC and Access Control

**Status:** Foundational access-control specification  
**Related:** [Self-Hosted Server Platform](SELF_HOSTED_SERVER_PLATFORM.md)

## Purpose

Tallystead contains highly sensitive household financial and document data. Access control must be server-enforced, scoped to a household, auditable, and understandable to non-technical household members.

RBAC determines what a signed-in person can do. It does not replace safe device access, encryption, backups, or explicit confirmation for destructive actions.

## Core rules

- Every protected record belongs to exactly one household.
- Every server request runs with an authenticated actor and household scope.
- Roles grant permissions; do not infer permissions from client UI visibility.
- Every active member may manage their own passwordless credentials and device sessions from Profile, regardless of household role.
- Only an Owner may view or revoke sessions belonging to other household members; those controls live with Household access.
- Server-side authorization is mandatory for reads, writes, imports, exports, configuration, and background jobs.
- Audit access-sensitive actions, permission changes, exports, integration credential changes, and destructive operations.
- Never use email address, device, or local-network location as a substitute for authorization.

## Initial roles

| Role | Intended use | Capabilities | Explicit limits |
| --- | --- | --- | --- |
| **Owner** | Household administrator and recovery authority | All household data and settings; manage members/roles; manage encryption, backups, integrations, exports, and server security; delete household | Avoid everyday use for shared or unattended sessions; owner actions should be strongly confirmed and audited. |
| **Manager** | Trusted adult who manages household finances | View and edit financial data, transactions, bills, plans, documents; run imports/reconciliation; create reports; configure non-security preferences | Cannot manage owners, household deletion, key/recovery settings, or server-level credential/security policy. |
| **Contributor** | Household member who participates in financial tracking | View shared financial views; add/edit their permitted transactions/documents; participate in review flows; view bills and plans | Cannot access backups, exports, integration credentials, household membership, or server/security configuration. |
| **Viewer** | Read-only household visibility | View approved/shared financial views, bills, plans, and selected documents | Cannot create, edit, import, export, change settings, or view hidden/private data. |

The first release supports Owner, Manager, Contributor, and Viewer. Do not expose any role until its permission boundaries are implemented and tested.

## Permission areas

| Area | Owner | Manager | Contributor | Viewer |
| --- | ---: | ---: | ---: | ---: |
| View shared financial data | ✓ | ✓ | ✓ | ✓ |
| Add/edit transactions and splits | ✓ | ✓ | Scoped | — |
| Add/edit bills, income, debt, plans, and goals | ✓ | ✓ | Scoped | — |
| Import files and reconcile | ✓ | ✓ | Scoped review | — |
| View/add documents | ✓ | ✓ | Scoped | Selected/shared only |
| Export household data | ✓ | Optional, owner-configured | — | — |
| Configure planner defaults | ✓ | ✓ | — | — |
| Configure email, AI, or integration credentials | ✓ | Optional, owner-configured | — | — |
| Manage members and roles | ✓ | — | — | — |
| Manage own passkeys and device sessions | ✓ | ✓ | ✓ | ✓ |
| View/revoke other members' sessions | ✓ | — | — | — |
| Manage backups, encryption, recovery, server security | ✓ | — | — | — |
| Delete household or irreversible data | ✓, confirmed | — | — | — |

“Scoped” is a future policy decision: it could mean only self-entered records, selected accounts, or approval-required changes. Until it is fully specified, default to no access rather than broad partial access.

## Resource-level rules

- A household member cannot read or modify a resource outside their household, even if they know its identifier.
- Raw import data, attachment contents, AI prompts/outputs, and audit history use the same household boundary as related financial records.
- Credentials are never readable after storage. Authorized users can replace, test, disable, or revoke them according to their role.
- Exports require explicit scope, actor confirmation, and audit logging.
- Background jobs run as a service actor with the least permissions required; the originating user and configuration are retained for auditability.

## Sensitive-action safeguards

Require explicit confirmation and audit events for:

- Household deletion, member removal, and role escalation.
- Exporting financial data or documents.
- Enabling remote access, cloud services, or data-sharing integrations.
- Changing encryption/recovery settings, backup destination, or integration credentials.
- Deleting documents, imported batches, or financial records beyond normal reversible correction flows.
- Confirming an action that resolves multiple reconciliation items at once.

Consider a recent-authentication check for owner-level security, export, recovery, and deletion actions after the authentication model is selected.

## Audit requirements

At a minimum, record actor, household, action, resource type/identifier, timestamp, result, and relevant before/after policy state for:

- Sign-in/session and failed authorization attempts (without logging secrets).
- Member, role, and access-policy changes.
- Imports, exports, credential/integration changes, and AI enable/disable changes.
- Reconciliation confirmations/overrides and planner-setting changes.
- Backup/restore, migration, deletion, and recovery actions.

Audit history should be viewable by the Owner and protected from ordinary editing.

## Build checklist

- [x] Choose the first-release role set and document any staged restrictions.
- [x] Define server-side role policies independent of UI routes or labels.
- [x] Add household scoping and authorization middleware/service checks.
- [x] Add role-management UI restricted to Owners.
- [x] Give every role Profile-based control of its own passkeys and active device sessions.
- [x] Keep household-wide session administration restricted to Owners.
- [x] Add audit events for all sensitive actions and administrative changes.
- [x] Test cross-household isolation, role change, session expiration/revocation, and direct API authorization failures.
- [x] Review export, integration credential, backup, and deletion paths for authorization and confirmation behavior.

## Open decisions

- Whether Contributors can see all household balances or only selected accounts/views.
- Whether Managers may export data or configure integrations, and whether Owner approval is required.
- Whether documents can have additional privacy labels within a household.
- Password, passkey, local identity-provider, and account-recovery choices for the self-hosted server.
- Whether high-risk actions require reauthentication, a second household approver, or both.

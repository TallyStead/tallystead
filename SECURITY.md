# Security policy

Tallystead stores financial records, credentials, receipts, and household documents. Treat suspected exposure as sensitive even when the affected installation is only reachable on a local network.

## Reporting a vulnerability

Do not open a public issue containing a real password, passkey response, session token, integration secret, household export, receipt, statement, database dump, backup, or log with private financial data. Contact the repository owner privately and include only:

- the affected version or commit;
- the smallest reproducible description;
- the expected and observed security boundary; and
- sanitized evidence created with fictional data.

Revoke exposed credentials and sessions immediately. Preserve the original evidence privately for follow-up.

## Supported code

Security fixes target the current `main` branch until versioned releases are introduced. A release candidate must pass the checks in `docs/operations/RELEASE_CHECKLIST.md`. Self-hosters remain responsible for host security, operating-system updates, trusted TLS, firewalling, backup-key custody, and restricting physical access.

## Security expectations for contributions

- Never commit runtime `.env` files, age identities, private keys, database/object-store contents, backups, or real household samples.
- Use fictional fixtures in tests and bug reports.
- Keep pull-request workflows read-only and do not make secrets available to untrusted code.
- Treat authentication, authorization, migrations, cryptography, import parsing, network boundaries, and workflow changes as security-sensitive.
- Do not weaken a security check merely to make a build pass. Document and review any time-bounded exception.

See `docs/security/THREAT_MODEL.md`, `docs/security/PRIVACY_AND_DATA_FLOWS.md`, and `docs/operations/BACKUP_AND_RESTORE.md` for the implemented boundaries.

# Release checklist

Run this checklist from a clean checkout for every release candidate. Do not use real household data in tests, CI, screenshots, or artifacts.

## Application and data integrity

- [ ] API lint and complete automated test suite pass.
- [ ] The selected published Tallystead Web image passed its repository checks and the server/Caddy integration smoke test.
- [ ] The selected web image manifest supports every target CPU architecture; emulation-only success is not accepted for a supported production platform.
- [ ] Deterministic realistic-demo totals and report drill-downs reconcile.
- [ ] Owner, Manager, Contributor, and Viewer authorization checks pass; cross-household references fail closed.
- [ ] Consequential imports, exports, deletion, matching overrides, plans, membership, security, networking, and integration changes produce safe audit events.

## Security and privacy

- [ ] Repository forbidden-artifact policy passes against every tracked file.
- [ ] Dependency audits and Trivy filesystem/IaC/secret scans pass, and **Container Security** passes for the exact release tag or commit with no unreviewed high/critical result; if GitHub Code Security has been enabled, CodeQL passes too.
- [ ] Trusted package-repository CA certificates are installed and all temporary package-manager TLS bypasses are removed from image builds.
- [ ] Release SBOM artifacts contain dependency metadata only.
- [ ] GitHub branch protection requires pull requests and passing Quality/Security checks, blocks force pushes and deletion, and limits reviewed exceptions.
- [ ] Core use succeeds without internet access; SMTP, IMAP, AI, and reverse-proxy connections remain visibly opt-in.
- [ ] Threat model and privacy-flow document still match the release.
- [ ] Every entry in `SECURITY_EXCEPTIONS.md` is still necessary, unexpired, and within its stated release boundary.

## Recovery and upgrades

- [ ] Generate an encrypted backup with the public age recipient.
- [ ] Verify authenticated decryption, payload checksums, object archive, and database restore using the separate identity.
- [ ] Confirm the recovery identity is stored separately and that its loss would make backups unrecoverable.
- [ ] Run the automated migration path from the maintained earlier revision to current head and verify seeded records survive.
- [ ] Review the household's backup retention choice and retained-backup deletion disclosure.

## User experience

- [ ] Keyboard-only review covers setup/sign-in, navigation, transaction creation, review, reports, goals, settings, profile/security, export, and confirmation dialogs.
- [ ] Core pages preserve visible focus, programmatic labels, useful headings, non-color-only status, text zoom/reflow, and mobile sidebar behavior.
- [ ] Screen-reader smoke review covers setup/sign-in, main navigation, transaction entry, review decision, and destructive confirmations.
- [ ] Realistic-volume performance gate passes and its measured times are recorded in `docs/security/TESTING.md`.

## Deployment gates

- [ ] HTTPS, security headers, canonical origin, session logout/revocation, idle expiration, and trusted-proxy enforcement pass through the deployed Caddy route.
- [ ] Migration, service health, browser refresh, and server reconnection pass after deployment.
- [ ] Before any internet exposure, validate the real Cloudflare zone/certificate and Proxmox backup/restore procedure.
- [ ] Before public release, complete the external Tallystead name/trademark/marketplace review.

## Release artifacts

- [ ] Dispatch the manual **Release** workflow from the intended `main` commit with a new semantic version and the recorded checklist confirmation.
- [ ] Confirm Quality, Security, scans of every included image, SBOM generation, bundle validation, server-image publication, and artifact attestations all pass.
- [ ] Download the draft's server archive and `SHA256SUMS`; verify the checksum and GitHub attestation from a clean machine.
- [ ] Install the archive on a disposable host, complete setup/health/backup/restore checks, and verify the three server images and separately versioned web image match the release manifest.
- [ ] Review generated notes, migrations, exceptions, package visibility, and rollback instructions before manually publishing the draft. Never reuse or overwrite a release version.

A checked release must record the commit, date, operator, results, and any consciously accepted exception. An exception needs an owner, rationale, compensating control, and expiry date.

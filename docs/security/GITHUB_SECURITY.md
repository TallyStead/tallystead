# GitHub repository security

The repository workflows are intentionally separate:

- **Quality** runs brand policy, API lint/tests, and an earlier-database migration test.
- **Security** runs the fast repository artifact policy, Python advisories, and Trivy source/IaC/secret scans on every pull request and `main` push.
- **Container Security** builds API, backup, and Caddy images in parallel, fails on unresolved high/critical findings, and retains a dependency-only CycloneDX SBOM for each image. It runs for relevant Docker/dependency/infrastructure changes, release tags, a weekly schedule, and manual dispatch—not for unrelated application or documentation changes.
- **Release** is manual-only and calls the complete reusable Quality and Security gates. After those pass, it builds the exact server-owned images, pulls and scans the selected immutable web image, publishes explicit immutable server-image tags, creates server-image and bundle attestations, and prepares an installable archive, checksums, SBOMs, and a draft GitHub Release. It never publishes the release or a floating `latest` tag automatically.

Every routine workflow has read-only repository permission, bounded runtime, cancellation of superseded runs, non-persisted checkout credentials, and immutable action commit SHAs with reviewed version comments. The manual Release workflow remains read-only until its final job, where only `contents`, `packages`, `attestations`, and OIDC receive the write permissions needed to prepare a draft. Pull-request workflows do not use `pull_request_target`, deployment credentials, Cloudflare credentials, signing secrets, or household data.

Dependabot proposes grouped weekly updates for GitHub Actions, Python, and server-owned container images, with tightly bounded open-PR counts. The web repository independently manages npm and web-container updates. Major, minor, and patch updates remain visible; major updates require migration and compatibility review instead of being ignored. Dependabot security updates and the dependency-audit workflow remain release gates. Action updates must keep the immutable SHA and the human-readable reviewed version comment together.

CodeQL upload was exercised on 2026-08-14 but is unavailable while this repository is private and GitHub Code Security is not enabled. The workflow must not contain a permanently failing CodeQL job. Re-enable Python CodeQL when this repository becomes public or GitHub Code Security is enabled; JavaScript/TypeScript CodeQL belongs in the web repository.

## Required `main` protection

Configure the following in GitHub before a release:

1. Require a pull request and at least one approval for workflow, authorization, networking, migration, cryptography, or dependency changes.
2. Require every **Quality** and **Security** job to pass and require the branch to be current before merging. Do not make the path-filtered **Container Security** workflow a universal required check because an unrelated pull request legitimately skips it.
3. Block force pushes and branch deletion.
4. Limit bypass permission to repository recovery administrators; review and record every bypass.
5. Dismiss stale approvals after a security-sensitive change and require conversation resolution.
6. Enable GitHub's secret scanning/push protection and dependency graph where the repository plan supports them.

Branch protection is external repository state and cannot be guaranteed by committed YAML. Confirm it directly in repository settings for each release using `docs/operations/RELEASE_CHECKLIST.md`.

## Findings and exceptions

High or critical findings fail the workflow. Do not globally lower the severity or add a blanket ignore. A temporary exception must identify the advisory/finding, affected component, exposure, compensating control, owner, approval, and expiration date. Remove it as soon as the fixed dependency or configuration is available.

Before a release, confirm a successful **Container Security** run for the exact release tag or commit. Run it manually if the tag or relevant path trigger did not produce the expected run.

Use [the release process](../operations/RELEASING.md) for the operator procedure. The release workflow repeats scans against the exact server images it publishes and the selected external web image; the earlier Container Security run remains an independent pre-release signal.

CI artifacts must never contain `.env` files, private keys, age identities, runtime databases, object-store contents, backups, imports, receipts, documents, or logs. SBOM uploads are assembled in a dedicated directory and include only generated dependency metadata.

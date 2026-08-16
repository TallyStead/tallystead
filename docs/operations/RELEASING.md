# Tallystead release process

Tallystead releases are intentionally manual. The **Release** GitHub Actions workflow is the only supported publisher for versioned application images and server bundles.

## What the workflow enforces

The workflow accepts a semantic version such as `v0.1.0` or `v0.1.0-rc.1`, must be dispatched from `main`, rejects an existing Git tag, and requires explicit confirmation that the manual release checklist has been recorded. It then:

1. Runs the complete reusable **Quality** and **Security** workflows.
2. Builds the API, backup, and Caddy images from the selected `main` commit and pulls the explicitly selected immutable Tallystead Web image.
3. Fails on an unresolved high or critical Trivy finding in any included image.
4. Generates dependency-only CycloneDX SBOMs and validates the resolved release Compose bundle.
5. Publishes immutable API, backup, and Caddy version tags to GitHub Container Registry; the web repository publishes and attests its own image.
6. Publishes the matching immutable `@tallystead/contracts` package to GitHub Packages.

Before opening the release change, coordinate the checked-in component versions from the repository root:

```sh
python3 ../tallystead-development/set_version.py 0.2.0 --web-version 0.2.0
python3 ../tallystead-development/set_version.py 0.2.0 --web-version 0.2.0 --check
```

The server version applies to the API, Caddy, backup, and contracts package. Web remains independently versioned, so pass its exact intended release version when it differs.

For a routine bump, use `python3 ../tallystead-development/set_version.py next patch`, `next minor`, or `next major`. The script calculates server/contracts and web from their respective checked-in versions.
7. Creates GitHub artifact attestations for the three server-owned images, installable archive, and contract package archive.
8. Creates a draft GitHub Release with generated notes, the server archive, OpenAPI contract, contract package archive, checksums, and SBOMs.

The draft is a review boundary. The workflow never publishes a GitHub Release automatically and never creates a `latest` image tag. Operators and clients must choose an explicit version.

## Before dispatch

1. Work from a clean, current `main` commit whose required checks passed.
2. Complete `docs/operations/RELEASE_CHECKLIST.md` and record the commit, date, operator, results, and exceptions.
3. Confirm every `docs/security/SECURITY_EXCEPTIONS.md` item is allowed at the intended release boundary. A temporary development-only TLS bypass blocks a production or public release even if CI scans pass.
4. Confirm the requested Git tag and corresponding GHCR version tags have never been used.
5. Choose the exact tested `ghcr.io/tallystead/tallystead-web` semantic tag or digest to include in the server bundle.
6. Choose **prerelease** for an alpha, beta, or release candidate.

In GitHub, open **Actions → Release → Run workflow**, select `main`, enter the server version and exact web image, choose prerelease status, confirm the checklist, and run it.

## Review the draft

Before publishing the draft:

- confirm the workflow used the intended commit and all gates passed;
- download the server archive and validate it on a clean disposable host;
- verify `SHA256SUMS` and the archive attestation;
- pull each explicit image and confirm the three server-image attestations plus the web image's attestation from `tallystead/tallystead-web`;
- verify the released OpenAPI document and `@tallystead/contracts` package match the selected server version;
- verify the contract package archive attestation;
- review generated release notes for migrations, known issues, security exceptions, and upgrade/rollback instructions;
- verify package visibility matches the release audience; and
- complete any remaining external brand, Cloudflare, Proxmox, accessibility, and branch-protection gates.

Only publish the draft after these checks pass. If a post-publish workflow step fails after an image upload, keep the GitHub Release in draft, do not reuse the version, record the failure, and issue a new patch or prerelease version after correcting it. Version tags are immutable by policy.

## Permissions and secrets

The default workflow permission is read-only. Only the final publish job receives `contents: write`, `packages: write`, `attestations: write`, and `id-token: write`. It uses the short-lived repository `GITHUB_TOKEN`; no personal registry, signing, Cloudflare, deployment, or household secret is required. Checkout credentials are not persisted, and every external action is pinned to an immutable commit SHA.

GitHub's artifact-attestation verification behavior depends on repository visibility and GitHub plan. The release archive checksum remains mandatory regardless of attestation availability.

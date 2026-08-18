# Install a Tallystead server release

Use the server bundle attached to a Tallystead GitHub Release. It contains the release Compose file, an environment template, and the operational guides needed by a self-hosted installation. The versioned Caddy image embeds its tested configuration, so production installations do not maintain or mount a separate host Caddyfile. The bundle does not contain household data, credentials, private keys, or container images.

## Verify and prepare

1. Download `tallystead-vX.Y.Z-server.tar.gz` and `SHA256SUMS` from the same release.
2. Verify the archive before extracting it:

   ```sh
   sha256sum --check --ignore-missing SHA256SUMS
   ```

3. Extract the archive and enter its directory.
4. Copy `.env.example` to `.env`. Replace every `replace-with-...` value with a different long random secret. Set the optional direct-local HTTPS hostname and choose where the private HTTP ingress binds. No public URL, internal URL, access mode, certificate mode, or browser-origin list is required. Keep this file outside source control.
5. If the GitHub Container Registry packages are private, authenticate Docker with a GitHub personal access token that has `read:packages`. Public packages do not require this step.

The server release version and tested `TALLYSTEAD_WEB_IMAGE` are already written into `.env.example`. Do not change either during initial installation. The web image is released independently and therefore may have a different semantic version from the server images.

## Start and verify

Validate the resolved configuration, pull the signed release images, and start the server:

```sh
docker compose --env-file .env config --quiet
docker compose --env-file .env pull
docker compose --env-file .env up -d
docker compose --env-file .env ps
```

Open `https://<TALLYSTEAD_SERVER_HOST>` and complete first-owner setup. For local Caddy certificates, install Caddy's local root certificate on each client before enrolling passkeys. See `docs/NETWORK_AND_CERTIFICATES.md` in the bundle.

## Back up before upgrading

Before changing versions, generate and verify an encrypted backup using the bundled `docs/BACKUP_AND_RESTORE.md` instructions. Keep the age recovery identity separately from the server and the backup archive.

To upgrade, download and verify the newer bundle, carry forward the existing `.env`, Caddy data, application data, and named volumes, then pull and start the new version. The API container runs database migrations before serving traffic. Do not skip versions whose release notes specify a required intermediate upgrade.

## Roll back safely

Application images are versioned immutably, but database migrations may not be backward compatible. Do not change only `TALLYSTEAD_VERSION` to roll back after a migration. Restore the pre-upgrade encrypted backup and its matching release bundle together, then verify health and household totals.

## Verify provenance

Each server-owned release image and the server archive receive GitHub artifact attestations from this repository. The web image receives its attestation from `tallystead/tallystead-web`. With the GitHub CLI installed, verify an archive against this repository:

```sh
gh attestation verify tallystead-vX.Y.Z-server.tar.gz --repo OWNER/REPOSITORY
```

Verify the selected web image against its repository:

```sh
gh attestation verify oci://ghcr.io/tallystead/tallystead-web:VERSION --repo tallystead/tallystead-web
```

The GitHub Release also includes one CycloneDX SBOM per custom image. SBOMs describe software dependencies only and contain no household data.

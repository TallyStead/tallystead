# Tallystead Proxmox Deployment

**Supported target:** A small Debian QEMU virtual machine running Docker Compose.  
**Reason:** A VM gives Tallystead a conventional kernel, storage, networking, and backup boundary without requiring privileged or nested-container LXC settings.

## Recommended VM baseline

- 2–4 vCPUs and 4–8 GB RAM for the application, PostgreSQL, MinIO, OCR, and routine background work.
- More CPU/RAM when local AI runs inside the same VM; a separately hosted Ollama or LM Studio runtime is preferred when it needs a GPU.
- 40 GB or more of expandable storage, sized for documents, database history, Caddy state, and multiple verified backups.
- VirtIO network and storage devices.
- QEMU guest agent enabled.
- Static DHCP reservation or static address on the trusted server VLAN.

Resource sizing is a starting point, not a data limit. Monitor document volume, database growth, backup duration, and OCR queue latency.

## Persistent data

The Compose deployment keeps state outside disposable container layers:

| Data | Persistence | Recovery role |
| --- | --- | --- |
| PostgreSQL ledger and configuration | `postgres-data` volume | Authoritative structured records and encrypted settings |
| MinIO documents | `minio-data` volume | Receipt, statement, and attachment objects |
| Caddy certificates and local CA | `caddy-data` volume | Stable HTTPS identity and local trust root |
| Caddy runtime support | `caddy-config` and `caddy-admin` volumes | Runtime state and private administration socket |
| Application backups | `data/backups` host directory | Portable backup/restore workflow |

Do not treat a container image or writable container layer as a backup.

## Installation

1. Create and fully update the Debian VM.
2. Install Docker Engine and the Compose plugin from a trusted package source.
3. Create a dedicated non-root operator account with narrowly controlled Docker access.
4. Clone the Tallystead repository into an application directory owned by that operator.
5. Copy `infrastructure/compose/.env.example` to the repository-root `.env` and replace every placeholder secret.
6. Set the bootstrap server host and HTTPS port. Keep the initial deployment private.
7. Start the Compose stack and wait for PostgreSQL and the API to report healthy.
8. Complete first-run Owner setup through the canonical local HTTPS address.
9. Export and trust the Caddy root on household devices when using local-CA mode.
10. Configure the final canonical/internal URLs in **Settings → Server**, stage, test, and activate them.

Keep `.env`, API keys, exported backups, and private certificates out of Git.

## DNS and load balancer

For the reference reverse-proxy topology:

- Canonical DNS points clients to the load balancer or approved private entry point.
- Internal upstream DNS points the load balancer to the Tallystead VM/Caddy address.
- The load balancer preserves the canonical host and scheme.
- Only the load balancer's exact addresses/CIDRs are configured as trusted proxies.
- The load balancer verifies the Caddy certificate using either the exported persistent local CA or a Cloudflare DNS-01 certificate.
- PostgreSQL, MinIO API/console, web, API, and the Caddy administration socket remain private.

Use split DNS so the canonical URL remains unchanged inside and outside the household network.

## Firewall baseline

- Allow SSH only from the administration network.
- Allow the chosen HTTPS port only from household/VPN clients or the load balancer.
- Allow port 80 only when intentionally using public HTTP-01.
- Do not expose PostgreSQL 5432, MinIO 9000/9001, web 3000, API 8000, or any Docker/internal administration endpoint.
- Restrict outbound traffic according to enabled features. Cloudflare DNS-01, software updates, SMTP/IMAP, and explicitly configured integrations require their corresponding destinations.

## Backups

Use both layers:

1. **Tallystead application backup:** consistent PostgreSQL export, document objects, configuration metadata, checksums, and restore verification.
2. **Proxmox guest backup:** VM-level disaster recovery stored outside the VM and preferably outside the physical host.

Schedule application backups before VM snapshots/backups where practical. A crash-consistent VM backup is not a substitute for application-level verification.

Quarterly restore drill:

1. Restore into an isolated VM/network.
2. Prevent email, imports, notifications, and public ingress.
3. Verify PostgreSQL migration state and record counts.
4. Verify MinIO object checksums and document retrieval.
5. Verify Caddy state or deliberately establish a new test hostname.
6. Run Tallystead's non-destructive restore verification.
7. Record duration, warnings, missing prerequisites, and operator actions.

## Upgrade and rollback

1. Read release notes and migration requirements.
2. Run and verify an application backup.
3. Create a Proxmox snapshot only as a short-lived rollback aid; do not retain database snapshots indefinitely.
4. Pull/build the intended version and start the stack.
5. Confirm migrations, API health, worker heartbeat, document access, network identity, and certificate status.
6. If verification fails, restore the prior application version and follow its documented database compatibility path. Use the VM snapshot only when the application rollback cannot safely restore service.
7. Remove the temporary snapshot after the upgrade is accepted.

Never downgrade across a destructive database migration without the matching verified backup and release instructions.


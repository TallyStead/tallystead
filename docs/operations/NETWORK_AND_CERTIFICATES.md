# Tallystead Network and Certificate Operations

**Status:** Phase 6A implemented operational specification  
**Related:** [Self-hosted server platform](../architecture/SELF_HOSTED_SERVER_PLATFORM.md) · [RBAC](../architecture/RBAC_AND_ACCESS_CONTROL.md) · [Proxmox deployment](PROXMOX_DEPLOYMENT.md)

## Identity model

Tallystead has one **canonical client URL**. Browsers, password-reset links, passkeys, and future iOS and Android clients use this exact HTTPS origin. Changing it can require saved clients to reconnect and users to enroll replacement passkeys.

An optional **internal upstream URL** identifies the HTTPS endpoint used by a load balancer, reverse proxy, health monitor, or administrator. It does not replace the canonical client identity.

Recommended split-DNS topology:

```text
Client → https://tallystead.example.com
       → internal load balancer
       → https://tallystead-service.internal.example.com
       → Caddy → web/API
```

Internal DNS should resolve the canonical name to the private load balancer. External DNS may resolve it to an explicitly approved public entry point, VPN, or tunnel. Clients must not switch origins merely because they move between networks.

## Access modes

- **Local network only:** Caddy local CA; no supported public ingress.
- **Internal reverse proxy:** only configured proxy CIDRs may supply forwarded client headers; the proxy verifies Caddy's upstream certificate.
- **VPN/private tunnel:** the canonical origin remains stable while routing stays private.
- **Direct internet:** explicit Owner opt-in only. Public certificate, firewall, update, authentication, backup, and Phase 7 readiness are mandatory.

Tallystead never changes router, firewall, Proxmox, tunnel, or Cloudflare proxy rules. The Server page tests application-side prerequisites and reports the remaining infrastructure work.

## Trusted proxies

Tallystead uses two boundaries:

1. Caddy accepts client-forwarding information only from the Owner-configured IP addresses or CIDRs.
2. The API accepts Caddy's normalized forwarded headers only when the immediate connection comes from the private container network and carries the dedicated `PROXY_SHARED_SECRET`. Existing installations fall back to `API_SECRET_KEY` until that separate value is added to `.env`.

Direct clients cannot make `X-Forwarded-Host`, `X-Forwarded-Proto`, or `X-Forwarded-For` authoritative. The Server page's request-identity test displays the effective URL, source address, and whether forwarded headers were trusted.

When a load balancer has multiple addresses, enter the smallest stable CIDRs that contain those addresses. Do not enter an entire household LAN merely for convenience.

## Certificate modes

### Caddy local CA

Use for development and LAN-only installations. Caddy's `/data` volume preserves its CA. Each client must trust the exported root certificate before passkeys are used. The authenticated Server page can export the public root chain; it never exports a private key.

### Public ACME

Use HTTP-01 or TLS-ALPN-01 only when public DNS and inbound ports are intentionally configured. Port 443 must reach Caddy for TLS-ALPN-01. To expose port 80 for HTTP-01, add the public override:

```bash
docker compose --env-file .env \
  -f infrastructure/compose/compose.yaml \
  -f infrastructure/compose/compose.public.yaml up -d
```

### Cloudflare DNS-01

The bundled Caddy image includes the Cloudflare DNS module. Create a token restricted to the selected zone with only:

- `Zone:Read`
- `DNS:Edit`

DNS-01 does not require public inbound ports. Tallystead encrypts the token in PostgreSQL, never returns it to the browser, and does not record it in audit details. Caddy's dynamic configuration persistence is disabled so the token is not written into Caddy's JSON state. After a restart, the API reloads the encrypted active configuration through the private administration socket.

### Externally terminated TLS

The load balancer may terminate the canonical public certificate, but the load-balancer-to-Caddy hop remains HTTPS. Either:

- import and trust the persistent Tallystead Caddy root; or
- use Cloudflare DNS-01 to give the internal upstream hostname a publicly verifiable certificate.

Never disable certificate verification on the load balancer as a permanent configuration.

## Safe activation and rollback

Network changes follow a state machine:

1. **Stage:** validate URLs, modes, CIDRs, certificate requirements, and write-only credentials without changing live routing.
2. **Test:** verify Caddy syntax, DNS resolution, Cloudflare zone access when selected, application upstream health, and security prerequisites.
3. **Activate:** atomically load the complete configuration through Caddy's permissioned Unix socket.
4. **Verify:** complete an HTTPS handshake using the configured hostname and appropriate trust chain.
5. **Commit or restore:** save the new revision only after verification; otherwise reload the last known-good configuration.

Caddy automatically retains its active configuration when a reload cannot be provisioned. Tallystead adds post-load certificate/health verification and an audited application-level rollback. A manual **Restore previous configuration** action is also available.

The administration socket is a named volume mounted only into Caddy and the API. It is not published as a TCP port, and Tallystead does not mount the Docker socket.

## Recovery when the configured hostname is unavailable

1. Restore the previous DNS/load-balancer route if possible.
2. Use the VM console and confirm the core containers are healthy.
3. Keep the bootstrap `.env` values pointing at a known local hostname.
4. Restart Caddy with the checked-in bootstrap Caddyfile if the dynamic configuration cannot be reached.
5. Reconnect through the bootstrap address and use **Restore previous configuration**.
6. Do not delete PostgreSQL, Caddy, or application backup volumes during network recovery.

Every stage, test, activation, automatic rollback, manual rollback, and canonical-origin change is audited without credentials.

## Internet-facing readiness

Direct internet mode is never inferred. Before enabling it, complete at least:

- Phase 7 role-aware UI and authorization verification.
- Supported security updates and a documented patch cadence.
- Rate limiting and account-recovery review.
- Validated off-host encrypted backups and a restore drill.
- Firewall rules exposing only intended entry points.
- No public MinIO console, PostgreSQL, Caddy admin socket, or internal service ports.
- Threat-model and logging review with no secrets or financial contents in diagnostics.

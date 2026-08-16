# Network Configuration Reference

This reference explains every option on **Settings → Server**, how the options work together, and how to recover when a change makes Tallystead unreachable. For deployment commands and certificate operations, also see [Network and certificate operations](NETWORK_AND_CERTIFICATES.md).

## The two decisions

Tallystead asks two related but separate questions:

1. **Access mode:** How does a browser or mobile device reach the server?
2. **Certificate mode:** Which component establishes and maintains the HTTPS identity?

A Pangolin deployment, for example, uses **Internal load balancer / reverse proxy** as its access mode and **TLS terminated by an upstream load balancer** as its certificate mode. Pangolin owns the public certificate, while Tallystead still enforces that public hostname as its canonical identity.

## Recommended combinations

| Deployment | Access mode | Certificate mode | Canonical URL | Internal URL | Trusted proxy CIDR |
| --- | --- | --- | --- | --- | --- |
| Home LAN, direct to Caddy | Local network only | Caddy local CA | LAN HTTPS hostname | Empty | Empty |
| Private VPN or overlay | VPN or private tunnel | Caddy local CA | Stable VPN HTTPS hostname | Empty | Empty unless a proxy is present |
| Pangolin on the same Docker host | Internal load balancer / reverse proxy | TLS terminated upstream | Public Pangolin HTTPS URL | Usually empty | Pangolin Docker subnet |
| Internal HTTPS reverse proxy | Internal load balancer / reverse proxy | Local CA, DNS-01, or upstream TLS | Client-facing HTTPS URL | Optional Caddy HTTPS hostname | Proxy address or narrow subnet |
| Caddy directly on the internet | Direct internet access | Public ACME or Cloudflare DNS-01 | Public HTTPS URL | Usually empty | Empty unless a proxy is present |

Do not use an IP address as the long-term identity. Stable hostnames are required for certificates, passkeys, saved clients, and generated links.

## Server addresses

### Canonical client URL

This is Tallystead's authoritative browser and mobile identity, such as `https://tallystead.example.com`. It controls accepted request hosts and origins, API base URLs, passkey origin binding, generated links, and Caddy certificate hostnames.

It must be a complete `https://` URL with a hostname and no application path. Changing it moves the application to another browser origin. Existing passkeys and browser storage remain associated with the old origin, so users may need to reconnect or enroll replacement passkeys.

Prepare DNS or proxy routing first, keep terminal access open, and keep the working browser tab open until the new address is verified.

### Internal HTTPS upstream URL

This optional address gives Caddy another HTTPS hostname for a load balancer, split-DNS route, health monitor, or administrator. It does not replace the canonical URL.

```text
Client → https://tallystead.example.com
       → internal load balancer
       → https://tallystead-service.internal.example.com
       → Caddy → web/API
```

Leave it empty for simple LAN deployments and the Pangolin shared-network profile. When used, it must be a complete HTTPS URL whose DNS resolves from the connecting component.

## Access modes

### Local network only

Household devices connect directly to Caddy over the LAN. The normal certificate choice is **Caddy local CA**, and each device must trust the exported root certificate.

This option does not change LAN DNS, router rules, or the firewall. The hostname must already resolve to the Tallystead host.

### Internal load balancer / reverse proxy

Another trusted component receives requests first, such as Pangolin, Traefik, HAProxy, Nginx, or a hardware load balancer. The proxy must preserve the public host and send standard forwarding headers.

Selecting this mode does not expose ports, attach Docker networks, or configure the proxy. Those remain deployment responsibilities.

### VPN or private tunnel

Routing is provided by WireGuard, Tailscale, or another private overlay while clients normally connect directly to Caddy. Keep one stable HTTPS hostname across networks. This option does not install or configure the VPN.

### Direct internet access

Public traffic reaches Caddy without an upstream TLS proxy. This requires explicit owner confirmation and a public certificate mode. Confirm firewall scope, security updates, rate limiting, recovery, monitoring, and tested off-host backups first.

PostgreSQL, MinIO, the API, the web container, and the Caddy administration socket must never be exposed directly.

## Certificate modes

### Caddy local CA

Caddy maintains a private certificate authority in its persistent `/data` volume. Use it for development, LAN, VPN, or a verified internal HTTPS hop.

Clients must trust the exported public root. Never delete the Caddy data volume during routine troubleshooting; that creates a new CA and invalidates existing device trust.

### Public ACME

Caddy obtains a publicly trusted certificate using HTTP-01 or TLS-ALPN-01. Public DNS must point to the server. TLS-ALPN-01 requires inbound TCP 443; HTTP-01 requires inbound TCP 80 and the public Compose override.

This is unsuitable for private-only hostnames or proxy-only containers that cannot receive the challenge.

### Cloudflare DNS-01

Caddy proves ownership through Cloudflare without requiring inbound port 80. Provide a token restricted to the selected zone with only `Zone:Read` and `DNS:Edit`.

The zone must contain every configured hostname. Tallystead encrypts the token in PostgreSQL, never returns it to the browser, omits it from audit details, and reloads it into Caddy after restart.

### TLS terminated by an upstream load balancer

Pangolin or another upstream owns the browser-facing certificate. For conventional load balancers, keep the upstream-to-Caddy hop on verified HTTPS.

The Pangolin proxy-only profile is a deliberate exception: it uses HTTP only across a private shared Docker network that is not published on the host.

## Advanced proxy fields

### Trusted proxy IP addresses or CIDRs

Only these sources may supply authoritative `X-Forwarded-For`, `X-Forwarded-Host`, and `X-Forwarded-Proto` values.

```text
192.168.10.20/32
172.24.0.0/24
```

Use `/32` for one IPv4 address and the narrowest practical subnet for containers with changing addresses. Never use `0.0.0.0/0`, an entire household LAN, or all private address space just to pass a check.

Tallystead has two trust boundaries: Caddy validates the upstream address, and the API accepts Caddy's normalized values only from the private application network with `PROXY_SHARED_SECRET`.

### Accept Pangolin forwarded identity

This optional setting allows Pangolin SSO to authenticate an already-created, active Tallystead household member. It is available only in reverse-proxy mode with at least one trusted proxy CIDR.

Pangolin supplies `Remote-User`, `Remote-Email`, and `Remote-Name`. Caddy overwrites private Tallystead identity headers with those values, records the actual immediate proxy source, and removes the original `Remote-*` headers before the API request. The API then requires both the private Caddy transport secret and a source address inside the configured trusted proxy CIDRs.

On first sign-in, `Remote-Email` must exactly match an existing active Tallystead member. Tallystead links Pangolin's stable `Remote-User` subject to that member. Later sign-ins require the same subject and email; an email change never silently relinks the account.

Pangolin's `Remote-Role` is discarded. Household membership and roles remain authoritative in Tallystead. Forwarded identity never creates a user, promotes a member, re-enables a disabled member, or replaces local Owner recovery.

### Cloudflare DNS zone

Enter the zone, not the complete host. For `tallystead.example.com`, this is normally `example.com`.

### Cloudflare API token

Enter the restricted DNS token. After configuration, leaving this field blank preserves the saved token. The existing value is never displayed.

### ACME account email

An optional address for ACME account and expiration communication. It applies to Public ACME and Cloudflare DNS-01, not local CA or Pangolin-managed TLS.

### Internet exposure confirmation

Records that the owner deliberately selected direct internet access. It does not configure a firewall or secure the deployment by itself.

## Pangolin recipe

The supported proxy-only flow is:

```text
Client HTTPS → Pangolin → shared Docker network → HTTP caddy:8080 → web/API
```

1. List existing networks with `docker network ls`. Create `pangolin` only if needed.
2. Attach the Pangolin connector and Tallystead Caddy to that external network.
3. Start Tallystead with both Compose files:

   ```sh
   docker compose --env-file .env \
     -f compose.yaml \
     -f compose.pangolin.yaml up -d
   ```

4. Set the Pangolin resource target to:

   ```text
   Protocol: HTTP
   Hostname: caddy
   Port: 8080
   ```

5. On the Server page, select **Use Pangolin proxy settings**, enter the public Pangolin HTTPS URL as the canonical URL, leave the internal URL empty unless separately required, and add the Pangolin network subnet as a trusted proxy CIDR.
6. Enable **Accept Pangolin forwarded identity** when the Pangolin resource uses SSO and each intended person already has a matching active Tallystead member.
7. Stage, test, and activate.

Forwarded identity is available only for Pangolin authentication methods that provide user identity. Pangolin PINs, resource passwords, and shareable links do not provide these headers. See [Pangolin's forwarded-header documentation](https://docs.pangolin.net/manage/access-control/forwarded-headers).

Set `PANGOLIN_NETWORK_NAME` when the external network is not named `pangolin`. Find its subnet with `docker network inspect <network-name>`.

Do not point Pangolin at `web:3000`; that bypasses Caddy's API routing and security headers. Do not point it at `https://<server-ip>:443`; that causes certificate-name or SNI failures.

## What each action does

### Stage configuration

Stores a draft after validating URL formats, modes, CIDRs, certificate requirements, and relevant credentials. Live routing does not change.

### Run readiness checks

Checks staged Caddy syntax, applicable DNS resolution, Cloudflare access, application upstream health, and security prerequisites. It cannot prove that an external router, firewall, VPN, or Pangolin resource is correct.

### Activate tested configuration

Loads the complete configuration through Caddy's private Unix socket and verifies the HTTPS identity. When activation fails, Tallystead attempts to restore the prior active configuration.

### Restore previous configuration

Restores the last known-good revision, clears staged test state, reloads Caddy, and records an audit event. It is available only when a prior revision exists.

### Inspect current connection

The owner-only diagnostic sends a fresh request through the URL currently open in the browser. It shows the API transport address, effective client address, effective scheme and host, whether the route is direct or proxied, and whether forwarded headers passed the Caddy trust boundary. Through Pangolin, the effective URL should be the canonical public HTTPS URL and the route should be `trusted proxy`.

It also displays received header names and safe diagnostic values. Authorization credentials, cookies, proxy authorization, and Tallystead's Caddy shared secret are always redacted. Values for headers outside the diagnostic allowlist are omitted. The result is not stored or audited, but screenshots can still contain hostnames, internal addresses, forwarded identity, and email addresses; sanitize them before sharing publicly.

### Export local CA certificate

Downloads Caddy's public root chain for trusted devices or a verifying upstream proxy. It never exports a private key.

## Safe change sequence

1. Keep a terminal or VM-console session open.
2. Record the current canonical URL and Compose command.
3. Prepare DNS, proxy routing, and Docker networks first.
4. Keep the old route working when possible.
5. Stage the configuration.
6. Run all readiness checks.
7. Activate only after they pass.
8. Open the new URL in a separate browser tab.
9. Test sign-in, `/health`, and forwarded request identity.
10. Remove the old route only after verification.

## Troubleshooting and recovery

### “Request does not match the configured HTTPS server identity”

The host reaching the API differs from the active canonical URL. Use the canonical hostname, correct the proxy's public host forwarding, or restore the prior revision. A raw IP request is not equivalent to the configured hostname.

### Pangolin authenticates, but Tallystead denies sign-in

Confirm that forwarded identity is enabled, the Pangolin source belongs to the configured trusted CIDR, and `Remote-Email` exactly matches an active Tallystead member. If that member was previously linked, the stable `Remote-User` subject and email must still match the saved link. Owners can continue using local password or passkey authentication for recovery.

### Pangolin reports a gateway or upstream error

Confirm that both containers share a network and that the target is `http://caddy:8080`:

```sh
docker inspect caddy --format '{{json .NetworkSettings.Networks}}'
docker inspect <pangolin-connector> --format '{{json .NetworkSettings.Networks}}'
docker exec <pangolin-connector> wget -S -O- http://caddy:8080/health
```

If `caddy` does not resolve, the containers do not share the same Docker network or the service uses another network alias.

### The web page is unreachable

Use the host console. For proxy-only deployment, temporarily start the normal deployment without `compose.pangolin.yaml` so Caddy publishes HTTPS again:

```sh
docker compose --env-file .env -f compose.yaml up -d --force-recreate caddy api
```

Confirm that `.env` has the last working `TALLYSTEAD_SERVER_HOST` and `TALLYSTEAD_HTTPS_PORT`. Do not delete volumes. After reconnecting, use **Restore previous configuration**.

### Last-resort reset to `.env`

When no usable previous revision exists, first verify that `.env` contains the recovery hostname and HTTPS port you want. Then run the recovery script from the Compose directory:

```sh
cd infrastructure/compose
sh ./reset-network-configuration.sh
```

For a non-interactive host-console run, use `--yes`. A different environment file can be selected with `--env-file PATH`.

The script deletes only the saved network-setting record and recreates API and Caddy using the base `compose.yaml`:

```sh
docker compose --env-file .env -f compose.yaml exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DELETE FROM system_settings WHERE key = '\''network_configuration'\'';"'
docker compose --env-file .env -f compose.yaml up -d --force-recreate api caddy
```

Using the base Compose file deliberately restores Caddy's host HTTPS port even if the installation previously used the proxy-only override. This removes staged, active, and last-known-good network revisions, including the Pangolin forwarded-auth switch and trusted-proxy list. It does not remove users, external-identity links, household data, unrelated secrets, or Caddy certificates.

After API starts, its network controller renders the default configuration from `TALLYSTEAD_PUBLIC_URL`; Caddy initially boots from the checked-in Caddyfile using `TALLYSTEAD_SERVER_HOST`. Keep those `.env` values aligned. Once direct access works, the Server page can safely stage and test Pangolin again.

## Common mistakes

- Opening the server IP instead of the canonical hostname.
- Changing the canonical URL before DNS or Pangolin routing works.
- Confusing allowed browser origins with firewall or Docker-network rules.
- Trusting overly broad proxy address ranges.
- Sending Pangolin directly to `web:3000`.
- Sending Pangolin to Caddy HTTPS by IP.
- Publishing private port 8080 on the host.
- Deleting Caddy's `/data` volume and replacing the local CA.
- Assuming Tallystead automatically changes DNS, router, firewall, VPN, or Pangolin settings.

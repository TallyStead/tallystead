# Network configuration reference

Tallystead 0.2.2 is host-neutral. An IP address or hostname that routes through the deployment reaches the same web client and API; the application does not require a canonical public URL, internal URL, access mode, certificate mode, or browser-origin allowlist.

## Variables

| Variable | Purpose |
| --- | --- |
| `TALLYSTEAD_SERVER_HOST` | Optional direct-local Caddy HTTPS hostname. It controls only the local-CA certificate site, not API authorization or browser routing. |
| `TALLYSTEAD_HTTPS_PORT` | Optional host port publishing direct-local Caddy HTTPS. |
| `TALLYSTEAD_PROXY_BIND_IP` | Address that publishes Caddy's host-neutral private HTTP ingress. Defaults to loopback; use the server's LAN address for a proxy on another host. |
| `TALLYSTEAD_PROXY_HTTP_PORT` | Private HTTP ingress port; defaults to `8080`. |
| `TALLYSTEAD_TRUSTED_PROXY_CIDRS` | Space-separated immediate Pangolin/Newt addresses allowed to supply forwarded identity. Use the narrowest entries possible. It does not decide which clients may reach Tallystead. |
| `TALLYSTEAD_FORWARD_AUTH_ENABLED` | Allows trusted Pangolin identity for an existing active member. Normal Tallystead login remains available from every routed hostname. |
| `PROXY_SHARED_SECRET` | Private Caddy-to-API transport value. Generate a unique long secret and never send it from browsers or Pangolin. |

## Direct and proxied addresses

The bundled browser client calls relative `/v1` and `/health` paths. These may all route to one deployment:

```text
http://10.10.200.204:8080
https://tallystead.example.com
https://tallystead.local.example.us
```

TLS for a public hostname is owned by Pangolin or another external proxy. Direct-local Caddy HTTPS uses `TALLYSTEAD_SERVER_HOST` and Caddy's local CA. HTTP should remain on a trusted LAN; do not publish port 8080 to the internet.

## Pangolin on another host

```dotenv
TALLYSTEAD_SERVER_HOST=tallystead.local.example.us
TALLYSTEAD_HTTPS_PORT=443
TALLYSTEAD_PROXY_BIND_IP=10.10.200.204
TALLYSTEAD_PROXY_HTTP_PORT=8080
TALLYSTEAD_TRUSTED_PROXY_CIDRS=10.10.200.43/32
TALLYSTEAD_FORWARD_AUTH_ENABLED=true
```

Start the normal stack:

```sh
docker compose --env-file .env up -d
```

Configure Pangolin's target as `http://10.10.200.204:8080`. Restrict that address and port in the host firewall to the proxy when practical.

## Standalone browser client

The bundled client automatically uses its own origin. If a separately hosted client cannot find an API on that origin, it presents a fallback server URL field and stores the selected API address in that browser origin's local storage. Cross-origin API access uses explicit bearer tokens; Tallystead does not enable credentialed cross-origin cookies.

## Forwarded identity

Trusted Pangolin requests may supply `Remote-User`, `Remote-Email`, and `Remote-Name`. Tallystead requires an existing active member and ignores proxy roles. For any source outside `TALLYSTEAD_TRUSTED_PROXY_CIDRS`, Caddy strips identity headers and the application uses normal Tallystead authentication.

## Diagnostics

Settings → Server reports the effective hostname, scheme, transport address, client address, and whether forwarded headers were trusted. Credentials, cookies, and the proxy token are redacted.

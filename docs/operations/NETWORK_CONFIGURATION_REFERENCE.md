# Network configuration reference

The `.env` file is the only authoritative network configuration. Environment changes take effect when API and Caddy are recreated; they never require SQL or access to the web interface.

## Variables

| Variable | Purpose |
| --- | --- |
| `TALLYSTEAD_SERVER_HOST` | Hostname on Caddy's direct HTTPS certificate. Use a resolvable hostname, not an IP, for durable passkey use. |
| `TALLYSTEAD_HTTPS_PORT` | Host port publishing Caddy HTTPS. |
| `TALLYSTEAD_PUBLIC_URL` | Complete public HTTPS identity used by browsers, passkeys, generated links, and mobile clients. |
| `TALLYSTEAD_INTERNAL_URL` | Optional second complete HTTPS URL accepted for direct recovery access. |
| `TALLYSTEAD_ALLOWED_ORIGINS` | Comma-separated browser origins. Include the public URL and the internal URL when both are used. |
| `TALLYSTEAD_ACCESS_MODE` | `lan`, `vpn`, `reverse_proxy`, or `internet`. Pangolin uses `reverse_proxy`. |
| `TALLYSTEAD_TRUSTED_PROXY_CIDRS` | Space-separated immediate Pangolin/Newt IP addresses or Docker subnets allowed to reach port 8080 and supply forwarded headers. Use the narrowest entries possible. |
| `TALLYSTEAD_FORWARD_AUTH_ENABLED` | Allows Pangolin forwarded identity when `true`; it never creates users or imports Pangolin roles. |
| `TALLYSTEAD_CERTIFICATE_MODE` | Descriptive direct-certificate mode reported by the API. The standard Caddyfile uses `local_ca`; Pangolin terminates the public certificate externally. |
| `TALLYSTEAD_PROXY_BIND_IP` | Host address on which the remote-host override publishes port 8080. Use the Tallystead server's private/LAN address. |
| `TALLYSTEAD_PROXY_HTTP_PORT` | Host port used by a remote Pangolin/Newt connector; defaults to `8080`. |
| `PROXY_SHARED_SECRET` | Private Caddy-to-API authentication value. Use a long random secret and never send it from Pangolin. |

`TALLYSTEAD_TRUSTED_PROXY_CIDRS` serves two related boundaries: Caddy rejects unlisted clients on its HTTP ingress, and forwarded Pangolin identity must report an immediate source within the same list. The API separately trusts only Caddy containers in `TALLYSTEAD_CADDY_PROXY_CIDRS` that present `PROXY_SHARED_SECRET`; the release defaults already cover the private Compose network.

## Direct LAN example

```dotenv
TALLYSTEAD_SERVER_HOST=tallystead.local.example
TALLYSTEAD_HTTPS_PORT=443
TALLYSTEAD_PUBLIC_URL=https://tallystead.local.example
TALLYSTEAD_INTERNAL_URL=
TALLYSTEAD_ALLOWED_ORIGINS=https://tallystead.local.example
TALLYSTEAD_ACCESS_MODE=lan
TALLYSTEAD_TRUSTED_PROXY_CIDRS=127.0.0.1/32
TALLYSTEAD_FORWARD_AUTH_ENABLED=false
TALLYSTEAD_CERTIFICATE_MODE=local_ca
```

Start the base deployment and trust the Caddy local root certificate on each client.

## Pangolin on the same Docker host

Set the public URL and the narrow subnet of Pangolin's existing external Docker network:

```dotenv
TALLYSTEAD_PUBLIC_URL=https://tallystead.example.com
TALLYSTEAD_INTERNAL_URL=
TALLYSTEAD_ALLOWED_ORIGINS=https://tallystead.example.com
TALLYSTEAD_ACCESS_MODE=reverse_proxy
TALLYSTEAD_TRUSTED_PROXY_CIDRS=172.30.0.0/24
TALLYSTEAD_FORWARD_AUTH_ENABLED=true
TALLYSTEAD_CERTIFICATE_MODE=external_tls
PANGOLIN_NETWORK_NAME=pangolin
```

```sh
docker compose --env-file .env -f compose.yaml -f compose.pangolin.yaml up -d
```

Configure the Pangolin resource target as HTTP host `caddy`, port `8080`. This proxy-only profile removes host-published ports, so host-console access is required for recovery.

## Pangolin or Newt on another host

Use the connector's actual source address as a `/32` where possible and retain an internal HTTPS recovery URL:

```dotenv
TALLYSTEAD_SERVER_HOST=tallystead.local.example
TALLYSTEAD_HTTPS_PORT=443
TALLYSTEAD_PUBLIC_URL=https://tallystead.example.com
TALLYSTEAD_INTERNAL_URL=https://tallystead.local.example
TALLYSTEAD_ALLOWED_ORIGINS=https://tallystead.example.com,https://tallystead.local.example
TALLYSTEAD_ACCESS_MODE=reverse_proxy
TALLYSTEAD_TRUSTED_PROXY_CIDRS=10.10.200.43/32
TALLYSTEAD_FORWARD_AUTH_ENABLED=true
TALLYSTEAD_CERTIFICATE_MODE=external_tls
TALLYSTEAD_PROXY_BIND_IP=10.10.200.204
TALLYSTEAD_PROXY_HTTP_PORT=8080
```

```sh
docker compose --env-file .env -f compose.yaml -f compose.pangolin-host.yaml up -d
```

Configure Pangolin's target as `http://10.10.200.204:8080`. Restrict that address and port in the host firewall to `10.10.200.43`. Direct recovery remains available at `https://tallystead.local.example`.

## Forwarded identity

Pangolin may supply `Remote-User`, `Remote-Email`, and `Remote-Name`. When enabled, the first sign-in requires `Remote-Email` to exactly match an existing active Tallystead member. Tallystead links the stable Pangolin subject to that member. It discards `Remote-Role`; household roles remain authoritative. PINs, resource passwords, and share links that do not supply user headers cannot use forwarded sign-in.

## Diagnostics and troubleshooting

Run `./validate-network-config.sh .env` before recreating services. From Settings -> Server, inspect the current request through both the public and internal URLs. The Pangolin request should report the public HTTPS URL, `trusted proxy`, and trusted forwarded headers.

If the API returns `Request does not match the configured HTTPS server identity`, verify that Pangolin preserves the public Host and sends `X-Forwarded-Proto: https`, its immediate source matches `TALLYSTEAD_TRUSTED_PROXY_CIDRS`, and the public URL exactly matches `TALLYSTEAD_PUBLIC_URL`. Default HTTPS ports are normalized, so `example.com` and `example.com:443` are equivalent.

If the page becomes unreachable, correct `.env` on the host and run:

```sh
docker compose --env-file .env -f compose.yaml up -d --force-recreate api caddy
```

No PostgreSQL command is needed.

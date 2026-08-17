# Network and certificate operations

Tallystead routes by connection rather than by a configured application identity. The same server may be reached through an IP address, a public proxy hostname, and an optional local hostname. Hostnames affect DNS, TLS, cookies, and passkey scope; they do not select a different household or authorize a request.

See the [network configuration reference](NETWORK_CONFIGURATION_REFERENCE.md) for the small set of optional deployment variables.

## Connection paths

```text
Direct client -> Caddy HTTP or optional local HTTPS -> web/API
Browser -> HTTPS Pangolin -> private Caddy HTTP :8080 -> web/API
```

The bundled web client uses the origin from which it was loaded. A standalone client may select another API URL. CORS does not use a deployment allowlist; standalone requests use explicit bearer tokens and never credentialed cross-origin cookies.

Port 8080 accepts ordinary clients with normal Tallystead authentication. Only a source in `TALLYSTEAD_TRUSTED_PROXY_CIDRS` can supply Pangolin identity, and Caddy strips forwarded identity headers from every other source before proxying to the API.

## Certificates and passkeys

Pangolin owns TLS for public hostnames. Caddy can provide optional direct-local HTTPS for `TALLYSTEAD_SERVER_HOST` using its local CA. Trust that root certificate on local devices before using the local HTTPS address.

Passkeys are created and verified against the hostname used for that ceremony. Because browsers scope passkeys and local storage by origin, a household may need separate passkey enrollment and sign-in sessions for different aliases. Password authentication remains available.

## Applying and recovering changes

The Caddyfile is embedded in the versioned Caddy image; production does not mount a host copy. Edit `.env` only for bind addresses, trusted proxy sources, optional forwarded authentication, direct-local HTTPS, and secrets, then recreate the relevant services:

```sh
docker compose --env-file .env up -d --force-recreate api caddy
```

No database or browser setting can make an otherwise routed hostname fail API identity matching. Use Settings → Server to inspect the effective current connection.

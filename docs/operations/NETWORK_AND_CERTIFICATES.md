# Network and certificate operations

Tallystead network configuration is owned by the installation's `.env` file and Docker Compose. The application does not store or rewrite routing, certificate, origin, or trusted-proxy settings in PostgreSQL. The authenticated Server page is read-only and reports the loaded environment, certificate, service health, and sanitized connection details.

See the [network configuration reference](NETWORK_CONFIGURATION_REFERENCE.md) for every variable and complete Pangolin recipes.

## Connection paths

### Direct local HTTPS

```text
Browser -> HTTPS hostname -> Caddy -> web/API
```

Caddy issues a certificate from its local CA for `TALLYSTEAD_SERVER_HOST`. Set `TALLYSTEAD_PUBLIC_URL` to the complete URL household clients use and trust Caddy's exported root certificate on those devices.

### Pangolin with external HTTPS

```text
Browser -> HTTPS Pangolin -> restricted HTTP Caddy :8080 -> web/API
```

The HTTP hop is acceptable only on a private Docker network or a trusted LAN path restricted to the Pangolin/Newt source. Caddy rejects port-8080 connections outside `TALLYSTEAD_TRUSTED_PROXY_CIDRS`. For accepted connections, Caddy preserves Pangolin's forwarded HTTPS scheme and public host. The API independently requires Caddy's private transport secret before those values become authoritative.

Do not expose port 8080 to the internet. Do not target `web:3000`, because doing so bypasses API routing and security headers.

## Applying and recovering changes

Edit `.env`, validate it, and recreate API and Caddy:

```sh
./validate-network-config.sh .env
docker compose --env-file .env -f compose.yaml up -d --force-recreate api caddy
```

There is no database network setting to reset. If a value is wrong, correct `.env` from the host console and recreate the two services. The base Compose deployment retains direct HTTPS even when `compose.pangolin-host.yaml` publishes the restricted proxy ingress.

Changing `TALLYSTEAD_PUBLIC_URL` changes the passkey relying-party origin and generated-link identity. Existing passkeys may need to be enrolled again after a hostname change.

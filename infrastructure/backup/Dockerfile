# This operator-invoked tool must read/replace PostgreSQL and MinIO recovery
# volumes whose ownership is managed by their official service images.
FROM postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193

# Temporary development-machine workaround for intercepted Alpine repository TLS.
# Install the household CA and remove --no-check-certificate before deployment.
RUN apk --no-cache --no-check-certificate add age && rm -f /usr/local/bin/gosu

COPY infrastructure/backup/*.sh /opt/tallystead/
RUN chmod 0555 /opt/tallystead/*.sh

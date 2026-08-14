# The final Caddy process binds 443, owns its certificate/config volumes, and
# creates the shared root-only administration socket used for staged rollback.
FROM caddy:2-builder-alpine@sha256:2b9994510fadb5dfa5257a5357cbe26a2c4a3298f8cc675796a6570218280ce7 AS builder

# Temporary development build workaround for this environment's intercepted TLS.
# Replace with the trusted local CA before a production/home-server deployment.
RUN GIT_SSL_NO_VERIFY=true GOINSECURE='*' GOPROXY=direct GOSUMDB=off \
    xcaddy build \
      --with github.com/caddy-dns/cloudflare@v0.2.4 \
      --replace golang.org/x/net=golang.org/x/net@v0.56.0 \
      --replace golang.org/x/text=golang.org/x/text@v0.39.0 \
      --replace google.golang.org/grpc=google.golang.org/grpc@v1.82.1

FROM caddy:2-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648

RUN apk --no-cache --no-check-certificate upgrade c-ares curl libcurl
COPY --from=builder /usr/bin/caddy /usr/bin/caddy

#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file=${1:-$script_dir/.env}
[ -f "$env_file" ] || { echo "Environment file not found: $env_file" >&2; exit 1; }
compose_file="$script_dir/compose.yaml"
[ -f "$compose_file" ] || compose_file="$script_dir/compose.release.yaml"
[ -f "$compose_file" ] || { echo "Compose file not found beside this script." >&2; exit 1; }

value() {
  sed -n "s/^$1=//p" "$env_file" | tail -n 1
}

public_url=$(value TALLYSTEAD_PUBLIC_URL)
internal_url=$(value TALLYSTEAD_INTERNAL_URL)
allowed_origins=$(value TALLYSTEAD_ALLOWED_ORIGINS)
access_mode=$(value TALLYSTEAD_ACCESS_MODE)
trusted_proxies=$(value TALLYSTEAD_TRUSTED_PROXY_CIDRS)
forward_auth=$(value TALLYSTEAD_FORWARD_AUTH_ENABLED)

case "$public_url" in https://*) ;; *) echo "TALLYSTEAD_PUBLIC_URL must be a complete HTTPS URL." >&2; exit 1;; esac
case ",$allowed_origins," in *",$public_url,"*) ;; *) echo "TALLYSTEAD_ALLOWED_ORIGINS must include TALLYSTEAD_PUBLIC_URL." >&2; exit 1;; esac
if [ -n "$internal_url" ]; then
  case "$internal_url" in https://*) ;; *) echo "TALLYSTEAD_INTERNAL_URL must be empty or a complete HTTPS URL." >&2; exit 1;; esac
fi
if [ "$access_mode" = reverse_proxy ] && { [ -z "$trusted_proxies" ] || [ "$trusted_proxies" = 127.0.0.1/32 ]; }; then
  echo "Reverse-proxy mode requires the narrow Pangolin/Newt source address or Docker subnet." >&2
  exit 1
fi
if [ "$forward_auth" = true ] && [ "$access_mode" != reverse_proxy ]; then
  echo "Forwarded authentication requires TALLYSTEAD_ACCESS_MODE=reverse_proxy." >&2
  exit 1
fi

docker compose --env-file "$env_file" --file "$compose_file" config --quiet
echo "Tallystead network environment is valid."

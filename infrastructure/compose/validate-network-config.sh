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

trusted_proxies=$(value TALLYSTEAD_TRUSTED_PROXY_CIDRS)
forward_auth=$(value TALLYSTEAD_FORWARD_AUTH_ENABLED)

if [ "$forward_auth" = true ] && { [ -z "$trusted_proxies" ] || [ "$trusted_proxies" = 127.0.0.1/32 ]; }; then
  echo "Forwarded authentication requires the narrow Pangolin/Newt source address or Docker subnet." >&2
  exit 1
fi

docker compose --env-file "$env_file" --file "$compose_file" config --quiet
echo "Tallystead network environment is valid."

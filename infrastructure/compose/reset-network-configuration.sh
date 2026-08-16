#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Reset Tallystead's saved network configuration to the values in .env.

Usage:
  ./reset-network-configuration.sh [--env-file PATH] [--yes]

The script deletes only the network_configuration setting, then recreates API
and Caddy from compose.yaml. The base Compose file also restores the host HTTPS
port when proxy-only mode was active.
EOF
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file="$script_dir/.env"
confirmed=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      [ "$#" -ge 2 ] || { echo "--env-file requires a path" >&2; exit 2; }
      env_file=$2
      shift 2
      ;;
    --yes)
      confirmed=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[ -f "$env_file" ] || {
  echo "Environment file not found: $env_file" >&2
  exit 1
}

if [ "$confirmed" != true ]; then
  echo "This will remove the saved active, staged, and rollback network revisions."
  echo "The replacement hostname and HTTPS port will come from: $env_file"
  printf "Type RESET to continue: "
  read -r answer
  [ "$answer" = RESET ] || { echo "Reset cancelled."; exit 1; }
fi

compose() {
  docker compose --env-file "$env_file" --file "$script_dir/compose.yaml" "$@"
}

echo "Removing only the saved Tallystead network configuration..."
compose exec -T postgres sh -c \
  'psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command "DELETE FROM system_settings WHERE key = '\''network_configuration'\'';"'

echo "Recreating API and Caddy with direct HTTPS publishing from compose.yaml..."
compose up -d --force-recreate api caddy

echo "Network configuration reset complete."
echo "Open the hostname and HTTPS port configured in $env_file."
echo "After recovery, reconfigure and test proxy-only mode from Server settings."

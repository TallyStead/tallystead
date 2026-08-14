#!/bin/sh
set -eu

key_path="${TALLYSTEAD_BACKUP_AGE_IDENTITY_FILE:-/keys/identity.txt}"
if [ -e "${key_path}" ]; then
  printf '%s\n' "Refusing to overwrite existing backup identity: ${key_path}" >&2
  exit 2
fi
mkdir -p "$(dirname "${key_path}")"
umask 077
age-keygen --output "${key_path}"
printf '%s\n' "Backup recipient (safe to place in .env):"
age-keygen -y "${key_path}"
printf '%s\n' "Keep ${key_path} offline and copy it to a second encrypted location. Losing it makes backups unrecoverable."

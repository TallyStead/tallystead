#!/bin/sh
set -eu

if [ -z "${BACKUP_ARCHIVE:-}" ]; then
  printf '%s\n' "Set BACKUP_ARCHIVE to an encrypted backup filename in /backups." >&2
  exit 2
fi
case "${BACKUP_ARCHIVE}" in */*|..*) printf '%s\n' "BACKUP_ARCHIVE must be a filename, not a path." >&2; exit 2 ;; esac

identity_file="${TALLYSTEAD_BACKUP_AGE_IDENTITY_FILE:-/keys/identity.txt}"
test -f "${identity_file}"
archive_path="/backups/${BACKUP_ARCHIVE}"
test -f "${archive_path}"
verify_dir="$(mktemp -d /tmp/tallystead-verify.XXXXXX)"
plain_archive="${verify_dir}/backup.tar.gz"
verify_db="tallystead_verify_$(tr -d '-' < /proc/sys/kernel/random/uuid | cut -c1-12)"

cleanup() {
  dropdb --if-exists "${verify_db}" >/dev/null 2>&1 || true
  rm -rf "${verify_dir}"
}
trap cleanup EXIT INT TERM

age --decrypt --identity "${identity_file}" --output "${plain_archive}" "${archive_path}"
members="$(tar -tzf "${plain_archive}")"
for required in MANIFEST.txt CHECKSUMS.sha256 database.dump objects.tar; do
  printf '%s\n' "${members}" | grep -Fx "${required}" >/dev/null
done
test "$(printf '%s\n' "${members}" | wc -l | tr -d ' ')" -eq 4
tar -C "${verify_dir}" -xzf "${plain_archive}"
(cd "${verify_dir}" && sha256sum -c CHECKSUMS.sha256)
tar -tf "${verify_dir}/objects.tar" >/dev/null
createdb "${verify_db}"
pg_restore --no-owner --dbname="${verify_db}" "${verify_dir}/database.dump"
table_count="$(psql --dbname="${verify_db}" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
test "${table_count}" -gt 0
printf '%s\n' "Encrypted backup verified: ${archive_path} (${table_count} database tables)"

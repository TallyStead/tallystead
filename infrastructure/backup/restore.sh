#!/bin/sh
set -eu

if [ "${RESTORE_CONFIRMATION:-}" != "RESTORE TALLYSTEAD" ]; then
  printf '%s\n' "Set RESTORE_CONFIRMATION='RESTORE TALLYSTEAD' to replace the active database and object store." >&2
  exit 2
fi
if [ -z "${BACKUP_ARCHIVE:-}" ]; then
  printf '%s\n' "Set BACKUP_ARCHIVE to an encrypted backup filename in /backups." >&2
  exit 2
fi
case "${BACKUP_ARCHIVE}" in */*|..*) printf '%s\n' "BACKUP_ARCHIVE must be a filename, not a path." >&2; exit 2 ;; esac

identity_file="${TALLYSTEAD_BACKUP_AGE_IDENTITY_FILE:-/keys/identity.txt}"
test -f "${identity_file}"
archive_path="/backups/${BACKUP_ARCHIVE}"
test -f "${archive_path}"
restore_dir="$(mktemp -d /tmp/tallystead-restore.XXXXXX)"
plain_archive="${restore_dir}/backup.tar.gz"
cleanup() { rm -rf "${restore_dir}"; }
trap cleanup EXIT INT TERM

age --decrypt --identity "${identity_file}" --output "${plain_archive}" "${archive_path}"
members="$(tar -tzf "${plain_archive}")"
for required in MANIFEST.txt CHECKSUMS.sha256 database.dump objects.tar; do
  printf '%s\n' "${members}" | grep -Fx "${required}" >/dev/null
done
test "$(printf '%s\n' "${members}" | wc -l | tr -d ' ')" -eq 4
tar -C "${restore_dir}" -xzf "${plain_archive}"
(cd "${restore_dir}" && sha256sum -c CHECKSUMS.sha256)

object_members="$(tar -tf "${restore_dir}/objects.tar")"
if printf '%s\n' "${object_members}" | grep -E '(^/|(^|/)\.\.(/|$))' >/dev/null; then
  printf '%s\n' "Object archive contains an unsafe path." >&2
  exit 2
fi
pg_restore --clean --if-exists --no-owner --dbname="${POSTGRES_DB}" "${restore_dir}/database.dump"
find /minio-data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
tar -C /minio-data -xf "${restore_dir}/objects.tar"
printf '%s\n' "Restore completed from ${archive_path}. Restart Tallystead services before use."

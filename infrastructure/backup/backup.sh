#!/bin/sh
set -eu

if [ -z "${TALLYSTEAD_BACKUP_AGE_RECIPIENT:-}" ]; then
  printf '%s\n' "Set TALLYSTEAD_BACKUP_AGE_RECIPIENT to the public age recipient from the offline backup identity." >&2
  exit 2
fi

backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_id="$(cat /proc/sys/kernel/random/uuid)"
work_dir="$(mktemp -d "/backups/.work-${backup_stamp}.XXXXXX")"
archive_name="tallystead-${backup_stamp}.tar.gz.age"
archive_path="/backups/${archive_name}"
plain_archive="${work_dir}/backup.tar.gz"

mkdir -p "${work_dir}/payload"
psql -v ON_ERROR_STOP=1 -c "INSERT INTO backup_runs (id,status,started_at) VALUES ('${backup_id}','running',CURRENT_TIMESTAMP)" >/dev/null

on_exit() {
  status="$?"
  rm -rf "${work_dir}"
  if [ "${status}" -ne 0 ]; then
    rm -f "${archive_path}"
    psql -c "UPDATE backup_runs SET status='failed',detail='Encrypted backup command failed',completed_at=CURRENT_TIMESTAMP WHERE id='${backup_id}'" >/dev/null 2>&1 || true
  fi
  exit "${status}"
}
trap on_exit EXIT INT TERM

pg_dump --format=custom --file="${work_dir}/payload/database.dump"
tar -C /minio-data -cf "${work_dir}/payload/objects.tar" .
printf '%s\n' \
  "Tallystead encrypted backup ${backup_stamp}" \
  "Envelope: age X25519 authenticated encryption" \
  "Database format: PostgreSQL custom" \
  "Objects format: tar" > "${work_dir}/payload/MANIFEST.txt"
(cd "${work_dir}/payload" && sha256sum MANIFEST.txt database.dump objects.tar > CHECKSUMS.sha256)
tar -C "${work_dir}/payload" -czf "${plain_archive}" MANIFEST.txt CHECKSUMS.sha256 database.dump objects.tar
age --recipient "${TALLYSTEAD_BACKUP_AGE_RECIPIENT}" --output "${archive_path}" "${plain_archive}"
archive_size="$(wc -c < "${archive_path}" | tr -d ' ')"
psql -v ON_ERROR_STOP=1 -c "UPDATE backup_runs SET status='succeeded',archive_name='${archive_name}',size_bytes=${archive_size},detail='age-encrypted;checksummed',completed_at=CURRENT_TIMESTAMP WHERE id='${backup_id}'" >/dev/null

retention_count="${TALLYSTEAD_BACKUP_RETENTION_COUNT:-0}"
case "${retention_count}" in *[!0-9]*|'') printf '%s\n' "TALLYSTEAD_BACKUP_RETENTION_COUNT must be zero or a positive integer." >&2; exit 2 ;; esac
if [ "${retention_count}" -gt 0 ]; then
  find /backups -maxdepth 1 -type f -name 'tallystead-*.tar.gz.age' -print | sort -r | awk -v keep="${retention_count}" 'NR > keep' | while IFS= read -r expired; do
    rm -f "${expired}"
  done
fi

trap - EXIT INT TERM
rm -rf "${work_dir}"
printf '%s\n' "Encrypted backup created: ${archive_path}"

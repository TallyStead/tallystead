# Tallystead Backup and Restore

Operator backups are local, age-encrypted archives containing a PostgreSQL custom-format dump, MinIO object data, a manifest, and SHA-256 checksums. The encrypted files are written under `data/backups/`, which is excluded from Git. The private recovery identity is stored under `data/backup-keys/`, is never added to an archive, and is also excluded from Git.

## Create the offline recovery identity

Create the identity once:

```sh
docker compose --env-file .env -f infrastructure/compose/compose.yaml --profile tools run --rm backup-keygen
```

The command prints a public `age1...` recipient. Put only that public recipient in `.env` as `TALLYSTEAD_BACKUP_AGE_RECIPIENT`. Keep `data/backup-keys/identity.txt` offline and copy it to a second encrypted device or password-manager attachment. The public recipient can create backups but cannot decrypt them. Losing the private identity makes every encrypted backup unrecoverable.

Never commit the identity, `.env`, a backup, a database dump, or object data to GitHub.

## Create and verify a backup

For a consistency-safe maintenance backup, stop write-producing services, create the archive while PostgreSQL remains available, then restart the stack:

```sh
docker compose --env-file .env -f infrastructure/compose/compose.yaml stop api worker minio
docker compose --env-file .env -f infrastructure/compose/compose.yaml --profile tools run --rm backup
docker compose --env-file .env -f infrastructure/compose/compose.yaml up -d
```

The backup is encrypted with authenticated age X25519 encryption before its final filename appears. An interrupted or failed operation removes its partial output. The command records running/success/failure state in PostgreSQL without storing the recovery key.

Verify a completed archive by authenticating and decrypting it, validating every checksum and expected member, validating the object archive, and restoring the database into a disposable verification database:

```sh
docker compose --env-file .env -f infrastructure/compose/compose.yaml --profile tools run --rm --entrypoint /opt/tallystead/verify.sh -e BACKUP_ARCHIVE=tallystead-YYYYMMDDTHHMMSSZ.tar.gz.age backup
```

A backup is not considered usable until this verification succeeds. Copy verified archives to a second local device; encryption does not protect against disk loss.

`TALLYSTEAD_BACKUP_RETENTION_COUNT=0` retains every archive. A positive value keeps only the newest configured number after a successful backup. Leave it at zero until the household explicitly chooses and documents a retention policy.

## Restore

Restore is intentionally explicit because it replaces current database records and object files. Verify the archive first, stop API/web/worker access, and require the exact destructive confirmation:

```sh
docker compose --env-file .env -f infrastructure/compose/compose.yaml stop api web worker
docker compose --env-file .env -f infrastructure/compose/compose.yaml --profile tools run --rm --entrypoint /opt/tallystead/restore.sh -e BACKUP_ARCHIVE=tallystead-YYYYMMDDTHHMMSSZ.tar.gz.age -e 'RESTORE_CONFIRMATION=RESTORE TALLYSTEAD' backup
docker compose --env-file .env -f infrastructure/compose/compose.yaml up -d
```

Restore authenticates the encrypted envelope, accepts only the four expected top-level files, verifies checksums, rejects unsafe object paths, and only then replaces PostgreSQL and MinIO data. Always verify household identity, record counts, documents, and sign-in afterward.

Back up `.env` separately in an encrypted password manager or offline store. It contains deployment secrets and storage names and is deliberately excluded from application backups.

## Household archive versus operator backup

The Owner-facing `.tallystead.zip` household export is portable and validated but intentionally excludes authentication and server secrets. It is not encrypted by the application. The operator `.tar.gz.age` backup is encrypted and is the supported whole-server recovery artifact. Both flows disclose their inclusion scope; neither uploads data anywhere.

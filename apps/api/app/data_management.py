from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Uuid, delete, insert, or_, select, update
from sqlalchemy.orm import Session

from app.database import Base
from app.models import HouseholdDataState, utc_now
from app.object_store import get_object, put_object, remove_object

ARCHIVE_FORMAT = "tallystead-household-archive-v1"
LEGACY_ARCHIVE_FORMATS = {"nestledger-household-archive-v1"}
MAX_ARCHIVE_BYTES = 100_000_000
MAX_EXPANDED_BYTES = 500_000_000
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ROWS = 500_000

PROTECTED_TABLES = {
    "households",
    "users",
    "memberships",
    "session_tokens",
    "passkey_credentials",
    "passkey_challenges",
    "system_settings",
    "service_heartbeats",
    "login_attempts",
    "password_reset_tokens",
    "backup_runs",
}

DEFERRED_COLUMNS = {
    "ledger_transactions": {"reversal_of_transaction_id", "corrected_from_transaction_id"},
    "import_batches": {"mapping_version_id"},
}


def _domain_tables() -> list:
    return [table for table in Base.metadata.sorted_tables if table.name not in PROTECTED_TABLES]


def _json_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _db_value(column, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, Uuid):
        return UUID(value)
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    return value


def collect_household_rows(db: Session, household_id: UUID) -> dict[str, list[dict[str, Any]]]:
    tables = _domain_tables()
    rows: dict[str, list[dict[str, Any]]] = {}
    for table in tables:
        if "household_id" in table.c:
            found = db.execute(select(table).where(table.c.household_id == household_id)).mappings().all()
            if found:
                rows[table.name] = [dict(item) for item in found]

    changed = True
    while changed:
        changed = False
        for table in tables:
            if table.name in rows or "household_id" in table.c:
                continue
            conditions = []
            for foreign_key in table.foreign_keys:
                parent_rows = rows.get(foreign_key.column.table.name)
                if not parent_rows:
                    continue
                parent_values = [item[foreign_key.column.name] for item in parent_rows]
                conditions.append(foreign_key.parent.in_(parent_values))
            if conditions:
                found = db.execute(select(table).where(or_(*conditions))).mappings().all()
                if found:
                    rows[table.name] = [dict(item) for item in found]
                    changed = True
    return rows


def household_data_summary(db: Session, household_id: UUID) -> dict[str, Any]:
    rows = collect_household_rows(db, household_id)
    state = db.get(HouseholdDataState, household_id)
    return {
        "mode": state.mode if state else "standard",
        "demo_seed": state.demo_seed if state else None,
        "demo_volume": state.demo_volume if state else None,
        "demo_reference_date": state.demo_reference_date.isoformat() if state and state.demo_reference_date else None,
        "record_count": sum(len(items) for name, items in rows.items() if name != "audit_events"),
        "document_count": len(rows.get("documents", [])),
        "transaction_count": len(rows.get("ledger_transactions", [])),
        "table_counts": {name: len(items) for name, items in sorted(rows.items())},
    }


def _object_keys(rows: dict[str, list[dict[str, Any]]]) -> list[str]:
    keys: list[str] = []
    for item in rows.get("documents", []):
        keys.append(item["object_key"])
        if item.get("thumbnail_object_key"):
            keys.append(item["thumbnail_object_key"])
    return keys


def household_object_keys(db: Session, household_id: UUID) -> list[str]:
    return _object_keys(collect_household_rows(db, household_id))


def build_household_archive(db: Session, household_id: UUID, household_name: str) -> bytes:
    rows = collect_household_rows(db, household_id)
    serializable = {
        name: [{key: _json_value(value) for key, value in item.items()} for item in items]
        for name, items in rows.items()
    }
    object_entries = []
    content_by_path: dict[str, bytes] = {}
    for key in _object_keys(rows):
        content, content_type = get_object(key)
        path = f"objects/{hashlib.sha256(key.encode()).hexdigest()}"
        object_entries.append(
            {
                "object_key": key,
                "archive_path": path,
                "content_type": content_type,
                "size_bytes": len(content),
                "checksum_sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        content_by_path[path] = content
    manifest = {
        "format": ARCHIVE_FORMAT,
        "exported_at": utc_now().isoformat(),
        "household_id": str(household_id),
        "household_name": household_name,
        "record_count": sum(len(items) for items in rows.values()),
        "table_counts": {name: len(items) for name, items in sorted(rows.items())},
        "objects": object_entries,
        "excludes": ["passwords", "passkeys", "sessions", "integration secrets", "server configuration"],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
        archive.writestr("data.json", json.dumps({"tables": serializable}, sort_keys=True))
        for path, content in content_by_path.items():
            archive.writestr(path, content)
    return output.getvalue()


def inspect_archive(content: bytes, household_id: UUID) -> tuple[dict[str, Any], dict[str, list[dict]], zipfile.ZipFile]:
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds the 100 MB import limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content), "r")
    except zipfile.BadZipFile as error:
        raise ValueError("The selected file is not a valid Tallystead archive") from error
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES or sum(item.file_size for item in infos) > MAX_EXPANDED_BYTES:
        archive.close()
        raise ValueError("Archive expands beyond the supported safety limits")
    if {"manifest.json", "data.json"} - set(archive.namelist()):
        archive.close()
        raise ValueError("Archive is missing its manifest or household data")
    try:
        manifest = json.loads(archive.read("manifest.json"))
        tables = json.loads(archive.read("data.json"))["tables"]
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
        archive.close()
        raise ValueError("Archive metadata is invalid") from error
    if manifest.get("format") not in {ARCHIVE_FORMAT, *LEGACY_ARCHIVE_FORMATS}:
        archive.close()
        raise ValueError("Archive format is not supported by this Tallystead version")
    if manifest.get("household_id") != str(household_id):
        archive.close()
        raise ValueError("This archive belongs to a different household")
    allowed = {table.name: table for table in _domain_tables()}
    if not isinstance(tables, dict) or set(tables) - set(allowed):
        archive.close()
        raise ValueError("Archive contains unsupported data tables")
    if sum(len(items) for items in tables.values()) > MAX_ROWS:
        archive.close()
        raise ValueError("Archive contains too many records")
    for name, items in tables.items():
        table = allowed[name]
        if not isinstance(items, list):
            archive.close()
            raise TypeError(f"Archive table {name} is invalid")
        column_names = set(table.c.keys())
        for item in items:
            if not isinstance(item, dict) or set(item) - column_names:
                archive.close()
                raise ValueError(f"Archive table {name} contains unsupported columns")
            if "household_id" in item and item["household_id"] != str(household_id):
                archive.close()
                raise ValueError("Archive contains records from another household")
    return manifest, tables, archive


def delete_household_rows(db: Session, rows: dict[str, list[dict[str, Any]]]) -> None:
    for table in reversed(_domain_tables()):
        table_rows = rows.get(table.name, [])
        if not table_rows:
            continue
        primary_keys = list(table.primary_key.columns)
        if len(primary_keys) == 1:
            db.execute(delete(table).where(primary_keys[0].in_([item[primary_keys[0].name] for item in table_rows])))
        else:
            for item in table_rows:
                db.execute(delete(table).where(*[column == item[column.name] for column in primary_keys]))
    db.flush()


def delete_household_data(db: Session, household_id: UUID) -> list[str]:
    loaded_state = db.get(HouseholdDataState, household_id)
    rows = collect_household_rows(db, household_id)
    object_keys = _object_keys(rows)
    delete_household_rows(db, rows)
    if loaded_state is not None:
        db.expunge(loaded_state)
    db.add(HouseholdDataState(household_id=household_id, mode="standard"))
    db.flush()
    return object_keys


def remove_objects(keys: list[str]) -> None:
    for key in keys:
        remove_object(key)


def restore_household_archive(db: Session, household_id: UUID, content: bytes) -> tuple[dict[str, Any], list[str], list[str]]:
    manifest, serialized_tables, archive = inspect_archive(content, household_id)
    existing = collect_household_rows(db, household_id)
    old_keys = _object_keys(existing)
    staged_keys: list[str] = []
    key_map: dict[str, str] = {}
    token = uuid4().hex
    try:
        for item in manifest.get("objects", []):
            path = item.get("archive_path")
            original_key = item.get("object_key")
            if not isinstance(path, str) or path not in archive.namelist() or not isinstance(original_key, str):
                raise ValueError("Archive object manifest is incomplete")
            object_content = archive.read(path)
            if hashlib.sha256(object_content).hexdigest() != item.get("checksum_sha256"):
                raise ValueError("Archive contains a document with an invalid checksum")
            staged_key = f"{household_id}/restored/{token}/{hashlib.sha256(original_key.encode()).hexdigest()}"
            put_object(staged_key, object_content, item.get("content_type") or "application/octet-stream")
            staged_keys.append(staged_key)
            key_map[original_key] = staged_key

        tables_by_name = {table.name: table for table in _domain_tables()}
        decoded: dict[str, list[dict[str, Any]]] = {}
        deferred: list[tuple[Any, Any, dict[str, Any]]] = []
        for name, items in serialized_tables.items():
            table = tables_by_name[name]
            decoded[name] = []
            for item in items:
                values = {key: _db_value(table.c[key], value) for key, value in item.items()}
                if name == "documents":
                    for key in ("object_key", "thumbnail_object_key"):
                        if values.get(key):
                            if values[key] not in key_map:
                                raise ValueError("Archive is missing content for one or more documents")
                            values[key] = key_map[values[key]]
                deferred_values = {key: values.pop(key) for key in DEFERRED_COLUMNS.get(name, set()) if values.get(key) is not None}
                decoded[name].append(values)
                if deferred_values:
                    deferred.append((table, values["id"], deferred_values))

        delete_household_rows(db, existing)
        for table in _domain_tables():
            table_rows = decoded.get(table.name, [])
            if table_rows:
                db.execute(insert(table), table_rows)
        for table, row_id, values in deferred:
            db.execute(update(table).where(table.c.id == row_id).values(**values))
        db.flush()
        return manifest, old_keys, staged_keys
    except Exception:
        for key in staged_keys:
            remove_object(key)
        raise
    finally:
        archive.close()

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SystemSetting, utc_now

INTEGRATIONS_KEY = "integrations"


def cipher() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)


def load_encrypted_setting(db: Session, key: str) -> dict:
    row = db.get(SystemSetting, key)
    if row is None:
        return {}
    try:
        return json.loads(cipher().decrypt(row.encrypted_value.encode()).decode())
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return {}


def save_encrypted_setting(db: Session, setting_key: str, values: dict, actor_user_id, *, merge: bool = True) -> SystemSetting:
    existing = load_encrypted_setting(db, setting_key) if merge else {}
    for item_key, value in values.items():
        if value is not None:
            existing[item_key] = value
    encrypted = cipher().encrypt(json.dumps(existing).encode()).decode()
    row = db.get(SystemSetting, setting_key)
    if row is None:
        row = SystemSetting(key=setting_key, encrypted_value=encrypted, updated_by_user_id=actor_user_id)
        db.add(row)
    else:
        row.encrypted_value = encrypted
        row.updated_by_user_id = actor_user_id
        row.updated_at = utc_now()
    db.commit()
    return row


def load_integrations(db: Session) -> dict:
    return load_encrypted_setting(db, INTEGRATIONS_KEY)


def save_integrations(db: Session, values: dict, actor_user_id) -> SystemSetting:
    return save_encrypted_setting(db, INTEGRATIONS_KEY, values, actor_user_id)


def integration_status(db: Session) -> tuple[dict, SystemSetting | None]:
    values = load_integrations(db)
    return values, db.scalar(select(SystemSetting).where(SystemSetting.key == INTEGRATIONS_KEY))

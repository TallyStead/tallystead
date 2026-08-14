import hashlib
import secrets
from datetime import UTC, timedelta

from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SessionToken, User, utc_now

password_hash = PasswordHash.recommended()
def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    return password_hash.verify(password, stored_hash)


def issue_session(db: Session, user: User, device_name: str | None = None) -> str:
    raw_token = secrets.token_urlsafe(48)
    now = utc_now()
    db.add(
        SessionToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            expires_at=now + timedelta(days=settings.session_ttl_days),
            device_name=device_name,
            last_seen_at=now,
        )
    )
    return raw_token


def session_from_token(db: Session, raw_token: str) -> SessionToken | None:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    session = db.scalar(
        select(SessionToken).where(
            SessionToken.token_hash == token_hash,
            SessionToken.revoked_at.is_(None),
            SessionToken.expires_at > utc_now(),
        )
    )
    if session is None:
        return None
    now = utc_now()
    last_seen = session.last_seen_at or session.created_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    if last_seen <= now - timedelta(minutes=settings.session_idle_minutes):
        session.revoked_at = now
        db.commit()
        return None
    if last_seen <= now - timedelta(minutes=settings.session_touch_minutes):
        session.last_seen_at = now
        db.commit()
    return session


def user_from_session(db: Session, raw_token: str) -> User | None:
    session = session_from_token(db, raw_token)
    return db.get(User, session.user_id) if session is not None else None


def validate_role(role: str) -> str:
    from app.models import Role

    if role not in {candidate.value for candidate in Role}:
        raise ValueError("Invalid household role")
    return role

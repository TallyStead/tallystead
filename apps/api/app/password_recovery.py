import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.mailer import send_message, tallystead_message
from app.models import PasswordResetToken, User, utc_now
from app.networking import canonical_url
from app.settings_store import load_integrations


def send_password_reset(db: Session, user: User) -> bool:
    values = load_integrations(db)
    host = values.get("smtp_host")
    password = values.get("smtp_password")
    from_address = values.get("smtp_from_address") or values.get("smtp_username")
    if not host or not password or not from_address:
        return False
    raw_token = secrets.token_urlsafe(48)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            expires_at=utc_now() + timedelta(minutes=30),
        )
    )
    db.commit()
    reset_url = f"{canonical_url(db)}/?reset_token={raw_token}"
    message = tallystead_message(
        to_address=user.email,
        from_address=from_address,
        subject="Reset your Tallystead password",
        heading="Reset your password",
        paragraphs=(
            "A password reset was requested for your Tallystead account.",
            "Use the button below within 30 minutes. If you did not request this, you can ignore this email.",
        ),
        action_label="Reset password",
        action_url=reset_url,
        preheader="Use this secure local link to reset your Tallystead password.",
    )
    send_message(values, message)
    return True


def consume_password_reset(
    db: Session, raw_token: str
) -> tuple[PasswordResetToken | None, User | None]:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > utc_now(),
        )
    )
    return token, db.get(User, token.user_id) if token else None

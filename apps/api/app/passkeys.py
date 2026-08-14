import json
from datetime import timedelta
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json_dict
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.models import PasskeyChallenge, PasskeyCredential, User, utc_now
from app.networking import canonical_url

CHALLENGE_TTL_MINUTES = 5


def relying_party(db: Session) -> tuple[str, str]:
    public_url = canonical_url(db)
    parsed = urlparse(public_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("The canonical client URL must be a valid HTTPS URL for passkeys")
    return parsed.hostname, public_url.rstrip("/")


def registration_options(db: Session, user: User) -> tuple[PasskeyChallenge, dict]:
    rp_id, _ = relying_party(db)
    credentials = db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id)).all()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Tallystead",
        user_id=user.id.bytes,
        user_name=user.email,
        user_display_name=user.display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id)) for item in credentials],
    )
    ceremony = PasskeyChallenge(
        user_id=user.id,
        purpose="register",
        challenge=bytes_to_base64url(options.challenge),
        expires_at=utc_now() + timedelta(minutes=CHALLENGE_TTL_MINUTES),
    )
    db.add(ceremony)
    db.commit()
    return ceremony, options_to_json_dict(options)


def finish_registration(db: Session, user: User, ceremony_id, credential: dict) -> PasskeyCredential:
    ceremony = valid_ceremony(db, ceremony_id, user.id, "register")
    rp_id, origin = relying_party(db)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(ceremony.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passkey registration could not be verified") from exc
    encoded_id = bytes_to_base64url(verified.credential_id)
    if db.scalar(select(PasskeyCredential).where(PasskeyCredential.credential_id == encoded_id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Passkey is already registered")
    transports = credential.get("response", {}).get("transports")
    passkey = PasskeyCredential(
        user_id=user.id,
        credential_id=encoded_id,
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=verified.sign_count,
        transports=json.dumps(transports) if transports else None,
    )
    ceremony.used_at = utc_now()
    db.add(passkey)
    db.commit()
    return passkey


def authentication_options(db: Session, user: User) -> tuple[PasskeyChallenge, dict]:
    rp_id, _ = relying_party(db)
    credentials = db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id)).all()
    if not credentials:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No passkey is registered for this account")
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id)) for item in credentials],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    ceremony = PasskeyChallenge(
        user_id=user.id,
        purpose="authenticate",
        challenge=bytes_to_base64url(options.challenge),
        expires_at=utc_now() + timedelta(minutes=CHALLENGE_TTL_MINUTES),
    )
    db.add(ceremony)
    db.commit()
    return ceremony, options_to_json_dict(options)


def finish_authentication(db: Session, ceremony_id, credential: dict) -> User:
    ceremony = db.get(PasskeyChallenge, ceremony_id)
    if ceremony is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passkey ceremony is invalid")
    ceremony = valid_ceremony(db, ceremony_id, ceremony.user_id, "authenticate")
    credential_id = credential.get("id")
    passkey = db.scalar(select(PasskeyCredential).where(PasskeyCredential.credential_id == credential_id, PasskeyCredential.user_id == ceremony.user_id))
    if passkey is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Passkey is not registered")
    rp_id, origin = relying_party(db)
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(ceremony.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Passkey sign-in could not be verified") from exc
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = utc_now()
    ceremony.used_at = utc_now()
    user = db.get(User, ceremony.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is unavailable")
    db.commit()
    return user


def valid_ceremony(db: Session, ceremony_id, user_id, purpose: str) -> PasskeyChallenge:
    ceremony = db.get(PasskeyChallenge, ceremony_id)
    if ceremony is None or ceremony.user_id != user_id or ceremony.purpose != purpose or ceremony.used_at is not None or ceremony.expires_at <= utc_now():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passkey ceremony is invalid or expired")
    return ceremony

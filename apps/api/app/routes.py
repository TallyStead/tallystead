import csv
import hashlib
import html
import io
import json
import smtplib
import socket
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from urllib.parse import quote
from urllib.request import urlopen
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, or_, select, text

from app.automation import (
    ensure_mapping_version,
    learn_rule_from_row,
    merchant_for_payee,
    recompute_pending_rows,
    record_applied_decision,
)
from app.categorization import ai_import_proposals
from app.config import settings
from app.demo_household import create_demo_household
from app.dependencies import (
    DbSession,
    current_membership,
    current_session,
    current_user,
    require_roles,
)
from app.documents import decode_document, refresh_document_matches, thumbnail
from app.imports import inspect_csv, raw_value, validate_safe_notes
from app.ingestion import ingest_csv_evidence
from app.ledger import (
    account_balance,
    household_account,
    household_merchant,
    household_transaction,
    included_activity_query,
    merchant_response,
    seed_default_categories,
    transaction_response,
    transaction_snapshot,
    validate_splits,
)
from app.local_ai import test_local_vision_model, validate_local_ai_url
from app.mailer import send_test_email
from app.models import (
    AccountValuation,
    AuditEvent,
    BackupRun,
    BillInstance,
    BillPaymentLink,
    BillProfile,
    Category,
    CategoryRule,
    Debt,
    Document,
    DocumentExtraction,
    DocumentMatch,
    ExternalIdentity,
    FinancialAccount,
    FinancialGoal,
    Household,
    ImportBatch,
    ImportRow,
    ImportSource,
    IncomeEvent,
    IncomeSource,
    LedgerTransaction,
    LoginAttempt,
    Membership,
    Merchant,
    MerchantAlias,
    PasskeyCredential,
    PlannerSnapshot,
    ReconciliationException,
    ReconciliationMatch,
    ReportPreset,
    Role,
    ServiceHeartbeat,
    SessionToken,
    TransactionRevision,
    TransactionSplit,
    TransferLink,
    User,
    utc_now,
)
from app.net_worth import (
    account_defaults,
    account_net_value,
    latest_valuation,
    validate_planner_eligibility,
)
from app.networking import active_configuration, current_request_origin, trusted_forward_auth_source
from app.object_store import get_object, put_object, remove_object
from app.obligations import (
    bill_instance_response,
    income_event_response,
    next_occurrence,
    recalculate_debt_balance,
    transaction_linked_total,
    validate_range,
)
from app.passkeys import (
    authentication_options,
    finish_authentication,
    finish_registration,
    registration_options,
)
from app.password_recovery import consume_password_reset, send_password_reset
from app.planner import calculate_forecast, collect_planner_input
from app.reporting import ReportFilters, spending_report
from app.schemas import (
    AccountStatusRequest,
    AccountValuationCreateRequest,
    AccountValuationResponse,
    AdminPasswordResetRequest,
    BalanceExplanationResponse,
    BillInstanceResponse,
    BillInstanceUpdateRequest,
    BillProfileCreateRequest,
    BillProfileResponse,
    BillProfileUpdateRequest,
    BulkCreateImportedTransactionsRequest,
    CalendarItemResponse,
    CategoryCreateRequest,
    CategoryResponse,
    CategoryUpdateRequest,
    CreateImportedTransactionRequest,
    CsvImportRequest,
    CsvInspectionResponse,
    CurrentUserResponse,
    DebtCreateRequest,
    DebtResponse,
    DebtUpdateRequest,
    DocumentCreateRequest,
    DocumentDetailResponse,
    DocumentExtractionResponse,
    DocumentMatchCreateRequest,
    DocumentMatchDecisionRequest,
    DocumentMatchResponse,
    DocumentResponse,
    DocumentUpdateRequest,
    ExtractionDecisionRequest,
    FinancialAccountCreateRequest,
    FinancialAccountResponse,
    FinancialAccountUpdateRequest,
    GenerationResponse,
    ImportBatchResponse,
    ImportCategorySuggestionRequest,
    ImportSourceRequest,
    ImportSourceResponse,
    IncomeEventCreateRequest,
    IncomeEventReceiveRequest,
    IncomeEventResponse,
    IncomeSourceCreateRequest,
    IncomeSourceResponse,
    IncomeSourceUpdateRequest,
    IntegrationSettingsRequest,
    IntegrationStatusResponse,
    IntegrationTestResponse,
    LedgerTransactionCreateRequest,
    LedgerTransactionPageResponse,
    LedgerTransactionResponse,
    LoginRequest,
    MatchCandidateResponse,
    MatchDecisionRequest,
    MemberCreateRequest,
    MemberResponse,
    MemberRoleRequest,
    MerchantCreateRequest,
    MerchantProfileResponse,
    MerchantProfileSummaryResponse,
    MerchantResponse,
    MerchantUpdateRequest,
    NetWorthAccountResponse,
    NetWorthResponse,
    PasskeyLoginFinishRequest,
    PasskeyLoginOptionsRequest,
    PasskeyOptionsResponse,
    PasskeyRegistrationFinishRequest,
    PasskeyResponse,
    PasswordResetFinishRequest,
    PasswordResetRequest,
    PaymentLinkRequest,
    PaymentLinkResponse,
    PlannerForecastResponse,
    PlannerRequest,
    ProxyAuthStatusResponse,
    ProxyLinkStatusResponse,
    ProxyLoginRequest,
    ReconciliationExceptionResponse,
    ReminderUpdateRequest,
    ReportPresetCreateRequest,
    ReportPresetResponse,
    ReviewItemResponse,
    ReviewQueuePageResponse,
    ServerIdentityResponse,
    SessionResponse,
    SessionTokenResponse,
    SetupRequest,
    SetupStatusResponse,
    SpendingReportResponse,
    SystemStatusResponse,
    TransactionCorrectionRequest,
    TransactionDetailResponse,
    TransactionReconcileRequest,
    TransactionReverseRequest,
    TransactionRevisionResponse,
    TransactionUpdateRequest,
    TransferCreateRequest,
    TransferResponse,
    VisionModelTestResponse,
)
from app.security import hash_password, issue_session, validate_role, verify_password
from app.settings_store import integration_status, load_integrations, save_integrations

router = APIRouter(prefix="/v1")
obligations_router = APIRouter(prefix="/v1")

ledger_writer = require_roles(Role.OWNER, Role.MANAGER)


@router.get("/server/identity", response_model=ServerIdentityResponse, tags=["system"])
def server_identity(request: Request) -> ServerIdentityResponse:
    """Public, non-sensitive metadata used during client server connection."""
    return ServerIdentityResponse(public_url=current_request_origin(request), api_version="v1")


def session_response(
    db: DbSession,
    user: User,
    device_name: str | None,
    membership: Membership | None = None,
    method: str = "password_or_passkey_login",
) -> SessionResponse:
    if membership is None:
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No household membership")
    token = issue_session(db, user, device_name)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=user.id, action="auth.session_created", resource_type="session", detail=method))
    db.commit()
    return SessionResponse(access_token=token, user_id=user.id, household_id=membership.household_id, role=membership.role)


@router.get("/setup/status", response_model=SetupStatusResponse, tags=["setup"])
def setup_status(db: DbSession) -> SetupStatusResponse:
    owner_count = db.scalar(select(func.count()).select_from(Membership).where(Membership.role == Role.OWNER.value))
    return SetupStatusResponse(setup_required=owner_count == 0)


@router.post("/setup", response_model=SessionResponse, status_code=status.HTTP_201_CREATED, tags=["setup"])
def setup(request: SetupRequest, db: DbSession) -> SessionResponse:
    existing_owner = db.scalar(select(Membership).where(Membership.role == Role.OWNER.value))
    if existing_owner is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Server setup is already complete")
    if db.scalar(select(User).where(User.email == request.email.lower())) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")
    household = Household(name=request.household_name)
    user = User(email=request.email.lower(), display_name=request.display_name, password_hash=hash_password(request.password))
    db.add_all([household, user])
    db.flush()
    membership = Membership(household_id=household.id, user_id=user.id, role=Role.OWNER.value)
    db.add(membership)
    if request.create_demo:
        create_demo_household(db, household.id, user.id, request.demo_reference_date or utc_now().date(), request.demo_volume)
    else:
        seed_default_categories(db, household.id)
    db.add(AuditEvent(household_id=household.id, actor_user_id=user.id, action="household.setup_completed", resource_type="household", resource_id=str(household.id)))
    db.flush()
    token = issue_session(db, user, request.device_name)
    db.commit()
    return SessionResponse(
        access_token=token,
        user_id=user.id,
        household_id=household.id,
        role=Role.OWNER.value,
    )


@router.post("/auth/login", response_model=SessionResponse, tags=["auth"])
def login(request: LoginRequest, db: DbSession) -> SessionResponse:
    email = request.email.lower()
    cutoff = utc_now() - timedelta(minutes=15)
    failures = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.email == email, LoginAttempt.succeeded.is_(False), LoginAttempt.attempted_at >= cutoff)) or 0
    if failures >= 5:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many failed sign-in attempts. Try again later or ask an Owner for recovery.")
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or not verify_password(request.password, user.password_hash):
        db.add(LoginAttempt(email=email, succeeded=False))
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    db.execute(delete(LoginAttempt).where(LoginAttempt.email == email))
    return session_response(db, user, request.device_name)


def forwarded_pangolin_identity(request: Request, db: DbSession) -> tuple[str, str, str | None] | None:
    effective = getattr(request.state, "effective_request", None)
    if effective is None or not effective.forwarded_headers_trusted:
        return None
    source = request.headers.get("x-tallystead-forward-auth-source")
    if not trusted_forward_auth_source(source, active_configuration(db)):
        return None
    subject = (request.headers.get("x-tallystead-forward-auth-subject") or "").strip()
    email = (request.headers.get("x-tallystead-forward-auth-email") or "").strip().lower()
    display_name = (request.headers.get("x-tallystead-forward-auth-name") or "").strip() or None
    if not subject or not email or "@" not in email or len(subject) > 320 or len(email) > 320:
        return None
    return subject, email, display_name[:120] if display_name else None


@router.get("/auth/proxy/status", response_model=ProxyAuthStatusResponse, tags=["auth"])
def proxy_auth_status(request: Request, db: DbSession) -> ProxyAuthStatusResponse:
    identity = forwarded_pangolin_identity(request, db)
    if identity is None:
        return ProxyAuthStatusResponse(available=False)
    _, email, display_name = identity
    return ProxyAuthStatusResponse(available=True, email=email, display_name=display_name)


@router.post("/auth/proxy/login", response_model=SessionResponse, tags=["auth"])
def proxy_auth_login(request_data: ProxyLoginRequest, request: Request, db: DbSession) -> SessionResponse:
    forwarded = forwarded_pangolin_identity(request, db)
    if forwarded is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Trusted Pangolin identity is unavailable")
    subject, email, _ = forwarded
    identity = db.scalar(select(ExternalIdentity).where(ExternalIdentity.provider == "pangolin", ExternalIdentity.subject == subject))
    if identity is not None:
        user = db.get(User, identity.user_id)
        if user is None or not user.is_active or identity.email_at_link != email or user.email != email:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Pangolin identity no longer matches an active Tallystead member")
        identity.last_used_at = utc_now()
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
    else:
        user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id)) if user else None
        if user is None or membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Pangolin identity is not linked to an active Tallystead household member")
        existing_user_link = db.scalar(select(ExternalIdentity).where(ExternalIdentity.provider == "pangolin", ExternalIdentity.user_id == user.id))
        if existing_user_link is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This Tallystead member is already linked to a different Pangolin identity")
        identity = ExternalIdentity(user_id=user.id, provider="pangolin", subject=subject, email_at_link=email)
        db.add(identity)
        db.flush()
        db.add(AuditEvent(household_id=membership.household_id, actor_user_id=user.id, action="auth.external_identity_linked", resource_type="external_identity", resource_id=str(identity.id), detail="provider:pangolin;existing_member:true"))
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No household membership")
    return session_response(db, user, request_data.device_name or "Web browser · Pangolin", membership, "pangolin_forward_auth")


@router.get("/auth/proxy/link", response_model=ProxyLinkStatusResponse, tags=["auth"])
def proxy_link_status(db: DbSession, user: Annotated[User, Depends(current_user)]) -> ProxyLinkStatusResponse:
    identity = db.scalar(select(ExternalIdentity).where(ExternalIdentity.provider == "pangolin", ExternalIdentity.user_id == user.id))
    if identity is None:
        return ProxyLinkStatusResponse(linked=False)
    return ProxyLinkStatusResponse(linked=True, provider=identity.provider, email_at_link=identity.email_at_link, created_at=identity.created_at.isoformat(), last_used_at=identity.last_used_at.isoformat())


@router.delete("/auth/proxy/link", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
def remove_proxy_link(db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(current_membership)]) -> None:
    identity = db.scalar(select(ExternalIdentity).where(ExternalIdentity.provider == "pangolin", ExternalIdentity.user_id == actor.id))
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pangolin identity link not found")
    identity_id = identity.id
    db.delete(identity)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="auth.external_identity_unlinked", resource_type="external_identity", resource_id=str(identity_id), detail="provider:pangolin"))
    db.commit()


@router.post("/auth/password-reset/request", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
def request_password_reset(payload: PasswordResetRequest, request: Request, db: DbSession) -> None:
    user = db.scalar(select(User).where(User.email == payload.email.lower(), User.is_active.is_(True)))
    if user is not None:
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
        try:
            sent = send_password_reset(db, user, current_request_origin(request))
            action = "auth.password_reset_email_sent" if sent else "auth.password_reset_email_unavailable"
        except (OSError, smtplib.SMTPException):
            action = "auth.password_reset_email_failed"
        db.add(AuditEvent(household_id=membership.household_id if membership else None, actor_user_id=user.id, action=action, resource_type="user", resource_id=str(user.id)))
        db.commit()


@router.post("/auth/password-reset/finish", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
def finish_password_reset(request: PasswordResetFinishRequest, db: DbSession) -> None:
    token, user = consume_password_reset(db, request.token)
    if token is None or user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password reset link is invalid or expired")
    user.password_hash = hash_password(request.password)
    token.used_at = utc_now()
    for active in db.scalars(select(SessionToken).where(SessionToken.user_id == user.id, SessionToken.revoked_at.is_(None))):
        active.revoked_at = utc_now()
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
    db.add(AuditEvent(household_id=membership.household_id if membership else None, actor_user_id=user.id, action="auth.password_reset_completed", resource_type="user", resource_id=str(user.id)))
    db.commit()


@router.post("/auth/passkeys/register/options", response_model=PasskeyOptionsResponse, tags=["auth"])
def passkey_registration_options(
    request: Request, db: DbSession, user: Annotated[User, Depends(current_user)]
) -> PasskeyOptionsResponse:
    ceremony, public_key = registration_options(db, user, current_request_origin(request))
    return PasskeyOptionsResponse(ceremony_id=ceremony.id, public_key=public_key)


@router.post("/auth/passkeys/register/finish", response_model=PasskeyResponse, tags=["auth"])
def passkey_registration_finish(
    payload: PasskeyRegistrationFinishRequest,
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(current_membership)],
) -> PasskeyResponse:
    passkey = finish_registration(db, user, payload.ceremony_id, payload.credential, current_request_origin(request))
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=user.id, action="auth.passkey_registered", resource_type="passkey", resource_id=str(passkey.id)))
    db.commit()
    return PasskeyResponse(passkey_id=passkey.id, created_at=passkey.created_at.isoformat(), last_used_at=None)


@router.post("/auth/passkeys/login/options", response_model=PasskeyOptionsResponse, tags=["auth"])
def passkey_login_options(payload: PasskeyLoginOptionsRequest, request: Request, db: DbSession) -> PasskeyOptionsResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower(), User.is_active.is_(True)))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No passkey is registered for this account")
    ceremony, public_key = authentication_options(db, user, current_request_origin(request))
    return PasskeyOptionsResponse(ceremony_id=ceremony.id, public_key=public_key)


@router.post("/auth/passkeys/login/finish", response_model=SessionResponse, tags=["auth"])
def passkey_login_finish(payload: PasskeyLoginFinishRequest, request: Request, db: DbSession) -> SessionResponse:
    user = finish_authentication(db, payload.ceremony_id, payload.credential, current_request_origin(request))
    return session_response(db, user, payload.device_name)


@router.get("/auth/passkeys", response_model=list[PasskeyResponse], tags=["auth"])
def list_passkeys(db: DbSession, user: Annotated[User, Depends(current_user)]) -> list[PasskeyResponse]:
    rows = db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == user.id)).all()
    return [PasskeyResponse(passkey_id=item.id, created_at=item.created_at.isoformat(), last_used_at=item.last_used_at.isoformat() if item.last_used_at else None) for item in rows]


@router.get("/auth/sessions", response_model=list[SessionTokenResponse], tags=["auth"])
def list_personal_sessions(
    db: DbSession,
    user: Annotated[User, Depends(current_user)],
    active_session: Annotated[SessionToken, Depends(current_session)],
) -> list[SessionTokenResponse]:
    rows = db.scalars(
        select(SessionToken)
        .where(
            SessionToken.user_id == user.id,
            SessionToken.revoked_at.is_(None),
            SessionToken.expires_at > utc_now(),
        )
        .order_by(SessionToken.created_at.desc())
    ).all()
    return [
        SessionTokenResponse(
            session_id=item.id,
            user_id=item.user_id,
            device_name=item.device_name,
            created_at=item.created_at.isoformat(),
            expires_at=item.expires_at.isoformat(),
            is_current=item.id == active_session.id,
        )
        for item in rows
    ]


@router.delete("/auth/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
def revoke_personal_session(
    session_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(current_membership)],
) -> None:
    session = db.get(SessionToken, session_id)
    if session is None or session.user_id != actor.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    session.revoked_at = utc_now()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="auth.personal_session_revoked", resource_type="session", resource_id=str(session.id)))
    db.commit()


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
def logout(
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(current_membership)],
    session: Annotated[SessionToken, Depends(current_session)],
) -> None:
    session.revoked_at = utc_now()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="auth.session_signed_out", resource_type="session", resource_id=str(session.id)))
    db.commit()


@router.delete("/auth/passkeys/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
def delete_passkey(
    passkey_id: UUID,
    db: DbSession,
    user: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(current_membership)],
) -> None:
    passkey = db.get(PasskeyCredential, passkey_id)
    if passkey is None or passkey.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")
    db.delete(passkey)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=user.id, action="auth.passkey_removed", resource_type="passkey", resource_id=str(passkey.id)))
    db.commit()


@router.get("/auth/me", response_model=CurrentUserResponse, tags=["auth"])
def me(db: DbSession, user: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(current_membership)]) -> CurrentUserResponse:
    household = db.get(Household, membership.household_id)
    if household is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Household not found")
    return CurrentUserResponse(user_id=user.id, email=user.email, display_name=user.display_name, household_id=household.id, household_name=household.name, role=membership.role, session_idle_minutes=settings.session_idle_minutes)


def owner_membership(membership: Annotated[Membership, Depends(current_membership)]) -> Membership:
    if membership.role != Role.OWNER.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner permission required")
    return membership


def member_response(user: User, membership: Membership) -> MemberResponse:
    return MemberResponse(
        user_id=user.id,
        membership_id=membership.id,
        email=user.email,
        display_name=user.display_name,
        role=membership.role,
        is_active=user.is_active,
    )


@router.get("/household/members", response_model=list[MemberResponse], tags=["household"])
def list_members(db: DbSession, membership: Annotated[Membership, Depends(owner_membership)]) -> list[MemberResponse]:
    rows = db.execute(
        select(User, Membership).join(Membership, Membership.user_id == User.id).where(Membership.household_id == membership.household_id)
    ).all()
    return [member_response(user, household_membership) for user, household_membership in rows]


@router.post("/household/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED, tags=["household"])
def create_member(
    request: MemberCreateRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner_membership)],
) -> MemberResponse:
    try:
        role = validate_role(request.role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if db.scalar(select(User).where(User.email == request.email.lower())) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")
    user = User(email=request.email.lower(), display_name=request.display_name, password_hash=hash_password(request.password))
    db.add(user)
    db.flush()
    new_membership = Membership(household_id=membership.household_id, user_id=user.id, role=role)
    db.add(new_membership)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="household.member_created", resource_type="membership", resource_id=str(new_membership.id), detail=f"role={role}"))
    db.commit()
    return member_response(user, new_membership)


@router.patch("/household/members/{membership_id}", response_model=MemberResponse, tags=["household"])
def update_member_role(
    membership_id: UUID,
    request: MemberRoleRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    actor_membership: Annotated[Membership, Depends(owner_membership)],
) -> MemberResponse:
    try:
        role = validate_role(request.role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    target = db.get(Membership, membership_id)
    if target is None or target.household_id != actor_membership.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Household member not found")
    if target.role == Role.OWNER.value and role != Role.OWNER.value:
        owner_count = db.scalar(select(func.count()).select_from(Membership).where(Membership.household_id == target.household_id, Membership.role == Role.OWNER.value))
        if owner_count == 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A household must retain an Owner")
    target.role = role
    target_user = db.get(User, target.user_id)
    db.add(AuditEvent(household_id=target.household_id, actor_user_id=actor.id, action="household.member_role_changed", resource_type="membership", resource_id=str(target.id), detail=f"role={role}"))
    db.commit()
    return member_response(target_user, target)


@router.patch("/household/members/{membership_id}/status", response_model=MemberResponse, tags=["household"])
def update_member_status(
    membership_id: UUID,
    request: AccountStatusRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    actor_membership: Annotated[Membership, Depends(owner_membership)],
) -> MemberResponse:
    target = db.get(Membership, membership_id)
    if target is None or target.household_id != actor_membership.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Household member not found")
    if target.user_id == actor.id and not request.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An Owner cannot disable their current account")
    target_user = db.get(User, target.user_id)
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member user not found")
    target_user.is_active = request.is_active
    if not request.is_active:
        for active in db.scalars(select(SessionToken).where(SessionToken.user_id == target_user.id, SessionToken.revoked_at.is_(None))):
            active.revoked_at = utc_now()
    db.add(AuditEvent(household_id=target.household_id, actor_user_id=actor.id, action="household.member_enabled" if request.is_active else "household.member_disabled", resource_type="membership", resource_id=str(target.id)))
    db.commit()
    return member_response(target_user, target)


@router.get("/household/sessions", response_model=list[SessionTokenResponse], tags=["household"])
def list_sessions(
    db: DbSession,
    membership: Annotated[Membership, Depends(owner_membership)],
    active_session: Annotated[SessionToken, Depends(current_session)],
) -> list[SessionTokenResponse]:
    rows = db.execute(
        select(SessionToken).join(Membership, Membership.user_id == SessionToken.user_id).where(Membership.household_id == membership.household_id, SessionToken.revoked_at.is_(None))
    ).scalars()
    return [SessionTokenResponse(session_id=session.id, user_id=session.user_id, device_name=session.device_name, created_at=session.created_at.isoformat(), expires_at=session.expires_at.isoformat(), is_current=session.id == active_session.id) for session in rows]


@router.delete("/household/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["household"])
def revoke_session(
    session_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner_membership)],
) -> None:
    session = db.get(SessionToken, session_id)
    target_membership = db.scalar(select(Membership).where(Membership.user_id == session.user_id)) if session else None
    if session is None or target_membership is None or target_membership.household_id != membership.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    session.revoked_at = utc_now()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="auth.session_revoked", resource_type="session", resource_id=str(session.id)))
    db.commit()


@router.post("/household/members/{membership_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT, tags=["household"])
def admin_reset_member_password(
    membership_id: UUID,
    request: AdminPasswordResetRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    actor_membership: Annotated[Membership, Depends(owner_membership)],
) -> None:
    """Owner-operated recovery until SMTP-based recovery is configured."""
    target = db.get(Membership, membership_id)
    if target is None or target.household_id != actor_membership.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Household member not found")
    target_user = db.get(User, target.user_id)
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member user not found")
    target_user.password_hash = hash_password(request.password)
    for session in db.scalars(select(SessionToken).where(SessionToken.user_id == target_user.id, SessionToken.revoked_at.is_(None))):
        session.revoked_at = utc_now()
    db.add(AuditEvent(household_id=target.household_id, actor_user_id=actor.id, action="auth.password_reset_by_owner", resource_type="membership", resource_id=str(target.id)))
    db.commit()


@router.delete("/household/members/{membership_id}/passkeys", status_code=status.HTTP_204_NO_CONTENT, tags=["household"])
def admin_remove_member_passkeys(
    membership_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    actor_membership: Annotated[Membership, Depends(owner_membership)],
) -> None:
    target = db.get(Membership, membership_id)
    if target is None or target.household_id != actor_membership.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Household member not found")
    db.execute(delete(PasskeyCredential).where(PasskeyCredential.user_id == target.user_id))
    for active in db.scalars(select(SessionToken).where(SessionToken.user_id == target.user_id, SessionToken.revoked_at.is_(None))):
        active.revoked_at = utc_now()
    db.add(AuditEvent(household_id=target.household_id, actor_user_id=actor.id, action="auth.passkeys_removed_by_owner", resource_type="membership", resource_id=str(target.id)))
    db.commit()


@router.get("/system/status", response_model=SystemStatusResponse, tags=["system"])
def system_status(
    db: DbSession,
    _: Annotated[Membership, Depends(owner_membership)],
) -> SystemStatusResponse:
    db.execute(text("SELECT 1"))
    heartbeat = db.get(ServiceHeartbeat, "worker")
    worker_healthy = bool(heartbeat and heartbeat.heartbeat_at >= utc_now() - timedelta(minutes=2) and heartbeat.status == "healthy")
    latest_backup = db.scalar(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(1))
    integrations, _ = integration_status(db)
    return SystemStatusResponse(
        environment=settings.environment,
        database_connected=True,
        object_store_configured=bool(settings.object_store_endpoint),
        smtp_configured=bool(settings.smtp_host or integrations.get("smtp_host")),
        passkeys_enabled=True,
        worker_healthy=worker_healthy,
        worker_last_seen_at=heartbeat.heartbeat_at.isoformat() if heartbeat else None,
        latest_backup_status=latest_backup.status if latest_backup else None,
        latest_backup_at=(latest_backup.completed_at or latest_backup.started_at).isoformat() if latest_backup else None,
    )


@router.get("/system/integrations", response_model=IntegrationStatusResponse, tags=["system"])
def get_integration_status(
    db: DbSession, _: Annotated[Membership, Depends(owner_membership)]
) -> IntegrationStatusResponse:
    values, row = integration_status(db)
    return IntegrationStatusResponse(
        smtp_configured=bool(values.get("smtp_host") and values.get("smtp_password")),
        imap_configured=bool(values.get("imap_host") and values.get("imap_password")),
        smtp_host=values.get("smtp_host"),
        smtp_port=values.get("smtp_port"),
        smtp_username=values.get("smtp_username"),
        smtp_from_address=values.get("smtp_from_address"),
        smtp_security=values.get("smtp_security"),
        imap_host=values.get("imap_host"),
        imap_port=values.get("imap_port"),
        imap_username=values.get("imap_username"),
        ai_configured=bool(values.get("ai_provider") and values.get("ai_base_url")),
        ai_provider=values.get("ai_provider"),
        ai_base_url=values.get("ai_base_url"),
        ai_model=values.get("ai_model"),
        updated_at=row.updated_at.isoformat() if row else None,
        imap_archive_processed=values.get("imap_archive_processed", False),
        smtp_notifications_enabled=values.get("smtp_notifications_enabled", False),
        ai_enabled=values.get("ai_enabled", False),
        ai_extract_enabled=values.get("ai_extract_enabled", False),
        ai_suggestions_enabled=values.get("ai_suggestions_enabled", False),
        ai_resource_limit=values.get("ai_resource_limit", "medium"),
    )


@router.put("/system/integrations", response_model=IntegrationStatusResponse, tags=["system"])
def update_integrations(
    request: IntegrationSettingsRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner_membership)],
) -> IntegrationStatusResponse:
    validate_local_ai_url(request.ai_base_url)
    row = save_integrations(db, request.model_dump(), actor.id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="system.integrations_updated", resource_type="system_setting", resource_id=row.key, detail="Credentials updated; secret values are write-only"))
    db.commit()
    return get_integration_status(db, membership)


@router.post("/system/integrations/test/{integration}", response_model=IntegrationTestResponse, tags=["system"])
def test_integration(
    integration: str,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner_membership)],
) -> IntegrationTestResponse:
    values, _ = integration_status(db)
    try:
        if integration == "smtp":
            send_test_email(values, actor.email)
        elif integration == "imap":
            host = values.get(f"{integration}_host")
            port = values.get(f"{integration}_port") or 993
            if not host:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{integration.upper()} is not configured")
            with socket.create_connection((host, int(port)), timeout=5):
                pass
        elif integration == "ai":
            base_url = values.get("ai_base_url")
            provider = values.get("ai_provider")
            if not base_url or not provider:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Local AI is not configured")
            validate_local_ai_url(base_url)
            path = "/api/tags" if provider == "ollama" else "/v1/models"
            with urlopen(f"{base_url.rstrip('/')}{path}", timeout=5) as response:
                if response.status >= 400:
                    raise OSError("Runtime returned an error")
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown integration")
    except HTTPException:
        raise
    except (OSError, ValueError):
        db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="system.integration_test_failed", resource_type="integration", resource_id=integration))
        db.commit()
        return IntegrationTestResponse(integration=integration, reachable=False, detail="Connection failed; verify the local address, port, and service")
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="system.integration_test_succeeded", resource_type="integration", resource_id=integration))
    db.commit()
    detail = f"Test email sent to {actor.email}" if integration == "smtp" else "Local service is reachable"
    return IntegrationTestResponse(integration=integration, reachable=True, detail=detail)


@router.post("/system/integrations/ai/vision-test", response_model=VisionModelTestResponse, tags=["system"])
def test_ai_vision(
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner_membership)],
) -> VisionModelTestResponse:
    values = load_integrations(db)
    if not (values.get("ai_provider") and values.get("ai_base_url") and values.get("ai_model")):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Configure a local AI provider, URL, and vision model first")
    validate_local_ai_url(values["ai_base_url"])
    try:
        result = test_local_vision_model(values)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        result = {
            "success": False,
            "provider": values["ai_provider"],
            "model": values["ai_model"],
            "duration_ms": 0,
            "checks": {},
            "detail": "The local model test failed. Verify the address, loaded model, and image support.",
        }
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="system.ai_vision_tested", resource_type="integration", resource_id="ai", detail=f"success:{result['success']};model:{result['model']}"))
    db.commit()
    return VisionModelTestResponse(**result)


def financial_account_response(db: DbSession, item: FinancialAccount) -> FinancialAccountResponse:
    balance = account_balance(db, item)
    valuation = latest_valuation(db, item.id, utc_now().date())
    return FinancialAccountResponse(account_id=item.id, name=item.name, account_type=item.account_type, currency_code=item.currency_code, opening_balance_minor=item.opening_balance_minor, balance_minor=balance, include_in_planner=item.include_in_planner, include_in_net_worth=item.include_in_net_worth, ownership_scope=item.ownership_scope, balance_nature=item.balance_nature, liquidity=item.liquidity, tax_treatment=item.tax_treatment, institution=item.institution, masked_identifier=item.masked_identifier, current_value_minor=valuation.value_minor if valuation else balance, valuation_as_of=valuation.valuation_date if valuation else None, is_archived=item.is_archived)


@router.get("/ledger/accounts", response_model=list[FinancialAccountResponse], tags=["ledger"])
def list_financial_accounts(
    db: DbSession, membership: Annotated[Membership, Depends(current_membership)]
) -> list[FinancialAccountResponse]:
    accounts = db.scalars(select(FinancialAccount).where(FinancialAccount.household_id == membership.household_id).order_by(FinancialAccount.is_archived, FinancialAccount.name)).all()
    return [financial_account_response(db, item) for item in accounts]


@router.post("/ledger/accounts", response_model=FinancialAccountResponse, status_code=status.HTTP_201_CREATED, tags=["ledger"])
def create_financial_account(
    request: FinancialAccountCreateRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> FinancialAccountResponse:
    duplicate = db.scalar(select(FinancialAccount).where(FinancialAccount.household_id == membership.household_id, FinancialAccount.name == request.name.strip()))
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this name already exists")
    defaults = account_defaults(request.account_type)
    account = FinancialAccount(household_id=membership.household_id, name=request.name.strip(), account_type=request.account_type, currency_code=request.currency_code, opening_balance_minor=request.opening_balance_minor, include_in_planner=request.include_in_planner if request.include_in_planner is not None else bool(defaults["include_in_planner"]), include_in_net_worth=request.include_in_net_worth, ownership_scope=request.ownership_scope or str(defaults["ownership_scope"]), balance_nature=request.balance_nature or str(defaults["balance_nature"]), liquidity=request.liquidity or str(defaults["liquidity"]), tax_treatment=request.tax_treatment or str(defaults["tax_treatment"]), institution=request.institution, masked_identifier=request.masked_identifier)
    validate_planner_eligibility(account)
    db.add(account)
    db.flush()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.account_created", resource_type="financial_account", resource_id=str(account.id)))
    db.commit()
    return financial_account_response(db, account)


@router.patch("/ledger/accounts/{account_id}", response_model=FinancialAccountResponse, tags=["ledger"])
def update_financial_account(
    account_id: UUID,
    request: FinancialAccountUpdateRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> FinancialAccountResponse:
    account = household_account(db, membership.household_id, account_id)
    if request.name is not None:
        name = request.name.strip()
        duplicate = db.scalar(select(FinancialAccount).where(FinancialAccount.household_id == membership.household_id, FinancialAccount.name == name, FinancialAccount.id != account.id))
        if duplicate:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this name already exists")
        account.name = name
    for field in ("include_in_planner", "include_in_net_worth", "ownership_scope", "balance_nature", "liquidity", "tax_treatment", "institution", "masked_identifier", "is_archived"):
        value = getattr(request, field)
        if field in request.model_fields_set:
            setattr(account, field, value)
    validate_planner_eligibility(account)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.account_updated", resource_type="financial_account", resource_id=str(account.id), detail=json.dumps(request.model_dump(exclude_unset=True), sort_keys=True)))
    db.commit()
    return financial_account_response(db, account)


@router.delete("/ledger/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["ledger"])
def delete_empty_financial_account(
    account_id: UUID,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> Response:
    account = household_account(db, membership.household_id, account_id)
    references = (
        ("transactions", LedgerTransaction, LedgerTransaction.account_id),
        ("valuations", AccountValuation, AccountValuation.account_id),
        ("import sources", ImportSource, ImportSource.account_id),
        ("documents", Document, Document.account_id),
        ("debts", Debt, Debt.account_id),
        ("automation rules", CategoryRule, CategoryRule.account_id),
        ("financial goals", FinancialGoal, FinancialGoal.linked_account_id),
    )
    blockers = [
        label
        for label, model, field in references
        if db.scalar(select(func.count()).select_from(model).where(field == account.id))
    ]
    if account.opening_balance_minor != 0:
        blockers.insert(0, "an opening balance")
    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This account cannot be deleted because it has {', '.join(blockers)}. Archive it to preserve financial history.",
        )
    account_name = account.name
    db.delete(account)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.account_deleted", resource_type="financial_account", resource_id=str(account.id), detail=f"name:{account_name};empty_account:true"))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/ledger/accounts/{account_id}/valuations", response_model=AccountValuationResponse, status_code=201, tags=["ledger"])
def create_account_valuation(account_id: UUID, request: AccountValuationCreateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> AccountValuationResponse:
    account = household_account(db, membership.household_id, account_id)
    if account.currency_code != request.currency_code: raise HTTPException(status_code=422, detail="Valuation currency must match the account currency")
    existing = db.scalar(select(AccountValuation).where(AccountValuation.account_id == account.id, AccountValuation.valuation_date == request.valuation_date))
    if existing: raise HTTPException(status_code=409, detail="An account valuation already exists for this date")
    item = AccountValuation(household_id=membership.household_id, account_id=account.id, created_by_user_id=actor.id, **request.model_dump())
    db.add(item); db.flush(); db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.account_valuation_created", resource_type="account_valuation", resource_id=str(item.id), detail=f"account:{account.id};date:{item.valuation_date};value:{item.value_minor}"))
    response = AccountValuationResponse(valuation_id=item.id, account_id=account.id, **request.model_dump()); db.commit(); return response


@router.get("/ledger/net-worth", response_model=NetWorthResponse, tags=["ledger"])
def get_net_worth(db: DbSession, membership: Annotated[Membership, Depends(current_membership)], currency_code: str = "USD", as_of: date | None = None) -> NetWorthResponse:
    if currency_code not in {"USD", "CAD", "MXN"}: raise HTTPException(status_code=422, detail="Unsupported net-worth currency")
    effective = as_of or utc_now().date()
    accounts = db.scalars(select(FinancialAccount).where(FinancialAccount.household_id == membership.household_id, FinancialAccount.currency_code == currency_code, FinancialAccount.include_in_net_worth.is_(True), FinancialAccount.is_archived.is_(False)).order_by(FinancialAccount.ownership_scope, FinancialAccount.name)).all()
    rows = []
    for account in accounts:
        value, valuation = account_net_value(db, account, effective)
        rows.append(NetWorthAccountResponse(account_id=account.id, name=account.name, account_type=account.account_type, ownership_scope=account.ownership_scope, balance_nature=account.balance_nature, liquidity=account.liquidity, value_minor=value, currency_code=account.currency_code, valuation_as_of=valuation.valuation_date if valuation else None))
    assets = sum(max(row.value_minor, 0) for row in rows); liabilities = sum(abs(min(row.value_minor, 0)) for row in rows)
    household = sum(row.value_minor for row in rows if row.ownership_scope == "household"); business = sum(row.value_minor for row in rows if row.ownership_scope == "business")
    return NetWorthResponse(as_of=effective, currency_code=currency_code, asset_total_minor=assets, liability_total_minor=liabilities, net_worth_minor=assets-liabilities, household_net_worth_minor=household, business_net_worth_minor=business, accounts=rows)


@router.get("/ledger/accounts/{account_id}/balance", response_model=BalanceExplanationResponse, tags=["ledger"])
def explain_account_balance(
    account_id: UUID,
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
    as_of: date | None = None,
    include_pending: bool = True,
) -> BalanceExplanationResponse:
    account = household_account(db, membership.household_id, account_id)
    effective_date = as_of or utc_now().date()
    included = db.scalars(included_activity_query(account.id, effective_date, include_pending).order_by(LedgerTransaction.transaction_date, LedgerTransaction.created_at)).all()
    activity = sum(item.amount_minor for item in included)
    return BalanceExplanationResponse(account_id=account.id, account_name=account.name, currency_code=account.currency_code, as_of=effective_date, include_pending=include_pending, opening_balance_minor=account.opening_balance_minor, activity_minor=activity, balance_minor=account.opening_balance_minor + activity, included_transaction_ids=[item.id for item in included])


@router.get("/ledger/categories", response_model=list[CategoryResponse], tags=["ledger"])
def list_categories(
    db: DbSession, membership: Annotated[Membership, Depends(current_membership)]
) -> list[CategoryResponse]:
    items = db.scalars(select(Category).where(Category.household_id == membership.household_id).order_by(Category.category_type, Category.name)).all()
    return [CategoryResponse(category_id=item.id, name=item.name, category_type=item.category_type, is_system_default=item.is_system_default, is_archived=item.is_archived) for item in items]


@router.post("/ledger/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED, tags=["ledger"])
def create_category(
    request: CategoryCreateRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> CategoryResponse:
    duplicate = db.scalar(select(Category).where(Category.household_id == membership.household_id, Category.name == request.name.strip()))
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A category with this name already exists")
    category = Category(household_id=membership.household_id, name=request.name.strip(), category_type=request.category_type, is_system_default=False)
    db.add(category)
    db.flush()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.category_created", resource_type="category", resource_id=str(category.id)))
    db.commit()
    return CategoryResponse(category_id=category.id, name=category.name, category_type=category.category_type, is_system_default=category.is_system_default, is_archived=category.is_archived)


@router.patch("/ledger/categories/{category_id}", response_model=CategoryResponse, tags=["ledger"])
def update_category(
    category_id: UUID,
    request: CategoryUpdateRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> CategoryResponse:
    category = db.scalar(select(Category).where(Category.id == category_id, Category.household_id == membership.household_id))
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    if request.name is not None:
        name = request.name.strip()
        duplicate = db.scalar(select(Category).where(Category.household_id == membership.household_id, Category.name == name, Category.id != category.id))
        if duplicate:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A category with this name already exists")
        category.name = name
    if request.is_archived is not None:
        category.is_archived = request.is_archived
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.category_updated", resource_type="category", resource_id=str(category.id), detail=json.dumps(request.model_dump(exclude_unset=True), sort_keys=True)))
    db.commit()
    return CategoryResponse(category_id=category.id, name=category.name, category_type=category.category_type, is_system_default=category.is_system_default, is_archived=category.is_archived)


@router.get("/ledger/merchants", response_model=list[MerchantResponse], tags=["ledger"])
def list_merchants(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[MerchantResponse]:
    merchants = db.scalars(select(Merchant).where(Merchant.household_id == membership.household_id).order_by(Merchant.is_archived, Merchant.name)).all()
    return [merchant_response(db, item) for item in merchants]


def _payee_profile_id(household_id: UUID, payee_key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"tallystead:{household_id}:payee:{payee_key}")


def _merchant_profile_directory(db: DbSession, household_id: UUID) -> list[MerchantProfileSummaryResponse]:
    merchants = db.scalars(select(Merchant).where(Merchant.household_id == household_id)).all()
    aliases = db.scalars(select(MerchantAlias).where(MerchantAlias.household_id == household_id)).all()
    aliases_by_merchant: dict[UUID, list[str]] = {merchant.id: [] for merchant in merchants}
    merchant_by_payee = {merchant.name.strip().casefold(): merchant for merchant in merchants}
    for alias in aliases:
        aliases_by_merchant.setdefault(alias.merchant_id, []).append(alias.alias)
        merchant = next((item for item in merchants if item.id == alias.merchant_id), None)
        if merchant:
            merchant_by_payee[alias.alias.strip().casefold()] = merchant
    transactions = db.scalars(select(LedgerTransaction).where(
        LedgerTransaction.household_id == household_id,
        LedgerTransaction.voided_at.is_(None),
    )).all()
    transaction_ids = {transaction.id for transaction in transactions}
    transfer_ids: set[UUID] = set()
    if transaction_ids:
        for link in db.scalars(select(TransferLink).where(
            (TransferLink.from_transaction_id.in_(transaction_ids)) | (TransferLink.to_transaction_id.in_(transaction_ids))
        )):
            transfer_ids.update((link.from_transaction_id, link.to_transaction_id))
    normalized_counts: Counter[UUID] = Counter()
    raw_names: dict[str, Counter[str]] = {}
    for transaction in transactions:
        if transaction.id in transfer_ids or transaction.activity_type == "external_owned_transfer" or transaction.reversal_of_transaction_id or transaction.status == "reversed":
            continue
        payee = (transaction.payee or transaction.raw_payee or "").strip()
        payee_key = payee.casefold()
        linked = next((item for item in merchants if item.id == transaction.merchant_id), None) if transaction.merchant_id else None
        linked = linked or merchant_by_payee.get(payee_key)
        if linked:
            normalized_counts[linked.id] += 1
        elif payee_key:
            raw_names.setdefault(payee_key, Counter())[payee] += 1
    result = [
        MerchantProfileSummaryResponse(
            profile_id=merchant.id, merchant_id=merchant.id, name=merchant.name,
            aliases=sorted(aliases_by_merchant.get(merchant.id, [])), is_normalized=True,
            is_archived=merchant.is_archived, transaction_count=normalized_counts[merchant.id],
        )
        for merchant in merchants
    ]
    for payee_key, names in raw_names.items():
        name = names.most_common(1)[0][0]
        result.append(MerchantProfileSummaryResponse(
            profile_id=_payee_profile_id(household_id, payee_key), merchant_id=None, name=name,
            aliases=sorted(value for value in names if value != name), is_normalized=False,
            is_archived=False, transaction_count=sum(names.values()),
        ))
    return sorted(result, key=lambda item: (item.is_archived, item.name.casefold()))


@router.get("/ledger/merchant-profiles", response_model=list[MerchantProfileSummaryResponse], tags=["ledger"])
def list_merchant_profiles(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[MerchantProfileSummaryResponse]:
    return _merchant_profile_directory(db, membership.household_id)


def _merchant_profile_report(
    profile: MerchantProfileSummaryResponse, db: DbSession, household_id: UUID, start: date, end: date,
    currency_code: str, ownership_scope: str, include_pending: bool,
) -> MerchantProfileResponse:
    payee_keys = tuple({profile.name.strip().casefold(), *(alias.strip().casefold() for alias in profile.aliases)})
    report = spending_report(db, household_id, ReportFilters(
        date_from=start, date_to=end, currency_code=currency_code, ownership_scope=ownership_scope,
        include_pending=include_pending, merchant_id=profile.merchant_id,
        payee_key=None if profile.is_normalized else profile.name.strip().casefold(),
        merchant_payee_keys=payee_keys if profile.is_normalized else (),
    ))
    rows = report["transactions"]
    purchases = [item for item in rows if item["classification"] in {"spending", "debt_payment"}]
    refunds = [item for item in rows if item["classification"] == "refund"]
    dates = [date.fromisoformat(item["transaction_date"]) for item in rows]
    return MerchantProfileResponse(
        merchant=profile, rule_version=report["rule_version"], date_from=start, date_to=end,
        currency_code=currency_code, totals=report["totals"], transaction_count=len(rows),
        purchase_count=len(purchases), refund_count=len(refunds),
        average_purchase_minor=(sum(item["report_amount_minor"] for item in purchases) // len(purchases)) if purchases else 0,
        first_transaction_date=min(dates) if dates else None, last_transaction_date=max(dates) if dates else None,
        monthly_spending=report["monthly_spending"], by_category=report["by_category"],
        by_account=report["by_account"], transactions=list(reversed(rows)), warnings=report["signals"]["warnings"],
    )


@router.get("/ledger/merchant-profiles/{profile_id}", response_model=MerchantProfileResponse, tags=["ledger"])
def calculated_merchant_profile(
    profile_id: UUID, db: DbSession, membership: Annotated[Membership, Depends(current_membership)],
    date_from: date | None = None, date_to: date | None = None,
    currency_code: Annotated[str, Query(pattern="^(USD|CAD|MXN)$")] = "USD",
    ownership_scope: Annotated[str, Query(pattern="^(household|business|all)$")] = "household",
    include_pending: bool = False,
) -> MerchantProfileResponse:
    profile = next((item for item in _merchant_profile_directory(db, membership.household_id) if item.profile_id == profile_id), None)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchant profile not found")
    end = date_to or datetime.now(UTC).date()
    start = date_from or (end - timedelta(days=364))
    if start > end:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date_from must be on or before date_to")
    return _merchant_profile_report(profile, db, membership.household_id, start, end, currency_code, ownership_scope, include_pending)


@router.get("/ledger/merchants/{merchant_id}/profile", response_model=MerchantProfileResponse, tags=["ledger"])
def merchant_profile(
    merchant_id: UUID,
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
    date_from: date | None = None,
    date_to: date | None = None,
    currency_code: Annotated[str, Query(pattern="^(USD|CAD|MXN)$")] = "USD",
    ownership_scope: Annotated[str, Query(pattern="^(household|business|all)$")] = "household",
    include_pending: bool = False,
) -> MerchantProfileResponse:
    merchant = household_merchant(db, membership.household_id, merchant_id)
    end = date_to or datetime.now(UTC).date()
    start = date_from or (end - timedelta(days=364))
    if start > end:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date_from must be on or before date_to")
    normalized = merchant_response(db, merchant)
    profile = MerchantProfileSummaryResponse(
        profile_id=merchant.id, merchant_id=merchant.id, name=merchant.name, aliases=normalized.aliases,
        is_normalized=True, is_archived=merchant.is_archived, transaction_count=0,
    )
    return _merchant_profile_report(profile, db, membership.household_id, start, end, currency_code, ownership_scope, include_pending)


@router.post("/ledger/merchants", response_model=MerchantResponse, status_code=status.HTTP_201_CREATED, tags=["ledger"])
def create_merchant(
    request: MerchantCreateRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> MerchantResponse:
    name = request.name.strip()
    if db.scalar(select(Merchant).where(Merchant.household_id == membership.household_id, Merchant.name == name)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A merchant with this name already exists")
    aliases = {item.strip() for item in request.aliases if item.strip()}
    existing_alias = db.scalar(select(MerchantAlias).where(MerchantAlias.household_id == membership.household_id, MerchantAlias.alias.in_(aliases))) if aliases else None
    if existing_alias:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A merchant alias is already in use")
    merchant = Merchant(household_id=membership.household_id, name=name)
    db.add(merchant)
    db.flush()
    db.add_all([MerchantAlias(household_id=membership.household_id, merchant_id=merchant.id, alias=alias) for alias in sorted(aliases)])
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.merchant_created", resource_type="merchant", resource_id=str(merchant.id)))
    db.flush()
    response = merchant_response(db, merchant)
    db.commit()
    return response


@router.patch("/ledger/merchants/{merchant_id}", response_model=MerchantResponse, tags=["ledger"])
def update_merchant(
    merchant_id: UUID,
    request: MerchantUpdateRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> MerchantResponse:
    merchant = household_merchant(db, membership.household_id, merchant_id)
    if request.name is not None:
        name = request.name.strip()
        duplicate = db.scalar(select(Merchant).where(Merchant.household_id == membership.household_id, Merchant.name == name, Merchant.id != merchant.id))
        if duplicate:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A merchant with this name already exists")
        merchant.name = name
    if request.is_archived is not None:
        merchant.is_archived = request.is_archived
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.merchant_updated", resource_type="merchant", resource_id=str(merchant.id), detail=json.dumps(request.model_dump(exclude_unset=True), sort_keys=True)))
    db.commit()
    return merchant_response(db, merchant)


@router.get("/ledger/transactions", response_model=list[LedgerTransactionResponse], tags=["ledger"])
def list_ledger_transactions(
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
    account_id: UUID | None = None,
) -> list[LedgerTransactionResponse]:
    query = select(LedgerTransaction).where(LedgerTransaction.household_id == membership.household_id)
    if account_id:
        household_account(db, membership.household_id, account_id)
        query = query.where(LedgerTransaction.account_id == account_id)
    transactions = db.scalars(query.order_by(LedgerTransaction.transaction_date.desc(), LedgerTransaction.created_at.desc()).limit(500)).all()
    return [transaction_response(db, item) for item in transactions]


@router.get("/ledger/transactions/page", response_model=LedgerTransactionPageResponse, tags=["ledger"])
def page_ledger_transactions(
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=10, le=100)] = 25,
    search: Annotated[str | None, Query(max_length=200)] = None,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    transaction_status: Annotated[str | None, Query(pattern="^(pending|posted|voided)$")] = None,
    direction: Annotated[str | None, Query(pattern="^(inflow|outflow)$")] = None,
    currency_code: Annotated[str | None, Query(min_length=3, max_length=3)] = None,
    amount_minor: int | None = None,
    exclude_account_id: UUID | None = None,
    exclude_transaction_id: UUID | None = None,
    has_transfer: bool | None = None,
    has_splits: bool | None = None,
    reconciled: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> LedgerTransactionPageResponse:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Start date cannot be after end date")
    query = select(LedgerTransaction).where(LedgerTransaction.household_id == membership.household_id)
    if account_id:
        household_account(db, membership.household_id, account_id)
        query = query.where(LedgerTransaction.account_id == account_id)
    if category_id:
        category = db.get(Category, category_id)
        if not category or category.household_id != membership.household_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
        query = query.where(
            LedgerTransaction.id.in_(select(TransactionSplit.transaction_id).where(TransactionSplit.category_id == category_id))
        )
    if transaction_status:
        query = query.where(LedgerTransaction.status == transaction_status)
    if direction == "inflow":
        query = query.where(LedgerTransaction.amount_minor > 0)
    elif direction == "outflow":
        query = query.where(LedgerTransaction.amount_minor < 0)
    if currency_code:
        query = query.where(LedgerTransaction.currency_code == currency_code.upper())
    if amount_minor is not None:
        query = query.where(LedgerTransaction.amount_minor == amount_minor)
    if exclude_account_id:
        household_account(db, membership.household_id, exclude_account_id)
        query = query.where(LedgerTransaction.account_id != exclude_account_id)
    if exclude_transaction_id:
        query = query.where(LedgerTransaction.id != exclude_transaction_id)
    transfer_exists = select(TransferLink.id).where(
        (TransferLink.from_transaction_id == LedgerTransaction.id) | (TransferLink.to_transaction_id == LedgerTransaction.id)
    ).exists()
    if has_transfer is True:
        query = query.where(transfer_exists)
    elif has_transfer is False:
        query = query.where(~transfer_exists)
    split_exists = select(TransactionSplit.id).where(TransactionSplit.transaction_id == LedgerTransaction.id).exists()
    if has_splits is True:
        query = query.where(split_exists)
    elif has_splits is False:
        query = query.where(~split_exists)
    if reconciled is True:
        query = query.where(LedgerTransaction.reconciled_at.is_not(None))
    elif reconciled is False:
        query = query.where(LedgerTransaction.reconciled_at.is_(None))
    if date_from:
        query = query.where(LedgerTransaction.transaction_date >= date_from)
    if date_to:
        query = query.where(LedgerTransaction.transaction_date <= date_to)
    term = (search or "").strip().casefold()
    if term:
        pattern = f"%{term}%"
        account_matches = select(FinancialAccount.id).where(
            FinancialAccount.household_id == membership.household_id,
            func.lower(FinancialAccount.name).like(pattern),
        )
        merchant_matches = select(Merchant.id).where(
            Merchant.household_id == membership.household_id,
            func.lower(Merchant.name).like(pattern),
        )
        category_transactions = select(TransactionSplit.transaction_id).join(Category, Category.id == TransactionSplit.category_id).where(
            Category.household_id == membership.household_id,
            func.lower(Category.name).like(pattern),
        )
        query = query.where(
            or_(
                func.lower(func.coalesce(LedgerTransaction.payee, "")).like(pattern),
                func.lower(func.coalesce(LedgerTransaction.raw_payee, "")).like(pattern),
                func.lower(func.coalesce(LedgerTransaction.memo, "")).like(pattern),
                func.lower(func.coalesce(LedgerTransaction.source_type, "")).like(pattern),
                LedgerTransaction.account_id.in_(account_matches),
                LedgerTransaction.merchant_id.in_(merchant_matches),
                LedgerTransaction.id.in_(category_transactions),
            )
        )
    total_items = int(db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    effective_page = min(page, total_pages)
    transactions = db.scalars(
        query.order_by(LedgerTransaction.transaction_date.desc(), LedgerTransaction.created_at.desc(), LedgerTransaction.id.desc())
        .offset((effective_page - 1) * page_size)
        .limit(page_size)
    ).all()
    return LedgerTransactionPageResponse(
        items=[transaction_response(db, item) for item in transactions],
        page=effective_page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
    )


@router.post("/ledger/transactions", response_model=LedgerTransactionResponse, status_code=status.HTTP_201_CREATED, tags=["ledger"])
def create_ledger_transaction(
    request: LedgerTransactionCreateRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> LedgerTransactionResponse:
    account = household_account(db, membership.household_id, request.account_id)
    if account.is_archived:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Archived accounts cannot receive transactions")
    if account.currency_code != request.currency_code:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Transaction currency must match the account currency")
    if request.merchant_id is not None:
        merchant = household_merchant(db, membership.household_id, request.merchant_id)
        if merchant.is_archived:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Archived merchants cannot be assigned")
    validate_splits(db, membership.household_id, request.amount_minor, request.splits)
    transaction = LedgerTransaction(household_id=membership.household_id, account_id=account.id, merchant_id=request.merchant_id, created_by_user_id=actor.id, transaction_date=request.transaction_date, amount_minor=request.amount_minor, currency_code=request.currency_code, status=request.status, payee=request.payee, raw_payee=request.payee, memo=request.memo, source_type="manual", activity_type=request.activity_type)
    db.add(transaction)
    db.flush()
    db.add_all([TransactionSplit(transaction_id=transaction.id, category_id=item.category_id, amount_minor=item.amount_minor, memo=item.memo) for item in request.splits])
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.transaction_created", resource_type="ledger_transaction", resource_id=str(transaction.id), detail=f"manual:{request.status}"))
    db.flush()
    response = transaction_response(db, transaction)
    db.commit()
    return response


@router.get("/ledger/transactions/{transaction_id}", response_model=TransactionDetailResponse, tags=["ledger"])
def get_transaction_detail(
    transaction_id: UUID,
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
) -> TransactionDetailResponse:
    transaction = household_transaction(db, membership.household_id, transaction_id)
    revisions = db.scalars(select(TransactionRevision).where(TransactionRevision.transaction_id == transaction.id).order_by(TransactionRevision.created_at.desc())).all()
    return TransactionDetailResponse(transaction=transaction_response(db, transaction), revisions=[TransactionRevisionResponse(revision_id=item.id, reason=item.reason, before_snapshot=json.loads(item.before_snapshot), created_at=item.created_at.isoformat()) for item in revisions])


@router.patch("/ledger/transactions/{transaction_id}", response_model=LedgerTransactionResponse, tags=["ledger"])
def update_ledger_transaction(
    transaction_id: UUID,
    request: TransactionUpdateRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> LedgerTransactionResponse:
    transaction = household_transaction(db, membership.household_id, transaction_id)
    if transaction.reconciled_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unreconcile this transaction before correcting it")
    if transaction.status in {"voided", "reversed"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Voided or reversed transactions cannot be edited")
    fields = request.model_fields_set
    if "status" in fields and request.status != transaction.status and (transaction.status != "pending" or request.status not in {"posted", "voided"}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invalid transaction status transition")
    if "merchant_id" in fields and request.merchant_id is not None:
        merchant = household_merchant(db, membership.household_id, request.merchant_id)
        if merchant.is_archived:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Archived merchants cannot be assigned")
    if request.splits is not None:
        validate_splits(db, membership.household_id, transaction.amount_minor, request.splits)
    before = transaction_snapshot(db, transaction)
    if "payee" in fields:
        transaction.payee = request.payee
    if "merchant_id" in fields:
        transaction.merchant_id = request.merchant_id
    if "memo" in fields:
        transaction.memo = request.memo
    if "status" in fields and request.status is not None:
        transaction.status = request.status
        if request.status == "voided":
            transaction.voided_at = utc_now()
            transaction.voided_by_user_id = actor.id
    if request.splits is not None:
        db.execute(delete(TransactionSplit).where(TransactionSplit.transaction_id == transaction.id))
        db.add_all([TransactionSplit(transaction_id=transaction.id, category_id=item.category_id, amount_minor=item.amount_minor, memo=item.memo) for item in request.splits])
    db.add(TransactionRevision(household_id=membership.household_id, transaction_id=transaction.id, actor_user_id=actor.id, reason=request.reason, before_snapshot=before))
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.transaction_updated", resource_type="ledger_transaction", resource_id=str(transaction.id), detail=request.reason))
    db.flush()
    response = transaction_response(db, transaction)
    db.commit()
    return response


def reverse_transaction_event(
    db: DbSession,
    transaction: LedgerTransaction,
    actor: User,
    membership: Membership,
    transaction_date: date,
    reason: str,
    action: str,
) -> LedgerTransaction:
    if transaction.status != "posted":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only posted transactions can be reversed")
    if transaction.reconciled_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unreconcile this transaction before reversing it")
    existing = db.scalar(select(LedgerTransaction).where(LedgerTransaction.reversal_of_transaction_id == transaction.id))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Transaction has already been reversed")
    before = transaction_snapshot(db, transaction)
    reversal = LedgerTransaction(household_id=membership.household_id, account_id=transaction.account_id, merchant_id=transaction.merchant_id, created_by_user_id=actor.id, transaction_date=transaction_date, amount_minor=-transaction.amount_minor, currency_code=transaction.currency_code, status="posted", payee=transaction.payee, raw_payee=transaction.raw_payee, memo=reason, source_type="reversal", source_reference=str(transaction.id), reversal_of_transaction_id=transaction.id, activity_type=transaction.activity_type)
    db.add(reversal)
    db.flush()
    original_splits = db.scalars(select(TransactionSplit).where(TransactionSplit.transaction_id == transaction.id)).all()
    db.add_all([TransactionSplit(transaction_id=reversal.id, category_id=item.category_id, amount_minor=-item.amount_minor, memo=item.memo) for item in original_splits])
    transaction.status = "reversed"
    db.add(TransactionRevision(household_id=membership.household_id, transaction_id=transaction.id, actor_user_id=actor.id, reason=reason, before_snapshot=before))
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=action, resource_type="ledger_transaction", resource_id=str(transaction.id), detail=f"reversal:{reversal.id}:{reason}"))
    db.flush()
    return reversal


@router.post("/ledger/transactions/{transaction_id}/reverse", response_model=LedgerTransactionResponse, status_code=status.HTTP_201_CREATED, tags=["ledger"])
def reverse_ledger_transaction(
    transaction_id: UUID,
    request: TransactionReverseRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> LedgerTransactionResponse:
    transaction = household_transaction(db, membership.household_id, transaction_id)
    reversal = reverse_transaction_event(db, transaction, actor, membership, request.transaction_date, request.reason, "ledger.transaction_reversed")
    response = transaction_response(db, reversal)
    db.commit()
    return response


@router.post("/ledger/transactions/{transaction_id}/correct", response_model=LedgerTransactionResponse, status_code=status.HTTP_201_CREATED, tags=["ledger"])
def correct_ledger_transaction(
    transaction_id: UUID,
    request: TransactionCorrectionRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> LedgerTransactionResponse:
    original = household_transaction(db, membership.household_id, transaction_id)
    if request.status != "posted":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A replacement for a posted transaction must also be posted")
    account = household_account(db, membership.household_id, request.account_id)
    if account.currency_code != request.currency_code:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Replacement currency must match the account currency")
    if request.merchant_id is not None:
        household_merchant(db, membership.household_id, request.merchant_id)
    validate_splits(db, membership.household_id, request.amount_minor, request.splits)
    reverse_transaction_event(db, original, actor, membership, request.transaction_date, request.reason, "ledger.transaction_corrected")
    replacement = LedgerTransaction(household_id=membership.household_id, account_id=account.id, merchant_id=request.merchant_id, created_by_user_id=actor.id, transaction_date=request.transaction_date, amount_minor=request.amount_minor, currency_code=request.currency_code, status="posted", payee=request.payee, raw_payee=original.raw_payee, memo=request.memo, source_type="correction", source_reference=str(original.id), corrected_from_transaction_id=original.id, activity_type=request.activity_type)
    db.add(replacement)
    db.flush()
    db.add_all([TransactionSplit(transaction_id=replacement.id, category_id=item.category_id, amount_minor=item.amount_minor, memo=item.memo) for item in request.splits])
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.correction_replacement_created", resource_type="ledger_transaction", resource_id=str(replacement.id), detail=f"corrected_from:{original.id}"))
    db.flush()
    response = transaction_response(db, replacement)
    db.commit()
    return response


@router.put("/ledger/transactions/{transaction_id}/reconciliation", response_model=LedgerTransactionResponse, tags=["ledger"])
def reconcile_ledger_transaction(
    transaction_id: UUID,
    request: TransactionReconcileRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> LedgerTransactionResponse:
    transaction = household_transaction(db, membership.household_id, transaction_id)
    if transaction.status not in {"posted", "reversed"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only posted ledger events can be reconciled")
    before = transaction_snapshot(db, transaction)
    transaction.reconciled_at = utc_now() if request.reconciled else None
    transaction.reconciled_by_user_id = actor.id if request.reconciled else None
    action = "ledger.transaction_reconciled" if request.reconciled else "ledger.transaction_unreconciled"
    db.add(TransactionRevision(household_id=membership.household_id, transaction_id=transaction.id, actor_user_id=actor.id, reason=action, before_snapshot=before))
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=action, resource_type="ledger_transaction", resource_id=str(transaction.id)))
    db.flush()
    response = transaction_response(db, transaction)
    db.commit()
    return response


@router.post("/ledger/transfers", response_model=TransferResponse, status_code=status.HTTP_201_CREATED, tags=["ledger"])
def create_transfer(
    request: TransferCreateRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> TransferResponse:
    if request.from_account_id == request.to_account_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Transfer accounts must be different")
    source = household_account(db, membership.household_id, request.from_account_id)
    destination = household_account(db, membership.household_id, request.to_account_id)
    if source.is_archived or destination.is_archived:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Archived accounts cannot be used for transfers")
    if source.currency_code != request.currency_code or destination.currency_code != request.currency_code:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Both accounts must use the transfer currency")
    outgoing = LedgerTransaction(household_id=membership.household_id, account_id=source.id, created_by_user_id=actor.id, transaction_date=request.transaction_date, amount_minor=-request.amount_minor, currency_code=request.currency_code, status=request.status, payee=destination.name, raw_payee=destination.name, memo=request.memo, source_type="transfer")
    incoming = LedgerTransaction(household_id=membership.household_id, account_id=destination.id, created_by_user_id=actor.id, transaction_date=request.transaction_date, amount_minor=request.amount_minor, currency_code=request.currency_code, status=request.status, payee=source.name, raw_payee=source.name, memo=request.memo, source_type="transfer")
    db.add_all([outgoing, incoming])
    db.flush()
    link = TransferLink(household_id=membership.household_id, from_transaction_id=outgoing.id, to_transaction_id=incoming.id, created_by_user_id=actor.id)
    db.add(link)
    db.flush()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.transfer_created", resource_type="transfer", resource_id=str(link.id), detail=f"{source.id}->{destination.id}:{request.amount_minor} {request.currency_code}"))
    response = TransferResponse(transfer_id=link.id, from_transaction=transaction_response(db, outgoing), to_transaction=transaction_response(db, incoming))
    db.commit()
    return response


@router.post("/ledger/export", tags=["ledger"])
def export_household_ledger(
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> Response:
    household = db.get(Household, membership.household_id)
    accounts = db.scalars(select(FinancialAccount).where(FinancialAccount.household_id == membership.household_id).order_by(FinancialAccount.name)).all()
    categories = db.scalars(select(Category).where(Category.household_id == membership.household_id).order_by(Category.category_type, Category.name)).all()
    merchants = db.scalars(select(Merchant).where(Merchant.household_id == membership.household_id).order_by(Merchant.name)).all()
    transactions = db.scalars(select(LedgerTransaction).where(LedgerTransaction.household_id == membership.household_id).order_by(LedgerTransaction.transaction_date, LedgerTransaction.created_at)).all()
    transfers = db.scalars(select(TransferLink).where(TransferLink.household_id == membership.household_id).order_by(TransferLink.created_at)).all()
    revisions = db.scalars(select(TransactionRevision).where(TransactionRevision.household_id == membership.household_id).order_by(TransactionRevision.created_at)).all()
    valuations = db.scalars(select(AccountValuation).where(AccountValuation.household_id == membership.household_id).order_by(AccountValuation.valuation_date)).all()
    planner_snapshots = db.scalars(select(PlannerSnapshot).where(PlannerSnapshot.household_id == membership.household_id).order_by(PlannerSnapshot.created_at)).all()
    ledger_audit = db.scalars(select(AuditEvent).where(AuditEvent.household_id == membership.household_id, AuditEvent.action.like("ledger.%")).order_by(AuditEvent.created_at)).all()
    payload = {
        "format": "tallystead-ledger-export-v1",
        "exported_at": utc_now().isoformat(),
        "household": {"household_id": str(membership.household_id), "name": household.name if household else "Household"},
        "accounts": [{"account_id": str(item.id), "name": item.name, "account_type": item.account_type, "currency_code": item.currency_code, "opening_balance_minor": item.opening_balance_minor, "include_in_planner": item.include_in_planner, "include_in_net_worth": item.include_in_net_worth, "ownership_scope": item.ownership_scope, "balance_nature": item.balance_nature, "liquidity": item.liquidity, "tax_treatment": item.tax_treatment, "institution": item.institution, "masked_identifier": item.masked_identifier, "is_archived": item.is_archived} for item in accounts],
        "account_valuations": [{"valuation_id": str(item.id), "account_id": str(item.account_id), "valuation_date": item.valuation_date.isoformat(), "value_minor": item.value_minor, "currency_code": item.currency_code, "source_type": item.source_type, "note": item.note} for item in valuations],
        "planner_snapshots": [{"snapshot_id": str(item.id), "rule_version": item.rule_version, "currency_code": item.currency_code, "as_of_date": item.as_of_date.isoformat(), "horizon_date": item.horizon_date.isoformat(), "input_hash": item.input_hash, "input": json.loads(item.input_json), "output": json.loads(item.output_json), "created_at": item.created_at.isoformat()} for item in planner_snapshots],
        "categories": [{"category_id": str(item.id), "name": item.name, "category_type": item.category_type, "is_system_default": item.is_system_default, "is_archived": item.is_archived} for item in categories],
        "merchants": [merchant_response(db, item).model_dump(mode="json") for item in merchants],
        "transactions": [transaction_response(db, item).model_dump(mode="json") for item in transactions],
        "transfers": [{"transfer_id": str(item.id), "from_transaction_id": str(item.from_transaction_id), "to_transaction_id": str(item.to_transaction_id), "created_at": item.created_at.isoformat()} for item in transfers],
        "revisions": [{"revision_id": str(item.id), "transaction_id": str(item.transaction_id), "reason": item.reason, "before_snapshot": json.loads(item.before_snapshot), "created_at": item.created_at.isoformat()} for item in revisions],
        "audit_events": [{"action": item.action, "resource_type": item.resource_type, "resource_id": item.resource_id, "detail": item.detail, "created_at": item.created_at.isoformat()} for item in ledger_audit],
    }
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="ledger.export_created", resource_type="household", resource_id=str(membership.household_id), detail="tallystead-ledger-export-v1; credentials excluded"))
    db.commit()
    filename = f"tallystead-ledger-{utc_now().date().isoformat()}.json"
    return Response(content=json.dumps(payload, sort_keys=True), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def report_filters(
    date_from: date,
    date_to: date,
    currency_code: str,
    ownership_scope: str,
    include_pending: bool,
    account_id: UUID | None,
    category_id: UUID | None,
    merchant_id: UUID | None,
) -> ReportFilters:
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="Report end date must be on or after its start date")
    if (date_to - date_from).days > 3660:
        raise HTTPException(status_code=422, detail="Report period cannot exceed ten years")
    if currency_code not in {"USD", "CAD", "MXN"}:
        raise HTTPException(status_code=422, detail="Unsupported report currency")
    if ownership_scope not in {"household", "business", "all"}:
        raise HTTPException(status_code=422, detail="Ownership scope must be household, business, or all")
    return ReportFilters(date_from, date_to, currency_code, ownership_scope, include_pending, account_id, category_id, merchant_id)


@router.get("/reports/spending", response_model=SpendingReportResponse, tags=["reports"])
def get_spending_report(
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
    date_from: date,
    date_to: date,
    currency_code: str = "USD",
    ownership_scope: str = "household",
    include_pending: bool = False,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    merchant_id: UUID | None = None,
) -> SpendingReportResponse:
    filters = report_filters(date_from, date_to, currency_code, ownership_scope, include_pending, account_id, category_id, merchant_id)
    return SpendingReportResponse(**spending_report(db, membership.household_id, filters))


def report_csv_content(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Tallystead spending report", report["rule_version"]])
    writer.writerow(["Date from", report["filters"]["date_from"]])
    writer.writerow(["Date to", report["filters"]["date_to"]])
    writer.writerow(["Currency", report["filters"]["currency_code"]])
    writer.writerow(["Ownership scope", report["filters"]["ownership_scope"]])
    writer.writerow([])
    writer.writerow(["Summary", "Minor units"])
    for key, value in report["totals"].items():
        writer.writerow([key, value])
    writer.writerow([])
    writer.writerow(["Date", "Classification", "Account", "Payee", "Merchant", "Categories", "Ledger amount minor", "Report amount minor", "Currency", "Status", "Transaction ID"])
    for item in report["transactions"]:
        writer.writerow([item["transaction_date"], item["classification"], item["account_name"], item["payee"], item["merchant_name"], " | ".join(category["name"] for category in item["categories"]), item["amount_minor"], item["report_amount_minor"], item["currency_code"], item["status"], item["transaction_id"]])
    return output.getvalue()


@router.get("/reports/spending.csv", tags=["reports"])
def export_spending_report_csv(
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
    date_from: date,
    date_to: date,
    currency_code: str = "USD",
    ownership_scope: str = "household",
    include_pending: bool = False,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    merchant_id: UUID | None = None,
) -> Response:
    filters = report_filters(date_from, date_to, currency_code, ownership_scope, include_pending, account_id, category_id, merchant_id)
    report = spending_report(db, membership.household_id, filters)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="report.csv_exported", resource_type="report", resource_id="spending", detail=json.dumps(report["filters"], sort_keys=True)))
    db.commit()
    return Response(content=report_csv_content(report), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="tallystead-spending-{date_from}-{date_to}.csv"', "Cache-Control": "private, no-store"})


@router.get("/reports/spending/print", tags=["reports"])
def printable_spending_report(
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
    date_from: date,
    date_to: date,
    currency_code: str = "USD",
    ownership_scope: str = "household",
    include_pending: bool = False,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    merchant_id: UUID | None = None,
) -> Response:
    filters = report_filters(date_from, date_to, currency_code, ownership_scope, include_pending, account_id, category_id, merchant_id)
    report = spending_report(db, membership.household_id, filters)
    household = db.get(Household, membership.household_id)
    rows = "".join(f"<tr><td>{html.escape(item['transaction_date'])}</td><td>{html.escape(item['classification'].replace('_', ' ').title())}</td><td>{html.escape(item['payee'] or '—')}</td><td>{item['report_amount_minor'] / 100:,.2f} {html.escape(item['currency_code'])}</td></tr>" for item in report["transactions"])
    warnings = "".join(f"<li>{html.escape(item)}</li>" for item in report["signals"]["warnings"])
    content = f"""<!doctype html><html><head><meta charset='utf-8'><title>Tallystead spending report</title><style>body{{font:14px system-ui;margin:40px;color:#172131}}h1{{margin-bottom:4px}}.meta{{color:#667085}}.metrics{{display:flex;gap:24px;margin:24px 0}}.metric{{border:1px solid #d8dee8;padding:16px;min-width:160px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}@media print{{button{{display:none}}body{{margin:12mm}}}}</style></head><body><button onclick='window.print()'>Print or save as PDF</button><h1>{html.escape(household.name if household else 'Tallystead')} spending report</h1><p class='meta'>{date_from} through {date_to} · {currency_code} · {html.escape(ownership_scope)} · {report['rule_version']}</p><div class='metrics'><div class='metric'>Spending<br><b>{report['totals']['spending_minor']/100:,.2f} {currency_code}</b></div><div class='metric'>Income<br><b>{report['totals']['income_minor']/100:,.2f} {currency_code}</b></div><div class='metric'>Net cash flow<br><b>{report['totals']['net_cash_flow_minor']/100:,.2f} {currency_code}</b></div></div><ul>{warnings}</ul><table><thead><tr><th>Date</th><th>Classification</th><th>Payee</th><th>Report amount</th></tr></thead><tbody>{rows}</tbody></table></body></html>"""
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="report.print_opened", resource_type="report", resource_id="spending", detail=json.dumps(report["filters"], sort_keys=True)))
    db.commit()
    return Response(content=content, media_type="text/html", headers={"Cache-Control": "private, no-store"})


def report_preset_response(item: ReportPreset) -> ReportPresetResponse:
    return ReportPresetResponse(preset_id=item.id, name=item.name, report_type=item.report_type, filters=json.loads(item.filters_json), created_at=item.created_at.isoformat(), updated_at=item.updated_at.isoformat())


@router.get("/reports/presets", response_model=list[ReportPresetResponse], tags=["reports"])
def list_report_presets(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[ReportPresetResponse]:
    items = db.scalars(select(ReportPreset).where(ReportPreset.household_id == membership.household_id).order_by(ReportPreset.name)).all()
    return [report_preset_response(item) for item in items]


@router.post("/reports/presets", response_model=ReportPresetResponse, status_code=201, tags=["reports"])
def create_report_preset(request: ReportPresetCreateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> ReportPresetResponse:
    filters_json = json.dumps(request.filters, sort_keys=True)
    if len(filters_json) > 5000:
        raise HTTPException(status_code=422, detail="Saved report filters are too large")
    existing = db.scalar(select(ReportPreset).where(ReportPreset.household_id == membership.household_id, ReportPreset.name == request.name.strip()))
    if existing:
        existing.report_type = request.report_type
        existing.filters_json = filters_json
        existing.updated_at = utc_now()
        item = existing
    else:
        item = ReportPreset(household_id=membership.household_id, created_by_user_id=actor.id, name=request.name.strip(), report_type=request.report_type, filters_json=filters_json)
        db.add(item)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="report.preset_saved", resource_type="report_preset", resource_id=str(item.id), detail=request.report_type))
    db.flush()
    response = report_preset_response(item)
    db.commit()
    return response


@router.delete("/reports/presets/{preset_id}", status_code=204, tags=["reports"])
def delete_report_preset(preset_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> Response:
    item = db.scalar(select(ReportPreset).where(ReportPreset.id == preset_id, ReportPreset.household_id == membership.household_id))
    if not item:
        raise HTTPException(status_code=404, detail="Saved report not found")
    db.delete(item)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="report.preset_deleted", resource_type="report_preset", resource_id=str(item.id)))
    db.commit()
    return Response(status_code=204)


def bill_profile_response(item: BillProfile) -> BillProfileResponse:
    return BillProfileResponse(bill_profile_id=item.id, name=item.name, payee=item.payee, cadence=item.cadence, next_due_date=item.next_due_date, due_day=item.due_day, expected_amount_minor=item.expected_amount_minor, minimum_amount_minor=item.minimum_amount_minor, maximum_amount_minor=item.maximum_amount_minor, currency_code=item.currency_code, is_essential=item.is_essential, priority=item.priority, is_active=item.is_active)


def income_source_response(item: IncomeSource) -> IncomeSourceResponse:
    return IncomeSourceResponse(income_source_id=item.id, name=item.name, payer=item.payer, cadence=item.cadence, next_expected_date=item.next_expected_date, expected_amount_minor=item.expected_amount_minor, minimum_amount_minor=item.minimum_amount_minor, maximum_amount_minor=item.maximum_amount_minor, currency_code=item.currency_code, confidence_percent=item.confidence_percent, is_active=item.is_active)


def debt_response(item: Debt) -> DebtResponse:
    return DebtResponse(debt_id=item.id, name=item.name, lender=item.lender, account_id=item.account_id, balance_minor=item.balance_minor, balance_anchor_minor=item.balance_anchor_minor if item.balance_anchor_minor is not None else item.balance_minor, balance_as_of_date=item.balance_as_of_date, apr_basis_points=item.apr_basis_points, minimum_payment_minor=item.minimum_payment_minor, due_day=item.due_day, next_due_date=item.next_due_date, currency_code=item.currency_code, is_active=item.is_active)


@obligations_router.get("/obligations/bill-profiles", response_model=list[BillProfileResponse], tags=["obligations"])
def list_bill_profiles(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[BillProfileResponse]:
    return [bill_profile_response(item) for item in db.scalars(select(BillProfile).where(BillProfile.household_id == membership.household_id).order_by(BillProfile.name)).all()]


@obligations_router.post("/obligations/bill-profiles", response_model=BillProfileResponse, status_code=201, tags=["obligations"])
def create_bill_profile(request: BillProfileCreateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> BillProfileResponse:
    validate_range(request.expected_amount_minor, request.minimum_amount_minor, request.maximum_amount_minor)
    if db.scalar(select(BillProfile).where(BillProfile.household_id == membership.household_id, BillProfile.name == request.name.strip())):
        raise HTTPException(status_code=409, detail="A bill profile with this name already exists")
    item = BillProfile(household_id=membership.household_id, name=request.name.strip(), payee=request.payee, cadence=request.cadence, next_due_date=request.next_due_date, due_day=request.next_due_date.day if request.cadence in {"monthly", "quarterly", "yearly"} else None, expected_amount_minor=request.expected_amount_minor, minimum_amount_minor=request.minimum_amount_minor, maximum_amount_minor=request.maximum_amount_minor, currency_code=request.currency_code, is_essential=request.is_essential, priority=request.priority)
    db.add(item); db.flush()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.bill_profile_created", resource_type="bill_profile", resource_id=str(item.id)))
    response = bill_profile_response(item); db.commit(); return response


@obligations_router.patch("/obligations/bill-profiles/{profile_id}", response_model=BillProfileResponse, tags=["obligations"])
def update_bill_profile(profile_id: UUID, request: BillProfileUpdateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> BillProfileResponse:
    item = db.scalar(select(BillProfile).where(BillProfile.id == profile_id, BillProfile.household_id == membership.household_id))
    if not item: raise HTTPException(status_code=404, detail="Bill profile not found")
    changes = request.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        duplicate = db.scalar(select(BillProfile).where(BillProfile.household_id == membership.household_id, BillProfile.name == changes["name"], BillProfile.id != item.id))
        if duplicate: raise HTTPException(status_code=409, detail="A bill profile with this name already exists")
    expected = changes.get("expected_amount_minor", item.expected_amount_minor)
    minimum = changes.get("minimum_amount_minor", item.minimum_amount_minor)
    maximum = changes.get("maximum_amount_minor", item.maximum_amount_minor)
    validate_range(expected, minimum, maximum)
    for field, value in changes.items(): setattr(item, field, value)
    if "next_due_date" in changes and item.next_due_date and item.cadence in {"monthly", "quarterly", "yearly"}: item.due_day = item.next_due_date.day
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.bill_profile_updated", resource_type="bill_profile", resource_id=str(item.id), detail=json.dumps(changes, default=str)))
    db.flush(); response = bill_profile_response(item); db.commit(); return response


@obligations_router.delete("/obligations/bill-profiles/{profile_id}", status_code=204, tags=["obligations"])
def delete_bill_profile(profile_id: UUID, scope: str, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> None:
    if scope not in {"upcoming", "all"}: raise HTTPException(status_code=422, detail="Delete scope must be upcoming or all")
    item = db.scalar(select(BillProfile).where(BillProfile.id == profile_id, BillProfile.household_id == membership.household_id))
    if not item: raise HTTPException(status_code=404, detail="Bill profile not found")
    instances = db.scalars(select(BillInstance).where(BillInstance.bill_profile_id == item.id)).all()
    if scope == "upcoming":
        removable = []
        for instance in instances:
            links = db.scalar(select(func.count()).select_from(BillPaymentLink).where(BillPaymentLink.bill_instance_id == instance.id)) or 0
            if instance.due_date >= utc_now().date() and links == 0 and instance.status in {"upcoming", "changed", "skipped"}: removable.append(instance)
        for instance in removable: db.delete(instance)
        item.is_active = False
        detail = f"scope:upcoming;removed:{len(removable)};profile_archived:true"
        action = "obligation.bill_profile_upcoming_removed"
    else:
        removed = len(instances)
        db.delete(item)
        detail = f"scope:all;instances_removed:{removed};ledger_transactions_preserved:true"
        action = "obligation.bill_profile_deleted"
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=action, resource_type="bill_profile", resource_id=str(profile_id), detail=detail))
    db.commit()


@obligations_router.get("/obligations/debts", response_model=list[DebtResponse], tags=["obligations"])
def list_debts(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[DebtResponse]:
    return [debt_response(item) for item in db.scalars(select(Debt).where(Debt.household_id == membership.household_id).order_by(Debt.name)).all()]


@obligations_router.post("/obligations/debts", response_model=DebtResponse, status_code=201, tags=["obligations"])
def create_debt(request: DebtCreateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> DebtResponse:
    if db.scalar(select(Debt).where(Debt.household_id == membership.household_id, Debt.name == request.name.strip())):
        raise HTTPException(status_code=409, detail="A debt with this name already exists")
    if request.account_id is not None:
        account = household_account(db, membership.household_id, request.account_id)
        if account.currency_code != request.currency_code:
            raise HTTPException(status_code=422, detail="Debt and related account currencies must match")
    values = request.model_dump()
    item = Debt(household_id=membership.household_id, balance_anchor_minor=request.balance_minor, **(values | {"name": request.name.strip()}))
    db.add(item); db.flush(); db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.debt_created", resource_type="debt", resource_id=str(item.id)))
    response = debt_response(item); db.commit(); return response


@obligations_router.patch("/obligations/debts/{debt_id}", response_model=DebtResponse, tags=["obligations"])
def update_debt(debt_id: UUID, request: DebtUpdateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> DebtResponse:
    item = db.scalar(select(Debt).where(Debt.id == debt_id, Debt.household_id == membership.household_id))
    if not item:
        raise HTTPException(status_code=404, detail="Debt not found")
    changes = request.model_dump(exclude_unset=True)
    required_fields = {"name", "balance_minor", "apr_basis_points", "minimum_payment_minor", "due_day", "next_due_date", "currency_code", "is_active"}
    if any(field in changes and changes[field] is None for field in required_fields):
        raise HTTPException(status_code=422, detail="Required debt fields cannot be cleared")
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        duplicate = db.scalar(select(Debt).where(Debt.household_id == membership.household_id, Debt.name == changes["name"], Debt.id != item.id))
        if duplicate:
            raise HTTPException(status_code=409, detail="A debt with this name already exists")
    if "balance_minor" in changes:
        item.balance_anchor_minor = changes["balance_minor"]
    account_id = changes.get("account_id", item.account_id)
    currency_code = changes.get("currency_code", item.currency_code)
    if account_id is not None:
        account = household_account(db, membership.household_id, account_id)
        if account.currency_code != currency_code:
            raise HTTPException(status_code=422, detail="Debt and related account currencies must match")
    for field, value in changes.items():
        setattr(item, field, value)
    if "balance_minor" in changes or "balance_as_of_date" in changes:
        recalculate_debt_balance(db, item)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.debt_updated", resource_type="debt", resource_id=str(item.id), detail=json.dumps(changes, default=str)))
    db.flush(); response = debt_response(item); db.commit(); return response


@obligations_router.post("/obligations/debts/{debt_id}/recalculate", response_model=DebtResponse, tags=["obligations"])
def recalculate_debt(debt_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> DebtResponse:
    item = db.scalar(select(Debt).where(Debt.id == debt_id, Debt.household_id == membership.household_id))
    if not item:
        raise HTTPException(status_code=404, detail="Debt not found")
    principal = recalculate_debt_balance(db, item)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.debt_recalculated", resource_type="debt", resource_id=str(item.id), detail=f"anchor:{item.balance_anchor_minor};as_of:{item.balance_as_of_date};principal:{principal};balance:{item.balance_minor}"))
    db.flush(); response = debt_response(item); db.commit(); return response


@obligations_router.delete("/obligations/debts/{debt_id}", status_code=204, tags=["obligations"])
def delete_debt(debt_id: UUID, scope: str, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> None:
    if scope not in {"upcoming", "all"}:
        raise HTTPException(status_code=422, detail="Delete scope must be upcoming or all")
    item = db.scalar(select(Debt).where(Debt.id == debt_id, Debt.household_id == membership.household_id))
    if not item:
        raise HTTPException(status_code=404, detail="Debt not found")
    instances = db.scalars(select(BillInstance).where(BillInstance.debt_id == item.id)).all()
    if scope == "upcoming":
        removable = []
        for instance in instances:
            links = db.scalar(select(func.count()).select_from(BillPaymentLink).where(BillPaymentLink.bill_instance_id == instance.id)) or 0
            if instance.due_date >= utc_now().date() and links == 0 and instance.status in {"upcoming", "changed", "skipped"}:
                removable.append(instance)
        for instance in removable:
            db.delete(instance)
        item.is_active = False
        action = "obligation.debt_upcoming_removed"
        detail = f"scope:upcoming;removed:{len(removable)};debt_deactivated:true"
    else:
        removed = len(instances)
        db.delete(item)
        action = "obligation.debt_deleted"
        detail = f"scope:all;instances_removed:{removed};ledger_transactions_preserved:true"
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=action, resource_type="debt", resource_id=str(debt_id), detail=detail))
    db.commit()


@obligations_router.get("/obligations/income-sources", response_model=list[IncomeSourceResponse], tags=["obligations"])
def list_income_sources(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[IncomeSourceResponse]:
    return [income_source_response(item) for item in db.scalars(select(IncomeSource).where(IncomeSource.household_id == membership.household_id).order_by(IncomeSource.name)).all()]


@obligations_router.post("/obligations/income-sources", response_model=IncomeSourceResponse, status_code=201, tags=["obligations"])
def create_income_source(request: IncomeSourceCreateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> IncomeSourceResponse:
    validate_range(request.expected_amount_minor, request.minimum_amount_minor, request.maximum_amount_minor)
    if db.scalar(select(IncomeSource).where(IncomeSource.household_id == membership.household_id, IncomeSource.name == request.name.strip())):
        raise HTTPException(status_code=409, detail="An income source with this name already exists")
    item = IncomeSource(household_id=membership.household_id, **request.model_dump())
    db.add(item); db.flush(); db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.income_source_created", resource_type="income_source", resource_id=str(item.id)))
    response = income_source_response(item); db.commit(); return response


@obligations_router.patch("/obligations/income-sources/{source_id}", response_model=IncomeSourceResponse, tags=["obligations"])
def update_income_source(source_id: UUID, request: IncomeSourceUpdateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> IncomeSourceResponse:
    item = db.scalar(select(IncomeSource).where(IncomeSource.id == source_id, IncomeSource.household_id == membership.household_id))
    if not item: raise HTTPException(status_code=404, detail="Income source not found")
    changes = request.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        duplicate = db.scalar(select(IncomeSource).where(IncomeSource.household_id == membership.household_id, IncomeSource.name == changes["name"], IncomeSource.id != item.id))
        if duplicate: raise HTTPException(status_code=409, detail="An income source with this name already exists")
    expected = changes.get("expected_amount_minor", item.expected_amount_minor)
    minimum = changes.get("minimum_amount_minor", item.minimum_amount_minor)
    maximum = changes.get("maximum_amount_minor", item.maximum_amount_minor)
    validate_range(expected, minimum, maximum)
    for field, value in changes.items(): setattr(item, field, value)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.income_source_updated", resource_type="income_source", resource_id=str(item.id), detail=json.dumps(changes, default=str)))
    db.flush(); response = income_source_response(item); db.commit(); return response


@obligations_router.delete("/obligations/income-sources/{source_id}", status_code=204, tags=["obligations"])
def delete_income_source(source_id: UUID, scope: str, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> None:
    if scope not in {"upcoming", "all"}: raise HTTPException(status_code=422, detail="Delete scope must be upcoming or all")
    item = db.scalar(select(IncomeSource).where(IncomeSource.id == source_id, IncomeSource.household_id == membership.household_id))
    if not item: raise HTTPException(status_code=404, detail="Income source not found")
    events = db.scalars(select(IncomeEvent).where(IncomeEvent.income_source_id == item.id)).all()
    if scope == "upcoming":
        removable = [event for event in events if event.expected_date >= utc_now().date() and event.received_transaction_id is None]
        for event in removable: db.delete(event)
        item.is_active = False
        action = "obligation.income_source_upcoming_removed"
        detail = f"scope:upcoming;removed:{len(removable)};source_deactivated:true"
    else:
        removed = len(events)
        for event in events: db.delete(event)
        db.delete(item)
        action = "obligation.income_source_deleted"
        detail = f"scope:all;events_removed:{removed};ledger_transactions_preserved:true"
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=action, resource_type="income_source", resource_id=str(source_id), detail=detail))
    db.commit()


@obligations_router.post("/obligations/generate", response_model=GenerationResponse, tags=["obligations"])
def generate_obligations(through: date, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> GenerationResponse:
    bill_count = income_count = debt_count = 0
    for profile in db.scalars(select(BillProfile).where(BillProfile.household_id == membership.household_id, BillProfile.is_active.is_(True))).all():
        cursor = profile.next_due_date
        while cursor is not None and cursor <= through:
            if not db.scalar(select(BillInstance).where(BillInstance.bill_profile_id == profile.id, BillInstance.due_date == cursor)):
                db.add(BillInstance(household_id=membership.household_id, bill_profile_id=profile.id, name=profile.name, due_date=cursor, expected_amount_minor=profile.expected_amount_minor, minimum_amount_minor=profile.minimum_amount_minor, maximum_amount_minor=profile.maximum_amount_minor, currency_code=profile.currency_code, is_essential=profile.is_essential, priority=profile.priority)); bill_count += 1
            cursor = next_occurrence(cursor, profile.cadence, profile.due_day)
        profile.next_due_date = cursor
    for source in db.scalars(select(IncomeSource).where(IncomeSource.household_id == membership.household_id, IncomeSource.is_active.is_(True))).all():
        cursor = source.next_expected_date
        while cursor is not None and cursor <= through:
            if not db.scalar(select(IncomeEvent).where(IncomeEvent.income_source_id == source.id, IncomeEvent.expected_date == cursor)):
                db.add(IncomeEvent(household_id=membership.household_id, income_source_id=source.id, name=source.name, expected_date=cursor, expected_amount_minor=source.expected_amount_minor, minimum_amount_minor=source.minimum_amount_minor, maximum_amount_minor=source.maximum_amount_minor, currency_code=source.currency_code, confidence_percent=source.confidence_percent)); income_count += 1
            cursor = next_occurrence(cursor, source.cadence)
        source.next_expected_date = cursor
    for debt in db.scalars(select(Debt).where(Debt.household_id == membership.household_id, Debt.is_active.is_(True))).all():
        cursor = debt.next_due_date
        while cursor <= through:
            if not db.scalar(select(BillInstance).where(BillInstance.debt_id == debt.id, BillInstance.due_date == cursor)):
                db.add(BillInstance(household_id=membership.household_id, debt_id=debt.id, name=f"{debt.name} minimum", due_date=cursor, expected_amount_minor=debt.minimum_payment_minor, minimum_amount_minor=debt.minimum_payment_minor, currency_code=debt.currency_code, is_essential=True, priority=1)); debt_count += 1
            cursor = next_occurrence(cursor, "monthly", debt.due_day) or cursor
        debt.next_due_date = cursor
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.horizon_generated", resource_type="household", resource_id=str(membership.household_id), detail=f"through:{through};bills:{bill_count};income:{income_count};debts:{debt_count}"))
    db.commit(); return GenerationResponse(bill_instances_created=bill_count, income_events_created=income_count, debt_instances_created=debt_count)


@obligations_router.get("/obligations/bill-instances", response_model=list[BillInstanceResponse], tags=["obligations"])
def list_bill_instances(db: DbSession, membership: Annotated[Membership, Depends(current_membership)], date_from: date | None = None, date_to: date | None = None) -> list[BillInstanceResponse]:
    query = select(BillInstance).where(BillInstance.household_id == membership.household_id)
    if date_from: query = query.where(BillInstance.due_date >= date_from)
    if date_to: query = query.where(BillInstance.due_date <= date_to)
    return [bill_instance_response(db, item) for item in db.scalars(query.order_by(BillInstance.due_date, BillInstance.priority)).all()]


@obligations_router.patch("/obligations/bill-instances/{instance_id}", response_model=BillInstanceResponse, tags=["obligations"])
def update_bill_instance(instance_id: UUID, request: BillInstanceUpdateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> BillInstanceResponse:
    item = db.scalar(select(BillInstance).where(BillInstance.id == instance_id, BillInstance.household_id == membership.household_id))
    if not item: raise HTTPException(status_code=404, detail="Bill instance not found")
    for field, value in request.model_dump(exclude_unset=True).items(): setattr(item, field, value)
    if request.expected_amount_minor is not None and item.status == "upcoming": item.status = "changed"
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.bill_instance_updated", resource_type="bill_instance", resource_id=str(item.id), detail=json.dumps(request.model_dump(exclude_unset=True), default=str)))
    db.flush(); response = bill_instance_response(db, item); db.commit(); return response


@obligations_router.post("/obligations/bill-instances/{instance_id}/payments", response_model=PaymentLinkResponse, status_code=201, tags=["obligations"])
def link_bill_payment(instance_id: UUID, request: PaymentLinkRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> PaymentLinkResponse:
    item = db.scalar(select(BillInstance).where(BillInstance.id == instance_id, BillInstance.household_id == membership.household_id))
    if not item: raise HTTPException(status_code=404, detail="Bill instance not found")
    transaction = household_transaction(db, membership.household_id, request.transaction_id)
    if transaction.status != "posted" or transaction.amount_minor >= 0 or transaction.source_type == "transfer":
        raise HTTPException(status_code=422, detail="Bill payments require a posted non-transfer outflow")
    if transaction.currency_code != item.currency_code: raise HTTPException(status_code=422, detail="Payment and bill currencies must match")
    if transaction_linked_total(db, transaction.id) + request.amount_minor > abs(transaction.amount_minor):
        raise HTTPException(status_code=422, detail="Applied bill payments exceed the transaction amount")
    principal = request.principal_amount_minor if request.principal_amount_minor is not None else request.amount_minor
    if principal > request.amount_minor:
        raise HTTPException(status_code=422, detail="Principal cannot exceed the linked payment amount")
    link = BillPaymentLink(household_id=membership.household_id, bill_instance_id=item.id, transaction_id=transaction.id, amount_minor=request.amount_minor, principal_amount_minor=principal, created_by_user_id=actor.id)
    db.add(link); db.flush()
    if item.debt_id:
        debt = db.get(Debt, item.debt_id)
        if debt:
            if debt.balance_anchor_minor is None:
                debt.balance_anchor_minor = debt.balance_minor
            recalculate_debt_balance(db, debt)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.payment_linked", resource_type="bill_payment_link", resource_id=str(link.id)))
    response = PaymentLinkResponse(payment_link_id=link.id, transaction_id=link.transaction_id, amount_minor=link.amount_minor); db.commit(); return response


@obligations_router.delete("/obligations/bill-instances/{instance_id}/payments/{link_id}", status_code=204, tags=["obligations"])
def unlink_bill_payment(instance_id: UUID, link_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> None:
    link = db.scalar(select(BillPaymentLink).where(BillPaymentLink.id == link_id, BillPaymentLink.bill_instance_id == instance_id, BillPaymentLink.household_id == membership.household_id))
    if not link: raise HTTPException(status_code=404, detail="Payment link not found")
    instance = db.get(BillInstance, instance_id)
    db.delete(link); db.flush()
    if instance and instance.debt_id:
        debt = db.get(Debt, instance.debt_id)
        if debt:
            recalculate_debt_balance(db, debt)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.payment_unlinked", resource_type="bill_payment_link", resource_id=str(link.id))); db.commit()


@obligations_router.get("/obligations/income-events", response_model=list[IncomeEventResponse], tags=["obligations"])
def list_income_events(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[IncomeEventResponse]:
    return [income_event_response(item) for item in db.scalars(select(IncomeEvent).where(IncomeEvent.household_id == membership.household_id).order_by(IncomeEvent.expected_date)).all()]


@obligations_router.post("/obligations/income-events", response_model=IncomeEventResponse, status_code=201, tags=["obligations"])
def create_income_event(request: IncomeEventCreateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> IncomeEventResponse:
    validate_range(request.expected_amount_minor, request.minimum_amount_minor, request.maximum_amount_minor)
    item = IncomeEvent(household_id=membership.household_id, **request.model_dump())
    db.add(item); db.flush(); db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.income_event_created", resource_type="income_event", resource_id=str(item.id)))
    response = income_event_response(item); db.commit(); return response


@obligations_router.put("/obligations/income-events/{event_id}/received", response_model=IncomeEventResponse, tags=["obligations"])
def receive_income_event(event_id: UUID, request: IncomeEventReceiveRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> IncomeEventResponse:
    item = db.scalar(select(IncomeEvent).where(IncomeEvent.id == event_id, IncomeEvent.household_id == membership.household_id))
    if not item: raise HTTPException(status_code=404, detail="Income event not found")
    transaction = household_transaction(db, membership.household_id, request.transaction_id)
    if transaction.status != "posted" or transaction.amount_minor <= 0 or transaction.source_type == "transfer": raise HTTPException(status_code=422, detail="Received income requires a posted non-transfer inflow")
    if transaction.currency_code != item.currency_code: raise HTTPException(status_code=422, detail="Income and transaction currencies must match")
    item.received_transaction_id = transaction.id; item.status = "received"
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="obligation.income_received", resource_type="income_event", resource_id=str(item.id), detail=f"transaction:{transaction.id}"))
    db.flush(); response = income_event_response(item); db.commit(); return response


@obligations_router.get("/obligations/calendar", response_model=list[CalendarItemResponse], tags=["obligations"])
def obligation_calendar(db: DbSession, membership: Annotated[Membership, Depends(current_membership)], date_from: date, date_to: date) -> list[CalendarItemResponse]:
    bills = db.scalars(select(BillInstance).where(BillInstance.household_id == membership.household_id, BillInstance.due_date >= date_from, BillInstance.due_date <= date_to)).all()
    incomes = db.scalars(select(IncomeEvent).where(IncomeEvent.household_id == membership.household_id, IncomeEvent.expected_date >= date_from, IncomeEvent.expected_date <= date_to)).all()
    result = [CalendarItemResponse(item_type="debt" if item.debt_id else "bill", item_id=item.id, name=item.name, event_date=item.due_date, amount_minor=-item.expected_amount_minor, currency_code=item.currency_code, status=bill_instance_response(db, item).status, priority=item.priority) for item in bills]
    result += [CalendarItemResponse(item_type="income", item_id=item.id, name=item.name, event_date=item.expected_date, amount_minor=item.expected_amount_minor, currency_code=item.currency_code, status=item.status, priority=None) for item in incomes]
    return sorted(result, key=lambda item: (item.event_date, item.item_type, item.name))


def planner_result(db: DbSession, membership: Membership, request: PlannerRequest) -> tuple[dict, dict]:
    snapshot = collect_planner_input(
        db,
        membership.household_id,
        as_of_date=request.as_of_date,
        horizon_days=request.horizon_days,
        currency_code=request.currency_code,
        cash_buffer_minor=request.cash_buffer_minor,
        include_pending=request.include_pending,
    )
    return snapshot, calculate_forecast(snapshot)


@obligations_router.post("/planner/forecast", response_model=PlannerForecastResponse, tags=["planner"])
def preview_planner(
    request: PlannerRequest,
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
) -> PlannerForecastResponse:
    _, result = planner_result(db, membership, request)
    return PlannerForecastResponse.model_validate(result)


@obligations_router.post("/planner/snapshots", response_model=PlannerForecastResponse, status_code=201, tags=["planner"])
def save_planner_snapshot(
    request: PlannerRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(ledger_writer)],
) -> PlannerForecastResponse:
    input_data, result = planner_result(db, membership, request)
    snapshot = PlannerSnapshot(
        household_id=membership.household_id,
        created_by_user_id=actor.id,
        rule_version=result["rule_version"],
        currency_code=result["currency_code"],
        as_of_date=request.as_of_date,
        horizon_date=date.fromisoformat(result["horizon_date"]),
        input_hash=result["input_hash"],
        input_json=json.dumps(input_data, sort_keys=True, separators=(",", ":")),
        output_json=json.dumps(result, sort_keys=True, separators=(",", ":")),
    )
    db.add(snapshot)
    db.flush()
    result["snapshot_id"] = str(snapshot.id)
    snapshot.output_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
    db.add(AuditEvent(
        household_id=membership.household_id,
        actor_user_id=actor.id,
        action="planner.snapshot_created",
        resource_type="planner_snapshot",
        resource_id=str(snapshot.id),
        detail=f"rule:{snapshot.rule_version};input_hash:{snapshot.input_hash}",
    ))
    db.commit()
    return PlannerForecastResponse.model_validate(result)


@obligations_router.get("/planner/snapshots/latest", response_model=PlannerForecastResponse, tags=["planner"])
def latest_planner_snapshot(
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
    currency_code: str = "USD",
) -> PlannerForecastResponse:
    snapshot = db.scalar(
        select(PlannerSnapshot).where(
            PlannerSnapshot.household_id == membership.household_id,
            PlannerSnapshot.currency_code == currency_code,
        ).order_by(PlannerSnapshot.created_at.desc(), PlannerSnapshot.id.desc())
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No saved planner snapshot exists for this currency")
    return PlannerForecastResponse.model_validate(json.loads(snapshot.output_json))


def import_source_response(db: DbSession, item: ImportSource) -> ImportSourceResponse:
    account = household_account(db, item.household_id, item.account_id)
    today = utc_now().date()
    reminder_status = "disabled" if not item.reminders_enabled else "overdue" if item.next_reminder_date and item.next_reminder_date < today else "due" if item.next_reminder_date == today else "scheduled"
    return ImportSourceResponse(source_id=item.id, account_id=item.account_id, account_name=account.name, name=item.name, institution=item.institution, format_type=item.format_type, date_column=item.date_column, payee_column=item.payee_column, original_payee_column=item.original_payee_column, amount_column=item.amount_column, debit_column=item.debit_column, credit_column=item.credit_column, status_column=item.status_column, category_column=item.category_column, memo_column=item.memo_column, amount_sign=item.amount_sign, date_format=item.date_format, export_method=item.export_method, export_instructions=item.export_instructions, notes=item.notes, reminder_interval_days=item.reminder_interval_days, next_reminder_date=item.next_reminder_date, reminders_enabled=item.reminders_enabled, last_imported_at=item.last_imported_at.isoformat() if item.last_imported_at else None, reminder_status=reminder_status, is_active=item.is_active)


def batch_response(item: ImportBatch) -> ImportBatchResponse:
    return ImportBatchResponse(batch_id=item.id, source_id=item.source_id, filename=item.filename, file_checksum=item.file_checksum, parser_version=item.parser_version, status=item.status, row_count=item.row_count, candidate_count=item.candidate_count, duplicate_count=item.duplicate_count, invalid_count=item.invalid_count, ready_count=item.ready_count, transfer_count=item.transfer_count, recurring_count=item.recurring_count, review_count=item.review_count, mapping_version_id=item.mapping_version_id, ingestion_channel=item.ingestion_channel, upstream_reference=item.upstream_reference, created_at=item.created_at.isoformat())


@obligations_router.get("/imports/sources", response_model=list[ImportSourceResponse], tags=["imports"])
def list_import_sources(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[ImportSourceResponse]:
    return [import_source_response(db, item) for item in db.scalars(select(ImportSource).where(ImportSource.household_id == membership.household_id, ImportSource.is_active.is_(True)).order_by(ImportSource.name)).all()]


def normalized_import_source_values(request: ImportSourceRequest) -> dict:
    values = request.model_dump()
    for field in ("original_payee_column", "amount_column", "debit_column", "credit_column", "status_column", "category_column", "memo_column"):
        values[field] = values[field].strip() if values[field] and values[field].strip() else None
    return values


@obligations_router.post("/imports/sources", response_model=ImportSourceResponse, status_code=201, tags=["imports"])
def create_import_source(request: ImportSourceRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> ImportSourceResponse:
    household_account(db, membership.household_id, request.account_id)
    validate_safe_notes(request.export_instructions, request.notes)
    if db.scalar(select(ImportSource).where(ImportSource.household_id == membership.household_id, ImportSource.name == request.name.strip())): raise HTTPException(status_code=409, detail="An import source with this name already exists")
    item = ImportSource(household_id=membership.household_id, format_type="csv_mapped", **normalized_import_source_values(request))
    db.add(item); db.flush(); ensure_mapping_version(db, item, actor.id); db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="import.source_created", resource_type="import_source", resource_id=str(item.id)))
    response = import_source_response(db, item); db.commit(); return response


@obligations_router.put("/imports/sources/{source_id}", response_model=ImportSourceResponse, tags=["imports"])
def update_import_source(source_id: UUID, request: ImportSourceRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> ImportSourceResponse:
    item = db.scalar(select(ImportSource).where(ImportSource.id == source_id, ImportSource.household_id == membership.household_id, ImportSource.is_active.is_(True)))
    if not item: raise HTTPException(status_code=404, detail="Import source not found")
    household_account(db, membership.household_id, request.account_id)
    has_history = db.scalar(select(ImportBatch.id).where(ImportBatch.source_id == item.id).limit(1)) is not None
    if has_history and request.account_id != item.account_id:
        raise HTTPException(status_code=409, detail="The account cannot be changed after this Source has import history")
    validate_safe_notes(request.export_instructions, request.notes)
    duplicate = db.scalar(select(ImportSource).where(ImportSource.household_id == membership.household_id, ImportSource.name == request.name.strip(), ImportSource.id != item.id, ImportSource.is_active.is_(True)))
    if duplicate: raise HTTPException(status_code=409, detail="An import source with this name already exists")
    for field, value in normalized_import_source_values(request).items(): setattr(item, field, value)
    ensure_mapping_version(db, item, actor.id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="import.source_updated", resource_type="import_source", resource_id=str(item.id), detail="Updated source settings apply to future imports; preserved batches are unchanged"))
    db.flush(); response = import_source_response(db, item); db.commit(); return response


@obligations_router.delete("/imports/sources/{source_id}", status_code=204, tags=["imports"])
def delete_import_source(source_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> Response:
    item = db.scalar(select(ImportSource).where(ImportSource.id == source_id, ImportSource.household_id == membership.household_id, ImportSource.is_active.is_(True)))
    if not item: raise HTTPException(status_code=404, detail="Import source not found")
    has_history = db.scalar(select(ImportBatch.id).where(ImportBatch.source_id == item.id).limit(1)) is not None
    if has_history:
        item.is_active = False
        item.reminders_enabled = False
        action = "import.source_archived"
        detail = "Source disabled; prior import batches and reconciliation evidence preserved"
    else:
        db.delete(item)
        action = "import.source_deleted"
        detail = "Unused source permanently deleted"
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=action, resource_type="import_source", resource_id=str(item.id), detail=detail))
    db.commit()
    return Response(status_code=204)


@obligations_router.post("/imports/csv/inspect", response_model=CsvInspectionResponse, tags=["imports"])
def inspect_csv_upload(request: CsvImportRequest, membership: Annotated[Membership, Depends(ledger_writer)]) -> CsvInspectionResponse:
    del membership
    return CsvInspectionResponse.model_validate(inspect_csv(request.csv_text))


@obligations_router.patch("/imports/sources/{source_id}/reminder", response_model=ImportSourceResponse, tags=["imports"])
def update_import_reminder(source_id: UUID, request: ReminderUpdateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> ImportSourceResponse:
    item = db.scalar(select(ImportSource).where(ImportSource.id == source_id, ImportSource.household_id == membership.household_id))
    if not item: raise HTTPException(status_code=404, detail="Import source not found")
    for field, value in request.model_dump(exclude_unset=True).items(): setattr(item, field, value)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="import.reminder_updated", resource_type="import_source", resource_id=str(item.id)))
    db.flush(); response = import_source_response(db, item); db.commit(); return response


@obligations_router.get("/imports/batches", response_model=list[ImportBatchResponse], tags=["imports"])
def list_import_batches(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[ImportBatchResponse]:
    return [batch_response(item) for item in db.scalars(select(ImportBatch).where(ImportBatch.household_id == membership.household_id).order_by(ImportBatch.created_at.desc())).all()]


@obligations_router.post("/imports/sources/{source_id}/csv", response_model=ImportBatchResponse, status_code=201, tags=["imports"])
def ingest_csv(source_id: UUID, request: CsvImportRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> ImportBatchResponse:
    source = db.scalar(select(ImportSource).where(ImportSource.id == source_id, ImportSource.household_id == membership.household_id, ImportSource.is_active.is_(True)))
    if not source: raise HTTPException(status_code=404, detail="Import source not found")
    batch = ingest_csv_evidence(db, source=source, filename=request.filename, csv_text=request.csv_text, actor_user_id=actor.id)
    response=batch_response(batch); db.commit(); return response


def review_response(db: DbSession, row: ImportRow) -> ReviewItemResponse:
    source = db.get(ImportSource, row.source_id)
    matches = db.scalars(select(ReconciliationMatch).where(ReconciliationMatch.import_row_id == row.id).order_by(ReconciliationMatch.confidence_percent.desc())).all()
    candidate_rows=[]
    for match in matches:
        transaction=db.get(LedgerTransaction, match.transaction_id)
        candidate_rows.append(MatchCandidateResponse(match_id=match.id, transaction_id=match.transaction_id, transaction_date=transaction.transaction_date, payee=transaction.payee, amount_minor=transaction.amount_minor, confidence_percent=match.confidence_percent, evidence=match.evidence, status=match.status))
    proposed_category = db.get(Category, row.proposed_category_id) if row.proposed_category_id else None
    return ReviewItemResponse(row_id=row.id, batch_id=row.batch_id, source_name=source.name if source else "Deleted source", source_account_id=source.account_id, row_number=row.row_number, raw_values=json.loads(row.raw_json), transaction_date=row.transaction_date, amount_minor=row.amount_minor, currency_code=row.currency_code, raw_payee=row.raw_payee, normalized_payee=row.normalized_payee, status=row.status, exception_type=row.exception_type, validation_error=row.validation_error, automation_kind=row.automation_kind, proposed_category_id=row.proposed_category_id, proposed_category_name=proposed_category.name if proposed_category else None, automation_confidence=row.automation_confidence, automation_evidence=row.automation_evidence, candidates=candidate_rows)


@obligations_router.get("/reconciliation/queue", response_model=list[ReviewItemResponse], tags=["reconciliation"])
def reconciliation_queue(db: DbSession, membership: Annotated[Membership, Depends(current_membership)], include_resolved: bool = False) -> list[ReviewItemResponse]:
    query=select(ImportRow).where(ImportRow.household_id == membership.household_id)
    if not include_resolved: query=query.where(ImportRow.status.in_(["ready", "unmatched", "duplicate", "invalid", "deferred"]))
    query = query.order_by(ImportRow.transaction_date.is_(None), ImportRow.transaction_date.desc(), func.abs(ImportRow.amount_minor).desc(), ImportRow.amount_minor, ImportRow.created_at, ImportRow.row_number)
    return [review_response(db, row) for row in db.scalars(query).all()]


@obligations_router.get("/reconciliation/queue/page", response_model=ReviewQueuePageResponse, tags=["reconciliation"])
def reconciliation_queue_page(
    db: DbSession,
    membership: Annotated[Membership, Depends(current_membership)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=10, le=100)] = 25,
    search: Annotated[str | None, Query(max_length=200)] = None,
    queue_kind: Annotated[str, Query(pattern="^(standard|transfer|all)$")] = "standard",
    row_status: Annotated[str | None, Query(pattern="^(ready|unmatched|duplicate|invalid|deferred)$")] = None,
    source_id: UUID | None = None,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    direction: Annotated[str | None, Query(pattern="^(inflow|outflow)$")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ReviewQueuePageResponse:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Start date cannot be after end date")
    query = select(ImportRow).where(
        ImportRow.household_id == membership.household_id,
        ImportRow.status.in_(["ready", "unmatched", "duplicate", "invalid", "deferred"]),
    )
    description = func.lower(func.coalesce(ImportRow.raw_payee, ImportRow.normalized_payee, ""))
    transfer_filter = or_(
        func.coalesce(ImportRow.automation_kind, "") == "transfer_candidate",
        *[description.like(f"%{term}%") for term in ("transfer", "payment", "pymt", "zelle", "venmo", "cash app", "paypal", "overdraft protection", "overdraft transfer")],
    )
    if queue_kind == "transfer":
        query = query.where(transfer_filter)
    elif queue_kind == "standard":
        query = query.where(~transfer_filter)
    if row_status:
        query = query.where(ImportRow.status == row_status)
    if source_id:
        source = db.scalar(select(ImportSource).where(ImportSource.id == source_id, ImportSource.household_id == membership.household_id))
        if not source:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import source not found")
        query = query.where(ImportRow.source_id == source_id)
    if account_id:
        household_account(db, membership.household_id, account_id)
        source_ids = select(ImportSource.id).where(ImportSource.household_id == membership.household_id, ImportSource.account_id == account_id)
        query = query.where(ImportRow.source_id.in_(source_ids))
    if category_id:
        category = db.get(Category, category_id)
        if not category or category.household_id != membership.household_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
        query = query.where(ImportRow.proposed_category_id == category_id)
    if direction == "inflow":
        query = query.where(ImportRow.amount_minor >= 0)
    elif direction == "outflow":
        query = query.where(ImportRow.amount_minor < 0)
    if date_from:
        query = query.where(ImportRow.transaction_date >= date_from)
    if date_to:
        query = query.where(ImportRow.transaction_date <= date_to)
    term = (search or "").strip().casefold()
    if term:
        pattern = f"%{term}%"
        matching_sources = select(ImportSource.id).where(
            ImportSource.household_id == membership.household_id,
            func.lower(ImportSource.name).like(pattern),
        )
        matching_categories = select(Category.id).where(
            Category.household_id == membership.household_id,
            func.lower(Category.name).like(pattern),
        )
        query = query.where(or_(
            func.lower(func.coalesce(ImportRow.raw_payee, "")).like(pattern),
            func.lower(func.coalesce(ImportRow.normalized_payee, "")).like(pattern),
            func.lower(func.coalesce(ImportRow.raw_json, "")).like(pattern),
            func.lower(func.coalesce(ImportRow.automation_kind, "")).like(pattern),
            func.lower(func.coalesce(ImportRow.automation_evidence, "")).like(pattern),
            ImportRow.source_id.in_(matching_sources),
            ImportRow.proposed_category_id.in_(matching_categories),
        ))
    total_items = int(db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    effective_page = min(page, total_pages)
    rows = db.scalars(
        query.order_by(ImportRow.transaction_date.is_(None), ImportRow.transaction_date.desc(), func.abs(ImportRow.amount_minor).desc(), ImportRow.amount_minor, ImportRow.created_at, ImportRow.row_number)
        .offset((effective_page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ReviewQueuePageResponse(
        items=[review_response(db, row) for row in rows],
        page=effective_page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
    )


@obligations_router.post("/reconciliation/rows/category-suggestions", response_model=list[ReviewItemResponse], tags=["reconciliation"])
def suggest_import_row_categories(request: ImportCategorySuggestionRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> list[ReviewItemResponse]:
    if len(set(request.row_ids)) != len(request.row_ids):
        raise HTTPException(status_code=422, detail="Import row selection contains duplicates")
    values = load_integrations(db)
    if not (values.get("ai_enabled") and values.get("ai_provider") and values.get("ai_base_url")):
        raise HTTPException(status_code=409, detail="Local AI must be enabled and configured before reviewing import rows")
    rows = db.scalars(select(ImportRow).where(ImportRow.id.in_(request.row_ids), ImportRow.household_id == membership.household_id)).all()
    if len(rows) != len(request.row_ids):
        raise HTTPException(status_code=404, detail="One or more import rows were not found")
    eligible = [row for row in rows if row.status in {"ready", "unmatched", "deferred"} and row.amount_minor is not None and row.transaction_date is not None and row.automation_kind != "transfer_candidate"]
    if len(eligible) != len(rows):
        raise HTTPException(status_code=409, detail="Only valid, unmatched non-transfer rows can receive category suggestions")
    try:
        proposals = ai_import_proposals(db, membership.household_id, eligible, values)
    except (OSError, TimeoutError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="The local AI model could not review the selected rows") from exc
    for row in eligible:
        proposal = proposals.get(str(row.id))
        if proposal:
            row.proposed_category_id = proposal["category_id"]
            row.automation_kind = "local_ai_category_suggestion"
            row.automation_confidence = proposal["confidence_percent"]
            row.automation_evidence = f"Local AI ({proposal['model_version']}): {proposal['evidence']}"
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="reconciliation.ai_category_suggestions_generated", resource_type="import_row", detail=f"selected={len(rows)};suggested={len(proposals)}"))
    db.flush()
    responses = [review_response(db, row) for row in rows]
    db.commit()
    return responses


@obligations_router.get("/reconciliation/exceptions", response_model=list[ReconciliationExceptionResponse], tags=["reconciliation"])
def reconciliation_exceptions(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[ReconciliationExceptionResponse]:
    items=db.scalars(select(ReconciliationException).where(ReconciliationException.household_id == membership.household_id, ReconciliationException.status == "open").order_by(ReconciliationException.event_date, ReconciliationException.created_at)).all()
    return [ReconciliationExceptionResponse(exception_id=item.id, exception_type=item.exception_type, related_type=item.related_type, related_id=item.related_id, event_date=item.event_date, amount_minor=item.amount_minor, currency_code=item.currency_code, detail=item.detail, status=item.status) for item in items]


@obligations_router.put("/reconciliation/matches/{match_id}", response_model=ReviewItemResponse, tags=["reconciliation"])
def decide_match(match_id: UUID, request: MatchDecisionRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> ReviewItemResponse:
    match=db.scalar(select(ReconciliationMatch).where(ReconciliationMatch.id == match_id, ReconciliationMatch.household_id == membership.household_id))
    if not match: raise HTTPException(status_code=404, detail="Match candidate not found")
    row=db.get(ImportRow, match.import_row_id); transaction=household_transaction(db, membership.household_id, match.transaction_id)
    match.status={"confirm":"confirmed","reject":"rejected","defer":"deferred"}[request.action]
    match.reviewed_by_user_id=actor.id
    match.reviewed_at=utc_now()
    match.review_note=request.note
    if request.action == "confirm":
        db.execute(ReconciliationMatch.__table__.update().where(ReconciliationMatch.import_row_id == row.id, ReconciliationMatch.id != match.id).values(status="rejected")); row.status="matched"; transaction.reconciled_at=utc_now(); transaction.reconciled_by_user_id=actor.id
    elif request.action == "defer": row.status="deferred"
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=f"reconciliation.match_{request.action}", resource_type="reconciliation_match", resource_id=str(match.id), detail=request.note))
    db.flush(); response=review_response(db,row); db.commit(); return response


@obligations_router.post("/reconciliation/rows/{row_id}/transaction", response_model=ReviewItemResponse, status_code=201, tags=["reconciliation"])
def create_transaction_from_row(row_id: UUID, request: CreateImportedTransactionRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> ReviewItemResponse:
    row=db.scalar(select(ImportRow).where(ImportRow.id == row_id, ImportRow.household_id == membership.household_id))
    if not row: raise HTTPException(status_code=404, detail="Import row not found")
    if row.status in {"invalid", "duplicate", "matched"} or row.transaction_date is None or row.amount_minor is None: raise HTTPException(status_code=409, detail="This row cannot create a transaction")
    source=db.get(ImportSource,row.source_id)
    raw = json.loads(row.raw_json)
    imported_status = (raw_value(raw, source.status_column) if source.status_column else raw_value(raw, "Status")) or "posted"
    imported_status = imported_status.strip().casefold()
    transaction_status = imported_status if imported_status in {"posted", "pending"} else "posted"
    merchant = merchant_for_payee(db, membership.household_id, row.raw_payee or row.normalized_payee)
    transaction=LedgerTransaction(household_id=membership.household_id, account_id=source.account_id, merchant_id=merchant.id if merchant else None, created_by_user_id=actor.id, transaction_date=row.transaction_date, amount_minor=row.amount_minor, currency_code=row.currency_code, status=transaction_status, payee=merchant.name if merchant else row.normalized_payee or row.raw_payee, raw_payee=row.raw_payee, source_type="imported", source_reference=str(row.id))
    db.add(transaction); db.flush()
    category_id = request.category_id or row.proposed_category_id
    learned_rule = False
    if category_id:
        category = db.scalar(select(Category).where(Category.id == category_id, Category.household_id == membership.household_id, Category.is_archived.is_(False)))
        if not category or category.category_type != ("income" if row.amount_minor > 0 else "expense"):
            raise HTTPException(status_code=422, detail="Category direction does not match this imported transaction")
        db.add(TransactionSplit(transaction_id=transaction.id, category_id=category.id, amount_minor=row.amount_minor))
        rule = db.get(CategoryRule, row.applied_rule_id) if row.applied_rule_id else None
        if request.remember_rule and not rule:
            rule = learn_rule_from_row(db, row, source, category.id, actor.id)
            learned_rule = rule is not None
        if rule:
            rule.use_count += 1
            rule.last_applied_at = utc_now()
    match=ReconciliationMatch(household_id=membership.household_id, import_row_id=row.id, transaction_id=transaction.id, method="created_from_import", confidence_percent=100, evidence="User created ledger transaction from preserved import row", status="confirmed", reviewed_by_user_id=actor.id, reviewed_at=utc_now())
    db.add(match); row.status="matched"; transaction.reconciled_at=utc_now(); transaction.reconciled_by_user_id=actor.id
    record_applied_decision(db, row, actor.id, transaction.id)
    if learned_rule:
        db.flush()
        recompute_pending_rows(db, membership.household_id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="reconciliation.transaction_created", resource_type="import_row", resource_id=str(row.id), detail=f"transaction:{transaction.id};rule:{row.applied_rule_id};remember:{request.remember_rule}"))
    db.flush(); response=review_response(db,row).model_copy(update={"created_transaction_id": transaction.id}); db.commit(); return response


@obligations_router.post("/reconciliation/rows/create-batch", response_model=list[ReviewItemResponse], status_code=201, tags=["reconciliation"])
def create_transactions_from_rows(request: BulkCreateImportedTransactionsRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> list[ReviewItemResponse]:
    if len(set(request.row_ids)) != len(request.row_ids):
        raise HTTPException(status_code=422, detail="Import row selection contains duplicates")
    category = db.scalar(select(Category).where(Category.id == request.category_id, Category.household_id == membership.household_id, Category.is_archived.is_(False)))
    if not category:
        raise HTTPException(status_code=422, detail="Category is unavailable")
    rows = db.scalars(select(ImportRow).where(ImportRow.id.in_(request.row_ids), ImportRow.household_id == membership.household_id)).all()
    if len(rows) != len(request.row_ids):
        raise HTTPException(status_code=404, detail="One or more import rows were not found")
    for row in rows:
        if row.status not in {"ready", "unmatched", "deferred"} or row.transaction_date is None or row.amount_minor is None or row.automation_kind == "transfer_candidate":
            raise HTTPException(status_code=409, detail="Only valid, unmatched non-transfer rows can be approved in bulk")
        expected = "income" if row.amount_minor > 0 else "expense"
        if category.category_type != expected:
            raise HTTPException(status_code=422, detail="All selected rows must match the chosen category direction")
    responses = [create_transaction_from_row(row.id, CreateImportedTransactionRequest(category_id=category.id, remember_rule=False), db, actor, membership) for row in rows]
    return responses


@obligations_router.delete("/reconciliation/rows/{row_id}/match", response_model=ReviewItemResponse, tags=["reconciliation"])
def unmatch_import_row(row_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> ReviewItemResponse:
    row=db.scalar(select(ImportRow).where(ImportRow.id == row_id, ImportRow.household_id == membership.household_id))
    if not row: raise HTTPException(status_code=404, detail="Import row not found")
    matches=db.scalars(select(ReconciliationMatch).where(ReconciliationMatch.import_row_id == row.id, ReconciliationMatch.status == "confirmed")).all()
    if not matches: raise HTTPException(status_code=409, detail="Import row has no confirmed match")
    for match in matches: match.status="unmatched"; match.reviewed_by_user_id=actor.id; match.reviewed_at=utc_now()
    row.status="unmatched"; db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="reconciliation.unmatched", resource_type="import_row", resource_id=str(row.id)))
    db.flush(); response=review_response(db,row); db.commit(); return response


def household_document(db: DbSession, household_id: UUID, document_id: UUID) -> Document:
    document = db.scalar(select(Document).where(Document.id == document_id, Document.household_id == household_id))
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def document_response(db: DbSession, item: Document) -> DocumentResponse:
    account = db.get(FinancialAccount, item.account_id) if item.account_id else None
    linked = db.scalar(select(DocumentMatch).where(DocumentMatch.document_id == item.id, DocumentMatch.status == "confirmed"))
    return DocumentResponse(
        document_id=item.id,
        kind=item.kind,
        filename=item.filename,
        content_type=item.content_type,
        size_bytes=item.size_bytes,
        checksum_sha256=item.checksum_sha256,
        status=item.status,
        account_id=item.account_id,
        account_name=account.name if account else None,
        document_date=item.document_date,
        amount_minor=item.amount_minor,
        currency_code=item.currency_code,
        payee=item.payee,
        notes=item.notes,
        has_thumbnail=bool(item.thumbnail_object_key),
        linked_transaction_id=linked.transaction_id if linked else None,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


def document_match_response(db: DbSession, item: DocumentMatch) -> DocumentMatchResponse:
    transaction = db.get(LedgerTransaction, item.transaction_id)
    account = db.get(FinancialAccount, transaction.account_id)
    return DocumentMatchResponse(
        match_id=item.id,
        transaction_id=transaction.id,
        transaction_date=transaction.transaction_date,
        account_name=account.name,
        payee=transaction.payee or transaction.raw_payee,
        amount_minor=transaction.amount_minor,
        currency_code=transaction.currency_code,
        method=item.method,
        confidence_percent=item.confidence_percent,
        evidence=item.evidence,
        status=item.status,
        reviewed_at=item.reviewed_at.isoformat() if item.reviewed_at else None,
    )


def extraction_response(item: DocumentExtraction) -> DocumentExtractionResponse:
    return DocumentExtractionResponse(
        extraction_id=item.id,
        provider=item.provider,
        model_version=item.model_version,
        status=item.status,
        suggestions=json.loads(item.output_json) if item.output_json else None,
        confidence_percent=item.confidence_percent,
        failure_detail=item.failure_detail,
        user_disposition=item.user_disposition,
        created_at=item.created_at.isoformat(),
        completed_at=item.completed_at.isoformat() if item.completed_at else None,
    )


def document_detail_response(db: DbSession, item: Document) -> DocumentDetailResponse:
    matches = db.scalars(select(DocumentMatch).where(DocumentMatch.document_id == item.id).order_by(DocumentMatch.status, DocumentMatch.confidence_percent.desc(), DocumentMatch.created_at)).all()
    extractions = db.scalars(select(DocumentExtraction).where(DocumentExtraction.document_id == item.id).order_by(DocumentExtraction.created_at.desc())).all()
    return DocumentDetailResponse(
        document=document_response(db, item),
        matches=[document_match_response(db, match) for match in matches],
        extractions=[extraction_response(extraction) for extraction in extractions],
    )


@obligations_router.get("/documents", response_model=list[DocumentResponse], tags=["documents"])
def list_documents(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[DocumentResponse]:
    items = db.scalars(select(Document).where(Document.household_id == membership.household_id).order_by(Document.created_at.desc())).all()
    return [document_response(db, item) for item in items]


@obligations_router.post("/documents", response_model=DocumentDetailResponse, status_code=201, tags=["documents"])
def create_document(request: DocumentCreateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> DocumentDetailResponse:
    if request.account_id:
        household_account(db, membership.household_id, request.account_id)
    content = decode_document(request.data_base64, request.content_type)
    document_id = uuid4()
    object_key = f"{membership.household_id}/{document_id}/original"
    thumbnail_key = f"{membership.household_id}/{document_id}/thumbnail.jpg"
    thumbnail_content = thumbnail(content, request.content_type)
    try:
        put_object(object_key, content, request.content_type)
        if thumbnail_content:
            put_object(thumbnail_key, thumbnail_content, "image/jpeg")
    except Exception as error:
        remove_object(object_key)
        remove_object(thumbnail_key)
        raise HTTPException(status_code=503, detail="Local document storage is unavailable") from error
    item = Document(
        id=document_id,
        household_id=membership.household_id,
        uploaded_by_user_id=actor.id,
        account_id=request.account_id,
        kind=request.kind,
        filename=request.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
        content_type=request.content_type,
        size_bytes=len(content),
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        object_key=object_key,
        thumbnail_object_key=thumbnail_key if thumbnail_content else None,
        document_date=request.document_date,
        amount_minor=request.amount_minor,
        currency_code=request.currency_code,
        payee=request.payee,
        notes=request.notes,
    )
    db.add(item)
    db.flush()
    refresh_document_matches(db, item)
    integrations = load_integrations(db)
    if item.kind in {"receipt", "invoice"} and integrations.get("ai_enabled") and integrations.get("ai_extract_enabled") and integrations.get("ai_provider") and integrations.get("ai_base_url"):
        validate_local_ai_url(integrations["ai_base_url"])
        extraction = DocumentExtraction(
            household_id=membership.household_id,
            document_id=item.id,
            requested_by_user_id=actor.id,
            provider=integrations["ai_provider"],
            model_version=integrations.get("ai_model") or ("llava" if integrations["ai_provider"] == "ollama" else "local-model"),
        )
        db.add(extraction)
        item.status = "extraction_queued"
        db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="document.extraction_queued", resource_type="document", resource_id=str(item.id), detail=f"automatic:on_upload;provider:{extraction.provider};model:{extraction.model_version}"))
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="document.created", resource_type="document", resource_id=str(item.id), detail=f"kind:{item.kind};bytes:{item.size_bytes};checksum:{item.checksum_sha256}"))
    db.flush()
    response = document_detail_response(db, item)
    db.commit()
    return response


@obligations_router.get("/documents/{document_id}", response_model=DocumentDetailResponse, tags=["documents"])
def get_document_detail(document_id: UUID, db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> DocumentDetailResponse:
    return document_detail_response(db, household_document(db, membership.household_id, document_id))


@obligations_router.get("/documents/{document_id}/content", tags=["documents"])
def download_document(document_id: UUID, db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> Response:
    item = household_document(db, membership.household_id, document_id)
    try:
        content, content_type = get_object(item.object_key)
    except Exception as error:
        raise HTTPException(status_code=503, detail="Stored document is unavailable") from error
    return Response(content=content, media_type=content_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(item.filename)}", "Cache-Control": "private, no-store"})


@obligations_router.get("/documents/{document_id}/thumbnail", tags=["documents"])
def document_thumbnail(document_id: UUID, db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> Response:
    item = household_document(db, membership.household_id, document_id)
    if not item.thumbnail_object_key:
        raise HTTPException(status_code=404, detail="Document has no thumbnail")
    try:
        content, _ = get_object(item.thumbnail_object_key)
    except Exception as error:
        raise HTTPException(status_code=503, detail="Stored thumbnail is unavailable") from error
    return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@obligations_router.put("/documents/{document_id}", response_model=DocumentDetailResponse, tags=["documents"])
def update_document(document_id: UUID, request: DocumentUpdateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> DocumentDetailResponse:
    item = household_document(db, membership.household_id, document_id)
    values = request.model_dump(exclude_unset=True)
    if values.get("account_id"):
        household_account(db, membership.household_id, values["account_id"])
    for field, value in values.items():
        setattr(item, field, value)
    refresh_document_matches(db, item)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="document.metadata_updated", resource_type="document", resource_id=str(item.id), detail="Document metadata changed; ledger unchanged"))
    db.flush()
    response = document_detail_response(db, item)
    db.commit()
    return response


@obligations_router.delete("/documents/{document_id}", status_code=204, tags=["documents"])
def delete_document(document_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> Response:
    item = household_document(db, membership.household_id, document_id)
    remove_object(item.object_key)
    remove_object(item.thumbnail_object_key)
    db.delete(item)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="document.deleted", resource_type="document", resource_id=str(item.id), detail="Stored content and thumbnail deleted by authorized user"))
    db.commit()
    return Response(status_code=204)


@obligations_router.post("/documents/{document_id}/matches", response_model=DocumentDetailResponse, tags=["documents"])
def create_document_match(document_id: UUID, request: DocumentMatchCreateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> DocumentDetailResponse:
    item = household_document(db, membership.household_id, document_id)
    transaction = household_transaction(db, membership.household_id, request.transaction_id)
    if transaction.voided_at:
        raise HTTPException(status_code=409, detail="A document cannot be linked to a voided transaction")
    match = db.scalar(select(DocumentMatch).where(DocumentMatch.document_id == item.id, DocumentMatch.transaction_id == transaction.id))
    if match is None:
        match = DocumentMatch(household_id=membership.household_id, document_id=item.id, transaction_id=transaction.id, method="manual", confidence_percent=100, evidence="User selected this transaction")
        db.add(match)
    db.execute(DocumentMatch.__table__.update().where(DocumentMatch.document_id == item.id, DocumentMatch.id != match.id, DocumentMatch.status == "confirmed").values(status="rejected", reviewed_by_user_id=actor.id, reviewed_at=utc_now()))
    match.status = "confirmed"
    match.reviewed_by_user_id = actor.id
    match.reviewed_at = utc_now()
    match.review_note = request.note
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="document.match_confirmed", resource_type="document_match", resource_id=str(match.id), detail=f"document:{item.id};transaction:{transaction.id}"))
    db.flush()
    response = document_detail_response(db, item)
    db.commit()
    return response


@obligations_router.put("/documents/{document_id}/matches/{match_id}", response_model=DocumentDetailResponse, tags=["documents"])
def decide_document_match(document_id: UUID, match_id: UUID, request: DocumentMatchDecisionRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> DocumentDetailResponse:
    item = household_document(db, membership.household_id, document_id)
    match = db.scalar(select(DocumentMatch).where(DocumentMatch.id == match_id, DocumentMatch.document_id == item.id, DocumentMatch.household_id == membership.household_id))
    if not match:
        raise HTTPException(status_code=404, detail="Document match not found")
    match.status = {"confirm": "confirmed", "reject": "rejected", "defer": "deferred"}[request.action]
    match.reviewed_by_user_id = actor.id
    match.reviewed_at = utc_now()
    match.review_note = request.note
    if request.action == "confirm":
        db.execute(DocumentMatch.__table__.update().where(DocumentMatch.document_id == item.id, DocumentMatch.id != match.id, DocumentMatch.status == "confirmed").values(status="rejected", reviewed_by_user_id=actor.id, reviewed_at=utc_now()))
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=f"document.match_{request.action}", resource_type="document_match", resource_id=str(match.id), detail=request.note))
    db.flush()
    response = document_detail_response(db, item)
    db.commit()
    return response


@obligations_router.delete("/documents/{document_id}/match", response_model=DocumentDetailResponse, tags=["documents"])
def unmatch_document(document_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> DocumentDetailResponse:
    item = household_document(db, membership.household_id, document_id)
    matches = db.scalars(select(DocumentMatch).where(DocumentMatch.document_id == item.id, DocumentMatch.status == "confirmed")).all()
    if not matches:
        raise HTTPException(status_code=409, detail="Document is not linked")
    for match in matches:
        match.status = "unmatched"
        match.reviewed_by_user_id = actor.id
        match.reviewed_at = utc_now()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="document.unmatched", resource_type="document", resource_id=str(item.id)))
    db.flush()
    response = document_detail_response(db, item)
    db.commit()
    return response


@obligations_router.post("/documents/{document_id}/extractions", response_model=DocumentExtractionResponse, status_code=202, tags=["documents"])
def queue_document_extraction(document_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> DocumentExtractionResponse:
    item = household_document(db, membership.household_id, document_id)
    values = load_integrations(db)
    if not (values.get("ai_enabled") and values.get("ai_extract_enabled")):
        raise HTTPException(status_code=409, detail="Local AI document extraction is disabled")
    if not (values.get("ai_provider") and values.get("ai_base_url")):
        raise HTTPException(status_code=409, detail="Local AI is not configured")
    validate_local_ai_url(values["ai_base_url"])
    active = db.scalar(select(DocumentExtraction).where(DocumentExtraction.document_id == item.id, DocumentExtraction.status.in_(["queued", "processing"])))
    if active:
        return extraction_response(active)
    extraction = DocumentExtraction(
        household_id=membership.household_id,
        document_id=item.id,
        requested_by_user_id=actor.id,
        provider=values["ai_provider"],
        model_version=values.get("ai_model") or ("llava" if values["ai_provider"] == "ollama" else "local-model"),
    )
    item.status = "extraction_queued"
    db.add(extraction)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="document.extraction_queued", resource_type="document", resource_id=str(item.id), detail=f"provider:{extraction.provider};model:{extraction.model_version}"))
    db.flush()
    response = extraction_response(extraction)
    db.commit()
    return response


@obligations_router.put("/documents/{document_id}/extractions/{extraction_id}", response_model=DocumentDetailResponse, tags=["documents"])
def decide_document_extraction(document_id: UUID, extraction_id: UUID, request: ExtractionDecisionRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(ledger_writer)]) -> DocumentDetailResponse:
    item = household_document(db, membership.household_id, document_id)
    extraction = db.scalar(select(DocumentExtraction).where(DocumentExtraction.id == extraction_id, DocumentExtraction.document_id == item.id, DocumentExtraction.household_id == membership.household_id))
    if not extraction:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    if extraction.status != "complete" or not extraction.output_json:
        raise HTTPException(status_code=409, detail="Extraction has no completed suggestions to review")
    if request.action == "accept":
        suggestions = json.loads(extraction.output_json)
        merchant = suggestions.get("merchant") if isinstance(suggestions.get("merchant"), dict) else {}
        transaction = suggestions.get("transaction") if isinstance(suggestions.get("transaction"), dict) else {}
        amounts = suggestions.get("amounts") if isinstance(suggestions.get("amounts"), dict) else {}
        suggested_payee = merchant.get("name") or suggestions.get("payee")
        suggested_date = transaction.get("date") or suggestions.get("transaction_date")
        suggested_total = amounts.get("total_minor") if amounts else suggestions.get("amount_minor")
        suggested_currency = amounts.get("currency_code") if amounts else suggestions.get("currency_code")
        if suggested_payee:
            item.payee = str(suggested_payee)[:200]
        if suggested_date:
            try:
                item.document_date = date.fromisoformat(str(suggested_date))
            except ValueError:
                pass
        if isinstance(suggested_total, int) and not isinstance(suggested_total, bool) and suggested_total >= 0:
            item.amount_minor = suggested_total
        if suggested_currency in {"USD", "CAD", "MXN"}:
            item.currency_code = suggested_currency
        refresh_document_matches(db, item)
        item.status = "reviewed"
    extraction.user_disposition = "accepted" if request.action == "accept" else "rejected"
    extraction.reviewed_by_user_id = actor.id
    extraction.reviewed_at = utc_now()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=f"document.extraction_{request.action}", resource_type="document_extraction", resource_id=str(extraction.id), detail="User reviewed suggestions; ledger unchanged"))
    db.flush()
    response = document_detail_response(db, item)
    db.commit()
    return response

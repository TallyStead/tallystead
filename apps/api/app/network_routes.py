import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlparse
from urllib.request import urlopen

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.config import settings
from app.dependencies import DbSession, current_user
from app.models import AuditEvent, Membership, PasskeyCredential, User
from app.networking import (
    active_configuration,
    caddy,
    canonical_url,
    cloudflare_zone_access,
    configuration_ready,
    effective_request,
    live_certificate,
    load_network_state,
    normalize_configuration,
    public_configuration,
    render_caddyfile,
    resolve_host,
    save_network_state,
    validation_checks,
)
from app.routes import owner_membership
from app.schemas import (
    CertificateStatusResponse,
    EffectiveRequestResponse,
    NetworkConfigurationRequest,
    NetworkConfigurationResponse,
    NetworkStatusResponse,
    NetworkTestResponse,
)

router = APIRouter(prefix="/v1")


def fingerprint(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def response_for(db: DbSession) -> NetworkStatusResponse:
    state = load_network_state(db)
    active = state.get("active") or active_configuration(db)
    staged = state.get("staged")
    try:
        certificate = live_certificate(active) if settings.network_controller_enabled else None
    except (OSError, ValueError):
        certificate = None
    certificate_response = CertificateStatusResponse(**certificate) if certificate else CertificateStatusResponse(renewal_status="unavailable")
    return NetworkStatusResponse(
        active=NetworkConfigurationResponse(**public_configuration(active)),
        staged=NetworkConfigurationResponse(**public_configuration(staged)) if staged else None,
        last_known_good=NetworkConfigurationResponse(**public_configuration(state.get("last_known_good"))) if state.get("last_known_good") else None,
        last_test=NetworkTestResponse(**state["last_test"]) if state.get("last_test") else None,
        revision=int(state.get("revision", 0)),
        canonical_change_warning=bool(staged and staged.get("canonical_url") != active.get("canonical_url") and db.query(PasskeyCredential).count()),
        certificate=certificate_response,
    )


@router.get("/system/network", response_model=NetworkStatusResponse, tags=["system"])
def network_status(db: DbSession, _: Annotated[Membership, Depends(owner_membership)]) -> NetworkStatusResponse:
    return response_for(db)


@router.put("/system/network/stage", response_model=NetworkStatusResponse, tags=["system"])
def stage_network_configuration(
    request: NetworkConfigurationRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner_membership)],
) -> NetworkStatusResponse:
    state = load_network_state(db)
    base = state.get("staged") or state.get("active") or active_configuration(db)
    staged = normalize_configuration(request.model_dump(), base)
    checks = validation_checks(staged)
    if not configuration_ready(checks):
        detail = next(item["detail"] for item in checks if item["status"] == "fail")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
    state["staged"] = staged
    state["last_test"] = None
    save_network_state(db, state, actor.id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="system.network_configuration_staged", resource_type="network_configuration", resource_id=fingerprint(staged)[:16], detail="Network configuration staged; credentials remain encrypted and write-only"))
    db.commit()
    return response_for(db)


def run_tests(config: dict) -> list[dict]:
    checks = validation_checks(config)
    if not configuration_ready(checks):
        return checks
    try:
        caddy.adapt(render_caddyfile(config))
        checks.append({"name": "caddy_configuration", "status": "pass", "detail": "Caddy accepted the staged configuration syntax."})
    except (OSError, ValueError):
        checks.append({"name": "caddy_configuration", "status": "fail", "detail": "Caddy could not validate the staged configuration."})
    if config.get("certificate_mode") == "cloudflare_dns":
        ok, detail = cloudflare_zone_access(config)
        checks.append({"name": "cloudflare_zone_access", "status": "pass" if ok else "fail", "detail": detail})
    for label, value in (("canonical_dns", config.get("canonical_url")), ("internal_dns", config.get("internal_url"))):
        if not value:
            continue
        hostname = urlparse(value).hostname
        if hostname:
            ok, detail = resolve_host(hostname)
            checks.append({"name": label, "status": "pass" if ok else "fail", "detail": detail})
    try:
        with urlopen("http://web:3000", timeout=5) as response:
            healthy = response.status < 400
    except OSError:
        healthy = False
    checks.append({"name": "web_upstream", "status": "pass" if healthy else "fail", "detail": "Tallystead web upstream is healthy." if healthy else "Tallystead web upstream did not respond."})
    return checks


@router.post("/system/network/test", response_model=NetworkTestResponse, tags=["system"])
def test_network_configuration(
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner_membership)],
) -> NetworkTestResponse:
    state = load_network_state(db)
    staged = state.get("staged")
    if not staged:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Stage network changes before testing them")
    checks = run_tests(staged) if settings.network_controller_enabled else validation_checks(staged)
    tested_at = datetime.now(UTC).isoformat()
    result = {"ready": configuration_ready(checks), "tested_at": tested_at, "checks": checks, "configuration_fingerprint": fingerprint(staged)}
    state["last_test"] = result
    save_network_state(db, state, actor.id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="system.network_configuration_tested", resource_type="network_configuration", resource_id=fingerprint(staged)[:16], detail=f"ready:{result['ready']}"))
    db.commit()
    return NetworkTestResponse(**result)


def load_or_fail(config: dict) -> None:
    if settings.network_controller_enabled:
        caddy.load(render_caddyfile(config))
        last_error: OSError | ValueError | None = None
        for _ in range(5):
            try:
                live_certificate(config, verify=True)
                return
            except (OSError, ValueError) as exc:
                last_error = exc
                time.sleep(1)
        if last_error:
            raise last_error


@router.post("/system/network/apply", response_model=NetworkStatusResponse, tags=["system"])
def apply_network_configuration(
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner_membership)],
) -> NetworkStatusResponse:
    state = load_network_state(db)
    staged = state.get("staged")
    tested = state.get("last_test")
    if not staged or not tested or not tested.get("ready") or tested.get("configuration_fingerprint") != fingerprint(staged):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The staged configuration must pass its current test before activation")
    previous = state.get("active") or active_configuration(db)
    try:
        load_or_fail(staged)
    except (OSError, ValueError):
        try:
            if settings.network_controller_enabled:
                caddy.load(render_caddyfile(previous))
        except (OSError, ValueError):
            pass
        db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="system.network_configuration_rolled_back", resource_type="network_configuration", resource_id=fingerprint(previous)[:16], detail="Staged activation failed; restored last known-good configuration"))
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Activation failed and the last known-good network configuration was restored")
    state["last_known_good"] = previous
    state["active"] = staged
    state["staged"] = None
    state["last_test"] = None
    state["revision"] = int(state.get("revision", 0)) + 1
    save_network_state(db, state, actor.id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="system.network_configuration_applied", resource_type="network_configuration", resource_id=fingerprint(staged)[:16], detail=f"revision:{state['revision']};canonical_origin_changed:{previous.get('canonical_url') != staged.get('canonical_url')}"))
    db.commit()
    return response_for(db)


@router.post("/system/network/rollback", response_model=NetworkStatusResponse, tags=["system"])
def rollback_network_configuration(
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner_membership)],
) -> NetworkStatusResponse:
    state = load_network_state(db)
    target = state.get("last_known_good")
    current = state.get("active") or active_configuration(db)
    if not target:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No last known-good configuration is available")
    try:
        load_or_fail(target)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Caddy could not restore the last known-good configuration") from exc
    state["active"] = target
    state["last_known_good"] = current
    state["staged"] = None
    state["last_test"] = None
    state["revision"] = int(state.get("revision", 0)) + 1
    save_network_state(db, state, actor.id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="system.network_configuration_manual_rollback", resource_type="network_configuration", resource_id=fingerprint(target)[:16], detail=f"revision:{state['revision']}"))
    db.commit()
    return response_for(db)


@router.get("/system/network/effective-request", response_model=EffectiveRequestResponse, tags=["system"])
def effective_request_diagnostic(request: Request, db: DbSession, _: Annotated[Membership, Depends(owner_membership)]) -> EffectiveRequestResponse:
    config = active_configuration(db)
    result = effective_request(request.client.host if request.client else None, {key.lower(): value for key, value in request.headers.items()}, config)
    return EffectiveRequestResponse(effective_url=f"{result.scheme}://{result.host}", scheme=result.scheme, host=result.host, source_address=result.client_ip, forwarded_headers_trusted=result.forwarded_headers_trusted)


@router.get("/system/network/caddy-root", tags=["system"])
def export_caddy_root(_: Annotated[Membership, Depends(owner_membership)]) -> Response:
    if not settings.network_controller_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Caddy controller is not enabled")
    try:
        chain = caddy.local_ca_chain()
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Caddy local CA is unavailable") from exc
    return Response(content=chain, media_type="application/x-pem-file", headers={"Content-Disposition": "attachment; filename=tallystead-caddy-root.pem"})


@router.get("/server/canonical-url", tags=["system"])
def canonical_url_detail(db: DbSession) -> dict[str, str]:
    return {"canonical_url": canonical_url(db)}

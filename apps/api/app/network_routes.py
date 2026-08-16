from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.dependencies import DbSession
from app.models import Membership
from app.networking import (
    caddy,
    canonical_url,
    effective_request,
    environment_configuration,
    live_certificate,
)
from app.routes import owner_membership
from app.schemas import (
    CertificateStatusResponse,
    EffectiveRequestResponse,
    NetworkConfigurationResponse,
    NetworkStatusResponse,
)

router = APIRouter(prefix="/v1")


@router.get("/system/network", response_model=NetworkStatusResponse, tags=["system"])
def network_status(_: Annotated[Membership, Depends(owner_membership)]) -> NetworkStatusResponse:
    try:
        certificate = live_certificate()
    except (OSError, ValueError):
        certificate = None
    return NetworkStatusResponse(
        configuration=NetworkConfigurationResponse(**environment_configuration()),
        certificate=CertificateStatusResponse(**certificate) if certificate else CertificateStatusResponse(renewal_status="unavailable"),
    )


@router.get("/system/network/effective-request", response_model=EffectiveRequestResponse, tags=["system"])
def effective_request_diagnostic(request: Request, _: Annotated[Membership, Depends(owner_membership)]) -> EffectiveRequestResponse:
    transport_address = request.client.host if request.client else None
    incoming = {key.lower(): value for key, value in request.headers.items()}
    result = effective_request(transport_address, incoming)
    sensitive = {"authorization", "cookie", "proxy-authorization", "x-tallystead-proxy-token"}
    safe_values = {
        "accept", "accept-encoding", "accept-language", "connection", "content-type", "host", "origin", "referer",
        "user-agent", "via", "x-forwarded-for", "x-forwarded-host", "x-forwarded-port", "x-forwarded-proto", "x-real-ip",
        "x-tallystead-forward-auth-email", "x-tallystead-forward-auth-name", "x-tallystead-forward-auth-source",
        "x-tallystead-forward-auth-subject",
    }
    headers = []
    for name, value in sorted(incoming.items()):
        displayed = "[redacted]" if name in sensitive else value[:1000] if name in safe_values else "[value omitted]"
        headers.append({"name": name, "value": displayed})
    if result.forwarded_headers_trusted:
        route = "trusted_proxy"
    elif any(name.startswith("x-forwarded-") for name in incoming):
        route = "untrusted_forwarded_headers"
    else:
        route = "direct"
    return EffectiveRequestResponse(
        effective_url=f"{result.scheme}://{result.host}", scheme=result.scheme, host=result.host,
        source_address=result.client_ip, transport_address=transport_address, connection_route=route,
        forwarded_headers_trusted=result.forwarded_headers_trusted, headers=headers,
    )


@router.get("/system/network/caddy-root", tags=["system"])
def export_caddy_root(_: Annotated[Membership, Depends(owner_membership)]) -> Response:
    try:
        chain = caddy.local_ca_chain()
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Caddy local CA is unavailable") from exc
    return Response(content=chain, media_type="application/x-pem-file", headers={"Content-Disposition": "attachment; filename=tallystead-caddy-root.pem"})


@router.get("/server/canonical-url", tags=["system"])
def canonical_url_detail(_db: DbSession) -> dict[str, str]:
    return {"canonical_url": canonical_url()}

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.assistant_routes import router as assistant_router
from app.automation_routes import router as automation_router
from app.config import settings
from app.data_routes import router as data_router
from app.ingestion_routes import router as ingestion_router
from app.network_routes import router as network_router
from app.networking import (
    allowed_https_authorities,
    effective_request,
    environment_configuration,
    normalized_https_authority,
)
from app.plan_routes import router as plan_router
from app.routes import obligations_router, router

app = FastAPI(title="Tallystead API", version="0.2.1")


@app.middleware("http")
async def network_boundary(request: Request, call_next):
    config = environment_configuration()
    headers = {key.lower(): value for key, value in request.headers.items()}
    effective = effective_request(request.client.host if request.client else None, headers, config)
    request.state.effective_request = effective
    allowed_origins = {settings.public_url.rstrip("/"), *settings.cors_origins, config["canonical_url"].rstrip("/")}
    if config.get("internal_url"):
        allowed_origins.add(config["internal_url"].rstrip("/"))
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in allowed_origins:
        return JSONResponse(status_code=403, content={"detail": "Origin is not allowed by the canonical server configuration"})
    if (
        settings.network_enforcement_enabled
        and request.url.path.startswith("/v1/")
        and (normalized_https_authority(effective.host) not in allowed_https_authorities(config) or effective.scheme != "https")
    ):
        return JSONResponse(status_code=421, content={"detail": "Request does not match the configured HTTPS server identity"})
    if request.method == "OPTIONS" and origin:
        response = Response(status_code=204)
    else:
        response = await call_next(request)
    if origin and origin.rstrip("/") in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Vary"] = "Origin"
    # Individual download/report responses may add the stronger ``private``
    # directive. Preserve it while making every other API response non-cacheable.
    if "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    )
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if effective.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


app.include_router(router)
app.include_router(obligations_router)
app.include_router(assistant_router)
app.include_router(automation_router)
app.include_router(ingestion_router)
app.include_router(plan_router)
app.include_router(data_router)
app.include_router(network_router)


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    version: str


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="tallystead-api",
        environment=settings.environment,
        version=app.version,
    )

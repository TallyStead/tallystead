import ipaddress
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TALLYSTEAD_", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+psycopg://tallystead:tallystead@localhost:5432/tallystead"
    object_store_endpoint: str = "http://localhost:9000"
    object_store_access_key: str = "tallystead"
    object_store_secret_key: str = "tallystead"
    object_store_bucket: str = "tallystead-documents"
    max_document_bytes: int = 15_000_000
    secret_key: str = "development-only-change-me"
    allowed_origins: str = "https://localhost:8443,http://localhost:3001,http://localhost:3000"
    public_url: str = "https://localhost:8443"
    internal_url: str | None = None
    access_mode: str = "lan"
    trusted_proxy_cidrs: str = ""
    forward_auth_enabled: bool = False
    certificate_mode: str = "local_ca"
    caddy_admin_socket: str = "/run/tallystead-caddy/admin.sock"
    caddy_service_host: str = "caddy"
    caddy_service_port: int = 443
    network_enforcement_enabled: bool = False
    caddy_proxy_cidrs: str = "127.0.0.0/8,::1/128,172.16.0.0/12"
    proxy_shared_secret: str | None = None
    smtp_host: str | None = None
    backup_directory: str = "/backups"
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    session_idle_minutes: int = Field(default=1440, ge=15, le=43200)
    session_touch_minutes: int = Field(default=5, ge=1, le=60)

    @model_validator(mode="after")
    def validate_network_environment(self):
        public = urlparse(self.public_url)
        if public.scheme != "https" or not public.hostname:
            raise ValueError("TALLYSTEAD_PUBLIC_URL must be a complete HTTPS URL")
        if self.internal_url:
            internal = urlparse(self.internal_url)
            if internal.scheme != "https" or not internal.hostname:
                raise ValueError("TALLYSTEAD_INTERNAL_URL must be empty or a complete HTTPS URL")
        if self.access_mode not in {"lan", "reverse_proxy", "vpn", "internet"}:
            raise ValueError("TALLYSTEAD_ACCESS_MODE is not supported")
        if self.certificate_mode not in {"local_ca", "external_tls"}:
            raise ValueError("TALLYSTEAD_CERTIFICATE_MODE must be local_ca or external_tls")
        for value in self.proxy_cidrs:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(f"Invalid trusted proxy CIDR: {value}") from exc
        if self.access_mode == "reverse_proxy" and not self.proxy_cidrs:
            raise ValueError("Reverse-proxy mode requires TALLYSTEAD_TRUSTED_PROXY_CIDRS")
        if self.forward_auth_enabled and self.access_mode != "reverse_proxy":
            raise ValueError("Forwarded authentication requires reverse-proxy mode")
        configured_origins = {origin.rstrip("/") for origin in self.cors_origins}
        required_origins = {self.public_url.rstrip("/")}
        if self.internal_url:
            required_origins.add(self.internal_url.rstrip("/"))
        if not required_origins.issubset(configured_origins):
            raise ValueError("TALLYSTEAD_ALLOWED_ORIGINS must include the public and internal URLs")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def proxy_cidrs(self) -> list[str]:
        return [value.strip() for value in self.trusted_proxy_cidrs.replace(",", " ").split() if value.strip()]


settings = Settings()

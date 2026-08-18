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
    server_host: str | None = None
    allowed_origins: str = "http://localhost:3000,http://localhost:3001"
    public_url: str | None = None
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
        for label, value in (("TALLYSTEAD_PUBLIC_URL", self.public_url), ("TALLYSTEAD_INTERNAL_URL", self.internal_url)):
            if value:
                parsed = urlparse(value)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ValueError(f"{label} must be empty or a complete HTTP(S) URL")
        for value in self.proxy_cidrs:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(f"Invalid trusted proxy CIDR: {value}") from exc
        if self.forward_auth_enabled and not self.proxy_cidrs:
            raise ValueError("Forwarded authentication requires TALLYSTEAD_TRUSTED_PROXY_CIDRS")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def proxy_cidrs(self) -> list[str]:
        return [value.strip() for value in self.trusted_proxy_cidrs.replace(",", " ").split() if value.strip()]


settings = Settings()

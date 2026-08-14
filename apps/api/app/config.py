from pydantic import Field
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
    allowed_origins: str = "http://localhost:3001,http://localhost:3000"
    public_url: str = "https://localhost:8443"
    caddy_admin_socket: str = "/run/tallystead-caddy/admin.sock"
    caddy_service_host: str = "caddy"
    caddy_service_port: int = 443
    network_controller_enabled: bool = False
    network_enforcement_enabled: bool = False
    caddy_proxy_cidrs: str = "127.0.0.0/8,::1/128,172.16.0.0/12"
    proxy_shared_secret: str | None = None
    smtp_host: str | None = None
    backup_directory: str = "/backups"
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    session_idle_minutes: int = Field(default=1440, ge=15, le=43200)
    session_touch_minutes: int = Field(default=5, ge=1, le=60)

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


settings = Settings()

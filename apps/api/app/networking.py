import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from cryptography import x509

from app.config import settings


def environment_configuration() -> dict:
    return {
        "canonical_url": settings.public_url.rstrip("/"),
        "internal_url": settings.internal_url.rstrip("/") if settings.internal_url else None,
        "access_mode": settings.access_mode,
        "trusted_proxy_cidrs": settings.proxy_cidrs,
        "forward_auth_enabled": settings.forward_auth_enabled,
        "certificate_mode": settings.certificate_mode,
    }


def active_configuration(_db=None) -> dict:
    """Return environment-owned configuration; the database is not consulted."""
    return environment_configuration()


def canonical_url(_db=None) -> str:
    return settings.public_url.rstrip("/")


def client_in_cidrs(client_ip: str | None, cidrs: list[str]) -> bool:
    if not client_ip:
        return False
    try:
        address = ipaddress.ip_address(client_ip)
        return any(address in ipaddress.ip_network(value, strict=False) for value in cidrs)
    except ValueError:
        return False


def trusted_forward_auth_source(source: str | None, config: dict | None = None) -> bool:
    config = config or environment_configuration()
    return bool(
        source
        and config.get("forward_auth_enabled")
        and config.get("access_mode") == "reverse_proxy"
        and client_in_cidrs(source, config.get("trusted_proxy_cidrs", []))
    )


def normalized_https_authority(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return ""
    return hostname if parsed.port in {None, 443} else f"{hostname}:{parsed.port}"


def allowed_https_authorities(config: dict | None = None) -> set[str]:
    config = config or environment_configuration()
    return {
        normalized_https_authority(value)
        for value in (config.get("canonical_url"), config.get("internal_url"))
        if value
    }


@dataclass
class EffectiveRequest:
    client_ip: str | None
    scheme: str
    host: str
    forwarded_headers_trusted: bool


def effective_request(client_ip: str | None, headers: dict[str, str], _config: dict | None = None) -> EffectiveRequest:
    caddy_cidrs = [item.strip() for item in settings.caddy_proxy_cidrs.split(",") if item.strip()]
    secret_ok = bool(settings.proxy_shared_secret and headers.get("x-tallystead-proxy-token") == settings.proxy_shared_secret)
    trusted = client_in_cidrs(client_ip, caddy_cidrs) and secret_ok
    host = headers.get("host", "")
    scheme = "https" if headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https" and trusted else "http"
    source = client_ip
    if trusted:
        host = headers.get("x-forwarded-host") or host
        forwarded_for = headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded_for:
            source = forwarded_for
    return EffectiveRequest(client_ip=source, scheme=scheme, host=host, forwarded_headers_trusted=trusted)


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 8):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class CaddyController:
    def request(self, method: str, path: str) -> bytes:
        connection = UnixHTTPConnection(settings.caddy_admin_socket)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            body = response.read()
            if response.status >= 400:
                raise OSError(f"Caddy admin request failed with status {response.status}")
            return body
        finally:
            connection.close()

    def local_ca_chain(self) -> bytes:
        return self.request("GET", "/pki/ca/local/certificates")


caddy = CaddyController()


def live_certificate() -> dict | None:
    host = urlparse(settings.internal_url or settings.public_url).hostname
    if not host:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection((settings.caddy_service_host, settings.caddy_service_port), timeout=8) as raw,
        context.wrap_socket(raw, server_hostname=host) as secured,
    ):
        certificate = x509.load_der_x509_certificate(secured.getpeercert(binary_form=True))
    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    expires = certificate.not_valid_after_utc
    return {
        "subject": certificate.subject.rfc4514_string(),
        "issuer": certificate.issuer.rfc4514_string(),
        "names": names,
        "expires_at": expires.isoformat(),
        "renewal_status": "valid" if expires > datetime.now(UTC) else "expired",
    }

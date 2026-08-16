import http.client
import ipaddress
import json
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography import x509
from sqlalchemy.orm import Session

from app.config import settings
from app.settings_store import load_encrypted_setting, save_encrypted_setting

NETWORK_KEY = "network_configuration"
ACCESS_MODES = {"lan", "reverse_proxy", "vpn", "internet"}
CERTIFICATE_MODES = {"local_ca", "public_acme", "cloudflare_dns", "external_tls"}
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_\-.]{20,256}$")


def default_configuration() -> dict:
    return {
        "canonical_url": settings.public_url.rstrip("/"),
        "internal_url": None,
        "access_mode": "lan",
        "trusted_proxy_cidrs": [],
        "forward_auth_enabled": False,
        "certificate_mode": "local_ca",
        "dns_provider": None,
        "dns_zone": None,
        "cloudflare_api_token": None,
        "acme_email": None,
        "internet_exposure_confirmed": False,
    }


def load_network_state(db: Session) -> dict:
    stored = load_encrypted_setting(db, NETWORK_KEY)
    if not stored:
        initial = default_configuration()
        return {"active": initial, "staged": None, "last_known_good": initial, "last_test": None, "revision": 0}
    return stored


def save_network_state(db: Session, state: dict, actor_user_id) -> None:
    save_encrypted_setting(db, NETWORK_KEY, state, actor_user_id, merge=False)


def active_configuration(db: Session) -> dict:
    return load_network_state(db).get("active") or default_configuration()


def canonical_url(db: Session) -> str:
    return active_configuration(db)["canonical_url"].rstrip("/")


def public_configuration(config: dict | None) -> dict | None:
    if config is None:
        return None
    return {
        "canonical_url": config.get("canonical_url"),
        "internal_url": config.get("internal_url"),
        "access_mode": config.get("access_mode"),
        "trusted_proxy_cidrs": config.get("trusted_proxy_cidrs", []),
        "forward_auth_enabled": bool(config.get("forward_auth_enabled")),
        "certificate_mode": config.get("certificate_mode"),
        "dns_provider": config.get("dns_provider"),
        "dns_zone": config.get("dns_zone"),
        "cloudflare_configured": bool(config.get("cloudflare_api_token")),
        "acme_email": config.get("acme_email"),
        "internet_exposure_confirmed": bool(config.get("internet_exposure_confirmed")),
    }


def normalize_configuration(values: dict, existing: dict | None = None) -> dict:
    existing = existing or default_configuration()
    result = {**existing, **{key: value for key, value in values.items() if value is not None}}
    if values.get("internal_url") == "":
        result["internal_url"] = None
    if values.get("dns_zone") == "":
        result["dns_zone"] = None
    if values.get("acme_email") == "":
        result["acme_email"] = None
    if not values.get("cloudflare_api_token"):
        result["cloudflare_api_token"] = existing.get("cloudflare_api_token")
    result["canonical_url"] = str(result["canonical_url"]).rstrip("/")
    if result.get("internal_url"):
        result["internal_url"] = str(result["internal_url"]).rstrip("/")
    result["trusted_proxy_cidrs"] = [str(item).strip() for item in result.get("trusted_proxy_cidrs", []) if str(item).strip()]
    return result


def validation_checks(config: dict) -> list[dict]:
    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str, *, warning: bool = False) -> None:
        checks.append({"name": name, "status": "pass" if passed else ("warning" if warning else "fail"), "detail": detail})

    canonical = urlparse(config.get("canonical_url") or "")
    add("canonical_https", canonical.scheme == "https" and bool(canonical.hostname), "Canonical URL uses HTTPS and includes a hostname." if canonical.scheme == "https" and canonical.hostname else "Canonical URL must be a complete HTTPS URL.")
    internal_raw = config.get("internal_url")
    internal = urlparse(internal_raw) if internal_raw else None
    add("internal_https", internal is None or (internal.scheme == "https" and bool(internal.hostname)), "Internal upstream URL is HTTPS." if internal else "No separate internal upstream URL is configured.")
    add("access_mode", config.get("access_mode") in ACCESS_MODES, "Access mode is supported." if config.get("access_mode") in ACCESS_MODES else "Choose a supported access mode.")
    add("certificate_mode", config.get("certificate_mode") in CERTIFICATE_MODES, "Certificate mode is supported." if config.get("certificate_mode") in CERTIFICATE_MODES else "Choose a supported certificate mode.")
    cidrs_valid = True
    for value in config.get("trusted_proxy_cidrs", []):
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError:
            cidrs_valid = False
    add("trusted_proxy_cidrs", cidrs_valid, "Trusted proxy entries are valid CIDRs." if cidrs_valid else "One or more trusted proxy entries are not valid CIDRs.")
    if config.get("access_mode") in {"reverse_proxy", "internet"}:
        add("proxy_boundary", bool(config.get("trusted_proxy_cidrs")) or config.get("access_mode") == "internet", "Trusted proxy boundary is explicit." if config.get("trusted_proxy_cidrs") else "Direct internet mode has no upstream proxy CIDRs; forwarded headers will be ignored.", warning=config.get("access_mode") == "internet")
    if config.get("forward_auth_enabled"):
        forward_auth_ready = config.get("access_mode") == "reverse_proxy" and bool(config.get("trusted_proxy_cidrs"))
        add("forward_auth_boundary", forward_auth_ready, "Forwarded identity is limited to configured reverse proxies." if forward_auth_ready else "Forwarded identity requires reverse-proxy mode and at least one trusted proxy CIDR.")
    if config.get("certificate_mode") == "cloudflare_dns":
        token_ok = bool(config.get("cloudflare_api_token") and TOKEN_PATTERN.fullmatch(config["cloudflare_api_token"]))
        add("cloudflare_token", token_ok, "A write-only Cloudflare API token is configured." if token_ok else "Provide a valid scoped Cloudflare API token.")
        zone = (config.get("dns_zone") or "").strip(".").lower()
        hostnames = [item.hostname for item in (canonical, internal) if item and item.hostname]
        zone_ok = bool(zone) and all(host == zone or host.endswith(f".{zone}") for host in hostnames)
        add("cloudflare_zone", zone_ok, "Configured hostnames belong to the selected Cloudflare zone." if zone_ok else "Canonical and internal hostnames must belong to the configured Cloudflare zone.")
    if config.get("access_mode") == "internet":
        safe_cert = config.get("certificate_mode") in {"public_acme", "cloudflare_dns", "external_tls"}
        add("internet_certificate", safe_cert, "Internet mode uses a publicly trusted or externally terminated certificate." if safe_cert else "Internet mode cannot use the local Caddy CA.")
        add("internet_confirmation", bool(config.get("internet_exposure_confirmed")), "Owner explicitly acknowledged internet exposure." if config.get("internet_exposure_confirmed") else "Owner confirmation is required before internet exposure can be activated.")
    return checks


def configuration_ready(checks: list[dict]) -> bool:
    return all(item["status"] != "fail" for item in checks)


def trusted_forward_auth_source(source: str | None, config: dict) -> bool:
    if not source or not config.get("forward_auth_enabled") or config.get("access_mode") != "reverse_proxy":
        return False
    try:
        address = ipaddress.ip_address(source)
    except ValueError:
        return False
    for value in config.get("trusted_proxy_cidrs", []):
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False


def _site_hosts(config: dict) -> list[str]:
    hosts: list[str] = []
    for value in (config.get("canonical_url"), config.get("internal_url")):
        if value:
            parsed = urlparse(value)
            address = parsed.hostname or ""
            if address and address not in hosts:
                hosts.append(address)
    return hosts


def render_caddyfile(config: dict) -> str:
    checks = validation_checks(config)
    if not configuration_ready(checks):
        raise ValueError("Network configuration is not ready")
    trusted = config.get("trusted_proxy_cidrs", [])
    server_options = ""
    if trusted:
        server_options = "\n  servers {\n    trusted_proxies static " + " ".join(trusted) + "\n    trusted_proxies_strict\n    client_ip_headers X-Forwarded-For\n  }"
    email_option = f"\n  email {config['acme_email']}" if config.get("acme_email") else ""
    global_options = "{\n  admin unix//run/tallystead-caddy/admin.sock\n  persist_config off" + email_option + server_options + "\n}"
    tls = ""
    mode = config["certificate_mode"]
    if mode in {"local_ca", "external_tls"}:
        tls = "\n  tls internal"
    elif mode == "cloudflare_dns":
        tls = f"\n  tls {{\n    dns cloudflare {config['cloudflare_api_token']}\n    resolvers 1.1.1.1\n  }}"
    hosts = ", ".join(f"https://{host}" for host in _site_hosts(config))
    if config.get("forward_auth_enabled"):
        forward_auth_headers = """    header_up X-Tallystead-Forward-Auth-Subject {http.request.header.Remote-User}
    header_up X-Tallystead-Forward-Auth-Email {http.request.header.Remote-Email}
    header_up X-Tallystead-Forward-Auth-Name {http.request.header.Remote-Name}
    header_up X-Tallystead-Forward-Auth-Source {http.request.remote.host}"""
    else:
        forward_auth_headers = """    header_up -X-Tallystead-Forward-Auth-Subject
    header_up -X-Tallystead-Forward-Auth-Email
    header_up -X-Tallystead-Forward-Auth-Name
    header_up -X-Tallystead-Forward-Auth-Source"""
    routes = """  header {
    -Server
    -X-Powered-By
    Strict-Transport-Security "max-age=31536000"
    X-Content-Type-Options "nosniff"
    X-Frame-Options "DENY"
    Referrer-Policy "no-referrer"
    Permissions-Policy "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    Content-Security-Policy "default-src 'self'; base-uri 'self'; connect-src 'self'; font-src 'self'; form-action 'self'; frame-ancestors 'none'; img-src 'self' data: blob:; object-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
  }
  @api path /v1/* /health /docs* /openapi.json
  reverse_proxy @api api:8000 {
    flush_interval -1
    header_up Host {http.request.host}
    header_up X-Tallystead-Proxy-Token {env.TALLYSTEAD_PROXY_TOKEN}
__FORWARD_AUTH_HEADERS__
    header_up -Remote-User
    header_up -Remote-Email
    header_up -Remote-Name
    header_up -Remote-Role
  }

  reverse_proxy web:3000""".replace("__FORWARD_AUTH_HEADERS__", forward_auth_headers)
    return f"""{global_options}

{hosts} {{{tls}
{routes}
}}

http://:8080 {{
{routes}
}}
"""


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 8):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(self.socket_path)
        self.sock = connection


class CaddyController:
    def __init__(self, socket_path: str | None = None):
        self.socket_path = socket_path or settings.caddy_admin_socket

    def request(self, method: str, path: str, body: str | bytes | None = None, content_type: str = "application/json") -> bytes:
        connection = UnixHTTPConnection(self.socket_path)
        payload = body.encode() if isinstance(body, str) else body
        try:
            connection.request(method, path, body=payload, headers={"Content-Type": content_type})
            response = connection.getresponse()
            result = response.read()
            if response.status >= 400:
                raise OSError(f"Caddy rejected the configuration ({response.status}).")
            return result
        finally:
            connection.close()

    def adapt(self, caddyfile: str) -> None:
        self.request("POST", "/adapt", caddyfile, "text/caddyfile")

    def load(self, caddyfile: str) -> None:
        self.request("POST", "/load", caddyfile, "text/caddyfile")

    def local_ca_chain(self) -> bytes:
        return self.request("GET", "/pki/ca/local/certificates")


caddy = CaddyController()


def cloudflare_zone_access(config: dict) -> tuple[bool, str]:
    token = config.get("cloudflare_api_token")
    zone = config.get("dns_zone")
    if not token or not zone:
        return False, "Cloudflare token and DNS zone are required."
    request = Request(
        f"https://api.cloudflare.com/client/v4/zones?name={zone}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError, json.JSONDecodeError):
        return False, "Cloudflare could not verify this token and zone."
    if not payload.get("success") or len(payload.get("result", [])) != 1:
        return False, "Cloudflare token cannot read the selected zone."
    return True, "Cloudflare token can read the selected DNS zone."


def resolve_host(hostname: str) -> tuple[bool, str]:
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)})
    except OSError:
        return False, f"{hostname} does not resolve from the Tallystead server."
    return True, f"{hostname} resolves to {', '.join(addresses[:4])}."


def live_certificate(config: dict, verify: bool = False) -> dict | None:
    host = urlparse(config.get("internal_url") or config["canonical_url"]).hostname
    if not host:
        return None
    context = ssl.create_default_context()
    if config.get("certificate_mode") in {"local_ca", "external_tls"}:
        context.load_verify_locations(cadata=caddy.local_ca_chain().decode())
    if not verify:
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


def client_in_cidrs(client_ip: str | None, cidrs: list[str]) -> bool:
    if not client_ip:
        return False
    try:
        address = ipaddress.ip_address(client_ip)
        return any(address in ipaddress.ip_network(value, strict=False) for value in cidrs)
    except ValueError:
        return False


@dataclass
class EffectiveRequest:
    client_ip: str | None
    scheme: str
    host: str
    forwarded_headers_trusted: bool


def effective_request(client_ip: str | None, headers: dict[str, str], config: dict) -> EffectiveRequest:
    proxy_cidrs = [item.strip() for item in settings.caddy_proxy_cidrs.split(",") if item.strip()]
    secret_ok = bool(settings.proxy_shared_secret and headers.get("x-tallystead-proxy-token") == settings.proxy_shared_secret)
    trusted = client_in_cidrs(client_ip, proxy_cidrs) and secret_ok
    host = headers.get("host", "")
    scheme = "https" if headers.get("x-forwarded-proto") == "https" and trusted else "http"
    source = client_ip
    if trusted:
        host = headers.get("x-forwarded-host") or host
        forwarded_for = headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded_for:
            source = forwarded_for
    return EffectiveRequest(client_ip=source, scheme=scheme, host=host, forwarded_headers_trusted=trusted)

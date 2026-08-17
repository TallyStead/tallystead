import base64
import hashlib
import io
import json
import os
import time
import zipfile
from collections.abc import Generator
from datetime import timedelta
from uuid import UUID

os.environ["TALLYSTEAD_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    AuditEvent,
    ExternalIdentity,
    FinancialAccount,
    Household,
    PasskeyCredential,
    PasswordResetToken,
    SessionToken,
    SystemSetting,
    User,
    utc_now,
)

test_engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


def overridden_db() -> Generator[Session, None, None]:
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = overridden_db


def setup_function() -> None:
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)


def test_health_reports_service_metadata() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "tallystead-api"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_owner_can_create_export_delete_restore_and_reset_demo_household() -> None:
    client = TestClient(app)
    owner = client.post(
        "/v1/setup",
        json={"household_name": "Demo test household", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"},
    ).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}

    initial = client.get("/v1/data/status", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["can_create_demo"] is True

    started = time.perf_counter()
    created = client.post(
        "/v1/data/demo",
        headers=headers,
        json={"confirmation": "CREATE DEMO", "reference_date": "2026-08-13"},
    )
    demo_seconds = time.perf_counter() - started
    assert created.status_code == 200, created.text
    assert created.json()["transaction_count"] >= 80
    assert created.json()["review_count"] == 5
    assert demo_seconds < 5.0

    populated = client.get("/v1/data/status", headers=headers).json()
    assert populated["mode"] == "demo"
    assert populated["transaction_count"] == created.json()["transaction_count"]
    assert populated["document_count"] == 1
    assert populated["can_create_demo"] is False
    started = time.perf_counter()
    report = client.get(
        "/v1/reports/spending?date_from=2025-11-01&date_to=2026-08-13&currency_code=USD&ownership_scope=household&include_pending=true",
        headers=headers,
    )
    report_seconds = time.perf_counter() - started
    assert report.status_code == 200, report.text
    assert report.json()["totals"]["income_minor"] > 0
    assert report.json()["totals"]["spending_minor"] > 0
    assert report_seconds < 2.0

    exported = client.post("/v1/data/export", headers=headers)
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "tallystead-household-archive-v1"
        assert manifest["household_id"] == owner["household_id"]
        assert len(manifest["objects"]) == 1
        legacy_output = io.BytesIO()
        with zipfile.ZipFile(legacy_output, "w", compression=zipfile.ZIP_DEFLATED) as legacy_archive:
            for item in archive.infolist():
                content = archive.read(item.filename)
                if item.filename == "manifest.json":
                    legacy_manifest = json.loads(content)
                    legacy_manifest["format"] = "nestledger-household-archive-v1"
                    content = json.dumps(legacy_manifest).encode()
                legacy_archive.writestr(item, content)

    deleted = client.request(
        "DELETE",
        "/v1/data/household",
        headers=headers,
        json={"confirmation": "DELETE Demo test household"},
    )
    assert deleted.status_code == 200, deleted.text
    assert client.get("/v1/auth/me", headers=headers).status_code == 200
    empty = client.get("/v1/data/status", headers=headers).json()
    assert empty["transaction_count"] == 0
    assert empty["document_count"] == 0

    restored = client.post(
        "/v1/data/import",
        headers=headers,
        json={"confirmation": "RESTORE MY DATA", "archive_base64": base64.b64encode(legacy_output.getvalue()).decode()},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "restored"
    assert restored.json()["manifest"]["format"] == "nestledger-household-archive-v1"
    assert restored.json()["summary"]["transaction_count"] == populated["transaction_count"]
    assert restored.json()["summary"]["document_count"] == 1

    reset = client.post(
        "/v1/data/demo/reset",
        headers=headers,
        json={"confirmation": "RESET DEMO", "reference_date": "2026-08-13"},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["transaction_count"] == populated["transaction_count"]
    reset_documents = client.get("/v1/documents", headers=headers).json()
    assert len(reset_documents) == 1
    reset_document_content = client.get(f"/v1/documents/{reset_documents[0]['document_id']}/content", headers=headers)
    assert reset_document_content.status_code == 200
    assert b"FICTIONAL DEMO RECEIPT" in reset_document_content.content

    removed = client.request("DELETE", "/v1/data/demo", headers=headers, json={"confirmation": "REMOVE DEMO"})
    assert removed.status_code == 200, removed.text
    assert removed.json()["summary"]["mode"] == "standard"
    assert removed.json()["summary"]["transaction_count"] == 0


def test_server_identity_uses_the_current_routed_host() -> None:
    response = TestClient(app).get("/v1/server/identity", headers={"Host": "10.10.200.204"})

    assert response.status_code == 200
    assert response.json()["public_url"] == "http://10.10.200.204"
    assert response.json()["api_version"] == "v1"


def test_standalone_browser_origin_can_use_bearer_api_without_configured_allowlist() -> None:
    client = TestClient(app)
    origin = "https://standalone.example.test"
    preflight = client.options(
        "/v1/setup/status",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Authorization"},
    )
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == origin
    response = client.get("/v1/setup/status", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers


def test_network_configuration_is_read_only_and_environment_owned(monkeypatch) -> None:
    from app.config import settings as runtime_settings

    monkeypatch.setattr(runtime_settings, "public_url", "https://ledger.example.test")
    monkeypatch.setattr(runtime_settings, "internal_url", "https://service.example.test")
    monkeypatch.setattr(runtime_settings, "access_mode", "reverse_proxy")
    monkeypatch.setattr(runtime_settings, "trusted_proxy_cidrs", "10.20.0.9/32")
    monkeypatch.setattr(runtime_settings, "forward_auth_enabled", True)
    monkeypatch.setattr(runtime_settings, "certificate_mode", "external_tls")
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    response = client.get("/v1/system/network", headers=headers)
    assert response.status_code == 200
    assert response.json()["configuration"] == {
        "canonical_url": "https://ledger.example.test", "internal_url": "https://service.example.test",
        "access_mode": "reverse_proxy", "trusted_proxy_cidrs": ["10.20.0.9/32"],
        "forward_auth_enabled": True, "certificate_mode": "external_tls",
    }
    assert client.put("/v1/system/network/stage", headers=headers, json={}).status_code == 404
    assert client.get("/v1/server/identity").json()["public_url"] == "http://testserver"


def test_phase6a_forwarded_headers_require_caddy_network_and_shared_secret(monkeypatch) -> None:
    from app.config import settings as runtime_settings
    from app.networking import effective_request

    monkeypatch.setattr(runtime_settings, "caddy_proxy_cidrs", "172.20.0.0/16")
    monkeypatch.setattr(runtime_settings, "proxy_shared_secret", "shared-proxy-secret")
    config = {"canonical_url": "https://ledger.example.com", "internal_url": None}
    spoofed = effective_request("192.168.1.50", {"host": "api:8000", "x-forwarded-host": "evil.example", "x-forwarded-proto": "https", "x-forwarded-for": "203.0.113.7", "x-tallystead-proxy-token": "shared-proxy-secret"}, config)
    assert spoofed.forwarded_headers_trusted is False
    assert spoofed.host == "api:8000" and spoofed.client_ip == "192.168.1.50"
    trusted = effective_request("172.20.0.4", {"host": "api:8000", "x-forwarded-host": "ledger.example.com", "x-forwarded-proto": "https", "x-forwarded-for": "203.0.113.7", "x-tallystead-proxy-token": "shared-proxy-secret"}, config)
    assert trusted.forwarded_headers_trusted is True
    assert trusted.host == "ledger.example.com" and trusted.scheme == "https" and trusted.client_ip == "203.0.113.7"


def test_owner_connection_diagnostic_reports_route_and_redacts_secrets(monkeypatch) -> None:
    from app.config import settings as runtime_settings

    monkeypatch.setattr(runtime_settings, "caddy_proxy_cidrs", "127.0.0.0/8")
    monkeypatch.setattr(runtime_settings, "proxy_shared_secret", "test-proxy-secret")
    client = TestClient(app, client=("127.0.0.1", 50000))
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    response = client.get(
        "/v1/system/network/effective-request",
        headers={
            "Authorization": f"Bearer {owner['access_token']}",
            "Cookie": "private=value",
            "X-Tallystead-Proxy-Token": "test-proxy-secret",
            "X-Forwarded-For": "10.55.0.9",
            "X-Forwarded-Host": "tallystead.example.test",
            "X-Forwarded-Proto": "https",
            "User-Agent": "Tallystead diagnostic test",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["connection_route"] == "trusted_proxy"
    assert payload["transport_address"] == "127.0.0.1"
    assert payload["source_address"] == "10.55.0.9"
    headers = {item["name"]: item["value"] for item in payload["headers"]}
    assert headers["authorization"] == "[redacted]"
    assert headers["cookie"] == "[redacted]"
    assert headers["x-tallystead-proxy-token"] == "[redacted]"
    assert headers["x-forwarded-for"] == "10.55.0.9"
    assert owner["access_token"] not in response.text
    assert "private=value" not in response.text
    assert "test-proxy-secret" not in response.text


def test_transaction_page_searches_filters_and_pages_complete_ledger() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Household Checking", "account_type": "checking", "currency_code": "USD"}).json()
    savings = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Rainy Day Savings", "account_type": "savings", "currency_code": "USD"}).json()
    groceries = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Groceries")
    for index in range(12):
        account = checking if index < 11 else savings
        response = client.post(
            "/v1/ledger/transactions",
            headers=headers,
            json={
                "account_id": account["account_id"],
                "transaction_date": f"2026-08-{index + 1:02d}",
                "amount_minor": 50000 if index == 0 else -(1000 + index),
                "currency_code": "USD",
                "status": "pending" if index == 10 else "posted",
                "payee": "Acme Market" if index in {2, 7} else f"Payee {index}",
                "memo": "weekly food" if index == 7 else None,
                "splits": [] if index == 0 else [{"category_id": groceries["category_id"], "amount_minor": -(1000 + index)}],
            },
        )
        assert response.status_code == 201, response.text

    first = client.get("/v1/ledger/transactions/page?page=1&page_size=10", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["total_items"] == 12
    assert first.json()["total_pages"] == 2
    assert len(first.json()["items"]) == 10
    assert first.json()["items"][0]["transaction_date"] == "2026-08-12"
    second = client.get("/v1/ledger/transactions/page?page=2&page_size=10", headers=headers).json()
    assert len(second["items"]) == 2

    searched = client.get("/v1/ledger/transactions/page?search=weekly%20food&page_size=10", headers=headers).json()
    assert searched["total_items"] == 1 and searched["items"][0]["payee"] == "Acme Market"
    category = client.get(f"/v1/ledger/transactions/page?category_id={groceries['category_id']}&page_size=10", headers=headers).json()
    assert category["total_items"] == 11
    filtered = client.get(
        f"/v1/ledger/transactions/page?account_id={checking['account_id']}&transaction_status=pending&direction=outflow&date_from=2026-08-10&date_to=2026-08-11&page_size=10",
        headers=headers,
    ).json()
    assert filtered["total_items"] == 1 and filtered["items"][0]["transaction_date"] == "2026-08-11"
    candidates = client.get(
        f"/v1/ledger/transactions/page?transaction_status=posted&direction=outflow&currency_code=usd&amount_minor=-1002&exclude_account_id={savings['account_id']}&has_transfer=false&has_splits=true&page_size=10",
        headers=headers,
    )
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()["total_items"] == 1
    assert candidates.json()["items"][0]["transaction_date"] == "2026-08-03"
    without_splits = client.get(
        "/v1/ledger/transactions/page?transaction_status=posted&direction=inflow&has_splits=false&page_size=10",
        headers=headers,
    ).json()
    assert without_splits["total_items"] == 1
    invalid_dates = client.get("/v1/ledger/transactions/page?date_from=2026-08-12&date_to=2026-08-01", headers=headers)
    assert invalid_dates.status_code == 422


def test_pangolin_forward_auth_links_only_an_existing_active_member(monkeypatch) -> None:
    from app.config import settings as runtime_settings

    monkeypatch.setattr(runtime_settings, "caddy_proxy_cidrs", "127.0.0.0/8")
    monkeypatch.setattr(runtime_settings, "proxy_shared_secret", "test-proxy-secret")
    monkeypatch.setattr(runtime_settings, "public_url", "https://tallystead.example.test")
    monkeypatch.setattr(runtime_settings, "access_mode", "reverse_proxy")
    monkeypatch.setattr(runtime_settings, "trusted_proxy_cidrs", "10.55.0.0/24")
    monkeypatch.setattr(runtime_settings, "forward_auth_enabled", True)
    client = TestClient(app, client=("127.0.0.1", 50000))
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    member = client.post("/v1/household/members", headers=owner_headers, json={"email": "member@example.com", "display_name": "Member", "password": "another-long-local-password", "role": "viewer"}).json()
    proxy_headers = {
        "Host": "api:8000",
        "X-Forwarded-Host": "tallystead.example.test",
        "X-Forwarded-Proto": "https",
        "X-Tallystead-Proxy-Token": "test-proxy-secret",
        "X-Tallystead-Forward-Auth-Source": "10.55.0.8",
        "X-Tallystead-Forward-Auth-Subject": "pangolin-user-123",
        "X-Tallystead-Forward-Auth-Email": "member@example.com",
        "X-Tallystead-Forward-Auth-Name": "Pangolin Display Name",
        "Remote-Role": "admin",
    }
    status_response = client.get("/v1/auth/proxy/status", headers=proxy_headers)
    assert status_response.status_code == 200
    assert status_response.json() == {"available": True, "email": "member@example.com", "display_name": "Pangolin Display Name"}
    signed_in = client.post("/v1/auth/proxy/login", headers=proxy_headers, json={"device_name": "Pangolin browser"})
    assert signed_in.status_code == 200
    assert signed_in.json()["user_id"] == member["user_id"]
    assert signed_in.json()["role"] == "viewer"
    with TestSession() as db:
        identity = db.query(ExternalIdentity).one()
        assert identity.subject == "pangolin-user-123"
        assert identity.email_at_link == "member@example.com"
        assert db.query(AuditEvent).filter(AuditEvent.action == "auth.external_identity_linked").count() == 1
        assert db.query(AuditEvent).filter(AuditEvent.action == "auth.session_created", AuditEvent.detail == "pangolin_forward_auth").count() == 1

    changed_email_headers = {**proxy_headers, "X-Tallystead-Forward-Auth-Email": "changed@example.com"}
    assert client.post("/v1/auth/proxy/login", headers=changed_email_headers, json={}).status_code == 403
    unmatched_headers = {**proxy_headers, "X-Tallystead-Forward-Auth-Subject": "unknown-subject", "X-Tallystead-Forward-Auth-Email": "unknown@example.com"}
    assert client.post("/v1/auth/proxy/login", headers=unmatched_headers, json={}).status_code == 403
    untrusted_headers = {key: value for key, value in proxy_headers.items() if key != "X-Tallystead-Proxy-Token"}
    assert client.get("/v1/auth/proxy/status", headers=untrusted_headers).json()["available"] is False
    member_headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
    link_status = client.get("/v1/auth/proxy/link", headers=member_headers)
    assert link_status.status_code == 200 and link_status.json()["linked"] is True
    assert client.delete("/v1/auth/proxy/link", headers=member_headers).status_code == 204
    assert client.get("/v1/auth/proxy/link", headers=member_headers).json()["linked"] is False
    with TestSession() as db:
        assert db.query(AuditEvent).filter(AuditEvent.action == "auth.external_identity_unlinked").count() == 1


def test_first_run_setup_creates_owner_and_authenticated_session() -> None:
    client = TestClient(app)
    assert client.get("/v1/setup/status").json() == {"setup_required": True}

    response = client.post(
        "/v1/setup",
        json={
            "household_name": "Dawson household",
            "email": "owner@example.com",
            "display_name": "Owner",
            "password": "a-long-local-test-password",
            "device_name": "Test browser",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["role"] == "owner"
    assert client.get("/v1/setup/status").json() == {"setup_required": False}
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert me.status_code == 200
    assert me.json()["household_name"] == "Dawson household"
    assert me.json()["role"] == "owner"
    assert me.json()["session_idle_minutes"] == 1440
    categories = client.get("/v1/ledger/categories", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert categories.status_code == 200
    assert len(categories.json()) == 26
    assert all(item["is_system_default"] for item in categories.json())
    assert {"Paycheck", "Groceries", "Housing", "Utilities"}.issubset({item["name"] for item in categories.json()})


def test_first_run_can_start_with_a_marked_fictional_demo() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/setup",
        json={
            "household_name": "Fictional demo household",
            "email": "owner@example.com",
            "display_name": "Owner",
            "password": "a-long-local-test-password",
            "create_demo": True,
            "demo_reference_date": "2026-08-13",
            "demo_volume": "smoke",
        },
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    demo = client.get("/v1/data/demo/status", headers=headers)
    assert demo.status_code == 200
    assert demo.json() == {
        "is_demo": True,
        "seed": "tallystead-fictional-dawson-v1",
        "volume": "smoke",
        "reference_date": "2026-08-13",
    }
    assert client.get("/v1/data/status", headers=headers).json()["transaction_count"] == 27


def test_setup_cannot_run_twice() -> None:
    client = TestClient(app)
    payload = {"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}
    assert client.post("/v1/setup", json=payload).status_code == 201
    assert client.post("/v1/setup", json=payload).status_code == 409


def test_owner_can_create_roles_and_revoke_member_session() -> None:
    client = TestClient(app)
    owner = client.post(
        "/v1/setup",
        json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"},
    ).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}

    created = client.post(
        "/v1/household/members",
        headers=headers,
        json={"email": "viewer@example.com", "display_name": "Viewer", "password": "another-long-local-password", "role": "viewer"},
    )
    assert created.status_code == 201
    member = created.json()
    assert member["role"] == "viewer"
    promoted = client.patch(f"/v1/household/members/{member['membership_id']}", headers=headers, json={"role": "contributor"})
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "contributor"

    member_session = client.post("/v1/auth/login", json={"email": "viewer@example.com", "password": "another-long-local-password", "device_name": "Member phone"})
    assert member_session.status_code == 200
    sessions = client.get("/v1/household/sessions", headers=headers)
    assert any(item["is_current"] for item in sessions.json())
    member_session_id = next(item["session_id"] for item in sessions.json() if item["user_id"] == member["user_id"])
    assert client.delete(f"/v1/household/sessions/{member_session_id}", headers=headers).status_code == 204
    assert client.get("/v1/auth/me", headers={"Authorization": f"Bearer {member_session.json()['access_token']}"}).status_code == 401


def test_non_owner_cannot_manage_household_members() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    client.post("/v1/household/members", headers=headers, json={"email": "viewer@example.com", "display_name": "Viewer", "password": "another-long-local-password", "role": "viewer"})
    viewer = client.post("/v1/auth/login", json={"email": "viewer@example.com", "password": "another-long-local-password"}).json()
    assert client.get("/v1/household/members", headers={"Authorization": f"Bearer {viewer['access_token']}"}).status_code == 403


def test_member_can_manage_only_their_own_device_sessions() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    member = client.post("/v1/household/members", headers=owner_headers, json={"email": "member@example.com", "display_name": "Member", "password": "another-long-local-password", "role": "viewer"}).json()
    first = client.post("/v1/auth/login", json={"email": "member@example.com", "password": "another-long-local-password", "device_name": "Member phone"}).json()
    second = client.post("/v1/auth/login", json={"email": "member@example.com", "password": "another-long-local-password", "device_name": "Member laptop"}).json()
    member_headers = {"Authorization": f"Bearer {second['access_token']}"}

    sessions = client.get("/v1/auth/sessions", headers=member_headers)
    assert sessions.status_code == 200
    assert {item["user_id"] for item in sessions.json()} == {member["user_id"]}
    assert {item["device_name"] for item in sessions.json()} == {"Member phone", "Member laptop"}
    assert next(item for item in sessions.json() if item["device_name"] == "Member laptop")["is_current"] is True

    owner_session_id = next(item["session_id"] for item in client.get("/v1/household/sessions", headers=owner_headers).json() if item["user_id"] == owner["user_id"])
    assert client.delete(f"/v1/auth/sessions/{owner_session_id}", headers=member_headers).status_code == 404
    phone_session_id = next(item["session_id"] for item in sessions.json() if item["device_name"] == "Member phone")
    assert client.delete(f"/v1/auth/sessions/{phone_session_id}", headers=member_headers).status_code == 204
    assert client.get("/v1/auth/me", headers={"Authorization": f"Bearer {first['access_token']}"}).status_code == 401


def test_logout_revokes_current_session_and_is_audited() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}

    assert client.post("/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/v1/auth/me", headers=headers).status_code == 401
    with TestSession() as db:
        assert db.query(AuditEvent).filter(AuditEvent.action == "auth.session_signed_out").count() == 1


def test_idle_session_is_revoked_server_side() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    with TestSession() as db:
        session = db.query(SessionToken).filter(SessionToken.user_id == UUID(owner["user_id"])).one()
        session.last_seen_at = utc_now() - timedelta(days=2)
        db.commit()

    assert client.get("/v1/auth/me", headers=headers).status_code == 401
    with TestSession() as db:
        session = db.query(SessionToken).filter(SessionToken.user_id == UUID(owner["user_id"])).one()
        assert session.revoked_at is not None


def test_owner_password_reset_revokes_active_member_sessions() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    member = client.post("/v1/household/members", headers=headers, json={"email": "member@example.com", "display_name": "Member", "password": "another-long-local-password", "role": "contributor"}).json()
    active_session = client.post("/v1/auth/login", json={"email": "member@example.com", "password": "another-long-local-password"}).json()

    reset = client.post(f"/v1/household/members/{member['membership_id']}/reset-password", headers=headers, json={"password": "a-new-long-local-password"})
    assert reset.status_code == 204
    assert client.get("/v1/auth/me", headers={"Authorization": f"Bearer {active_session['access_token']}"}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": "member@example.com", "password": "another-long-local-password"}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": "member@example.com", "password": "a-new-long-local-password"}).status_code == 200


def test_passkey_options_and_owned_credential_management() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}

    options = client.post("/v1/auth/passkeys/register/options", headers=headers)
    assert options.status_code == 200
    assert options.json()["public_key"]["rp"]["id"] == "testserver"
    with TestSession() as db:
        passkey = PasskeyCredential(user_id=UUID(owner["user_id"]), credential_id="test-credential", public_key="test-public-key", sign_count=0)
        db.add(passkey)
        db.commit()
        passkey_id = passkey.id
    listed = client.get("/v1/auth/passkeys", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["passkey_id"] == str(passkey_id)
    assert client.delete(f"/v1/auth/passkeys/{passkey_id}", headers=headers).status_code == 204
    assert client.get("/v1/auth/passkeys", headers=headers).json() == []


def test_login_rate_limit_and_owner_account_disable() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    member = client.post("/v1/household/members", headers=headers, json={"email": "member@example.com", "display_name": "Member", "password": "another-long-local-password", "role": "viewer"}).json()
    for _ in range(5):
        assert client.post("/v1/auth/login", json={"email": "unknown@example.com", "password": "incorrect-password"}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": "unknown@example.com", "password": "incorrect-password"}).status_code == 429
    disabled = client.patch(f"/v1/household/members/{member['membership_id']}/status", headers=headers, json={"is_active": False})
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    assert client.post("/v1/auth/login", json={"email": "member@example.com", "password": "another-long-local-password"}).status_code == 401


def test_integration_secrets_are_encrypted_and_write_only() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    saved = client.put("/v1/system/integrations", headers=headers, json={"smtp_host": "smtp.local", "smtp_port": 587, "smtp_username": "mailer", "smtp_password": "top-secret-value", "smtp_from_address": "nest@example.com", "smtp_security": "starttls", "imap_host": "imap.local", "imap_port": 993, "imap_username": "receipts@example.com", "imap_password": "another-secret", "ai_provider": "ollama", "ai_base_url": "http://ollama:11434", "ai_model": "receipt-vision"})
    assert saved.status_code == 200
    assert saved.json()["smtp_configured"] is True
    assert "password" not in saved.json()
    assert saved.json()["ai_provider"] == "ollama"
    assert saved.json()["ai_base_url"] == "http://ollama:11434"
    assert saved.json()["ai_model"] == "receipt-vision"
    reloaded = client.get("/v1/system/integrations", headers=headers).json()
    assert reloaded["ai_base_url"] == "http://ollama:11434"
    assert reloaded["ai_model"] == "receipt-vision"
    assert reloaded["smtp_host"] == "smtp.local" and reloaded["smtp_port"] == 587
    assert reloaded["smtp_username"] == "mailer" and reloaded["smtp_from_address"] == "nest@example.com"
    assert reloaded["smtp_security"] == "starttls"
    assert reloaded["imap_host"] == "imap.local" and reloaded["imap_port"] == 993
    assert reloaded["imap_username"] == "receipts@example.com"
    assert "smtp_password" not in reloaded and "imap_password" not in reloaded
    with TestSession() as db:
        encrypted = db.get(SystemSetting, "integrations").encrypted_value
        assert "top-secret-value" not in encrypted


def test_smtp_check_sends_standard_test_email_to_signed_in_owner(monkeypatch) -> None:
    from app import routes

    client = TestClient(app)
    owner = client.post(
        "/v1/setup",
        json={
            "household_name": "Home",
            "email": "owner@example.com",
            "display_name": "Owner",
            "password": "a-long-local-test-password",
        },
    ).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    client.put(
        "/v1/system/integrations",
        headers=headers,
        json={
            "smtp_host": "smtp.local",
            "smtp_port": 587,
            "smtp_username": "mailer",
            "smtp_password": "top-secret-value",
            "smtp_from_address": "nest@example.com",
            "smtp_security": "starttls",
        },
    )
    delivery = {}

    def record_delivery(values: dict, to_address: str) -> None:
        delivery["host"] = values["smtp_host"]
        delivery["to"] = to_address

    monkeypatch.setattr(routes, "send_test_email", record_delivery)

    response = client.post("/v1/system/integrations/test/smtp", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "integration": "smtp",
        "reachable": True,
        "detail": "Test email sent to owner@example.com",
    }
    assert delivery == {"host": "smtp.local", "to": "owner@example.com"}


def test_email_recovery_request_is_private_and_reset_revokes_sessions() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    assert client.post("/v1/auth/password-reset/request", json={"email": "unknown@example.com"}).status_code == 204
    raw_token = "a-valid-local-password-reset-token-1234567890"
    with TestSession() as db:
        user = db.query(User).filter(User.email == "owner@example.com").one()
        db.add(PasswordResetToken(user_id=user.id, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(), expires_at=utc_now() + timedelta(minutes=30)))
        db.commit()
    reset = client.post("/v1/auth/password-reset/finish", json={"token": raw_token, "password": "the-new-long-local-password"})
    assert reset.status_code == 204
    assert client.get("/v1/auth/me", headers={"Authorization": f"Bearer {owner['access_token']}"}).status_code == 401
    assert client.post("/v1/auth/login", json={"email": "owner@example.com", "password": "the-new-long-local-password"}).status_code == 200


def test_ledger_accounts_transactions_and_split_invariant() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 100000}).json()
    defaults = client.get("/v1/ledger/categories", headers=headers).json()
    groceries = next(item for item in defaults if item["name"] == "Groceries")
    household = client.post("/v1/ledger/categories", headers=headers, json={"name": "Home maintenance", "category_type": "expense"}).json()

    invalid = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-13", "amount_minor": -5000, "currency_code": "USD", "payee": "Market", "splits": [{"category_id": groceries["category_id"], "amount_minor": -4000}]})
    assert invalid.status_code == 422

    created = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-13", "amount_minor": -5000, "currency_code": "USD", "payee": "Market", "splits": [{"category_id": groceries["category_id"], "amount_minor": -3500}, {"category_id": household["category_id"], "amount_minor": -1500}]})
    assert created.status_code == 201
    assert sum(item["amount_minor"] for item in created.json()["splits"]) == -5000
    accounts = client.get("/v1/ledger/accounts", headers=headers).json()
    assert accounts[0]["balance_minor"] == 95000


def test_transfer_legs_are_linked_and_household_neutral() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 100000}).json()
    savings = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Savings", "account_type": "savings", "currency_code": "USD", "opening_balance_minor": 50000}).json()
    transfer = client.post("/v1/ledger/transfers", headers=headers, json={"from_account_id": checking["account_id"], "to_account_id": savings["account_id"], "transaction_date": "2026-08-13", "amount_minor": 25000, "currency_code": "USD"})
    assert transfer.status_code == 201
    payload = transfer.json()
    assert payload["from_transaction"]["amount_minor"] + payload["to_transaction"]["amount_minor"] == 0
    assert payload["from_transaction"]["transfer_id"] == payload["transfer_id"]
    balances = {item["name"]: item["balance_minor"] for item in client.get("/v1/ledger/accounts", headers=headers).json()}
    assert balances == {"Checking": 75000, "Savings": 75000}
    assert sum(balances.values()) == 150000


def test_viewer_can_read_but_cannot_write_ledger() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    client.post("/v1/ledger/accounts", headers=owner_headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"})
    client.post("/v1/household/members", headers=owner_headers, json={"email": "viewer@example.com", "display_name": "Viewer", "password": "another-long-local-password", "role": "viewer"})
    viewer = client.post("/v1/auth/login", json={"email": "viewer@example.com", "password": "another-long-local-password"}).json()
    viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
    assert client.get("/v1/ledger/accounts", headers=viewer_headers).status_code == 200
    assert client.post("/v1/ledger/accounts", headers=viewer_headers, json={"name": "Hidden", "account_type": "cash", "currency_code": "USD"}).status_code == 403


def test_empty_account_can_be_deleted_but_financial_history_is_protected() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    accidental = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Accidental", "account_type": "checking", "currency_code": "USD"}).json()
    assert client.delete(f"/v1/ledger/accounts/{accidental['account_id']}", headers=headers).status_code == 204
    assert all(item["account_id"] != accidental["account_id"] for item in client.get("/v1/ledger/accounts", headers=headers).json())

    used = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Used", "account_type": "checking", "currency_code": "USD"}).json()
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": used["account_id"], "transaction_date": "2026-08-14", "amount_minor": -100, "currency_code": "USD", "payee": "Test"})
    blocked = client.delete(f"/v1/ledger/accounts/{used['account_id']}", headers=headers)
    assert blocked.status_code == 409
    assert "Archive it" in blocked.json()["detail"]


def test_transaction_revision_reconciliation_and_reversal_preserve_history() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 10000}).json()
    groceries = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Groceries")
    merchant = client.post("/v1/ledger/merchants", headers=headers, json={"name": "Neighborhood Market", "aliases": ["MARKET #42"]}).json()
    created = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "merchant_id": merchant["merchant_id"], "transaction_date": "2026-08-10", "amount_minor": -2500, "currency_code": "USD", "payee": "MARKET #42", "splits": [{"category_id": groceries["category_id"], "amount_minor": -2500}]}).json()
    transaction_id = created["transaction_id"]
    updated = client.patch(f"/v1/ledger/transactions/{transaction_id}", headers=headers, json={"payee": "Neighborhood Market", "memo": "Weekly groceries", "reason": "Normalize payee", "splits": [{"category_id": groceries["category_id"], "amount_minor": -2500}]})
    assert updated.status_code == 200
    assert updated.json()["raw_payee"] == "MARKET #42"
    assert client.put(f"/v1/ledger/transactions/{transaction_id}/reconciliation", headers=headers, json={"reconciled": True}).status_code == 200
    assert client.patch(f"/v1/ledger/transactions/{transaction_id}", headers=headers, json={"memo": "Blocked", "reason": "Should fail"}).status_code == 409
    assert client.put(f"/v1/ledger/transactions/{transaction_id}/reconciliation", headers=headers, json={"reconciled": False}).status_code == 200
    reversed_response = client.post(f"/v1/ledger/transactions/{transaction_id}/reverse", headers=headers, json={"transaction_date": "2026-08-11", "reason": "Charge was refunded"})
    assert reversed_response.status_code == 201
    assert reversed_response.json()["amount_minor"] == 2500
    detail = client.get(f"/v1/ledger/transactions/{transaction_id}", headers=headers).json()
    assert detail["transaction"]["status"] == "reversed"
    assert len(detail["revisions"]) >= 3
    assert detail["revisions"][-1]["before_snapshot"]["raw_payee"] == "MARKET #42"
    assert client.get("/v1/ledger/accounts", headers=headers).json()[0]["balance_minor"] == 10000


def test_historical_balance_replay_void_and_correction_are_deterministic() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 10000}).json()
    paycheck = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Paycheck")
    income = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-01", "amount_minor": 5000, "currency_code": "USD", "payee": "Employer", "splits": [{"category_id": paycheck["category_id"], "amount_minor": 5000}]}).json()
    pending = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-03", "amount_minor": -2000, "currency_code": "USD", "status": "pending"}).json()
    historical = client.get(f"/v1/ledger/accounts/{account['account_id']}/balance?as_of=2026-08-02&include_pending=false", headers=headers).json()
    assert historical["balance_minor"] == 15000
    assert client.patch(f"/v1/ledger/transactions/{pending['transaction_id']}", headers=headers, json={"status": "voided", "reason": "Pending authorization disappeared"}).status_code == 200
    corrected = client.post(f"/v1/ledger/transactions/{income['transaction_id']}/correct", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-01", "amount_minor": 5500, "currency_code": "USD", "status": "posted", "payee": "Employer", "splits": [{"category_id": paycheck["category_id"], "amount_minor": 5500}], "reason": "Correct paycheck amount"})
    assert corrected.status_code == 201
    first = client.get(f"/v1/ledger/accounts/{account['account_id']}/balance", headers=headers).json()
    second = client.get(f"/v1/ledger/accounts/{account['account_id']}/balance", headers=headers).json()
    assert first == second
    assert first["balance_minor"] == 15500


def test_ledger_export_excludes_secrets_and_cross_household_resources_are_hidden() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    client.put("/v1/system/integrations", headers=headers, json={"smtp_host": "smtp.local", "smtp_password": "never-export-this"})
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    with TestSession() as db:
        other_household = Household(name="Other household")
        db.add(other_household)
        db.flush()
        hidden_account = FinancialAccount(household_id=other_household.id, name="Hidden", account_type="checking", currency_code="USD")
        db.add(hidden_account)
        db.commit()
        hidden_id = hidden_account.id
    assert client.get(f"/v1/ledger/accounts/{hidden_id}/balance", headers=headers).status_code == 404
    exported = client.post("/v1/ledger/export", headers=headers)
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.json()["format"] == "tallystead-ledger-export-v1"
    assert exported.json()["accounts"][0]["account_id"] == account["account_id"]
    assert "never-export-this" not in exported.text
    assert "password_hash" not in exported.text


def test_phase2_recurrence_generation_is_idempotent_and_clamps_month_end() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    profile = client.post("/v1/obligations/bill-profiles", headers=headers, json={"name": "Rent", "payee": "Landlord", "cadence": "monthly", "next_due_date": "2026-08-31", "expected_amount_minor": 125000, "currency_code": "USD", "is_essential": True, "priority": 1})
    assert profile.status_code == 201
    income = client.post("/v1/obligations/income-sources", headers=headers, json={"name": "Paycheck", "payer": "Employer", "cadence": "biweekly", "next_expected_date": "2026-08-14", "expected_amount_minor": 200000, "currency_code": "USD", "confidence_percent": 100})
    assert income.status_code == 201
    first = client.post("/v1/obligations/generate?through=2026-10-31", headers=headers)
    assert first.status_code == 200
    assert first.json()["bill_instances_created"] == 3
    second = client.post("/v1/obligations/generate?through=2026-10-31", headers=headers)
    assert second.json() == {"bill_instances_created": 0, "income_events_created": 0, "debt_instances_created": 0}
    bills = client.get("/v1/obligations/bill-instances", headers=headers).json()
    assert [item["due_date"] for item in bills] == ["2026-08-31", "2026-09-30", "2026-10-31"]


def test_phase2_partial_payment_links_and_expected_income_do_not_change_balance() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 50000}).json()
    client.post("/v1/obligations/bill-profiles", headers=headers, json={"name": "Electric", "cadence": "irregular", "next_due_date": "2026-08-20", "expected_amount_minor": 10000, "minimum_amount_minor": 8000, "maximum_amount_minor": 12000, "currency_code": "USD", "priority": 2})
    client.post("/v1/obligations/income-events", headers=headers, json={"name": "Freelance", "expected_date": "2026-08-22", "expected_amount_minor": 30000, "currency_code": "USD", "confidence_percent": 60})
    client.post("/v1/obligations/generate?through=2026-08-31", headers=headers)
    assert client.get("/v1/ledger/accounts", headers=headers).json()[0]["balance_minor"] == 50000
    payment = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-20", "amount_minor": -6000, "currency_code": "USD", "payee": "Electric Utility"}).json()
    instance = client.get("/v1/obligations/bill-instances", headers=headers).json()[0]
    linked = client.post(f"/v1/obligations/bill-instances/{instance['bill_instance_id']}/payments", headers=headers, json={"transaction_id": payment["transaction_id"], "amount_minor": 6000})
    assert linked.status_code == 201
    assert client.get("/v1/obligations/bill-instances", headers=headers).json()[0]["status"] == "partial"
    assert client.post(f"/v1/obligations/bill-instances/{instance['bill_instance_id']}/payments", headers=headers, json={"transaction_id": payment["transaction_id"], "amount_minor": 1}).status_code in {409, 422}


def test_phase2_debt_minimum_calendar_and_read_only_policy() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    debt = client.post("/v1/obligations/debts", headers=headers, json={"name": "Auto loan", "lender": "Local Credit Union", "balance_minor": 1800000, "apr_basis_points": 625, "minimum_payment_minor": 35000, "due_day": 15, "next_due_date": "2026-09-15", "currency_code": "USD"})
    assert debt.status_code == 201
    client.post("/v1/obligations/generate?through=2026-10-31", headers=headers)
    calendar = client.get("/v1/obligations/calendar?date_from=2026-09-01&date_to=2026-10-31", headers=headers).json()
    assert [(item["item_type"], item["event_date"], item["amount_minor"]) for item in calendar] == [("debt", "2026-09-15", -35000), ("debt", "2026-10-15", -35000)]
    client.post("/v1/household/members", headers=headers, json={"email": "viewer@example.com", "display_name": "Viewer", "password": "another-long-local-password", "role": "viewer"})
    viewer = client.post("/v1/auth/login", json={"email": "viewer@example.com", "password": "another-long-local-password"}).json()
    viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
    assert client.get("/v1/obligations/debts", headers=viewer_headers).status_code == 200
    assert client.post("/v1/obligations/generate?through=2026-10-31", headers=viewer_headers).status_code == 403


def test_debt_edit_and_scoped_deletion_preserve_linked_ledger_evidence() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    loan = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Auto loan account", "account_type": "loan", "currency_code": "USD"}).json()
    debt = client.post("/v1/obligations/debts", headers=headers, json={"name": "Auto loan", "lender": "Local Credit Union", "account_id": loan["account_id"], "balance_minor": 1800000, "apr_basis_points": 625, "minimum_payment_minor": 35000, "due_day": 15, "next_due_date": "2026-09-15", "currency_code": "USD"}).json()

    edited = client.patch(f"/v1/obligations/debts/{debt['debt_id']}", headers=headers, json={"name": "Family auto loan", "balance_minor": 1750000, "minimum_payment_minor": 36000, "apr_basis_points": 600})
    assert edited.status_code == 200
    assert edited.json()["name"] == "Family auto loan"
    assert edited.json()["balance_minor"] == 1750000
    assert edited.json()["minimum_payment_minor"] == 36000

    client.post("/v1/obligations/generate?through=2026-10-31", headers=headers)
    instances = client.get("/v1/obligations/bill-instances", headers=headers).json()
    assert len(instances) == 2
    payment = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-09-15", "amount_minor": -36000, "currency_code": "USD", "status": "posted", "payee": "Local Credit Union"}).json()
    linked = client.post(f"/v1/obligations/bill-instances/{instances[0]['bill_instance_id']}/payments", headers=headers, json={"transaction_id": payment["transaction_id"], "amount_minor": 36000})
    assert linked.status_code == 201

    removed = client.delete(f"/v1/obligations/debts/{debt['debt_id']}?scope=upcoming", headers=headers)
    assert removed.status_code == 204
    remaining = client.get("/v1/obligations/bill-instances", headers=headers).json()
    assert len(remaining) == 1
    assert remaining[0]["payment_links"][0]["transaction_id"] == payment["transaction_id"]
    archived = client.get("/v1/obligations/debts", headers=headers).json()[0]
    assert archived["is_active"] is False
    assert client.post("/v1/obligations/generate?through=2026-12-31", headers=headers).json()["debt_instances_created"] == 0

    assert client.delete(f"/v1/obligations/debts/{debt['debt_id']}?scope=all", headers=headers).status_code == 204
    assert client.get("/v1/obligations/debts", headers=headers).json() == []
    assert client.get(f"/v1/ledger/transactions/{payment['transaction_id']}", headers=headers).status_code == 200


def test_bill_profile_edit_and_scoped_deletion_preserve_ledger_evidence() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    profile = client.post("/v1/obligations/bill-profiles", headers=headers, json={"name": "Internet", "cadence": "monthly", "next_due_date": "2026-09-01", "expected_amount_minor": 8000, "currency_code": "USD", "priority": 3}).json()
    edited = client.patch(f"/v1/obligations/bill-profiles/{profile['bill_profile_id']}", headers=headers, json={"name": "Home internet", "expected_amount_minor": 8500, "priority": 2})
    assert edited.status_code == 200
    assert edited.json()["name"] == "Home internet"
    client.post("/v1/obligations/generate?through=2026-11-30", headers=headers)
    assert len(client.get("/v1/obligations/bill-instances", headers=headers).json()) == 3
    removed = client.delete(f"/v1/obligations/bill-profiles/{profile['bill_profile_id']}?scope=upcoming", headers=headers)
    assert removed.status_code == 204
    assert client.get("/v1/obligations/bill-instances", headers=headers).json() == []
    archived = client.get("/v1/obligations/bill-profiles", headers=headers).json()[0]
    assert archived["is_active"] is False
    assert client.post("/v1/obligations/generate?through=2026-12-31", headers=headers).json()["bill_instances_created"] == 0
    assert client.delete(f"/v1/obligations/bill-profiles/{profile['bill_profile_id']}?scope=all", headers=headers).status_code == 204
    assert client.get("/v1/obligations/bill-profiles", headers=headers).json() == []


def test_income_source_edit_and_scoped_deletion_preserve_ledger_evidence() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    source = client.post("/v1/obligations/income-sources", headers=headers, json={"name": "Paycheck", "payer": "Employer", "cadence": "monthly", "next_expected_date": "2026-09-01", "expected_amount_minor": 80000, "currency_code": "USD", "confidence_percent": 100}).json()
    edited = client.patch(f"/v1/obligations/income-sources/{source['income_source_id']}", headers=headers, json={"name": "Primary paycheck", "expected_amount_minor": 85000, "confidence_percent": 95})
    assert edited.status_code == 200
    assert edited.json()["name"] == "Primary paycheck"
    client.post("/v1/obligations/generate?through=2026-11-30", headers=headers)
    events = client.get("/v1/obligations/income-events", headers=headers).json()
    assert len(events) == 3
    transaction = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-09-01", "amount_minor": 85000, "currency_code": "USD", "status": "posted", "payee": "Employer"}).json()
    assert client.put(f"/v1/obligations/income-events/{events[0]['income_event_id']}/received", headers=headers, json={"transaction_id": transaction["transaction_id"]}).status_code == 200
    removed = client.delete(f"/v1/obligations/income-sources/{source['income_source_id']}?scope=upcoming", headers=headers)
    assert removed.status_code == 204
    remaining = client.get("/v1/obligations/income-events", headers=headers).json()
    assert len(remaining) == 1 and remaining[0]["status"] == "received"
    archived = client.get("/v1/obligations/income-sources", headers=headers).json()[0]
    assert archived["is_active"] is False
    assert client.delete(f"/v1/obligations/income-sources/{source['income_source_id']}?scope=all", headers=headers).status_code == 204
    assert client.get("/v1/obligations/income-sources", headers=headers).json() == []
    assert client.get(f"/v1/ledger/transactions/{transaction['transaction_id']}", headers=headers).status_code == 200


def test_phase2b_account_defaults_prevent_non_spendable_planner_cash() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    retirement = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Work 401k", "account_type": "401k", "currency_code": "USD"}).json()
    hsa = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Health savings", "account_type": "hsa", "currency_code": "USD"}).json()
    business = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Studio checking", "account_type": "business_checking", "currency_code": "USD"}).json()
    assert checking["include_in_planner"] is True and checking["liquidity"] == "spendable"
    assert retirement["include_in_planner"] is False and retirement["liquidity"] == "invested" and retirement["tax_treatment"] == "tax_deferred"
    assert hsa["include_in_planner"] is False and hsa["liquidity"] == "restricted"
    assert business["include_in_planner"] is False and business["ownership_scope"] == "business"
    invalid = client.patch(f"/v1/ledger/accounts/{retirement['account_id']}", headers=headers, json={"include_in_planner": True})
    assert invalid.status_code == 422


def test_phase2b_valuations_and_liabilities_produce_explainable_net_worth() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 100000}).json()
    retirement = client.post("/v1/ledger/accounts", headers=headers, json={"name": "401k", "account_type": "401k", "currency_code": "USD"}).json()
    mortgage = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Mortgage", "account_type": "mortgage", "currency_code": "USD", "opening_balance_minor": 20000000}).json()
    valuation = client.post(f"/v1/ledger/accounts/{retirement['account_id']}/valuations", headers=headers, json={"valuation_date": "2026-08-13", "value_minor": 5000000, "currency_code": "USD", "note": "Quarterly statement"})
    assert valuation.status_code == 201
    net = client.get("/v1/ledger/net-worth?currency_code=USD&as_of=2026-08-13", headers=headers).json()
    assert net["asset_total_minor"] == 5100000
    assert net["liability_total_minor"] == 20000000
    assert net["net_worth_minor"] == -14900000
    retirement_row = next(item for item in net["accounts"] if item["account_id"] == retirement["account_id"])
    assert retirement_row["valuation_as_of"] == "2026-08-13"
    assert checking["account_id"] != mortgage["account_id"]


def test_phase2b_investment_activity_type_is_preserved_in_ledger() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Roth IRA", "account_type": "roth_ira", "currency_code": "USD"}).json()
    contribution = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-13", "amount_minor": 25000, "currency_code": "USD", "payee": "Household contribution", "activity_type": "contribution"})
    assert contribution.status_code == 201
    assert contribution.json()["activity_type"] == "contribution"
    assert client.get("/v1/ledger/transactions", headers=headers).json()[0]["activity_type"] == "contribution"


def test_phase3_paycheck_to_rent_forecast_is_deterministic_and_explainable() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 100000})
    client.post("/v1/ledger/accounts", headers=headers, json={"name": "Credit card", "account_type": "credit_card", "currency_code": "USD", "opening_balance_minor": 500000})
    client.post("/v1/obligations/bill-profiles", headers=headers, json={"name": "Rent", "cadence": "irregular", "next_due_date": "2026-08-20", "expected_amount_minor": 80000, "currency_code": "USD", "is_essential": True, "priority": 1})
    client.post("/v1/obligations/income-events", headers=headers, json={"name": "Paycheck", "expected_date": "2026-08-15", "expected_amount_minor": 50000, "currency_code": "USD", "confidence_percent": 100})
    client.post("/v1/obligations/generate?through=2026-09-12", headers=headers)
    request = {"as_of_date": "2026-08-13", "horizon_days": 30, "currency_code": "USD", "cash_buffer_minor": 10000, "include_pending": True}
    first = client.post("/v1/planner/forecast", headers=headers, json=request)
    second = client.post("/v1/planner/forecast", headers=headers, json=request)
    assert first.status_code == 200
    assert first.json() == second.json()
    result = first.json()
    assert result["planning_balance_minor"] == 100000
    assert result["expected_income_minor"] == 50000
    assert result["required_outflow_minor"] == 80000
    assert result["safe_to_spend_minor"] == 60000
    assert result["reserved_now_minor"] == 30000
    assert result["shortfalls"] == []
    assert any("Credit card" in item for item in result["excluded_accounts"])
    assert result["timeline"][0]["name"] == "Paycheck"


def test_phase3_variable_estimates_and_shortfalls_are_explicit() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 50000})
    client.post("/v1/obligations/bill-profiles", headers=headers, json={"name": "Electric", "cadence": "irregular", "next_due_date": "2026-08-14", "expected_amount_minor": 80000, "minimum_amount_minor": 70000, "maximum_amount_minor": 100000, "currency_code": "USD", "is_essential": True, "priority": 1})
    client.post("/v1/obligations/income-events", headers=headers, json={"name": "Freelance", "expected_date": "2026-08-20", "expected_amount_minor": 100000, "minimum_amount_minor": 60000, "maximum_amount_minor": 120000, "currency_code": "USD", "confidence_percent": 70})
    client.post("/v1/obligations/generate?through=2026-09-12", headers=headers)
    result = client.post("/v1/planner/forecast", headers=headers, json={"as_of_date": "2026-08-13", "horizon_days": 30, "currency_code": "USD", "cash_buffer_minor": 10000}).json()
    assert result["expected_income_minor"] == 60000
    assert result["required_outflow_minor"] == 100000
    assert result["safe_to_spend_minor"] == 0
    assert result["reserves"][0]["funded_minor"] == 40000
    assert result["reserves"][0]["shortfall_minor"] == 60000
    assert result["shortfalls"][0]["obligation_name"] == "Electric"
    assert any("cautious income" in item for item in result["warnings"])
    assert any("maximum" in item for item in result["warnings"])


def test_phase3_saved_snapshot_is_reproducible_and_role_protected() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 75000})
    request = {"as_of_date": "2026-08-13", "horizon_days": 30, "currency_code": "USD", "cash_buffer_minor": 5000}
    saved = client.post("/v1/planner/snapshots", headers=headers, json=request)
    assert saved.status_code == 201
    assert saved.json()["snapshot_id"] is not None
    latest = client.get("/v1/planner/snapshots/latest?currency_code=USD", headers=headers)
    assert latest.json() == saved.json()
    client.post("/v1/household/members", headers=headers, json={"email": "viewer@example.com", "display_name": "Viewer", "password": "another-long-local-password", "role": "viewer"})
    viewer = client.post("/v1/auth/login", json={"email": "viewer@example.com", "password": "another-long-local-password"}).json()
    viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
    assert client.post("/v1/planner/forecast", headers=viewer_headers, json=request).status_code == 200
    assert client.post("/v1/planner/snapshots", headers=viewer_headers, json=request).status_code == 403


def test_phase3_missing_paycheck_and_pending_card_payment_are_conservative() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 100000}).json()
    card = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Credit card", "account_type": "credit_card", "currency_code": "USD", "opening_balance_minor": 250000}).json()
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-08-13", "amount_minor": -20000, "currency_code": "USD", "status": "pending", "payee": "Card payment"})
    client.post("/v1/obligations/income-events", headers=headers, json={"name": "Late paycheck", "expected_date": "2026-08-12", "expected_amount_minor": 90000, "currency_code": "USD", "confidence_percent": 100})
    request = {"as_of_date": "2026-08-13", "horizon_days": 30, "currency_code": "USD", "cash_buffer_minor": 0}
    conservative = client.post("/v1/planner/forecast", headers=headers, json={**request, "include_pending": True}).json()
    posted_only = client.post("/v1/planner/forecast", headers=headers, json={**request, "include_pending": False}).json()
    assert conservative["planning_balance_minor"] == 80000
    assert posted_only["planning_balance_minor"] == 100000
    assert conservative["expected_income_minor"] == 0
    assert any("still missing" in item for item in conservative["warnings"])
    assert any("Credit card" in item for item in conservative["excluded_accounts"])
    assert card["include_in_planner"] is False


def test_phase4_csv_import_is_idempotent_and_preserves_raw_evidence() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    account=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Checking","account_type":"checking","currency_code":"USD"}).json()
    source=client.post("/v1/imports/sources",headers=headers,json={"account_id":account["account_id"],"name":"Credit Union CSV","institution":"Local CU","export_instructions":"Transactions then Download CSV","reminder_interval_days":30,"reminders_enabled":True}).json()
    csv_text="date,description,amount\n2026-08-10,MARKET #42,-25.00\n"
    first=client.post(f"/v1/imports/sources/{source['source_id']}/csv",headers=headers,json={"filename":"checking.csv","csv_text":csv_text})
    second=client.post(f"/v1/imports/sources/{source['source_id']}/csv",headers=headers,json={"filename":"checking-again.csv","csv_text":csv_text})
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["batch_id"] == second.json()["batch_id"]
    assert len(client.get("/v1/imports/batches",headers=headers).json()) == 1
    row=client.get("/v1/reconciliation/queue",headers=headers).json()[0]
    assert row["raw_values"]["description"] == "MARKET #42" and row["amount_minor"] == -2500
    updated=client.get("/v1/imports/sources",headers=headers).json()[0]
    assert updated["last_imported_at"] is not None and updated["next_reminder_date"] is not None


def test_phase4_candidate_confirmation_and_unmatch_keep_manual_transaction() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    account=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Checking","account_type":"checking","currency_code":"USD"}).json()
    manual=client.post("/v1/ledger/transactions",headers=headers,json={"account_id":account["account_id"],"transaction_date":"2026-08-10","amount_minor":-2500,"currency_code":"USD","payee":"Market #42"}).json()
    source=client.post("/v1/imports/sources",headers=headers,json={"account_id":account["account_id"],"name":"Bank"}).json()
    client.post(f"/v1/imports/sources/{source['source_id']}/csv",headers=headers,json={"filename":"bank.csv","csv_text":"date,description,amount\n2026-08-10,Market #42,-25.00\n"})
    row=client.get("/v1/reconciliation/queue",headers=headers).json()[0]; candidate=row["candidates"][0]
    assert candidate["transaction_id"] == manual["transaction_id"] and candidate["confidence_percent"] == 100
    confirmed=client.put(f"/v1/reconciliation/matches/{candidate['match_id']}",headers=headers,json={"action":"confirm","note":"Statement evidence agrees"})
    assert confirmed.json()["status"] == "matched"
    unmatched=client.delete(f"/v1/reconciliation/rows/{row['row_id']}/match",headers=headers)
    assert unmatched.json()["status"] == "unmatched"
    assert client.get(f"/v1/ledger/transactions/{manual['transaction_id']}",headers=headers).status_code == 200
    assert unmatched.json()["raw_values"]["description"] == "Market #42"


def test_phase4_explicit_transaction_creation_duplicate_detection_and_role_policy() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    account=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Checking","account_type":"checking","currency_code":"USD"}).json()
    assert client.post("/v1/imports/sources",headers=headers,json={"account_id":account["account_id"],"name":"Unsafe","notes":"password=bad"}).status_code == 422
    source=client.post("/v1/imports/sources",headers=headers,json={"account_id":account["account_id"],"name":"Bank"}).json()
    csv_text="date,description,amount\n2026-08-11,Employer,1000.00\n2026-08-11,Employer,1000.00\n"
    batch=client.post(f"/v1/imports/sources/{source['source_id']}/csv",headers=headers,json={"filename":"bank.csv","csv_text":csv_text}).json()
    assert batch["duplicate_count"] == 1
    rows=client.get("/v1/reconciliation/queue",headers=headers).json()
    created=client.post(f"/v1/reconciliation/rows/{rows[0]['row_id']}/transaction",headers=headers,json={})
    assert created.status_code == 201 and created.json()["status"] == "matched"
    transaction=client.get("/v1/ledger/transactions",headers=headers).json()[0]
    assert transaction["source_type"] == "imported" and transaction["raw_payee"] == "Employer"
    client.post("/v1/household/members",headers=headers,json={"email":"viewer@example.com","display_name":"Viewer","password":"another-long-local-password","role":"viewer"})
    viewer=client.post("/v1/auth/login",json={"email":"viewer@example.com","password":"another-long-local-password"}).json(); vh={"Authorization":f"Bearer {viewer['access_token']}"}
    assert client.get("/v1/reconciliation/queue",headers=vh).status_code == 200
    assert client.post(f"/v1/imports/sources/{source['source_id']}/csv",headers=vh,json={"filename":"x.csv","csv_text":csv_text}).status_code == 403


def test_review_queue_bulk_category_approval_is_direction_safe() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    source = client.post("/v1/imports/sources", headers=headers, json={"account_id": account["account_id"], "name": "Bank"}).json()
    client.post(f"/v1/imports/sources/{source['source_id']}/csv", headers=headers, json={"filename": "bank.csv", "csv_text": "date,description,amount\n2026-08-10,Market,-25.00\n2026-08-11,Cafe,-12.00\n2026-08-12,Employer,1000.00\n"})
    rows = client.get("/v1/reconciliation/queue", headers=headers).json()
    groceries = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Groceries")
    expense_rows = [row["row_id"] for row in rows if row["amount_minor"] < 0]
    approved = client.post("/v1/reconciliation/rows/create-batch", headers=headers, json={"row_ids": expense_rows, "category_id": groceries["category_id"]})
    assert approved.status_code == 201 and len(approved.json()) == 2
    remaining = client.get("/v1/reconciliation/queue", headers=headers).json()
    assert len(remaining) == 1 and remaining[0]["amount_minor"] > 0
    mixed = client.post("/v1/reconciliation/rows/create-batch", headers=headers, json={"row_ids": [remaining[0]["row_id"]], "category_id": groceries["category_id"]})
    assert mixed.status_code == 422


def test_review_queue_page_searches_filters_and_separates_transfers() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    source = client.post("/v1/imports/sources", headers=headers, json={"account_id": account["account_id"], "name": "Seven Month Import"}).json()
    rows = ["date,description,amount"]
    rows.extend(f"2026-08-{index + 1:02d},Store {index},-{10 + index}.00" for index in range(12))
    rows.append("2026-08-13,Transfer to savings,-500.00")
    imported = client.post(f"/v1/imports/sources/{source['source_id']}/csv", headers=headers, json={"filename": "history.csv", "csv_text": "\n".join(rows)})
    assert imported.status_code == 201, imported.text

    first = client.get("/v1/reconciliation/queue/page?queue_kind=standard&page=1&page_size=10", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["total_items"] == 12
    assert first.json()["total_pages"] == 2
    assert len(first.json()["items"]) == 10
    second = client.get("/v1/reconciliation/queue/page?queue_kind=standard&page=2&page_size=10", headers=headers).json()
    assert len(second["items"]) == 2
    searched = client.get("/v1/reconciliation/queue/page?queue_kind=standard&search=Store%207&page_size=10", headers=headers).json()
    assert searched["total_items"] == 1 and searched["items"][0]["raw_payee"] == "Store 7"
    filtered = client.get(f"/v1/reconciliation/queue/page?queue_kind=standard&source_id={source['source_id']}&account_id={account['account_id']}&direction=outflow&date_from=2026-08-05&date_to=2026-08-06&page_size=10", headers=headers).json()
    assert filtered["total_items"] == 2
    transfers = client.get("/v1/reconciliation/queue/page?queue_kind=transfer&page_size=10", headers=headers).json()
    assert transfers["total_items"] == 1 and transfers["items"][0]["raw_payee"] == "Transfer to savings"


def test_review_queue_orders_same_day_opposite_transfers_together() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    savings = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Savings", "account_type": "savings", "currency_code": "USD"}).json()
    checking_source = client.post("/v1/imports/sources", headers=headers, json={"account_id": checking["account_id"], "name": "Checking CSV"}).json()
    savings_source = client.post("/v1/imports/sources", headers=headers, json={"account_id": savings["account_id"], "name": "Savings CSV"}).json()
    client.post(f"/v1/imports/sources/{checking_source['source_id']}/csv", headers=headers, json={"filename": "checking.csv", "csv_text": "date,description,amount\n2026-08-14,Transfer to savings,-500.00\n2026-08-13,Market,-20.00\n"})
    client.post(f"/v1/imports/sources/{savings_source['source_id']}/csv", headers=headers, json={"filename": "savings.csv", "csv_text": "date,description,amount\n2026-08-14,Transfer from checking,500.00\n2026-08-14,Interest,1.00\n"})
    rows = client.get("/v1/reconciliation/queue", headers=headers).json()
    assert [row["transaction_date"] for row in rows] == ["2026-08-14", "2026-08-14", "2026-08-14", "2026-08-13"]
    transfer_indexes = [index for index, row in enumerate(rows) if abs(row["amount_minor"] or 0) == 50000]
    assert transfer_indexes == [0, 1]


def test_review_queue_local_ai_proposals_require_review(monkeypatch) -> None:
    from app import routes

    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    source = client.post("/v1/imports/sources", headers=headers, json={"account_id": account["account_id"], "name": "Bank"}).json()
    client.post(f"/v1/imports/sources/{source['source_id']}/csv", headers=headers, json={"filename": "bank.csv", "csv_text": "date,description,amount\n2026-08-10,Market,-25.00\n"})
    row = client.get("/v1/reconciliation/queue", headers=headers).json()[0]
    groceries = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Groceries")
    disabled = client.post("/v1/reconciliation/rows/category-suggestions", headers=headers, json={"row_ids": [row["row_id"]]})
    assert disabled.status_code == 409
    client.put("/v1/system/integrations", headers=headers, json={"ai_enabled": True, "ai_provider": "lm_studio", "ai_base_url": "http://127.0.0.1:1234", "ai_model": "local-test"})
    monkeypatch.setattr(routes, "ai_import_proposals", lambda db, household_id, rows, values: {str(rows[0].id): {"category_id": UUID(groceries["category_id"]), "category_name": "Groceries", "confidence_percent": 82, "evidence": "Merchant appears grocery-related.", "provider": "lm_studio", "model_version": "local-test"}})
    suggested = client.post("/v1/reconciliation/rows/category-suggestions", headers=headers, json={"row_ids": [row["row_id"]]})
    assert suggested.status_code == 200
    proposal = suggested.json()[0]
    assert proposal["proposed_category_name"] == "Groceries" and proposal["automation_confidence"] == 82
    assert proposal["status"] == "unmatched"
    assert client.get("/v1/ledger/transactions", headers=headers).json() == []


def test_phase4_import_surfaces_missing_expected_bill_exception() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    account=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Checking","account_type":"checking","currency_code":"USD"}).json()
    client.post("/v1/obligations/bill-profiles",headers=headers,json={"name":"Rent","cadence":"irregular","next_due_date":"2026-08-15","expected_amount_minor":100000,"currency_code":"USD","is_essential":True,"priority":1})
    client.post("/v1/obligations/generate?through=2026-08-31",headers=headers)
    source=client.post("/v1/imports/sources",headers=headers,json={"account_id":account["account_id"],"name":"Bank"}).json()
    client.post(f"/v1/imports/sources/{source['source_id']}/csv",headers=headers,json={"filename":"bank.csv","csv_text":"date,description,amount\n2026-08-14,Groceries,-25.00\n2026-08-16,Fuel,-40.00\n"})
    exceptions=client.get("/v1/reconciliation/exceptions",headers=headers).json()
    assert len(exceptions) == 1
    assert exceptions[0]["exception_type"] == "missing_expected_bill"
    assert exceptions[0]["amount_minor"] == -100000


def test_phase4_title_case_export_preserves_original_description_and_status() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    account=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Checking","account_type":"checking","currency_code":"USD"}).json()
    source=client.post("/v1/imports/sources",headers=headers,json={"account_id":account["account_id"],"name":"Title case export"}).json()
    csv_text="Date,Description,Original Description,Category,Amount,Status\n2026-08-13,Clean Merchant,RAW MERCHANT 123,Groceries,-12.34,Pending\n"
    imported=client.post(f"/v1/imports/sources/{source['source_id']}/csv",headers=headers,json={"filename":"example.csv","csv_text":csv_text})
    assert imported.status_code == 201
    row=client.get("/v1/reconciliation/queue",headers=headers).json()[0]
    assert row["raw_payee"] == "RAW MERCHANT 123"
    assert row["normalized_payee"] == "CLEAN MERCHANT"
    created=client.post(f"/v1/reconciliation/rows/{row['row_id']}/transaction",headers=headers,json={})
    assert created.status_code == 201
    transaction=client.get("/v1/ledger/transactions",headers=headers).json()[0]
    assert transaction["status"] == "pending"
    assert transaction["payee"] == "CLEAN MERCHANT"
    assert transaction["raw_payee"] == "RAW MERCHANT 123"


def test_phase4_csv_inspection_guesses_different_export_shapes_for_review() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    compact="Post Date,Amount,Check Number,Payee,Memo\n08/13/2026,-12.34,,Corner Shop,\n"
    detailed="Date,Description,Original Description,Category,Amount,Status\n2026-08-13,Corner Shop,RAW SHOP 123,Groceries,-12.34,Posted\n"
    first=client.post("/v1/imports/csv/inspect",headers=headers,json={"filename":"compact.csv","csv_text":compact})
    second=client.post("/v1/imports/csv/inspect",headers=headers,json={"filename":"detailed.csv","csv_text":detailed})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["mappings"]["date"] == "Post Date"
    assert first.json()["mappings"]["payee"] == "Payee"
    assert first.json()["date_format"] == "%m/%d/%Y"
    assert second.json()["mappings"]["date"] == "Date"
    assert second.json()["mappings"]["payee"] == "Description"
    assert second.json()["mappings"]["original_payee"] == "Original Description"
    assert second.json()["mappings"]["category"] == "Category"
    assert second.json()["mappings"]["amount"] == "Amount"
    assert second.json()["mappings"]["status"] == "Status"


def test_phase4_reviewed_mapping_imports_payee_export_and_split_amount_export() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    account=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Checking","account_type":"checking","currency_code":"USD"}).json()
    payee_source=client.post("/v1/imports/sources",headers=headers,json={"account_id":account["account_id"],"name":"Payee export","date_column":"Post Date","payee_column":"Payee","amount_column":"Amount","date_format":"%m/%d/%Y"}).json()
    imported=client.post(f"/v1/imports/sources/{payee_source['source_id']}/csv",headers=headers,json={"filename":"payee.csv","csv_text":"Post Date,Amount,Payee,Memo\n08/13/2026,-12.34,Corner Shop,\n"})
    assert imported.status_code == 201
    assert client.get("/v1/reconciliation/queue",headers=headers).json()[0]["amount_minor"] == -1234

    split_source=client.post("/v1/imports/sources",headers=headers,json={"account_id":account["account_id"],"name":"Debit credit export","date_column":"Date","payee_column":"Description","amount_column":None,"debit_column":"Withdrawal","credit_column":"Deposit","date_format":"%Y-%m-%d"}).json()
    split=client.post(f"/v1/imports/sources/{split_source['source_id']}/csv",headers=headers,json={"filename":"split.csv","csv_text":"Date,Description,Withdrawal,Deposit\n2026-08-12,Utility,40.25,\n2026-08-13,Paycheck,,1000.00\n"})
    assert split.status_code == 201
    rows=client.get("/v1/reconciliation/queue",headers=headers).json()
    assert sorted(row["amount_minor"] for row in rows) == [-4025,-1234,100000]


def test_phase4_sources_can_be_edited_and_unused_sources_deleted() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    account=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Checking","account_type":"checking","currency_code":"USD"}).json()
    source=client.post("/v1/imports/sources",headers=headers,json={"account_id":account["account_id"],"name":"Old name"}).json()
    updated=client.put(f"/v1/imports/sources/{source['source_id']}",headers=headers,json={"account_id":account["account_id"],"name":"Updated bank export","date_column":"Post Date","payee_column":"Payee","amount_column":"Amount","date_format":"%m/%d/%Y","notes":"Mapping checked locally"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated bank export"
    assert updated.json()["payee_column"] == "Payee"
    assert client.delete(f"/v1/imports/sources/{source['source_id']}",headers=headers).status_code == 204
    assert client.get("/v1/imports/sources",headers=headers).json() == []
    assert client.post(f"/v1/imports/sources/{source['source_id']}/csv",headers=headers,json={"filename":"bank.csv","csv_text":"Post Date,Payee,Amount\n08/13/2026,Market,-12.34\n"}).status_code == 404


def test_phase4_deleting_used_source_preserves_import_evidence() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    account=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Checking","account_type":"checking","currency_code":"USD"}).json()
    source=client.post("/v1/imports/sources",headers=headers,json={"account_id":account["account_id"],"name":"Used source"}).json()
    imported=client.post(f"/v1/imports/sources/{source['source_id']}/csv",headers=headers,json={"filename":"bank.csv","csv_text":"date,description,amount\n2026-08-13,Market,-12.34\n"})
    assert imported.status_code == 201
    assert client.delete(f"/v1/imports/sources/{source['source_id']}",headers=headers).status_code == 204
    assert client.get("/v1/imports/sources",headers=headers).json() == []
    assert len(client.get("/v1/imports/batches",headers=headers).json()) == 1
    queue=client.get("/v1/reconciliation/queue",headers=headers).json()
    assert len(queue) == 1 and queue[0]["source_name"] == "Used source"


def test_phase4_used_source_cannot_move_preserved_rows_to_another_account() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    first=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Checking","account_type":"checking","currency_code":"USD"}).json()
    second=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Savings","account_type":"savings","currency_code":"USD"}).json()
    source=client.post("/v1/imports/sources",headers=headers,json={"account_id":first["account_id"],"name":"Used source"}).json()
    client.post(f"/v1/imports/sources/{source['source_id']}/csv",headers=headers,json={"filename":"bank.csv","csv_text":"date,description,amount\n2026-08-13,Market,-12.34\n"})
    moved=client.put(f"/v1/imports/sources/{source['source_id']}",headers=headers,json={"account_id":second["account_id"],"name":"Used source"})
    assert moved.status_code == 409
    assert "cannot be changed" in moved.json()["detail"]


def test_phase5_document_storage_is_authenticated_and_can_remain_unlinked() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    content=b"local receipt evidence"
    created=client.post("/v1/documents",headers=headers,json={"kind":"receipt","filename":"receipt.txt","content_type":"text/plain","data_base64":base64.b64encode(content).decode(),"notes":"Manual path"})
    assert created.status_code == 201
    document=created.json()["document"]
    assert document["linked_transaction_id"] is None
    assert document["checksum_sha256"] == hashlib.sha256(content).hexdigest()
    assert client.get(f"/v1/documents/{document['document_id']}/content").status_code == 401
    download=client.get(f"/v1/documents/{document['document_id']}/content",headers=headers)
    assert download.status_code == 200 and download.content == content
    assert download.headers["cache-control"] == "private, no-store"


def test_phase5_image_thumbnail_is_generated_and_protected() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    png=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    created=client.post("/v1/documents",headers=headers,json={"kind":"receipt","filename":"receipt.png","content_type":"image/png","data_base64":base64.b64encode(png).decode()})
    assert created.status_code == 201 and created.json()["document"]["has_thumbnail"] is True
    document_id=created.json()["document"]["document_id"]
    assert client.get(f"/v1/documents/{document_id}/thumbnail").status_code == 401
    thumbnail=client.get(f"/v1/documents/{document_id}/thumbnail",headers=headers)
    assert thumbnail.status_code == 200 and thumbnail.headers["content-type"] == "image/jpeg"


def test_phase5_manual_document_matching_is_deterministic_and_reversible() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    account=client.post("/v1/ledger/accounts",headers=headers,json={"name":"Checking","account_type":"checking","currency_code":"USD"}).json()
    transaction=client.post("/v1/ledger/transactions",headers=headers,json={"account_id":account["account_id"],"transaction_date":"2026-08-12","amount_minor":-2499,"currency_code":"USD","payee":"Corner Shop"}).json()
    created=client.post("/v1/documents",headers=headers,json={"kind":"receipt","filename":"shop.txt","content_type":"text/plain","data_base64":base64.b64encode(b"receipt").decode(),"account_id":account["account_id"],"document_date":"2026-08-12","amount_minor":2499,"currency_code":"USD","payee":"Corner Shop"}).json()
    candidate=created["matches"][0]
    assert candidate["transaction_id"] == transaction["transaction_id"]
    assert candidate["confidence_percent"] == 100
    confirmed=client.put(f"/v1/documents/{created['document']['document_id']}/matches/{candidate['match_id']}",headers=headers,json={"action":"confirm"})
    assert confirmed.json()["document"]["linked_transaction_id"] == transaction["transaction_id"]
    unmatched=client.delete(f"/v1/documents/{created['document']['document_id']}/match",headers=headers)
    assert unmatched.json()["document"]["linked_transaction_id"] is None
    ledger=client.get(f"/v1/ledger/transactions/{transaction['transaction_id']}",headers=headers).json()
    assert ledger["transaction"]["amount_minor"] == -2499


def test_phase5_document_write_policy_and_safe_deletion() -> None:
    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    created=client.post("/v1/documents",headers=headers,json={"kind":"general","filename":"note.txt","content_type":"text/plain","data_base64":base64.b64encode(b"private note").decode()}).json()["document"]
    client.post("/v1/household/members",headers=headers,json={"email":"viewer@example.com","display_name":"Viewer","password":"another-long-local-password","role":"viewer"})
    viewer=client.post("/v1/auth/login",json={"email":"viewer@example.com","password":"another-long-local-password"}).json(); viewer_headers={"Authorization":f"Bearer {viewer['access_token']}"}
    assert client.get("/v1/documents",headers=viewer_headers).status_code == 200
    assert client.get(f"/v1/documents/{created['document_id']}/content",headers=viewer_headers).status_code == 200
    assert client.post("/v1/documents",headers=viewer_headers,json={"kind":"general","filename":"x.txt","content_type":"text/plain","data_base64":base64.b64encode(b"x").decode()}).status_code == 403
    assert client.delete(f"/v1/documents/{created['document_id']}",headers=viewer_headers).status_code == 403
    assert client.delete(f"/v1/documents/{created['document_id']}",headers=headers).status_code == 204
    assert client.get(f"/v1/documents/{created['document_id']}/content",headers=headers).status_code == 404


def test_phase5_ai_is_local_optional_and_suggestions_require_review(monkeypatch) -> None:
    from app import documents as document_service

    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    created=client.post("/v1/documents",headers=headers,json={"kind":"receipt","filename":"ai.txt","content_type":"text/plain","data_base64":base64.b64encode(b"receipt content").decode()}).json()["document"]
    assert client.post(f"/v1/documents/{created['document_id']}/extractions",headers=headers).status_code == 409
    assert client.put("/v1/system/integrations",headers=headers,json={"ai_provider":"ollama","ai_base_url":"https://public-ai.example.com","ai_enabled":True,"ai_extract_enabled":True}).status_code == 422
    configured=client.put("/v1/system/integrations",headers=headers,json={"ai_provider":"ollama","ai_base_url":"http://ollama:11434","ai_model":"receipt-local","ai_enabled":True,"ai_extract_enabled":True})
    assert configured.status_code == 200
    queued=client.post(f"/v1/documents/{created['document_id']}/extractions",headers=headers)
    assert queued.status_code == 202 and queued.json()["status"] == "queued"
    monkeypatch.setattr(document_service,"extract_document",lambda content,content_type,values:{"schema_version":"receipt-extraction-v1","merchant":{"name":"Local Market"},"transaction":{"date":"2026-08-13","time":"15:42","receipt_number":"A-123"},"amounts":{"subtotal_minor":3000,"discount_minor":0,"tax_minor":210,"tip_minor":0,"total_minor":3210,"currency_code":"USD"},"payment":{"method":"Visa","card_last_four":"4242"},"line_items":[{"description":"Groceries","quantity":1,"unit_price_minor":3000,"total_minor":3000,"sku":None,"category_hint":"Groceries"}],"category_hint":"Groceries","confidence":{"overall_percent":91,"field_percent":{"amounts.total_minor":98}},"validation":{"arithmetic_consistent":True,"warnings":[],"review_required":True},"explanation":"Visible total"})
    with TestSession() as db:
        assert document_service.process_next_extraction(db) == queued.json()["extraction_id"]
    detail=client.get(f"/v1/documents/{created['document_id']}",headers=headers).json()
    assert detail["document"]["amount_minor"] is None
    extraction=detail["extractions"][0]
    assert extraction["status"] == "complete" and extraction["user_disposition"] == "pending"
    assert extraction["suggestions"]["line_items"][0]["description"] == "Groceries"
    assert extraction["suggestions"]["amounts"]["tax_minor"] == 210
    accepted=client.put(f"/v1/documents/{created['document_id']}/extractions/{extraction['extraction_id']}",headers=headers,json={"action":"accept"}).json()
    assert accepted["document"]["payee"] == "Local Market"
    assert accepted["document"]["amount_minor"] == 3210


def test_phase5_receipt_extraction_normalizes_exact_fields_and_flags_bad_arithmetic() -> None:
    from app.local_ai import normalize_extraction

    result=normalize_extraction({"document_type":"receipt","merchant":{"name":"Corner Market","address":"10 Main St"},"transaction":{"date":"2026-08-13","time":"09:14","receipt_number":"R-42"},"amounts":{"subtotal_minor":1000,"discount_minor":0,"tax_minor":80,"tip_minor":0,"total_minor":1200,"currency_code":"USD"},"payment":{"method":"Mastercard","card_last_four":"1234"},"line_items":[{"description":"Milk","quantity":2,"unit_price_minor":500,"total_minor":1000,"category_hint":"Groceries"}],"category_hint":"Groceries","confidence":{"overall_percent":94,"field_percent":{"merchant.name":99,"amounts.total_minor":98}},"explanation":"Most fields were clear"})
    assert result["schema_version"] == "receipt-extraction-v1"
    assert result["merchant"]["name"] == "Corner Market"
    assert result["transaction"]["receipt_number"] == "R-42"
    assert result["line_items"][0]["quantity"] == 2
    assert result["confidence"]["overall_percent"] == 94
    assert result["validation"]["review_required"] is True
    assert result["validation"]["arithmetic_consistent"] is False
    assert "do not add up" in result["validation"]["warnings"][0]


def test_local_vision_model_check_is_structured_audited_and_non_persistent(monkeypatch) -> None:
    from app import routes

    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    client.put("/v1/system/integrations",headers=headers,json={"ai_provider":"lm_studio","ai_base_url":"http://host.docker.internal:1234","ai_model":"receipt-vision"})
    monkeypatch.setattr(routes,"test_local_vision_model",lambda values:{"success":True,"provider":"lm_studio","model":"receipt-vision","duration_ms":1250,"checks":{"model_available":True,"structured_response":True,"merchant":True,"date":True,"subtotal":True,"tax":True,"total":True,"currency":True,"line_items":True,"arithmetic":True},"detail":"Vision extraction passed every synthetic receipt check."})
    response=client.post("/v1/system/integrations/ai/vision-test",headers=headers)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["checks"]["line_items"] is True
    assert client.get("/v1/documents",headers=headers).json() == []
    with TestSession() as db:
        assert db.query(AuditEvent).filter(AuditEvent.action == "system.ai_vision_tested").count() == 1


def test_phase5_disabling_ai_leaves_manual_documents_and_queued_work_untouched() -> None:
    from app.documents import process_next_extraction

    client=TestClient(app)
    owner=client.post("/v1/setup",json={"household_name":"Home","email":"owner@example.com","display_name":"Owner","password":"a-long-local-test-password"}).json(); headers={"Authorization":f"Bearer {owner['access_token']}"}
    client.put("/v1/system/integrations",headers=headers,json={"ai_provider":"ollama","ai_base_url":"http://ollama:11434","ai_enabled":True,"ai_extract_enabled":True})
    created_response=client.post("/v1/documents",headers=headers,json={"kind":"invoice","filename":"invoice.txt","content_type":"text/plain","data_base64":base64.b64encode(b"invoice").decode()}).json()
    created=created_response["document"]
    assert created_response["extractions"][0]["status"] == "queued"
    queued=client.post(f"/v1/documents/{created['document_id']}/extractions",headers=headers).json()
    client.put("/v1/system/integrations",headers=headers,json={"ai_enabled":False,"ai_extract_enabled":False})
    with TestSession() as db:
        assert process_next_extraction(db) is None
    detail=client.get(f"/v1/documents/{created['document_id']}",headers=headers).json()
    assert detail["extractions"][0]["extraction_id"] == queued["extraction_id"]
    assert detail["extractions"][0]["status"] == "queued"
    updated=client.put(f"/v1/documents/{created['document_id']}",headers=headers,json={"payee":"Manual Vendor","amount_minor":4500,"currency_code":"USD"})
    assert updated.status_code == 200 and updated.json()["document"]["payee"] == "Manual Vendor"


def test_phase5a_spending_report_classifies_and_filters_ledger_activity() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    savings = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Savings", "account_type": "savings", "currency_code": "USD"}).json()
    retirement = client.post("/v1/ledger/accounts", headers=headers, json={"name": "401k", "account_type": "401k", "currency_code": "USD"}).json()
    business = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Studio", "account_type": "business_checking", "currency_code": "USD"}).json()
    cad = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Canadian cash", "account_type": "cash", "currency_code": "CAD"}).json()
    categories = client.get("/v1/ledger/categories", headers=headers).json()
    groceries = next(item for item in categories if item["name"] == "Groceries")
    utilities = next(item for item in categories if item["name"] == "Utilities")
    paycheck = next(item for item in categories if item["name"] == "Paycheck")
    merchant = client.post("/v1/ledger/merchants", headers=headers, json={"name": "Neighborhood Market"}).json()

    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "merchant_id": merchant["merchant_id"], "transaction_date": "2026-08-05", "amount_minor": -12000, "currency_code": "USD", "payee": "Market", "splits": [{"category_id": groceries["category_id"], "amount_minor": -8000}, {"category_id": utilities["category_id"], "amount_minor": -4000}]})
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "merchant_id": merchant["merchant_id"], "transaction_date": "2026-08-06", "amount_minor": 2000, "currency_code": "USD", "payee": "Market refund", "splits": [{"category_id": groceries["category_id"], "amount_minor": 2000}]})
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-08-07", "amount_minor": 50000, "currency_code": "USD", "payee": "Employer", "splits": [{"category_id": paycheck["category_id"], "amount_minor": 50000}]})
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-08-08", "amount_minor": -3000, "currency_code": "USD", "status": "pending", "payee": "Pending market", "splits": [{"category_id": groceries["category_id"], "amount_minor": -3000}]})
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": retirement["account_id"], "transaction_date": "2026-08-09", "amount_minor": 10000, "currency_code": "USD", "payee": "Contribution", "activity_type": "contribution"})
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": business["account_id"], "transaction_date": "2026-08-10", "amount_minor": -7000, "currency_code": "USD", "payee": "Business supplies"})
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": cad["account_id"], "transaction_date": "2026-08-10", "amount_minor": -9000, "currency_code": "CAD", "payee": "Canadian purchase"})
    client.post("/v1/ledger/transfers", headers=headers, json={"from_account_id": checking["account_id"], "to_account_id": savings["account_id"], "transaction_date": "2026-08-11", "amount_minor": 5000, "currency_code": "USD"})
    reversed_item = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-08-11", "amount_minor": -4000, "currency_code": "USD", "payee": "Reversed charge"}).json()
    client.post(f"/v1/ledger/transactions/{reversed_item['transaction_id']}/reverse", headers=headers, json={"transaction_date": "2026-08-12", "reason": "Duplicate charge"})

    base = "/v1/reports/spending?date_from=2026-08-01&date_to=2026-08-31&currency_code=USD&ownership_scope=household"
    report = client.get(base, headers=headers)
    assert report.status_code == 200
    payload = report.json()
    assert payload["rule_version"] == "spending-report-v1"
    assert payload["totals"] == {"spending_minor": 10000, "income_minor": 50000, "refunds_minor": 2000, "debt_payments_minor": 0, "investment_activity_minor": 10000, "net_cash_flow_minor": 50000}
    assert payload["counts"]["transfers_excluded"] == 2
    assert payload["counts"]["reversals_excluded"] == 2
    assert {item["classification"] for item in payload["transactions"]} == {"spending", "refund", "income", "investment_activity"}
    assert sum(item["amount_minor"] for item in payload["by_category"]) == payload["totals"]["spending_minor"]
    assert client.get(f"{base}&include_pending=true", headers=headers).json()["totals"]["spending_minor"] == 13000
    grocery_report = client.get(f"{base}&category_id={groceries['category_id']}", headers=headers).json()
    assert grocery_report["totals"]["spending_minor"] == 6000
    assert grocery_report["totals"]["net_cash_flow_minor"] == -6000
    business_report = client.get(base.replace("ownership_scope=household", "ownership_scope=business"), headers=headers).json()
    assert business_report["totals"]["spending_minor"] == 7000
    assert client.get(base.replace("currency_code=USD", "currency_code=CAD"), headers=headers).json()["totals"]["spending_minor"] == 9000


def test_phase5a_report_exports_presets_and_role_policy() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-13", "amount_minor": -1234, "currency_code": "USD", "payee": "Corner Shop"})
    query = "date_from=2026-08-01&date_to=2026-08-31&currency_code=USD&ownership_scope=household"
    exported = client.get(f"/v1/reports/spending.csv?{query}", headers=headers)
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    assert "Corner Shop" in exported.text and "spending-report-v1" in exported.text
    printable = client.get(f"/v1/reports/spending/print?{query}", headers=headers)
    assert printable.status_code == 200 and printable.headers["cache-control"] == "private, no-store"
    assert "Print or save as PDF" in printable.text
    preset = client.post("/v1/reports/presets", headers=headers, json={"name": "Monthly spending", "report_type": "spending", "filters": {"currency_code": "USD", "ownership_scope": "household"}})
    assert preset.status_code == 201
    preset_id = preset.json()["preset_id"]
    client.post("/v1/household/members", headers=headers, json={"email": "viewer@example.com", "display_name": "Viewer", "password": "another-long-local-password", "role": "viewer"})
    viewer = client.post("/v1/auth/login", json={"email": "viewer@example.com", "password": "another-long-local-password"}).json()
    viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
    assert client.get(f"/v1/reports/spending?{query}", headers=viewer_headers).status_code == 200
    assert len(client.get("/v1/reports/presets", headers=viewer_headers).json()) == 1
    assert client.get(f"/v1/reports/spending.csv?{query}", headers=viewer_headers).status_code == 403
    assert client.get(f"/v1/reports/spending/print?{query}", headers=viewer_headers).status_code == 403
    assert client.post("/v1/reports/presets", headers=viewer_headers, json={"name": "Blocked", "report_type": "spending", "filters": {}}).status_code == 403
    assert client.delete(f"/v1/reports/presets/{preset_id}", headers=headers).status_code == 204
    assert client.get("/v1/reports/presets", headers=headers).json() == []


def test_phase6b_rules_are_user_created_and_only_prepare_future_rows() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    category = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Groceries")
    source = client.post("/v1/imports/sources", headers=headers, json={"account_id": account["account_id"], "name": "Bank CSV"}).json()
    assert client.get("/v1/automation/rules", headers=headers).json() == []

    first = client.post(f"/v1/imports/sources/{source['source_id']}/csv", headers=headers, json={"filename": "first.csv", "csv_text": "date,description,amount\n2026-08-01,Local Market,-25.00\n"}).json()
    row = client.get("/v1/reconciliation/queue", headers=headers).json()[0]
    approved = client.post(f"/v1/reconciliation/rows/{row['row_id']}/transaction", headers=headers, json={"category_id": category["category_id"], "remember_rule": True})
    assert approved.status_code == 201
    rules = client.get("/v1/automation/rules", headers=headers).json()
    assert len(rules) == 1 and rules[0]["created_from_action"] == "apply_and_remember"

    second = client.post(f"/v1/imports/sources/{source['source_id']}/csv", headers=headers, json={"filename": "second.csv", "csv_text": "date,description,amount\n2026-08-08,Local Market,-31.00\n"}).json()
    assert second["ready_count"] == 1 and second["mapping_version_id"] == first["mapping_version_id"]
    ready = client.get("/v1/reconciliation/queue", headers=headers).json()[0]
    assert ready["status"] == "ready" and ready["proposed_category_name"] == "Groceries"
    batch_approval = client.post(f"/v1/automation/batches/{second['batch_id']}/approve-ready", headers=headers)
    assert batch_approval.json()["approved_count"] == 1
    assert client.post(f"/v1/automation/batches/{second['batch_id']}/undo-ready", headers=headers).json()["undone_count"] == 1
    assert client.get("/v1/reconciliation/queue", headers=headers).json()[0]["status"] == "ready"

    deleted = client.delete(f"/v1/automation/rules/{rules[0]['rule_id']}", headers=headers)
    assert deleted.status_code == 204
    refreshed = client.get("/v1/reconciliation/queue", headers=headers).json()[0]
    assert refreshed["status"] == "unmatched" and refreshed["proposed_category_id"] is None
    client.post("/v1/household/members", headers=headers, json={"email": "viewer@example.com", "display_name": "Viewer", "password": "another-long-local-password", "role": "viewer"})
    viewer = client.post("/v1/auth/login", json={"email": "viewer@example.com", "password": "another-long-local-password"}).json()
    viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
    assert client.get("/v1/automation/summary", headers=viewer_headers).status_code == 200
    assert client.post(f"/v1/automation/batches/{second['batch_id']}/approve-ready", headers=viewer_headers).status_code == 403


def test_phase6b_transfer_and_recurring_suggestions_require_confirmation() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    savings = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Savings", "account_type": "savings", "currency_code": "USD"}).json()
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-08-10", "amount_minor": -5000, "currency_code": "USD", "payee": "Savings transfer"})
    source = client.post("/v1/imports/sources", headers=headers, json={"account_id": savings["account_id"], "name": "Savings CSV"}).json()
    imported = client.post(f"/v1/imports/sources/{source['source_id']}/csv", headers=headers, json={"filename": "savings.csv", "csv_text": "date,description,amount\n2026-08-11,Savings transfer,50.00\n"}).json()
    assert imported["transfer_count"] == 1
    candidate = client.get("/v1/automation/transfer-candidates", headers=headers).json()[0]
    assert client.put(f"/v1/automation/transfer-candidates/{candidate['candidate_id']}", headers=headers, json={"action": "confirm"}).status_code == 200

    for transaction_date in ("2026-07-01", "2026-07-31"):
        client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": transaction_date, "amount_minor": -1200, "currency_code": "USD", "payee": "Music Service"})
    third = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-08-30", "amount_minor": -1200, "currency_code": "USD", "payee": "Music Service"}).json()
    proposals = client.get("/v1/automation/recurring-candidates", headers=headers).json()
    proposal = next(item for item in proposals if item["transaction_id"] == third["transaction_id"])
    created = client.post("/v1/automation/recurring", headers=headers, json={"transaction_id": proposal["transaction_id"], "transaction_ids": proposal["transaction_ids"]})
    assert created.status_code == 201 and created.json()["profile_type"] == "bill"


def test_phase6b_reimbursement_links_are_explicit_and_reversible() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    category = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Travel")
    expense = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-01", "amount_minor": -10000, "currency_code": "USD", "payee": "Hotel", "splits": [{"category_id": category["category_id"], "amount_minor": -10000}]}).json()
    refund = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-05", "amount_minor": 4000, "currency_code": "USD", "payee": "Travel reimbursement"}).json()
    linked = client.post("/v1/automation/reimbursements", headers=headers, json={"reimbursement_transaction_id": refund["transaction_id"], "original_transaction_id": expense["transaction_id"], "amount_minor": 4000, "category_id": category["category_id"]})
    assert linked.status_code == 201
    links = client.get("/v1/automation/reimbursements", headers=headers).json()
    assert len(links) == 1 and links[0]["amount_minor"] == 4000
    report = client.get("/v1/reports/spending?date_from=2026-08-01&date_to=2026-08-31&currency_code=USD&ownership_scope=household", headers=headers).json()
    assert report["totals"]["spending_minor"] == 6000
    assert client.delete(f"/v1/automation/reimbursements/{links[0]['reimbursement_link_id']}", headers=headers).status_code == 204
    assert client.get("/v1/automation/reimbursements", headers=headers).json() == []


def test_phase6b_shared_ingestion_contract_preserves_transport_provenance() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    source = client.post("/v1/imports/sources", headers=headers, json={"account_id": account["account_id"], "name": "Mapped source"}).json()
    adapter = client.post(
        f"/v1/imports/sources/{source['source_id']}/adapter",
        headers=headers,
        json={"filename": "adapter.csv", "csv_text": "date,description,amount\n2026-08-01,Adapter row,-12.34\n", "upstream_reference": "provider:statement-1"},
    )
    assert adapter.status_code == 201
    assert adapter.json()["ingestion_channel"] == "financial_data_adapter"
    assert adapter.json()["upstream_reference"] == "provider:statement-1"
    assert adapter.json()["mapping_version_id"]

    document = client.post(
        "/v1/documents",
        headers=headers,
        json={"kind": "statement", "filename": "stored.csv", "content_type": "text/plain", "data_base64": base64.b64encode(b"date,description,amount\n2026-08-02,Stored row,-23.45\n").decode()},
    ).json()["document"]
    stored = client.post(f"/v1/imports/sources/{source['source_id']}/documents/{document['document_id']}", headers=headers)
    assert stored.status_code == 201
    assert stored.json()["ingestion_channel"] == "document_attachment"
    assert stored.json()["upstream_reference"] == f"document:{document['document_id']}"
    queue = client.get("/v1/reconciliation/queue", headers=headers).json()
    assert {item["raw_payee"] for item in queue} == {"Adapter row", "Stored row"}


def test_phase6b_configurable_windows_ranked_search_and_effect_previews() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    savings = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Savings", "account_type": "savings", "currency_code": "USD"}).json()
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-08-10", "amount_minor": -5000, "currency_code": "USD", "payee": "Savings transfer"})
    source = client.post("/v1/imports/sources", headers=headers, json={"account_id": savings["account_id"], "name": "Savings CSV"}).json()
    client.post(f"/v1/imports/sources/{source['source_id']}/csv", headers=headers, json={"filename": "savings.csv", "csv_text": "date,description,amount\n2026-08-13,Savings transfer,50.00\n"})
    candidate = client.get("/v1/automation/transfer-candidates", headers=headers).json()[0]
    preview = client.get(f"/v1/automation/transfer-candidates/{candidate['candidate_id']}/preview", headers=headers)
    assert preview.status_code == 200
    assert preview.json()["account_balances_change_minor"] == 0
    assert preview.json()["spending_removed_minor"] == 5000
    assert client.put("/v1/automation/preferences", headers=headers, json={"transfer_window_days": 1, "reimbursement_window_days": 90}).status_code == 200
    assert client.get("/v1/automation/transfer-candidates", headers=headers).json() == []
    assert client.put("/v1/automation/preferences", headers=headers, json={"transfer_window_days": 3, "reimbursement_window_days": 90}).status_code == 200
    assert len(client.get("/v1/automation/transfer-candidates", headers=headers).json()) == 1

    travel = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Travel")
    expense = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-07-15", "amount_minor": -12000, "currency_code": "USD", "payee": "Hotel", "splits": [{"category_id": travel["category_id"], "amount_minor": -12000}]}).json()
    refund = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-08-12", "amount_minor": 7000, "currency_code": "USD", "payee": "Hotel refund"}).json()
    candidates = client.get(
        f"/v1/automation/reimbursement-candidates/{refund['transaction_id']}?payee=Hotel&account_id={checking['account_id']}&category_id={travel['category_id']}&amount_minor=12000&date_from=2026-07-01&date_to=2026-08-12",
        headers=headers,
    ).json()
    assert candidates[0]["transaction_id"] == expense["transaction_id"]
    assert candidates[0]["account_name"] == "Checking"
    refund_preview = client.post("/v1/automation/reimbursements/preview", headers=headers, json={"reimbursement_transaction_id": refund["transaction_id"], "original_transaction_id": expense["transaction_id"], "amount_minor": 7000, "category_id": travel["category_id"]})
    assert refund_preview.status_code == 200
    assert refund_preview.json()["spending_change_minor"] == -7000
    assert refund_preview.json()["income_change_minor"] == 0


def test_phase6b_recurring_history_is_same_account_and_import_match_links_profile() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    other = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Other", "account_type": "checking", "currency_code": "USD"}).json()
    transaction_ids = []
    for transaction_date in ("2026-05-01", "2026-06-01", "2026-07-01"):
        created = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": transaction_date, "amount_minor": -1599, "currency_code": "USD", "payee": "Internet Service"}).json()
        transaction_ids.append(created["transaction_id"])
    unrelated = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": other["account_id"], "transaction_date": "2026-07-15", "amount_minor": -1599, "currency_code": "USD", "payee": "Internet Service"}).json()
    proposal = client.get(f"/v1/automation/recurring/{transaction_ids[-1]}/proposal", headers=headers).json()
    assert proposal["eligible"] is True
    assert unrelated["transaction_id"] not in proposal["transaction_ids"]
    created_profile = client.post("/v1/automation/recurring", headers=headers, json={"transaction_id": transaction_ids[-1], "transaction_ids": transaction_ids})
    assert created_profile.status_code == 201
    assert created_profile.json()["linked_transaction_count"] == 3

    source = client.post("/v1/imports/sources", headers=headers, json={"account_id": checking["account_id"], "name": "Checking CSV"}).json()
    imported = client.post(f"/v1/imports/sources/{source['source_id']}/csv", headers=headers, json={"filename": "august.csv", "csv_text": "date,description,amount\n2026-08-01,Internet Service,-15.99\n"}).json()
    assert imported["recurring_count"] == 1
    row = next(item for item in client.get("/v1/reconciliation/queue", headers=headers).json() if item["automation_kind"] == "recurring_match")
    approved = client.post(f"/v1/reconciliation/rows/{row['row_id']}/transaction", headers=headers, json={"category_id": None, "remember_rule": False})
    assert approved.status_code == 201
    assert approved.json()["created_transaction_id"]
    assert len(client.get("/v1/automation/recurring-links", headers=headers).json()) == 4


def test_phase6b_new_and_manual_rule_runs_recalculate_pending_review() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    groceries = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Groceries")
    source = client.post("/v1/imports/sources", headers=headers, json={"account_id": account["account_id"], "name": "Checking CSV"}).json()
    client.post(
        f"/v1/imports/sources/{source['source_id']}/csv",
        headers=headers,
        json={"filename": "market.csv", "csv_text": "date,description,amount\n2026-08-01,Local Market,-20.00\n2026-08-08,Local Market,-25.00\n"},
    )
    queue = client.get("/v1/reconciliation/queue", headers=headers).json()
    first = next(item for item in queue if item["transaction_date"] == "2026-08-01")
    learned = client.post(f"/v1/reconciliation/rows/{first['row_id']}/transaction", headers=headers, json={"category_id": groceries["category_id"], "remember_rule": True})
    assert learned.status_code == 201
    pending = client.get("/v1/reconciliation/queue", headers=headers).json()
    assert len(pending) == 1
    assert pending[0]["status"] == "ready"
    assert pending[0]["proposed_category_name"] == "Groceries"

    rule = client.get("/v1/automation/rules", headers=headers).json()[0]
    updated = client.patch(
        f"/v1/automation/rules/{rule['rule_id']}",
        headers=headers,
        json={"rule_name": "Neighborhood markets", "description_pattern": "*market*", "priority": 250, "account_id": account["account_id"], "source_id": source["source_id"]},
    )
    assert updated.status_code == 200
    assert updated.json()["description_pattern"] == "*market*"
    assert updated.json()["rule_name"] == "Neighborhood markets"
    assert updated.json()["priority"] == 250
    client.post(
        f"/v1/imports/sources/{source['source_id']}/csv",
        headers=headers,
        json={"filename": "another-market.csv", "csv_text": "date,description,amount\n2026-08-15,Northside Market #42,-30.00\n"},
    )
    rerun = client.post(f"/v1/automation/rules/{rule['rule_id']}/run", headers=headers)
    assert rerun.status_code == 200
    assert rerun.json()["preview_unconfirmed_count"] == 2


def test_phase6b_pending_import_rows_become_confirmable_transfer_pair() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    card = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Credit card", "account_type": "credit_card", "currency_code": "USD"}).json()
    checking_source = client.post("/v1/imports/sources", headers=headers, json={"account_id": checking["account_id"], "name": "Checking CSV"}).json()
    card_source = client.post("/v1/imports/sources", headers=headers, json={"account_id": card["account_id"], "name": "Card CSV"}).json()
    client.post(f"/v1/imports/sources/{checking_source['source_id']}/csv", headers=headers, json={"filename": "checking.csv", "csv_text": "date,description,amount\n2026-08-10,Card payment,-200.00\n"})
    imported = client.post(f"/v1/imports/sources/{card_source['source_id']}/csv", headers=headers, json={"filename": "card.csv", "csv_text": "date,description,amount\n2026-08-11,Payment received,200.00\n"}).json()
    assert imported["transfer_count"] == 1
    candidate = client.get("/v1/automation/transfer-candidates", headers=headers).json()[0]
    assert candidate["counterparty_import_row_id"]
    assert candidate["counterparty_transaction_id"] is None
    assert client.get(f"/v1/automation/transfer-candidates/{candidate['candidate_id']}/preview", headers=headers).status_code == 200
    assert client.put(f"/v1/automation/transfer-candidates/{candidate['candidate_id']}", headers=headers, json={"action": "confirm"}).status_code == 200
    assert client.get("/v1/reconciliation/queue", headers=headers).json() == []
    report = client.get("/v1/reports/spending?date_from=2026-08-01&date_to=2026-08-31&currency_code=USD&ownership_scope=household", headers=headers).json()
    assert report["totals"]["spending_minor"] == 0
    assert report["totals"]["income_minor"] == 0
    assert report["counts"]["transfers_excluded"] == 2


def test_phase6b_resolve_transfer_supports_tracked_external_and_real_spending() -> None:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    savings = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Savings", "account_type": "savings", "currency_code": "USD"}).json()
    source = client.post("/v1/imports/sources", headers=headers, json={"account_id": checking["account_id"], "name": "Checking CSV"}).json()
    debt = client.post("/v1/obligations/debts", headers=headers, json={"name": "Auto loan", "lender": "Local credit union", "balance_minor": 1250000, "balance_as_of_date": "2026-07-31", "apr_basis_points": 550, "minimum_payment_minor": 22500, "due_day": 4, "next_due_date": "2026-08-04", "currency_code": "USD"}).json()
    imported = client.post(f"/v1/imports/sources/{source['source_id']}/csv", headers=headers, json={"filename": "movements.csv", "csv_text": "date,description,amount\n2026-08-01,Move to savings,-100.00\n2026-08-02,Move to external brokerage,-50.00\n2026-08-03,Zelle rent,-75.00\n2026-08-04,Auto loan payment,-225.00\n"})
    assert imported.status_code == 201
    queue = client.get("/v1/reconciliation/queue", headers=headers).json()
    tracked = next(item for item in queue if item["raw_payee"] == "Move to savings")
    external = next(item for item in queue if item["raw_payee"] == "Move to external brokerage")
    payment = next(item for item in queue if item["raw_payee"] == "Zelle rent")
    loan_payment = next(item for item in queue if item["raw_payee"] == "Auto loan payment")
    housing = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Housing")

    tracked_payload = {"row_id": tracked["row_id"], "resolution_type": "tracked_account", "counterparty_account_id": savings["account_id"]}
    tracked_preview = client.post("/v1/automation/transfer-resolutions/preview", headers=headers, json=tracked_payload)
    assert tracked_preview.status_code == 200 and tracked_preview.json()["account_balances_change_minor"] == 0
    tracked_result = client.post("/v1/automation/transfer-resolutions", headers=headers, json=tracked_payload)
    assert tracked_result.status_code == 201 and tracked_result.json()["linked_id"]

    external_payload = {"row_id": external["row_id"], "resolution_type": "external_owned_account"}
    assert client.post("/v1/automation/transfer-resolutions/preview", headers=headers, json=external_payload).status_code == 200
    assert client.post("/v1/automation/transfer-resolutions", headers=headers, json=external_payload).status_code == 201

    payment_payload = {"row_id": payment["row_id"], "resolution_type": "payment", "category_id": housing["category_id"]}
    assert client.post("/v1/automation/transfer-resolutions/preview", headers=headers, json=payment_payload).status_code == 200
    assert client.post("/v1/automation/transfer-resolutions", headers=headers, json=payment_payload).status_code == 201
    loan_payload = {"row_id": loan_payment["row_id"], "resolution_type": "loan_or_repayment", "debt_id": debt["debt_id"], "category_id": housing["category_id"], "principal_amount_minor": 15000}
    loan_preview = client.post("/v1/automation/transfer-resolutions/preview", headers=headers, json=loan_payload)
    assert loan_preview.status_code == 200 and loan_preview.json()["spending_removed_minor"] == 0
    loan_result = client.post("/v1/automation/transfer-resolutions", headers=headers, json=loan_payload)
    assert loan_result.status_code == 201
    updated_debt = next(item for item in client.get("/v1/obligations/debts", headers=headers).json() if item["debt_id"] == debt["debt_id"])
    assert updated_debt["balance_minor"] == 1235000
    assert updated_debt["balance_anchor_minor"] == 1250000
    recalculated = client.post(f"/v1/obligations/debts/{debt['debt_id']}/recalculate", headers=headers)
    assert recalculated.status_code == 200 and recalculated.json()["balance_minor"] == 1235000
    loan_transaction = next(item for item in client.get("/v1/ledger/transactions", headers=headers).json() if item["transaction_id"] == loan_result.json()["transaction_id"])
    assert loan_transaction["activity_type"] == "debt_payment"
    assert loan_transaction["splits"][0]["category_name"] == "Housing"
    report = client.get("/v1/reports/spending?date_from=2026-08-01&date_to=2026-08-31&currency_code=USD&ownership_scope=household", headers=headers).json()
    assert report["totals"]["spending_minor"] == 30000
    assert report["totals"]["debt_payments_minor"] == 22500
    assert report["counts"]["transfers_excluded"] == 3

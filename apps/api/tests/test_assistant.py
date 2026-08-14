from fastapi.testclient import TestClient
from test_health import Base, app, test_engine


def setup_function() -> None:
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)


def owner_client() -> tuple[TestClient, dict[str, str]]:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    return client, {"Authorization": f"Bearer {owner['access_token']}"}


def test_deterministic_category_suggestion_requires_review_and_learns_reversible_rule() -> None:
    client, headers = owner_client()
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    groceries = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Groceries")
    merchant = client.post("/v1/ledger/merchants", headers=headers, json={"name": "Local Market", "aliases": ["MARKET 42"]}).json()
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "merchant_id": merchant["merchant_id"], "transaction_date": "2026-08-01", "amount_minor": -2500, "currency_code": "USD", "payee": "MARKET 42", "splits": [{"category_id": groceries["category_id"], "amount_minor": -2500}]})
    uncategorized = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "merchant_id": merchant["merchant_id"], "transaction_date": "2026-08-13", "amount_minor": -4200, "currency_code": "USD", "payee": "MARKET 42"}).json()

    generated = client.post("/v1/categorization/suggestions/generate", headers=headers, json={"use_ai": False})
    assert generated.status_code == 201 and len(generated.json()) == 1
    suggestion = generated.json()[0]
    assert suggestion["transaction_id"] == uncategorized["transaction_id"]
    assert suggestion["provider"] == "rules" and suggestion["proposed_splits"][0]["category_name"] == "Groceries"
    assert client.get(f"/v1/ledger/transactions/{uncategorized['transaction_id']}", headers=headers).json()["transaction"]["splits"] == []

    accepted = client.put(f"/v1/categorization/suggestions/{suggestion['suggestion_id']}/review", headers=headers, json={"action": "accept", "learn_rule": True})
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    transaction = client.get(f"/v1/ledger/transactions/{uncategorized['transaction_id']}", headers=headers).json()["transaction"]
    assert transaction["splits"][0]["category_name"] == "Groceries" and transaction["splits"][0]["amount_minor"] == -4200
    rules = client.get("/v1/categorization/rules", headers=headers).json()
    assert len(rules) == 1 and rules[0]["match_type"] == "merchant" and rules[0]["is_active"] is True
    rule_id = rules[0]["rule_id"]
    assert client.patch(f"/v1/categorization/rules/{rule_id}", headers=headers, json={"is_active": False}).json()["is_active"] is False
    assert client.delete(f"/v1/categorization/rules/{rule_id}", headers=headers).status_code == 204


def test_category_suggestion_edit_reject_batch_and_role_boundaries() -> None:
    client, headers = owner_client()
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    categories = client.get("/v1/ledger/categories", headers=headers).json()
    groceries = next(item for item in categories if item["name"] == "Groceries")
    dining = next(item for item in categories if item["name"] == "Dining out")
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-01", "amount_minor": -1000, "currency_code": "USD", "payee": "Cafe", "splits": [{"category_id": dining["category_id"], "amount_minor": -1000}]})
    first = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-02", "amount_minor": -1500, "currency_code": "USD", "payee": "Cafe"}).json()
    client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-03", "amount_minor": -2000, "currency_code": "USD", "payee": "Cafe"})
    suggestions = client.post("/v1/categorization/suggestions/generate", headers=headers, json={"use_ai": False}).json()
    accepted_id = next(item["suggestion_id"] for item in suggestions if item["transaction_id"] == first["transaction_id"])
    edited = client.put(f"/v1/categorization/suggestions/{accepted_id}/review", headers=headers, json={"action": "accept", "splits": [{"category_id": groceries["category_id"], "amount_minor": -1500}], "learn_rule": False})
    assert edited.status_code == 200 and edited.json()["proposed_splits"][0]["category_name"] == "Groceries"
    remaining = [item["suggestion_id"] for item in suggestions if item["suggestion_id"] != accepted_id]
    rejected = client.put("/v1/categorization/suggestions/review-batch", headers=headers, json={"suggestion_ids": remaining, "action": "reject"})
    assert rejected.status_code == 200 and all(item["status"] == "rejected" for item in rejected.json())

    client.post("/v1/household/members", headers=headers, json={"email": "viewer@example.com", "display_name": "Viewer", "password": "another-long-local-password", "role": "viewer"})
    viewer = client.post("/v1/auth/login", json={"email": "viewer@example.com", "password": "another-long-local-password"}).json()
    viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
    assert client.get("/v1/categorization/suggestions?include_resolved=true", headers=viewer_headers).status_code == 200
    assert client.post("/v1/categorization/suggestions/generate", headers=viewer_headers, json={"use_ai": False}).status_code == 403


def test_assistant_stream_uses_authorized_report_context_and_persists_sources(monkeypatch) -> None:
    from app import assistant_routes

    client, headers = owner_client()
    account = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD"}).json()
    groceries = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Groceries")
    transaction = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": account["account_id"], "transaction_date": "2026-08-13", "amount_minor": -1234, "currency_code": "USD", "payee": "Corner Shop", "splits": [{"category_id": groceries["category_id"], "amount_minor": -1234}]}).json()
    disabled_conversation = client.post("/v1/assistant/conversations", headers=headers, json={}).json()
    payload = {"conversation_id": disabled_conversation["conversation_id"], "currency_code": "USD", "ownership_scope": "household", "date_from": "2026-08-01", "date_to": "2026-08-31", "messages": [{"role": "user", "parts": [{"type": "text", "text": "How much did we spend?"}]}]}
    assert client.post("/v1/assistant/chat", headers=headers, json=payload).status_code == 409
    client.put("/v1/system/integrations", headers=headers, json={"ai_enabled": True, "ai_provider": "lm_studio", "ai_base_url": "http://127.0.0.1:1234", "ai_model": "local-test"})
    client.post("/v1/plans", headers=headers, json={"name": "Our baby steps", "currency_code": "USD", "effective_date": "2026-08-01"})
    captured = {}

    def fake_stream(values: dict, messages: list[dict], context: dict):
        captured["context"] = context
        yield "You spent $12.34 in this period [S1]."

    monkeypatch.setattr(assistant_routes, "stream_local_answer", fake_stream)
    before = client.get(f"/v1/ledger/transactions/{transaction['transaction_id']}", headers=headers).json()
    streamed = client.post("/v1/assistant/chat", headers=headers, json=payload)
    assert streamed.status_code == 200 and streamed.text == "You spent $12.34 in this period [S1]."
    assert captured["context"]["totals"]["spending"] == "$12.34 USD"
    assert "_minor" not in str(captured["context"])
    assert captured["context"]["transactions"][0]["amount"] == "-$12.34 USD"
    assert captured["context"]["active_financial_plan"]["name"] == "Our baby steps"
    detail = client.get(f"/v1/assistant/conversations/{disabled_conversation['conversation_id']}", headers=headers).json()
    assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["citations"][0]["id"] == "S1"
    assert any(item["id"] == "P1" for item in detail["messages"][1]["citations"])
    assert client.get(f"/v1/ledger/transactions/{transaction['transaction_id']}", headers=headers).json() == before


def test_assistant_conversations_are_private_to_the_signed_in_member() -> None:
    client, headers = owner_client()
    conversation = client.post("/v1/assistant/conversations", headers=headers, json={"title": "Owner questions", "currency_code": "CAD", "ownership_scope": "business"}).json()
    client.post("/v1/household/members", headers=headers, json={"email": "viewer@example.com", "display_name": "Viewer", "password": "another-long-local-password", "role": "viewer"})
    viewer = client.post("/v1/auth/login", json={"email": "viewer@example.com", "password": "another-long-local-password"}).json()
    viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
    assert client.get("/v1/assistant/conversations", headers=viewer_headers).json() == []
    assert client.get(f"/v1/assistant/conversations/{conversation['conversation_id']}", headers=viewer_headers).status_code == 404
    own = client.post("/v1/assistant/conversations", headers=viewer_headers, json={}).json()
    assert client.delete(f"/v1/assistant/conversations/{own['conversation_id']}", headers=viewer_headers).status_code == 204


def test_assistant_prompt_requests_safe_github_flavored_markdown() -> None:
    from app.assistant_service import ASSISTANT_PROMPT_VERSION, assistant_system_prompt

    prompt = assistant_system_prompt({"totals": {"spending": "$12.34 USD"}})
    assert ASSISTANT_PROMPT_VERSION == "assistant-readonly-v3-markdown"
    assert "GitHub-flavored Markdown" in prompt
    assert "Never emit raw HTML" in prompt
    assert '"spending": "$12.34 USD"' in prompt

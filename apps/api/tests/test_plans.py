from fastapi.testclient import TestClient
from test_health import Base, app, test_engine


def setup_function() -> None:
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)


def owner_client() -> tuple[TestClient, dict[str, str]]:
    client = TestClient(app)
    owner = client.post("/v1/setup", json={"household_name": "Home", "email": "owner@example.com", "display_name": "Owner", "password": "a-long-local-test-password"}).json()
    return client, {"Authorization": f"Bearer {owner['access_token']}"}


def create_baby_plan(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post("/v1/plans", headers=headers, json={"name": "Our baby steps", "currency_code": "USD", "effective_date": "2026-08-13", "debt_strategy": "smallest_balance"})
    assert response.status_code == 201
    return response.json()


def test_baby_steps_template_has_seven_editable_versioned_steps_and_one_active_plan() -> None:
    client, headers = owner_client()
    template = client.get("/v1/plans/templates", headers=headers).json()[0]
    assert [item["key"] for item in template["steps"]] == ["starter_emergency_fund", "debt_snowball", "full_emergency_fund", "retirement_15_percent", "childrens_college", "pay_off_home", "build_wealth_and_give"]
    plan = create_baby_plan(client, headers)
    assert len(plan["steps"]) == 7 and len(plan["goals"]) == 7 and plan["version_count"] == 1
    starter = next(item for item in plan["steps"] if item["step_key"] == "starter_emergency_fund")
    updated = client.patch(f"/v1/plans/{plan['plan_id']}/steps/{starter['step_id']}", headers=headers, json={"target_minor": 150000, "position": 2, "is_paused": True, "reason": "Household chose a larger starter buffer"})
    assert updated.status_code == 200 and updated.json()["version_count"] == 2
    changed = next(item for item in updated.json()["steps"] if item["step_id"] == starter["step_id"])
    assert changed["target_minor"] == 150000 and changed["position"] == 2 and changed["is_paused"] is True
    second = client.post("/v1/plans", headers=headers, json={"name": "Alternative", "currency_code": "USD", "effective_date": "2026-09-01"}).json()
    plans = client.get("/v1/plans", headers=headers).json()
    assert second["status"] == "active" and next(item for item in plans if item["plan_id"] == plan["plan_id"])["status"] == "paused"


def test_projection_protects_planner_obligations_orders_debt_and_records_overcommitment_without_mutation() -> None:
    client, headers = owner_client()
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 250000}).json()
    debt_payload = {"lender": "Bank", "account_id": None, "apr_basis_points": 1200, "minimum_payment_minor": 5000, "due_day": 20, "next_due_date": "2026-08-20", "currency_code": "USD"}
    large = client.post("/v1/obligations/debts", headers=headers, json={**debt_payload, "name": "Large card", "balance_minor": 300000}).json()
    small = client.post("/v1/obligations/debts", headers=headers, json={**debt_payload, "name": "Small card", "balance_minor": 50000}).json()
    client.post("/v1/obligations/generate?through=2026-09-01", headers=headers)
    category = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Groceries")
    payment = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-08-13", "amount_minor": -10000, "currency_code": "USD", "payee": "Small card payment", "splits": [{"category_id": category["category_id"], "amount_minor": -10000}]}).json()
    instance = next(item for item in client.get("/v1/obligations/bill-instances", headers=headers).json() if item["debt_id"] == small["debt_id"])
    assert client.post(f"/v1/obligations/bill-instances/{instance['bill_instance_id']}/payments", headers=headers, json={"transaction_id": payment["transaction_id"], "amount_minor": 10000}).status_code == 201
    plan = create_baby_plan(client, headers)
    before = client.get(f"/v1/ledger/accounts/{checking['account_id']}/balance?as_of=2026-08-13", headers=headers).json()
    projection = client.post(f"/v1/plans/{plan['plan_id']}/projection", headers=headers, json={"as_of_date": "2026-08-13", "horizon_days": 30, "cash_buffer_minor": 25000, "include_pending": True, "save_reserves": True})
    assert projection.status_code == 200
    result = projection.json()
    assert [item["debt_id"] for item in result["debt_order"]] == [small["debt_id"], large["debt_id"]]
    assert result["debt_order"][0]["balance_minor"] == 40000
    debt_step = next(item for item in result["steps"] if item["step_key"] == "debt_snowball")
    assert debt_step["actual_funded_minor"] == 10000 and "original target" in debt_step["actual_evidence"][0].lower()
    assert result["safe_to_spend_after_goals_minor"] >= 0 and result["overcommitment_minor"] > 0
    assert result["allocated_to_goals_minor"] <= result["safe_to_spend_before_goals_minor"]
    assert any("Required bills and debt minimums" in item for item in result["assumptions"])
    after = client.get(f"/v1/ledger/accounts/{checking['account_id']}/balance?as_of=2026-08-13", headers=headers).json()
    assert before == after


def test_actual_and_forecast_goal_progress_remain_separate_and_writes_require_manager() -> None:
    client, headers = owner_client()
    checking = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Checking", "account_type": "checking", "currency_code": "USD", "opening_balance_minor": 200000}).json()
    savings = client.post("/v1/ledger/accounts", headers=headers, json={"name": "Emergency savings", "account_type": "savings", "currency_code": "USD", "opening_balance_minor": 25000}).json()
    groceries = next(item for item in client.get("/v1/ledger/categories", headers=headers).json() if item["name"] == "Groceries")
    transaction = client.post("/v1/ledger/transactions", headers=headers, json={"account_id": checking["account_id"], "transaction_date": "2026-08-13", "amount_minor": -5000, "currency_code": "USD", "payee": "Goal transfer evidence", "splits": [{"category_id": groceries["category_id"], "amount_minor": -5000}]}).json()
    plan = create_baby_plan(client, headers)
    starter_step = next(item for item in plan["steps"] if item["step_key"] == "starter_emergency_fund")
    starter_goal = next(item for item in plan["goals"] if item["step_id"] == starter_step["step_id"])
    linked = client.patch(f"/v1/plans/{plan['plan_id']}/goals/{starter_goal['goal_id']}", headers=headers, json={"linked_account_id": savings["account_id"], "reason": "Use the dedicated emergency savings balance"})
    assert linked.status_code == 200
    planned = client.post(f"/v1/plans/{plan['plan_id']}/goals/{starter_goal['goal_id']}/allocations", headers=headers, json={"allocation_type": "recurring", "amount_minor": 10000, "allocation_date": "2026-08-20", "status": "planned"})
    assert planned.status_code == 201
    invalid = client.post(f"/v1/plans/{plan['plan_id']}/goals/{starter_goal['goal_id']}/allocations", headers=headers, json={"allocation_type": "transfer", "amount_minor": 5000, "allocation_date": "2026-08-13", "status": "confirmed"})
    assert invalid.status_code == 422
    confirmed = client.post(f"/v1/plans/{plan['plan_id']}/goals/{starter_goal['goal_id']}/allocations", headers=headers, json={"allocation_type": "transfer", "amount_minor": 5000, "allocation_date": "2026-08-13", "status": "confirmed", "transaction_id": transaction["transaction_id"]})
    assert confirmed.status_code == 201
    duplicate_evidence = client.post(f"/v1/plans/{plan['plan_id']}/goals/{starter_goal['goal_id']}/allocations", headers=headers, json={"allocation_type": "transfer", "amount_minor": 1, "allocation_date": "2026-08-13", "status": "confirmed", "transaction_id": transaction["transaction_id"]})
    assert duplicate_evidence.status_code == 422
    result = client.post(f"/v1/plans/{plan['plan_id']}/projection", headers=headers, json={"as_of_date": "2026-08-13", "horizon_days": 30}).json()
    starter = next(item for item in result["steps"] if item["step_key"] == "starter_emergency_fund")
    assert starter["actual_funded_minor"] == 25000 and starter["planned_allocation_minor"] == 10000
    assert "Linked account balance" in starter["actual_evidence"][0]
    client.post("/v1/household/members", headers=headers, json={"email": "viewer@example.com", "display_name": "Viewer", "password": "another-long-local-password", "role": "viewer"})
    viewer = client.post("/v1/auth/login", json={"email": "viewer@example.com", "password": "another-long-local-password"}).json()
    viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
    assert client.get("/v1/plans", headers=viewer_headers).status_code == 200
    assert client.patch(f"/v1/plans/{plan['plan_id']}", headers=viewer_headers, json={"name": "No", "reason": "Viewer cannot edit"}).status_code == 403


def test_debt_strategy_can_change_without_rewriting_prior_versions() -> None:
    client, headers = owner_client()
    plan = create_baby_plan(client, headers)
    changed = client.patch(f"/v1/plans/{plan['plan_id']}", headers=headers, json={"debt_strategy": "highest_rate", "reason": "Compare avalanche scenario"}).json()
    assert changed["debt_strategy"] == "highest_rate" and changed["version_count"] == 2
    assert [item["version_number"] for item in changed["versions"]] == [2, 1]

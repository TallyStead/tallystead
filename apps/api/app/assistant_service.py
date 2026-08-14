import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from urllib.request import Request, build_opener
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.local_ai import _NoRedirect
from app.models import BillInstance, FinancialGoal, FinancialPlan, PlanStep
from app.reporting import ReportFilters, spending_report

ASSISTANT_PROMPT_VERSION = "assistant-readonly-v3-markdown"


def format_money(amount_minor: int, currency_code: str) -> str:
    """Format ledger minor units before any financial value reaches the model."""
    symbols = {"USD": "$", "CAD": "CA$", "MXN": "MX$"}
    sign = "-" if amount_minor < 0 else ""
    return f"{sign}{symbols.get(currency_code, '')}{abs(amount_minor) / 100:,.2f} {currency_code}".strip()


def _display_breakdown(items: list[dict], currency_code: str) -> list[dict]:
    return [
        {"id": item["id"], "name": item["name"], "amount": format_money(item["amount_minor"], currency_code)}
        for item in items
    ]


def authorized_context(db: Session, household_id: UUID, currency_code: str, ownership_scope: str, date_from: date, date_to: date) -> tuple[dict, list[dict]]:
    report = spending_report(db, household_id, ReportFilters(date_from=date_from, date_to=date_to, currency_code=currency_code, ownership_scope=ownership_scope))
    citations = [{"id": "S1", "type": "report", "label": f"Spending report · {date_from} to {date_to}", "href": f"/reports?date_from={date_from}&date_to={date_to}&currency_code={currency_code}&ownership_scope={ownership_scope}"}]
    transaction_rows = []
    for index, item in enumerate(report["transactions"][:30], start=2):
        source_id = f"S{index}"
        citations.append({"id": source_id, "type": "transaction", "label": f"{item['transaction_date']} · {item['payee'] or item['account_name']}", "href": f"/transactions?transaction_id={item['transaction_id']}"})
        transaction_rows.append({
            "source_id": source_id,
            "transaction_id": item["transaction_id"],
            "transaction_date": item["transaction_date"],
            "account_name": item["account_name"],
            "payee": item["payee"],
            "merchant_name": item["merchant_name"],
            "amount": format_money(item["amount_minor"], currency_code),
            "report_amount": format_money(item["report_amount_minor"], currency_code),
            "status": item["status"],
            "activity_type": item["activity_type"],
            "classification": item["classification"],
            "categories": [
                {"name": category["name"], "amount": format_money(category["amount_minor"], currency_code)}
                for category in item["categories"]
            ],
        })
    upcoming = db.scalars(select(BillInstance).where(BillInstance.household_id == household_id, BillInstance.currency_code == currency_code, BillInstance.status.in_(("expected", "partially_paid")), BillInstance.due_date >= datetime.now(UTC).date()).order_by(BillInstance.due_date).limit(20)).all()
    plan = db.scalar(select(FinancialPlan).where(FinancialPlan.household_id == household_id, FinancialPlan.currency_code == currency_code, FinancialPlan.status == "active"))
    plan_context = None
    if plan:
        citations.append({"id": "P1", "type": "financial_plan", "label": f"Active plan · {plan.name}", "href": "/goals"})
        steps = db.scalars(select(PlanStep).where(PlanStep.plan_id == plan.id).order_by(PlanStep.position)).all()
        goals = {item.step_id: item for item in db.scalars(select(FinancialGoal).where(FinancialGoal.plan_id == plan.id)).all()}
        plan_context = {"source_id": "P1", "name": plan.name, "debt_strategy": plan.debt_strategy, "effective_date": plan.effective_date.isoformat(), "status": plan.status, "steps": [{"position": step.position, "title": step.title, "status": step.status, "paused": step.is_paused, "configured_target": format_money(goals[step.id].target_minor, currency_code) if goals.get(step.id) and goals[step.id].target_minor is not None else None} for step in steps]}
    context = {
        "rule_version": ASSISTANT_PROMPT_VERSION,
        "filters": report["filters"],
        "totals": {
            key.removesuffix("_minor"): format_money(value, currency_code)
            for key, value in report["totals"].items()
        },
        "counts": report["counts"],
        "category_breakdown": _display_breakdown(report["by_category"], currency_code),
        "merchant_breakdown": _display_breakdown(report["by_merchant"], currency_code),
        "account_breakdown": _display_breakdown(report["by_account"], currency_code),
        "transactions": transaction_rows,
        "upcoming_obligations": [{"name": item.name, "due_date": item.due_date.isoformat(), "expected_amount": format_money(item.expected_amount_minor, item.currency_code), "status": item.status} for item in upcoming],
        "active_financial_plan": plan_context,
    }
    return context, citations


def assistant_system_prompt(context: dict) -> str:
    return f"""You are Tallystead's read-only local financial assistant. Use only the authorized JSON facts below. Never invent, recalculate, or imply that you changed data. Every money value is already a display-ready currency string; quote it exactly and never describe or expose stored minor units. Cite facts inline with source IDs like [S1]. If the facts cannot answer the question, say so. You have no create, edit, delete, categorize, match, reconcile, or payment tools.

Write every answer as concise GitHub-flavored Markdown. Use short paragraphs and descriptive headings only when they improve scanning. Use bullet or numbered lists for grouped items and Markdown tables only for genuine comparisons. Use **bold** sparingly for important totals or warnings. Never emit raw HTML. Keep source IDs such as [S1] as ordinary text, not code.
AUTHORIZED FACTS:
{json.dumps(context, sort_keys=True)}"""


def stream_local_answer(values: dict, messages: list[dict], context: dict) -> Iterator[str]:
    provider = values.get("ai_provider")
    base_url = str(values.get("ai_base_url") or "").rstrip("/")
    model = values.get("ai_model") or ("llama3.2" if provider == "ollama" else "local-model")
    system = assistant_system_prompt(context)
    model_messages = [{"role": "system", "content": system}, *messages[-12:]]
    if provider == "ollama":
        endpoint = f"{base_url}/api/chat"
        body = {"model": model, "stream": True, "messages": model_messages}
    else:
        endpoint = f"{base_url}/v1/chat/completions"
        body = {"model": model, "temperature": 0, "stream": True, "messages": model_messages}
    request = Request(endpoint, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Accept": "text/event-stream"}, method="POST")
    with build_opener(_NoRedirect).open(request, timeout=120) as response:
        for raw in response:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            if provider == "ollama":
                payload = json.loads(line)
                chunk = payload.get("message", {}).get("content")
            else:
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                payload = json.loads(line.removeprefix("data:").strip())
                chunk = payload.get("choices", [{}])[0].get("delta", {}).get("content")
            if chunk:
                yield chunk


def last_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        if isinstance(message.get("content"), str):
            return message["content"].strip()
        parts = message.get("parts") if isinstance(message.get("parts"), list) else []
        return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("type") == "text").strip()
    return ""

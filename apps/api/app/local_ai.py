import base64
import io
import ipaddress
import json
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

ALLOWED_SCHEMES = {"http", "https"}
RECEIPT_SCHEMA_VERSION = "receipt-extraction-v1"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_local_ai_url(value: str | None) -> None:
    if not value:
        return
    parsed = urlsplit(value)
    host = parsed.hostname
    if parsed.scheme not in ALLOWED_SCHEMES or not host or parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="Local AI URL must be an HTTP(S) address without embedded credentials")
    try:
        address = ipaddress.ip_address(host)
        local = address.is_private or address.is_loopback or address.is_link_local
    except ValueError:
        local = "." not in host or host.endswith((".local", ".lan", ".home.arpa")) or host == "host.docker.internal"
    if not local:
        raise HTTPException(status_code=422, detail="Local AI URL must resolve to a loopback, private-network, or local hostname")


def _json_content(value: str) -> dict:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise TypeError("Local model extraction must be a JSON object")
    return result


def _minor(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: object, limit: int = 500) -> str | None:
    return str(value).strip()[:limit] if isinstance(value, str) and value.strip() else None


def normalize_extraction(result: dict) -> dict:
    merchant_source = result.get("merchant") if isinstance(result.get("merchant"), dict) else {}
    transaction_source = result.get("transaction") if isinstance(result.get("transaction"), dict) else {}
    amounts_source = result.get("amounts") if isinstance(result.get("amounts"), dict) else {}
    payment_source = result.get("payment") if isinstance(result.get("payment"), dict) else {}
    confidence_source = result.get("confidence") if isinstance(result.get("confidence"), dict) else {}
    raw_items = result.get("line_items") if isinstance(result.get("line_items"), list) else []
    line_items = []
    for raw in raw_items[:100]:
        if not isinstance(raw, dict):
            continue
        quantity = raw.get("quantity")
        line_items.append({
            "description": _text(raw.get("description"), 300),
            "quantity": quantity if isinstance(quantity, (int, float)) and not isinstance(quantity, bool) and quantity >= 0 else None,
            "unit_price_minor": _minor(raw.get("unit_price_minor")),
            "total_minor": _minor(raw.get("total_minor")),
            "sku": _text(raw.get("sku"), 100),
            "category_hint": _text(raw.get("category_hint"), 120),
        })
    currency = amounts_source.get("currency_code") or result.get("currency_code")
    currency = currency if currency in {"USD", "CAD", "MXN"} else None
    total = _minor(amounts_source.get("total_minor"))
    if total is None:
        total = _minor(result.get("amount_minor"))
    overall = _minor(confidence_source.get("overall_percent"))
    if overall is None:
        overall = _minor(result.get("confidence_percent"))
    overall = min(overall or 0, 100)
    normalized = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "document_type": _text(result.get("document_type"), 30) or "receipt",
        "merchant": {
            "name": _text(merchant_source.get("name") or result.get("payee"), 200),
            "address": _text(merchant_source.get("address"), 500),
            "phone": _text(merchant_source.get("phone"), 80),
            "tax_id": _text(merchant_source.get("tax_id"), 100),
        },
        "transaction": {
            "date": _text(transaction_source.get("date") or result.get("transaction_date"), 10),
            "time": _text(transaction_source.get("time"), 20),
            "timezone": _text(transaction_source.get("timezone"), 80),
            "receipt_number": _text(transaction_source.get("receipt_number") or result.get("receipt_number"), 120),
        },
        "amounts": {
            "subtotal_minor": _minor(amounts_source.get("subtotal_minor")),
            "discount_minor": _minor(amounts_source.get("discount_minor")),
            "tax_minor": _minor(amounts_source.get("tax_minor")),
            "tip_minor": _minor(amounts_source.get("tip_minor")),
            "total_minor": total,
            "currency_code": currency,
        },
        "payment": {
            "method": _text(payment_source.get("method"), 80),
            "card_last_four": _text(payment_source.get("card_last_four"), 4),
        },
        "line_items": line_items,
        "category_hint": _text(result.get("category_hint"), 120),
        "confidence": {
            "overall_percent": overall,
            "field_percent": confidence_source.get("field_percent") if isinstance(confidence_source.get("field_percent"), dict) else {},
        },
        "explanation": _text(result.get("explanation"), 1000),
    }
    warnings: list[str] = []
    amounts = normalized["amounts"]
    if amounts["total_minor"] is not None and amounts["subtotal_minor"] is not None:
        calculated = amounts["subtotal_minor"] - (amounts["discount_minor"] or 0) + (amounts["tax_minor"] or 0) + (amounts["tip_minor"] or 0)
        if abs(calculated - amounts["total_minor"]) > 1:
            warnings.append("Receipt subtotal, discount, tax, and tip do not add up to the extracted total.")
    else:
        warnings.append("A subtotal and total are required to verify the receipt arithmetic.")
    item_totals = [item["total_minor"] for item in line_items if item["total_minor"] is not None]
    if amounts["subtotal_minor"] is not None and len(item_totals) == len(line_items) and line_items and abs(sum(item_totals) - amounts["subtotal_minor"]) > 1:
        warnings.append("Extracted line items do not add up to the extracted subtotal.")
    if amounts["total_minor"] is None:
        warnings.append("No reliable receipt total was extracted.")
    normalized["validation"] = {
        "arithmetic_consistent": not warnings,
        "warnings": warnings,
        "review_required": True,
    }
    return normalized


def extract_document(content: bytes, content_type: str, values: dict) -> dict:
    provider = values["ai_provider"]
    base_url = values["ai_base_url"].rstrip("/")
    model = values.get("ai_model") or ("llava" if provider == "ollama" else "local-model")
    document_kind = values.get("document_kind") or "financial document"
    prompt = f"""Read this {document_kind} carefully and extract exact visible data as strict JSON.
Use integer minor units for every monetary value (for example $12.34 is 1234). Never infer digits or fields that are unreadable; return null instead. Do not use markdown.
Return exactly this structure:
{{
  "document_type": "receipt|invoice|statement|general",
  "merchant": {{"name": string|null, "address": string|null, "phone": string|null, "tax_id": string|null}},
  "transaction": {{"date": "YYYY-MM-DD"|null, "time": string|null, "timezone": string|null, "receipt_number": string|null}},
  "amounts": {{"subtotal_minor": integer|null, "discount_minor": integer|null, "tax_minor": integer|null, "tip_minor": integer|null, "total_minor": integer|null, "currency_code": "USD|CAD|MXN"|null}},
  "payment": {{"method": string|null, "card_last_four": string|null}},
  "line_items": [{{"description": string|null, "quantity": number|null, "unit_price_minor": integer|null, "total_minor": integer|null, "sku": string|null, "category_hint": string|null}}],
  "category_hint": string|null,
  "confidence": {{"overall_percent": integer, "field_percent": {{"merchant.name": integer, "transaction.date": integer, "amounts.total_minor": integer, "line_items": integer}}}},
  "explanation": "short statement of what was readable"
}}
Transcribe each line item separately. Check that line-item totals match the subtotal and that subtotal minus discounts plus tax and tip matches the total before responding."""
    source_text = None
    if content_type == "text/plain":
        source_text = content.decode("utf-8", errors="replace")[:40_000]
    elif content_type == "application/pdf":
        source_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages[:15]).strip()[:40_000]
        if not source_text:
            raise ValueError("PDF contains no embedded text; convert scanned pages to images for local vision extraction")
    encoded = base64.b64encode(content).decode() if source_text is None else None
    if provider == "ollama":
        message = {"role": "user", "content": f"{prompt}\n\nDocument text:\n{source_text}" if source_text is not None else prompt}
        if encoded:
            message["images"] = [encoded]
        body = {"model": model, "stream": False, "format": "json", "messages": [message]}
        endpoint = f"{base_url}/api/chat"
    else:
        message_content = [{"type": "text", "text": f"{prompt}\n\nDocument text:\n{source_text}" if source_text is not None else prompt}]
        if encoded:
            message_content.append({"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{encoded}"}})
        body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": message_content}]}
        endpoint = f"{base_url}/v1/chat/completions"
    request = Request(endpoint, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with build_opener(_NoRedirect).open(request, timeout=90) as response:
        payload = json.loads(response.read().decode())
    content_value = payload.get("message", {}).get("content") if provider == "ollama" else payload.get("choices", [{}])[0].get("message", {}).get("content")
    if not content_value:
        raise ValueError("Local model returned no extraction content")
    return normalize_extraction(_json_content(content_value))


def synthetic_receipt() -> bytes:
    image = Image.new("RGB", (900, 1050), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=32)
    bold = ImageFont.load_default(size=42)
    lines = [
        ("TALLYSTEAD TEST MARKET", bold),
        ("100 LOCAL LANE", font),
        ("Receipt TEST-0813", font),
        ("2026-08-13  14:30", font),
        ("", font),
        ("COFFEE          $4.00", font),
        ("BREAD           $6.00", font),
        ("", font),
        ("SUBTOTAL       $10.00", font),
        ("TAX             $0.80", font),
        ("TOTAL          $10.80", bold),
        ("", font),
        ("VISA **** 4242", font),
        ("USD", font),
    ]
    y = 55
    for value, line_font in lines:
        draw.text((55, y), value, fill="black", font=line_font)
        y += 70
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def test_local_vision_model(values: dict) -> dict:
    provider = values["ai_provider"]
    base_url = values["ai_base_url"].rstrip("/")
    model = values.get("ai_model") or ("llava" if provider == "ollama" else "local-model")
    models_path = "/api/tags" if provider == "ollama" else "/v1/models"
    with build_opener(_NoRedirect).open(f"{base_url}{models_path}", timeout=10) as response:
        model_payload = json.loads(response.read().decode())
    available = model_payload.get("models", []) if provider == "ollama" else model_payload.get("data", [])
    available_ids = {
        str(item.get("name") or item.get("model") or item.get("id"))
        for item in available
        if isinstance(item, dict) and (item.get("name") or item.get("model") or item.get("id"))
    }
    if model not in available_ids:
        return {
            "success": False,
            "provider": provider,
            "model": model,
            "duration_ms": 0,
            "checks": {"model_available": False},
            "detail": "The configured model is not currently available in the local runtime.",
        }
    started = time.monotonic()
    result = extract_document(synthetic_receipt(), "image/png", {**values, "document_kind": "receipt"})
    merchant = result.get("merchant", {})
    transaction = result.get("transaction", {})
    amounts = result.get("amounts", {})
    line_items = result.get("line_items", [])
    checks = {
        "model_available": True,
        "structured_response": result.get("schema_version") == RECEIPT_SCHEMA_VERSION,
        "merchant": "TALLYSTEAD" in str(merchant.get("name") or "").upper(),
        "date": transaction.get("date") == "2026-08-13",
        "subtotal": amounts.get("subtotal_minor") == 1000,
        "tax": amounts.get("tax_minor") == 80,
        "total": amounts.get("total_minor") == 1080,
        "currency": amounts.get("currency_code") == "USD",
        "line_items": len(line_items) >= 2,
        "arithmetic": result.get("validation", {}).get("arithmetic_consistent") is True,
    }
    success = all(checks.values())
    return {
        "success": success,
        "provider": provider,
        "model": model,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "checks": checks,
        "detail": "Vision extraction passed every synthetic receipt check." if success else "The model responded, but one or more receipt fields need attention.",
    }

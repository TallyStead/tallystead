import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.assistant_service import (
    ASSISTANT_PROMPT_VERSION,
    authorized_context,
    last_user_text,
    stream_local_answer,
)
from app.categorization import (
    CATEGORY_RULE_VERSION,
    ai_proposal,
    deterministic_proposal,
    suggestion_candidates,
)
from app.dependencies import DbSession, current_membership, current_user, require_roles
from app.ledger import transaction_snapshot
from app.models import (
    AssistantConversation,
    AssistantMessage,
    AuditEvent,
    Category,
    CategoryRule,
    CategorySuggestion,
    LedgerTransaction,
    Membership,
    Role,
    TransactionRevision,
    TransactionSplit,
    User,
    utc_now,
)
from app.settings_store import load_integrations

router = APIRouter(prefix="/v1", tags=["assistant"])
writer = require_roles(Role.OWNER, Role.MANAGER)


class SuggestionGenerateRequest(BaseModel):
    use_ai: bool = False


class ProposedSplit(BaseModel):
    category_id: UUID
    amount_minor: int


class SuggestionDecisionRequest(BaseModel):
    action: Literal["accept", "reject"]
    splits: list[ProposedSplit] | None = Field(default=None, max_length=100)
    learn_rule: bool = True


class BatchDecisionRequest(BaseModel):
    suggestion_ids: list[UUID] = Field(min_length=1, max_length=100)
    action: Literal["accept", "reject"]
    learn_rule: bool = True


class RuleUpdateRequest(BaseModel):
    category_id: UUID | None = None
    is_active: bool | None = None


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=160)
    currency_code: str = Field(default="USD", pattern="^(USD|CAD|MXN)$")
    ownership_scope: str = Field(default="household", pattern="^(household|business|all)$")


def _transaction(db: DbSession, household_id: UUID, transaction_id: UUID) -> LedgerTransaction:
    item = db.scalar(select(LedgerTransaction).where(LedgerTransaction.id == transaction_id, LedgerTransaction.household_id == household_id))
    if not item:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return item


def _suggestion(db: DbSession, household_id: UUID, suggestion_id: UUID) -> CategorySuggestion:
    item = db.scalar(select(CategorySuggestion).where(CategorySuggestion.id == suggestion_id, CategorySuggestion.household_id == household_id))
    if not item:
        raise HTTPException(status_code=404, detail="Category suggestion not found")
    return item


def suggestion_response(db: DbSession, item: CategorySuggestion) -> dict:
    transaction = _transaction(db, item.household_id, item.transaction_id)
    return {
        "suggestion_id": str(item.id), "transaction_id": str(item.transaction_id),
        "transaction_date": transaction.transaction_date.isoformat(), "payee": transaction.payee or transaction.raw_payee,
        "amount_minor": transaction.amount_minor, "currency_code": transaction.currency_code,
        "provider": item.provider, "model_version": item.model_version, "rule_version": item.rule_version,
        "confidence_percent": item.confidence_percent, "proposed_splits": json.loads(item.proposed_splits_json),
        "evidence": json.loads(item.evidence_json), "status": item.status,
        "created_at": item.created_at.isoformat(), "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
    }


@router.get("/categorization/suggestions")
def list_suggestions(db: DbSession, membership: Annotated[Membership, Depends(current_membership)], include_resolved: bool = False) -> list[dict]:
    query = select(CategorySuggestion).where(CategorySuggestion.household_id == membership.household_id)
    if not include_resolved:
        query = query.where(CategorySuggestion.status == "pending")
    return [suggestion_response(db, item) for item in db.scalars(query.order_by(CategorySuggestion.created_at.desc())).all()]


@router.post("/categorization/suggestions/generate", status_code=201)
def generate_suggestions(request: SuggestionGenerateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> list[dict]:
    values = load_integrations(db)
    if request.use_ai and not (values.get("ai_enabled") and values.get("ai_provider") and values.get("ai_base_url")):
        raise HTTPException(status_code=409, detail="Local AI must be enabled and configured before using AI suggestions")
    created = []
    for transaction in suggestion_candidates(db, membership.household_id):
        proposal = deterministic_proposal(db, membership.household_id, transaction)
        if proposal is None and request.use_ai:
            try:
                proposal = ai_proposal(db, membership.household_id, transaction, values)
            except (OSError, TimeoutError, ValueError):
                proposal = None
        if proposal is None:
            continue
        item = CategorySuggestion(household_id=membership.household_id, transaction_id=transaction.id, provider=proposal["provider"], model_version=proposal["model_version"], rule_version=CATEGORY_RULE_VERSION, confidence_percent=proposal["confidence_percent"], proposed_splits_json=json.dumps(proposal["splits"], sort_keys=True), evidence_json=json.dumps(proposal["evidence"], sort_keys=True))
        db.add(item)
        db.flush()
        created.append(suggestion_response(db, item))
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="categorization.suggestions_generated", resource_type="category_suggestion", detail=f"count={len(created)}; ai={request.use_ai}"))
    db.commit()
    return created


def _learn_rule(db: DbSession, membership: Membership, actor: User, suggestion: CategorySuggestion, transaction: LedgerTransaction, category_id: UUID) -> None:
    direction = "in" if transaction.amount_minor > 0 else "out"
    if transaction.merchant_id:
        match_type, match_value = "merchant", str(transaction.merchant_id)
    else:
        match_value = (transaction.payee or transaction.raw_payee or "").strip().casefold()
        if not match_value:
            return
        match_type = "payee"
    rule = db.scalar(select(CategoryRule).where(CategoryRule.household_id == membership.household_id, CategoryRule.match_type == match_type, CategoryRule.match_value == match_value, CategoryRule.direction == direction))
    if rule:
        rule.category_id = category_id
        rule.direction = direction
        rule.is_active = True
        rule.source_suggestion_id = suggestion.id
    else:
        db.add(CategoryRule(household_id=membership.household_id, category_id=category_id, match_type=match_type, match_value=match_value, direction=direction, source_suggestion_id=suggestion.id, created_by_user_id=actor.id))


def decide_suggestion(db: DbSession, membership: Membership, actor: User, suggestion: CategorySuggestion, request: SuggestionDecisionRequest, commit: bool = True) -> dict:
    if suggestion.status != "pending":
        raise HTTPException(status_code=409, detail="Category suggestion was already reviewed")
    transaction = _transaction(db, membership.household_id, suggestion.transaction_id)
    if request.action == "accept":
        if transaction.reconciled_at:
            raise HTTPException(status_code=409, detail="Reconciled transactions must be unreconciled before categorization changes")
        raw_splits = [split.model_dump() for split in request.splits] if request.splits is not None else json.loads(suggestion.proposed_splits_json)
        categories = []
        for split in raw_splits:
            category = db.scalar(select(Category).where(Category.id == UUID(str(split["category_id"])), Category.household_id == membership.household_id, Category.is_archived.is_(False)))
            if not category:
                raise HTTPException(status_code=422, detail="Suggested category is unavailable")
            if category.category_type != ("income" if transaction.amount_minor > 0 else "expense"):
                raise HTTPException(status_code=422, detail="Category direction does not match transaction direction")
            categories.append(category)
        if sum(int(split["amount_minor"]) for split in raw_splits) != transaction.amount_minor:
            raise HTTPException(status_code=422, detail="Split amounts must equal the transaction amount")
        db.add(TransactionRevision(household_id=membership.household_id, transaction_id=transaction.id, actor_user_id=actor.id, reason="Accepted reviewed category suggestion", before_snapshot=transaction_snapshot(db, transaction)))
        db.execute(delete(TransactionSplit).where(TransactionSplit.transaction_id == transaction.id))
        db.add_all([TransactionSplit(transaction_id=transaction.id, category_id=category.id, amount_minor=int(split["amount_minor"])) for split, category in zip(raw_splits, categories, strict=True)])
        suggestion.proposed_splits_json = json.dumps([{**split, "category_name": category.name, "category_id": str(category.id)} for split, category in zip(raw_splits, categories, strict=True)], sort_keys=True)
        if request.learn_rule and len(categories) == 1:
            _learn_rule(db, membership, actor, suggestion, transaction, categories[0].id)
        suggestion.status = "accepted"
    else:
        suggestion.status = "rejected"
    suggestion.reviewed_by_user_id = actor.id
    suggestion.reviewed_at = utc_now()
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action=f"categorization.suggestion_{suggestion.status}", resource_type="category_suggestion", resource_id=str(suggestion.id), detail=f"transaction={transaction.id}"))
    db.flush()
    response = suggestion_response(db, suggestion)
    if commit:
        db.commit()
    return response


@router.put("/categorization/suggestions/{suggestion_id}/review")
def review_suggestion(suggestion_id: UUID, request: SuggestionDecisionRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    return decide_suggestion(db, membership, actor, _suggestion(db, membership.household_id, suggestion_id), request)


@router.put("/categorization/suggestions/review-batch")
def review_suggestions_batch(request: BatchDecisionRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> list[dict]:
    responses = []
    for suggestion_id in request.suggestion_ids:
        responses.append(decide_suggestion(db, membership, actor, _suggestion(db, membership.household_id, suggestion_id), SuggestionDecisionRequest(action=request.action, learn_rule=request.learn_rule), commit=False))
    db.commit()
    return responses


@router.get("/categorization/rules")
def list_category_rules(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> list[dict]:
    rows = db.execute(select(CategoryRule, Category).join(Category, Category.id == CategoryRule.category_id).where(CategoryRule.household_id == membership.household_id).order_by(CategoryRule.match_type, CategoryRule.match_value)).all()
    return [{"rule_id": str(rule.id), "match_type": rule.match_type, "match_value": rule.match_value, "direction": rule.direction, "category_id": str(category.id), "category_name": category.name, "is_active": rule.is_active} for rule, category in rows]


@router.patch("/categorization/rules/{rule_id}")
def update_category_rule(rule_id: UUID, request: RuleUpdateRequest, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> dict:
    rule = db.scalar(select(CategoryRule).where(CategoryRule.id == rule_id, CategoryRule.household_id == membership.household_id))
    if not rule:
        raise HTTPException(status_code=404, detail="Category rule not found")
    if request.category_id:
        category = db.scalar(select(Category).where(Category.id == request.category_id, Category.household_id == membership.household_id, Category.is_archived.is_(False)))
        if not category:
            raise HTTPException(status_code=422, detail="Category is unavailable")
        rule.category_id = category.id
    if request.is_active is not None:
        rule.is_active = request.is_active
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="categorization.rule_updated", resource_type="category_rule", resource_id=str(rule.id)))
    db.commit()
    return next(item for item in list_category_rules(db, membership) if item["rule_id"] == str(rule.id))


@router.delete("/categorization/rules/{rule_id}", status_code=204)
def delete_category_rule(rule_id: UUID, db: DbSession, actor: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(writer)]) -> Response:
    rule = db.scalar(select(CategoryRule).where(CategoryRule.id == rule_id, CategoryRule.household_id == membership.household_id))
    if not rule:
        raise HTTPException(status_code=404, detail="Category rule not found")
    db.delete(rule)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="categorization.rule_deleted", resource_type="category_rule", resource_id=str(rule.id)))
    db.commit()
    return Response(status_code=204)


def conversation_response(db: DbSession, item: AssistantConversation, include_messages: bool = False) -> dict:
    result = {"conversation_id": str(item.id), "title": item.title, "currency_code": item.currency_code, "ownership_scope": item.ownership_scope, "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()}
    if include_messages:
        messages = db.scalars(select(AssistantMessage).where(AssistantMessage.conversation_id == item.id).order_by(AssistantMessage.created_at)).all()
        result["messages"] = [{"message_id": str(message.id), "role": message.role, "content": message.content, "citations": json.loads(message.citations_json), "provider": message.provider, "model_version": message.model_version, "created_at": message.created_at.isoformat()} for message in messages]
    return result


def owned_conversation(db: DbSession, membership: Membership, user: User, conversation_id: UUID) -> AssistantConversation:
    item = db.scalar(select(AssistantConversation).where(AssistantConversation.id == conversation_id, AssistantConversation.household_id == membership.household_id, AssistantConversation.user_id == user.id))
    if not item:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return item


@router.get("/assistant/conversations")
def list_conversations(db: DbSession, user: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(current_membership)]) -> list[dict]:
    items = db.scalars(select(AssistantConversation).where(AssistantConversation.household_id == membership.household_id, AssistantConversation.user_id == user.id).order_by(AssistantConversation.updated_at.desc())).all()
    return [conversation_response(db, item) for item in items]


@router.post("/assistant/conversations", status_code=201)
def create_conversation(request: ConversationCreateRequest, db: DbSession, user: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(current_membership)]) -> dict:
    item = AssistantConversation(household_id=membership.household_id, user_id=user.id, title=request.title.strip(), currency_code=request.currency_code, ownership_scope=request.ownership_scope)
    db.add(item)
    db.commit()
    return conversation_response(db, item, True)


@router.get("/assistant/conversations/{conversation_id}")
def get_conversation(conversation_id: UUID, db: DbSession, user: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(current_membership)]) -> dict:
    return conversation_response(db, owned_conversation(db, membership, user, conversation_id), True)


@router.delete("/assistant/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, db: DbSession, user: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(current_membership)]) -> Response:
    db.delete(owned_conversation(db, membership, user, conversation_id))
    db.commit()
    return Response(status_code=204)


@router.post("/assistant/chat")
def assistant_chat(payload: Annotated[dict, Body()], db: DbSession, user: Annotated[User, Depends(current_user)], membership: Annotated[Membership, Depends(current_membership)]) -> StreamingResponse:
    values = load_integrations(db)
    if not (values.get("ai_enabled") and values.get("ai_provider") and values.get("ai_base_url")):
        raise HTTPException(status_code=409, detail="The local assistant is disabled. Configure and enable Local AI first.")
    try:
        conversation_id = UUID(str(payload.get("conversation_id")))
        currency_code = str(payload.get("currency_code") or "USD")
        ownership_scope = str(payload.get("ownership_scope") or "household")
        date_from = date.fromisoformat(str(payload.get("date_from")))
        date_to = date.fromisoformat(str(payload.get("date_to")))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Conversation and report dates are required") from None
    if currency_code not in {"USD", "CAD", "MXN"} or ownership_scope not in {"household", "business", "all"} or date_to < date_from:
        raise HTTPException(status_code=422, detail="Assistant report filters are invalid")
    conversation = owned_conversation(db, membership, user, conversation_id)
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    prompt = last_user_text(messages)
    if not prompt or len(prompt) > 4000:
        raise HTTPException(status_code=422, detail="A message of 1 to 4000 characters is required")
    context, citations = authorized_context(db, membership.household_id, currency_code, ownership_scope, date_from, date_to)
    trigger = str(payload.get("trigger") or "submit-message")
    if trigger == "regenerate-message":
        latest = db.scalar(select(AssistantMessage).where(AssistantMessage.conversation_id == conversation.id, AssistantMessage.role == "assistant").order_by(AssistantMessage.created_at.desc()).limit(1))
        if latest:
            db.delete(latest)
    else:
        db.add(AssistantMessage(conversation_id=conversation.id, role="user", content=prompt))
    if conversation.title == "New conversation":
        conversation.title = prompt[:80]
    conversation.currency_code = currency_code
    conversation.ownership_scope = ownership_scope
    conversation.updated_at = utc_now()
    db.commit()

    def generate() -> Iterator[str]:
        chunks = []
        try:
            model_messages = [{"role": "user" if message.get("role") == "user" else "assistant", "content": last_user_text([message]) if message.get("role") == "user" else "".join(str(part.get("text") or "") for part in message.get("parts", []) if isinstance(part, dict) and part.get("type") == "text")} for message in messages]
            for chunk in stream_local_answer(values, model_messages, context):
                chunks.append(chunk)
                yield chunk
        except (OSError, TimeoutError, ValueError) as exc:
            message = f"\n\nThe local model stream stopped: {type(exc).__name__}."
            chunks.append(message)
            yield message
        finally:
            answer = "".join(chunks).strip()
            if answer:
                db.add(AssistantMessage(conversation_id=conversation.id, role="assistant", content=answer, citations_json=json.dumps(citations, sort_keys=True), provider=str(values.get("ai_provider")), model_version=str(values.get("ai_model") or "local-model")))
                db.add(AuditEvent(household_id=membership.household_id, actor_user_id=user.id, action="assistant.answer_generated", resource_type="assistant_conversation", resource_id=str(conversation.id), detail=f"{ASSISTANT_PROMPT_VERSION}; sources={len(citations)}"))
                conversation.updated_at = datetime.now(UTC)
                db.commit()

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no", "X-Tallystead-Assistant": ASSISTANT_PROMPT_VERSION})

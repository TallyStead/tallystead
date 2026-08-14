import base64
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.data_management import (
    ARCHIVE_FORMAT,
    build_household_archive,
    delete_household_data,
    household_data_summary,
    remove_objects,
    restore_household_archive,
)
from app.demo_household import (
    DEMO_SEED,
    create_demo_household,
    has_household_activity,
    reset_demo_household,
)
from app.dependencies import DbSession, current_membership, current_user, require_roles
from app.models import AuditEvent, Household, HouseholdDataState, Membership, Role, User, utc_now

router = APIRouter(prefix="/v1/data", tags=["data management"])
owner = require_roles(Role.OWNER)


class ConfirmationRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=200)


class ArchiveImportRequest(ConfirmationRequest):
    archive_base64: str = Field(min_length=1)


class DemoRequest(ConfirmationRequest):
    reference_date: date
    volume: Literal["smoke", "realistic"] = "realistic"


def household_for(db: DbSession, membership: Membership) -> Household:
    household = db.get(Household, membership.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return household


@router.get("/demo/status")
def demo_status(db: DbSession, membership: Annotated[Membership, Depends(current_membership)]) -> dict:
    state = db.get(HouseholdDataState, membership.household_id)
    return {
        "is_demo": bool(state and state.mode == "demo"),
        "seed": state.demo_seed if state and state.mode == "demo" else None,
        "volume": state.demo_volume if state and state.mode == "demo" else None,
        "reference_date": state.demo_reference_date.isoformat() if state and state.mode == "demo" and state.demo_reference_date else None,
    }


@router.get("/status")
def data_status(db: DbSession, membership: Annotated[Membership, Depends(owner)]) -> dict:
    household = household_for(db, membership)
    summary = household_data_summary(db, membership.household_id)
    summary.update(
        {
            "household_name": household.name,
            "export_format": ARCHIVE_FORMAT,
            "demo_seed": summary.get("demo_seed") or DEMO_SEED,
            "can_create_demo": not has_household_activity(db, membership.household_id),
            "delete_confirmation": f"DELETE {household.name}",
        }
    )
    return summary


@router.post("/export")
def export_all_data(
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner)],
) -> Response:
    household = household_for(db, membership)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="data.archive_exported", resource_type="household", resource_id=str(membership.household_id), detail="Complete household records and document objects; authentication and integration secrets excluded"))
    db.commit()
    try:
        content = build_household_archive(db, membership.household_id, household.name)
    except (KeyError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail="A stored document could not be included in the archive") from error
    filename = f"tallystead-household-{utc_now().date().isoformat()}.tallystead.zip"
    return Response(content=content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/import")
def import_all_data(
    request: ArchiveImportRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner)],
) -> dict:
    if request.confirmation != "RESTORE MY DATA":
        raise HTTPException(status_code=422, detail='Type "RESTORE MY DATA" to replace current household data')
    try:
        content = base64.b64decode(request.archive_base64, validate=True)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Archive upload is not valid base64 data") from error
    staged_keys: list[str] = []
    try:
        manifest, old_keys, staged_keys = restore_household_archive(db, membership.household_id, content)
        db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="data.archive_restored", resource_type="household", resource_id=str(membership.household_id), detail=f"format:{manifest['format']};exported_at:{manifest.get('exported_at')}"))
        db.commit()
    except (TypeError, ValueError) as error:
        db.rollback()
        remove_objects(staged_keys)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        db.rollback()
        remove_objects(staged_keys)
        raise
    remove_objects(old_keys)
    return {"status": "restored", "manifest": manifest, "summary": household_data_summary(db, membership.household_id)}


@router.delete("/household")
def delete_all_data(
    request: ConfirmationRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner)],
) -> dict:
    household = household_for(db, membership)
    expected = f"DELETE {household.name}"
    if request.confirmation != expected:
        raise HTTPException(status_code=422, detail=f'Type "{expected}" to delete household financial data')
    object_keys = delete_household_data(db, membership.household_id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="data.household_records_deleted", resource_type="household", resource_id=str(membership.household_id), detail="Financial records and documents deleted; members, authentication, server settings, and external backup archives retained"))
    db.commit()
    remove_objects(object_keys)
    return {"status": "deleted", "retained": ["household members", "passwords and passkeys", "server and integration settings", "backup archives stored outside the application"]}


@router.post("/demo")
def create_demo(
    request: DemoRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner)],
) -> dict:
    if request.confirmation != "CREATE DEMO":
        raise HTTPException(status_code=422, detail='Type "CREATE DEMO" to create fictional data')
    try:
        result = create_demo_household(db, membership.household_id, actor.id, request.reference_date, request.volume)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="demo.created", resource_type="household", resource_id=str(membership.household_id), detail=f"seed:{DEMO_SEED};reference_date:{request.reference_date}"))
    db.commit()
    return {"status": "created", **result, "summary": household_data_summary(db, membership.household_id)}


@router.post("/demo/reset")
def reset_demo(
    request: DemoRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner)],
) -> dict:
    if request.confirmation != "RESET DEMO":
        raise HTTPException(status_code=422, detail='Type "RESET DEMO" to recreate the fictional scenario')
    try:
        result, old_keys = reset_demo_household(db, membership.household_id, actor.id, request.reference_date, request.volume)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="demo.reset", resource_type="household", resource_id=str(membership.household_id), detail=f"seed:{DEMO_SEED};reference_date:{request.reference_date}"))
    db.commit()
    remove_objects(old_keys)
    return {"status": "reset", **result, "summary": household_data_summary(db, membership.household_id)}


@router.delete("/demo")
def remove_demo(
    request: ConfirmationRequest,
    db: DbSession,
    actor: Annotated[User, Depends(current_user)],
    membership: Annotated[Membership, Depends(owner)],
) -> dict:
    state = db.get(HouseholdDataState, membership.household_id)
    if not state or state.mode != "demo":
        raise HTTPException(status_code=409, detail="This household is not marked as a fictional demo")
    if request.confirmation != "REMOVE DEMO":
        raise HTTPException(status_code=422, detail='Type "REMOVE DEMO" to permanently remove fictional data')
    object_keys = delete_household_data(db, membership.household_id)
    db.add(AuditEvent(household_id=membership.household_id, actor_user_id=actor.id, action="demo.removed", resource_type="household", resource_id=str(membership.household_id), detail=f"seed:{DEMO_SEED}"))
    db.commit()
    remove_objects(object_keys)
    return {"status": "removed", "summary": household_data_summary(db, membership.household_id)}

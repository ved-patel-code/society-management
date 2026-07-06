"""Onboarding routes (docs/modules/onboarding.md §6), prefix ``/onboarding``.

Society comes from the JWT (``TenantContext.active_society_id``) — never a path id.
Every route gates on ``require_module('onboarding')`` + a permission (writes:
``onboarding.manage``, reads: ``onboarding.read``). The router stays thin: resolve
tenant → call ``OnboardingService`` → shape the response (docs/03 §2).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.common.errors import ValidationError
from app.core.db import get_session
from app.core.deps import AuthContext, TenantContext, get_tenant_context, require_module, require_permission
from app.modules.onboarding.schemas import (
    BuildingAddFloorsRequest,
    BuildingMapRequest,
    BuildingOut,
    BuildingRenameRequest,
    BuildingsCreateRequest,
    DraftSaveRequest,
    HouseNumberOverride,
    HouseOut,
    OnboardingStateOut,
    RowsCreateRequest,
    TypeSelectRequest,
)
from app.modules.onboarding.service import OnboardingService

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# Reusable gate dependencies (module enabled + the right permission).
_MANAGE = [Depends(require_module("onboarding")), Depends(require_permission("onboarding.manage"))]
_READ = [Depends(require_module("onboarding")), Depends(require_permission("onboarding.read"))]


def _society_id(tenant: TenantContext) -> int:
    """Resolve the active society id or fail — feature routes are society-scoped."""
    if tenant.society_id is None:
        raise ValidationError("No active society for this request.")
    return tenant.society_id


# --- state / resume --------------------------------------------------------

@router.get("/state", response_model=OnboardingStateOut, dependencies=_READ)
def get_state(
    auth: AuthContext = Depends(require_permission("onboarding.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> OnboardingStateOut:
    """Resume payload: type, buildings/rows so far, current step, draft, next action."""
    state = OnboardingService(session).get_state(_society_id(tenant))
    return OnboardingStateOut.model_validate(state)


@router.put("/draft", dependencies=_MANAGE)
def save_draft(
    body: DraftSaveRequest,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> dict:
    """Persist the in-progress building's typed inputs for exact resume."""
    OnboardingService(session).save_draft(
        _society_id(tenant), body.draft, actor_user_id=auth.user_id
    )
    return {"status": "saved"}


# --- type selection --------------------------------------------------------

@router.post("/type", dependencies=_MANAGE)
def select_type(
    body: TypeSelectRequest,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> dict:
    """Step 1 — set the society type (building | individual_houses)."""
    society = OnboardingService(session).select_type(
        _society_id(tenant), body.type, actor_user_id=auth.user_id
    )
    return {"society_id": society.id, "type": society.type, "status": society.status}


# --- building flow ---------------------------------------------------------

@router.post("/buildings", response_model=list[BuildingOut], dependencies=_MANAGE)
def create_buildings(
    body: BuildingsCreateRequest,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> list[BuildingOut]:
    """Create buildings from admin-typed names (also the later 'add building' path)."""
    buildings = OnboardingService(session).create_buildings(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )
    return [BuildingOut.model_validate(b) for b in buildings]


@router.post(
    "/buildings/{building_id}/map",
    response_model=list[HouseOut],
    dependencies=_MANAGE,
)
def map_building(
    building_id: int,
    body: BuildingMapRequest,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> list[HouseOut]:
    """Floors + ground toggle + per-floor houses + numbering config → generate houses."""
    houses = OnboardingService(session).map_building(
        _society_id(tenant), building_id, body, actor_user_id=auth.user_id
    )
    return [HouseOut.model_validate(h) for h in houses]


@router.post(
    "/buildings/{building_id}/floors",
    response_model=list[HouseOut],
    dependencies=_MANAGE,
)
def add_floors(
    building_id: int,
    body: BuildingAddFloorsRequest,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> list[HouseOut]:
    """Add floors to an already-mapped building, reusing its stored numbering config."""
    houses = OnboardingService(session).add_floors(
        _society_id(tenant), building_id, body, actor_user_id=auth.user_id
    )
    return [HouseOut.model_validate(h) for h in houses]


@router.get(
    "/buildings/{building_id}/preview",
    response_model=list[HouseOut],
    dependencies=_READ,
)
def preview_building(
    building_id: int,
    auth: AuthContext = Depends(require_permission("onboarding.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> list[HouseOut]:
    """The generated numbers for a building (review before completing)."""
    houses = OnboardingService(session).preview_building(
        _society_id(tenant), building_id
    )
    return [HouseOut.model_validate(h) for h in houses]


@router.patch("/buildings/{building_id}", response_model=BuildingOut, dependencies=_MANAGE)
def rename_building(
    building_id: int,
    body: BuildingRenameRequest,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> BuildingOut:
    """Rename a building (later edit)."""
    building = OnboardingService(session).rename_building(
        _society_id(tenant), building_id, body.name, actor_user_id=auth.user_id
    )
    return BuildingOut.model_validate(building)


# --- individual flow -------------------------------------------------------

@router.post("/rows", response_model=list[HouseOut], dependencies=_MANAGE)
def create_rows(
    body: RowsCreateRequest,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> list[HouseOut]:
    """Rows + houses/row + numbering config → generate individual houses."""
    houses = OnboardingService(session).create_rows(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )
    return [HouseOut.model_validate(h) for h in houses]


# --- overrides -------------------------------------------------------------

@router.patch("/houses/{house_id}", response_model=HouseOut, dependencies=_MANAGE)
def override_house_number(
    house_id: int,
    body: HouseNumberOverride,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> HouseOut:
    """Override a generated house number (wizard or later; clash → rejected)."""
    house = OnboardingService(session).override_house_number(
        _society_id(tenant), house_id, body.number, actor_user_id=auth.user_id
    )
    return HouseOut.model_validate(house)


# --- completion ------------------------------------------------------------

@router.post("/complete", dependencies=_MANAGE)
def complete(
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> dict:
    """Validate + flip society.status onboarding → active, unlocking the app."""
    society = OnboardingService(session).complete(
        _society_id(tenant), actor_user_id=auth.user_id
    )
    return {"society_id": society.id, "status": society.status}


# --- guarded deletes (later edits) -----------------------------------------

@router.delete(
    "/buildings/{building_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_MANAGE,
)
def delete_building(
    building_id: int,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> None:
    """Delete a building — blocked if any house is not 'empty' (deferred dues guard)."""
    OnboardingService(session).delete_building(
        _society_id(tenant), building_id, actor_user_id=auth.user_id
    )


@router.delete(
    "/floors/{floor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_MANAGE,
)
def delete_floor(
    floor_id: int,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> None:
    """Delete a floor — blocked if any house is not 'empty'."""
    OnboardingService(session).delete_floor(
        _society_id(tenant), floor_id, actor_user_id=auth.user_id
    )


@router.delete(
    "/houses/{house_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_MANAGE,
)
def delete_house(
    house_id: int,
    auth: AuthContext = Depends(require_permission("onboarding.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> None:
    """Delete a house — blocked if not 'empty'."""
    OnboardingService(session).delete_house(
        _society_id(tenant), house_id, actor_user_id=auth.user_id
    )

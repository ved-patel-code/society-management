"""House & Occupancy routes (docs/modules/house-occupancy.md §6), prefix ``/houses``.

Society comes from the JWT (``TenantContext.society_id``) — never a path id. Every
route gates on ``require_module('houses')`` + a permission (read: ``houses.read``;
status change: ``houses.update_status``; occupancy edit: ``houses.manage_occupancy``).
The router stays thin: resolve tenant → call ``HouseService`` → shape the response.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.common.errors import ValidationError
from app.common.pagination import Page, PageParams
from app.core.db import get_session
from app.core.deps import (
    AuthContext,
    TenantContext,
    get_tenant_context,
    require_module,
    require_permission,
)
from app.modules.houses.schemas import (
    HouseDetailOut,
    HouseOut,
    OccupancyEditRequest,
    StatusChangeRequest,
    StatusHistoryOut,
)
from app.modules.houses.service import HouseService

router = APIRouter(prefix="/houses", tags=["houses"])

# Reusable gate dependencies (module enabled + the right permission).
_READ = [Depends(require_module("houses")), Depends(require_permission("houses.read"))]
_UPDATE_STATUS = [
    Depends(require_module("houses")),
    Depends(require_permission("houses.update_status")),
]
_MANAGE_OCC = [
    Depends(require_module("houses")),
    Depends(require_permission("houses.manage_occupancy")),
]


def _society_id(tenant: TenantContext) -> int:
    """Resolve the active society id or fail — feature routes are society-scoped."""
    if tenant.society_id is None:
        raise ValidationError("No active society for this request.")
    return tenant.society_id


# --- list / detail / history ----------------------------------------------

@router.get("", response_model=Page[HouseOut], dependencies=_READ)
def list_houses(
    page: PageParams = Depends(),
    status: str | None = Query(default=None),
    building_id: int | None = Query(default=None),
    floor_id: int | None = Query(default=None),
    number: str | None = Query(default=None),
    auth: AuthContext = Depends(require_permission("houses.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> Page[HouseOut]:
    """List houses with filters (status, building, floor, number), paginated."""
    items, total = HouseService(session).list_houses(
        _society_id(tenant),
        status=status,
        building_id=building_id,
        floor_id=floor_id,
        number=number,
        offset=page.offset,
        limit=page.limit,
    )
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.get("/{house_id}", response_model=HouseDetailOut, dependencies=_READ)
def get_house(
    house_id: int,
    auth: AuthContext = Depends(require_permission("houses.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> HouseDetailOut:
    """House detail + current owner/tenant occupancy."""
    return HouseService(session).get_house_detail(_society_id(tenant), house_id)


@router.get(
    "/{house_id}/history",
    response_model=list[StatusHistoryOut],
    dependencies=_READ,
)
def get_history(
    house_id: int,
    auth: AuthContext = Depends(require_permission("houses.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> list[StatusHistoryOut]:
    """A house's status-change history (newest first)."""
    return HouseService(session).get_history(_society_id(tenant), house_id)


# --- status change / occupancy edit ---------------------------------------

@router.post(
    "/{house_id}/status",
    response_model=HouseDetailOut,
    dependencies=_UPDATE_STATUS,
)
def change_status(
    house_id: int,
    body: StatusChangeRequest,
    auth: AuthContext = Depends(require_permission("houses.update_status")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> HouseDetailOut:
    """Change a house's status with the target's occupancy payload."""
    return HouseService(session).change_status(
        _society_id(tenant), house_id, body, actor_user_id=auth.user_id
    )


@router.patch(
    "/{house_id}/occupancy/{party}",
    response_model=HouseDetailOut,
    dependencies=_MANAGE_OCC,
)
def edit_occupancy(
    house_id: int,
    party: str,
    body: OccupancyEditRequest,
    auth: AuthContext = Depends(require_permission("houses.manage_occupancy")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> HouseDetailOut:
    """Edit owner/tenant occupancy details (email change → owner replacement)."""
    return HouseService(session).edit_occupancy(
        _society_id(tenant), house_id, party, body, actor_user_id=auth.user_id
    )


@router.post(
    "/{house_id}/occupancy/{party}/id-proof",
    response_model=HouseDetailOut,
    dependencies=[
        Depends(require_module("houses")),
        Depends(require_module("vault")),
        Depends(require_permission("houses.manage_occupancy")),
    ],
)
async def upload_id_proof(
    house_id: int,
    party: str,
    file: UploadFile = File(...),
    id_proof_type: str | None = Form(default=None),
    auth: AuthContext = Depends(require_permission("houses.manage_occupancy")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> HouseDetailOut:
    """Upload an ID-proof image for the current owner/tenant into the vault.

    Requires BOTH ``houses`` and ``vault`` enabled — ID-proof storage lives in
    the vault, so a society without it gets the standard module_disabled 403.
    """
    data = await file.read()
    return HouseService(session).set_id_proof(
        _society_id(tenant),
        house_id,
        party,
        filename=file.filename,
        content_type=file.content_type,
        data=data,
        id_proof_type=id_proof_type,
        actor_user_id=auth.user_id,
    )

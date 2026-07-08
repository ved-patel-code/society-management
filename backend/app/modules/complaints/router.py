"""Complaints routes (docs/modules/complaints.md §6), prefix ``/complaints``.

Society comes from the JWT (``TenantContext.society_id``) — never a path id. Both
audiences share these endpoints; the service scopes data and gates actions by
permission (docs §2). Every route gates on ``require_module('complaints')`` + a
permission; the image + resolve routes ALSO gate ``require_module('vault')``
(they file bytes into the Vault). The read-vs-read_all visibility split lives in
the handlers (finance pattern): the route gates on ``complaints.read``; the
service is told whether the caller also holds ``complaints.read_all`` (or is a
super-admin) and scopes accordingly.

The router stays thin: resolve tenant → call ``ComplaintsService`` → shape
response. Image/resolve routes are multipart (``UploadFile``/``Form``).
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.common.errors import ValidationError
from app.common.pagination import PageParams
from app.core.db import get_session
from app.core.deps import (
    AuthContext,
    TenantContext,
    get_tenant_context,
    require_module,
    require_permission,
)
from app.modules.complaints.schemas import (
    CategoryCreateRequest,
    CategoryOut,
    CategoryUpdateRequest,
    ComplaintCreateRequest,
    ComplaintDetailOut,
    ComplaintImageOut,
    ComplaintListOut,
    ComplaintsConfigOut,
    ComplaintUpdateRequest,
    ConfigUpdateRequest,
    StatusChangeRequest,
)
from app.modules.complaints.service import ComplaintsService
from app.modules.complaints.spec import (
    PERM_CONFIGURE,
    PERM_CREATE,
    PERM_MANAGE_CATEGORIES,
    PERM_READ,
    PERM_READ_ALL,
    PERM_UPDATE_STATUS,
)

router = APIRouter(prefix="/complaints", tags=["complaints"])

_MODULE = require_module("complaints")
_VAULT = require_module("vault")


def _gate(perm: str) -> list:
    """Both gates for a permission: module enabled + the permission held."""
    return [Depends(_MODULE), Depends(require_permission(perm))]


def _gate_vault(perm: str) -> list:
    """Complaints + Vault modules enabled + the permission held (image routes)."""
    return [Depends(_MODULE), Depends(_VAULT), Depends(require_permission(perm))]


_CREATE = _gate(PERM_CREATE)
_READ = _gate(PERM_READ)
_UPDATE_STATUS = _gate(PERM_UPDATE_STATUS)
_MANAGE_CATEGORIES = _gate(PERM_MANAGE_CATEGORIES)
_CONFIGURE = _gate(PERM_CONFIGURE)
_CREATE_VAULT = _gate_vault(PERM_CREATE)
_UPDATE_STATUS_VAULT = _gate_vault(PERM_UPDATE_STATUS)


def _society_id(tenant: TenantContext) -> int:
    if tenant.society_id is None:
        raise ValidationError("No active society for this request.")
    return tenant.society_id


def _can_read_all(auth: AuthContext) -> bool:
    """Data-driven cross-house scope (docs §2/§4): super-admin or read_all.

    Never a hardcoded role list — a future role gains the society-wide view purely
    by including ``complaints.read_all`` in its grants (docs/02 §4).
    """
    return auth.is_super_admin or auth.has_permission(PERM_READ_ALL)


# ============================ Categories =====================================


@router.get(
    "/categories", response_model=list[CategoryOut], dependencies=_READ
)
def list_categories(
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> list[CategoryOut]:
    """List active categories (seeds system defaults on first access) (docs §6)."""
    return ComplaintsService(session).categories.list_categories(
        _society_id(tenant)
    )


@router.post(
    "/categories", response_model=CategoryOut, dependencies=_MANAGE_CATEGORIES
)
def create_category(
    body: CategoryCreateRequest,
    auth: AuthContext = Depends(require_permission(PERM_MANAGE_CATEGORIES)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> CategoryOut:
    """Create a category (docs §6)."""
    return ComplaintsService(session).categories.create_category(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


@router.patch(
    "/categories/{category_id}",
    response_model=CategoryOut,
    dependencies=_MANAGE_CATEGORIES,
)
def update_category(
    category_id: int,
    body: CategoryUpdateRequest,
    auth: AuthContext = Depends(require_permission(PERM_MANAGE_CATEGORIES)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> CategoryOut:
    """Rename / reactivate a category (docs §6)."""
    return ComplaintsService(session).categories.update_category(
        _society_id(tenant), category_id, body, actor_user_id=auth.user_id
    )


@router.delete(
    "/categories/{category_id}",
    response_model=CategoryOut,
    dependencies=_MANAGE_CATEGORIES,
)
def deactivate_category(
    category_id: int,
    auth: AuthContext = Depends(require_permission(PERM_MANAGE_CATEGORIES)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> CategoryOut:
    """Deactivate a category (soft; never hard-delete) (docs §6)."""
    return ComplaintsService(session).categories.deactivate_category(
        _society_id(tenant), category_id, actor_user_id=auth.user_id
    )


# ============================ Config =========================================
# Declared BEFORE the dynamic ``/{complaint_id}`` routes so "config" is never
# swallowed as a complaint id.


@router.get("/config", response_model=ComplaintsConfigOut, dependencies=_CONFIGURE)
def get_config(
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ComplaintsConfigOut:
    """Read module config (docs §6/§8)."""
    return ComplaintsService(session).config.get_config(_society_id(tenant))


@router.put("/config", response_model=ComplaintsConfigOut, dependencies=_CONFIGURE)
def update_config(
    body: ConfigUpdateRequest,
    auth: AuthContext = Depends(require_permission(PERM_CONFIGURE)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ComplaintsConfigOut:
    """Partial-merge update module config (docs §6/§8)."""
    return ComplaintsService(session).config.update_config(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


# ============================ Complaints =====================================


@router.post("", response_model=ComplaintDetailOut, dependencies=_CREATE)
def raise_complaint(
    body: ComplaintCreateRequest,
    auth: AuthContext = Depends(require_permission(PERM_CREATE)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ComplaintDetailOut:
    """Raise a complaint tied to the caller's owned house (docs §6).

    Report images are attached in follow-up calls to
    ``POST /complaints/{id}/images``.
    """
    return ComplaintsService(session).crud.raise_complaint(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


@router.get("", response_model=ComplaintListOut, dependencies=_READ)
def list_complaints(
    page: PageParams = Depends(),
    status: str | None = Query(default=None),
    category_id: int | None = Query(default=None),
    house_id: int | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    auth: AuthContext = Depends(require_permission(PERM_READ)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ComplaintListOut:
    """List complaints (docs §6). Resident → own house(s); read_all → all + filters.

    Newest first, paginated. Visibility is enforced in the repository query, not
    here.
    """
    return ComplaintsService(session).crud.list_complaints(
        _society_id(tenant),
        caller_user_id=auth.user_id,
        can_read_all=_can_read_all(auth),
        status=status,
        category_id=category_id,
        house_id=house_id,
        date_from=date_from,
        date_to=date_to,
        q=q,
        offset=page.offset,
        limit=page.limit,
    )


@router.get(
    "/{complaint_id}", response_model=ComplaintDetailOut, dependencies=_READ
)
def get_complaint(
    complaint_id: int,
    auth: AuthContext = Depends(require_permission(PERM_READ)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ComplaintDetailOut:
    """Complaint detail + status timeline + images (docs §6).

    Clears the caller's related alert (clear-on-read). Resident may only open a
    complaint on a house they own.
    """
    return ComplaintsService(session).crud.get_detail(
        _society_id(tenant),
        complaint_id,
        caller_user_id=auth.user_id,
        can_read_all=_can_read_all(auth),
    )


@router.patch(
    "/{complaint_id}", response_model=ComplaintDetailOut, dependencies=_CREATE
)
def edit_complaint(
    complaint_id: int,
    body: ComplaintUpdateRequest,
    auth: AuthContext = Depends(require_permission(PERM_CREATE)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ComplaintDetailOut:
    """Resident edit while ``open`` (title/description/category) (docs §6)."""
    return ComplaintsService(session).crud.edit_complaint(
        _society_id(tenant), complaint_id, body, actor_user_id=auth.user_id
    )


@router.post(
    "/{complaint_id}/withdraw",
    response_model=ComplaintDetailOut,
    dependencies=_CREATE,
)
def withdraw_complaint(
    complaint_id: int,
    auth: AuthContext = Depends(require_permission(PERM_CREATE)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ComplaintDetailOut:
    """Resident withdraw while ``open`` (docs §6)."""
    return ComplaintsService(session).crud.withdraw_complaint(
        _society_id(tenant), complaint_id, actor_user_id=auth.user_id
    )


# ============================ Status workflow ================================


@router.post(
    "/{complaint_id}/status",
    response_model=ComplaintDetailOut,
    dependencies=_UPDATE_STATUS,
)
def change_status(
    complaint_id: int,
    body: StatusChangeRequest,
    auth: AuthContext = Depends(require_permission(PERM_UPDATE_STATUS)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ComplaintDetailOut:
    """Admin non-resolve transition ``{to_status, note?}`` (docs §6).

    Handles open→in_progress, resolved→closed, and reopen resolved→in_progress.
    Resolving (in_progress→resolved) uses the multipart ``/resolve`` route below,
    since proof images are attached at resolve time.
    """
    return ComplaintsService(session).status.change_status(
        _society_id(tenant), complaint_id, body, actor_user_id=auth.user_id
    )


@router.post(
    "/{complaint_id}/resolve",
    response_model=ComplaintDetailOut,
    dependencies=_UPDATE_STATUS_VAULT,
)
async def resolve_complaint(
    complaint_id: int,
    note: str | None = Form(default=None),
    images: list[UploadFile] = File(default_factory=list),
    auth: AuthContext = Depends(require_permission(PERM_UPDATE_STATUS)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ComplaintDetailOut:
    """Resolve a complaint (in_progress→resolved) with a solution note + proof
    images (multipart) (docs §6). Proof images are filed into the Vault and locked
    after resolution.
    """
    return await ComplaintsService(session).status.resolve(
        _society_id(tenant),
        complaint_id,
        note=note,
        images=images,
        actor_user_id=auth.user_id,
    )


# ============================ Report images ==================================


@router.post(
    "/{complaint_id}/images",
    response_model=ComplaintImageOut,
    dependencies=_CREATE_VAULT,
)
async def add_report_image(
    complaint_id: int,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_permission(PERM_CREATE)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ComplaintImageOut:
    """Add a report image to an open complaint (resident; multipart) (docs §6)."""
    return ComplaintsService(session).images.add_report_image(
        _society_id(tenant), complaint_id, file, actor_user_id=auth.user_id
    )


@router.delete(
    "/{complaint_id}/images/{image_id}",
    status_code=204,
    dependencies=_CREATE_VAULT,
)
async def remove_report_image(
    complaint_id: int,
    image_id: int,
    auth: AuthContext = Depends(require_permission(PERM_CREATE)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> None:
    """Remove one's own report image while open (docs §6)."""
    ComplaintsService(session).images.remove_report_image(
        _society_id(tenant), complaint_id, image_id, actor_user_id=auth.user_id
    )

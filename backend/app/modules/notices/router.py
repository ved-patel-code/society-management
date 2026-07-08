"""Notice Board routes (docs/modules/notice-board.md §6), prefix ``/notices``.

Society comes from the JWT (``TenantContext.society_id``) — never a path id. Both
audiences share these endpoints; the service scopes data and gates actions by
permission (docs §2). Every route gates on ``require_module('notices')`` + a
permission; the attachment routes ALSO gate ``require_module('vault')`` (they
file bytes into the Vault). The resident-vs-admin split lives in the handlers:
read routes gate on ``notices.read``; the service is told whether the caller may
MANAGE (holds ``notices.publish`` or is a super-admin) so it can show drafts /
apply admin filters.

The router stays thin: resolve tenant → call ``NoticesService`` → shape response.
Attachment routes are multipart (``UploadFile``).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
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
from app.modules.notices.schemas import (
    NoticeCreateRequest,
    NoticeDetailOut,
    NoticeListOut,
    NoticeReceiptsOut,
    NoticeUpdateRequest,
)
from app.modules.notices.service import NoticesService
from app.modules.notices.spec import (
    PERM_PUBLISH,
    PERM_READ,
    PERM_READ_RECEIPTS,
)

router = APIRouter(prefix="/notices", tags=["notices"])

_MODULE = require_module("notices")
_VAULT = require_module("vault")


def _gate(perm: str) -> list:
    """Both gates for a permission: module enabled + the permission held."""
    return [Depends(_MODULE), Depends(require_permission(perm))]


def _gate_vault(perm: str) -> list:
    """Notices + Vault modules enabled + the permission held (attachment routes)."""
    return [Depends(_MODULE), Depends(_VAULT), Depends(require_permission(perm))]


_READ = _gate(PERM_READ)
_PUBLISH = _gate(PERM_PUBLISH)
_READ_RECEIPTS = _gate(PERM_READ_RECEIPTS)
_PUBLISH_VAULT = _gate_vault(PERM_PUBLISH)


def _society_id(tenant: TenantContext) -> int:
    if tenant.society_id is None:
        raise ValidationError("No active society for this request.")
    return tenant.society_id


def _can_manage(auth: AuthContext) -> bool:
    """Data-driven manage scope (docs §2): super-admin or ``notices.publish``.

    Never a hardcoded role list — a future role gains draft visibility + admin
    filters purely by holding ``notices.publish`` (docs/02 §4).
    """
    return auth.is_super_admin or auth.has_permission(PERM_PUBLISH)


# ============================ Static read routes =============================
# Declared BEFORE the dynamic ``/{notice_id}`` routes so "read-all" / "archive"
# are never swallowed as a notice id.


@router.post("/read-all", status_code=204, dependencies=_READ)
def read_all(
    auth: AuthContext = Depends(require_permission(PERM_READ)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> None:
    """Mark all active notices read for the caller (docs §6)."""
    NoticesService(session).receipts.read_all(
        _society_id(tenant), caller_user_id=auth.user_id
    )


@router.get("/archive", response_model=NoticeListOut, dependencies=_READ_RECEIPTS)
def list_archive(
    page: PageParams = Depends(),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> NoticeListOut:
    """Admin archive: expired + withdrawn notices (docs §6)."""
    return NoticesService(session).receipts.archive(
        _society_id(tenant), offset=page.offset, limit=page.limit
    )


# ============================ Feed + compose =================================


@router.get("", response_model=NoticeListOut, dependencies=_READ)
def list_notices(
    page: PageParams = Depends(),
    status: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    auth: AuthContext = Depends(require_permission(PERM_READ)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> NoticeListOut:
    """List notices (docs §6). Residents → active feed; admins → filters + drafts.

    Pinned-first then newest, paginated, with per-caller ``is_read`` +
    ``unread_count``. Visibility is enforced in the service/repository, not here.
    """
    return NoticesService(session).crud.list_feed(
        _society_id(tenant),
        caller_user_id=auth.user_id,
        can_manage=_can_manage(auth),
        status=status,
        scope=scope,
        offset=page.offset,
        limit=page.limit,
    )


@router.post("", response_model=NoticeDetailOut, dependencies=_PUBLISH)
def create_notice(
    body: NoticeCreateRequest,
    auth: AuthContext = Depends(require_permission(PERM_PUBLISH)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> NoticeDetailOut:
    """Compose a notice — draft, or published when ``publish=true`` (docs §6)."""
    return NoticesService(session).crud.create(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


@router.get("/{notice_id}", response_model=NoticeDetailOut, dependencies=_READ)
def get_notice(
    notice_id: int,
    auth: AuthContext = Depends(require_permission(PERM_READ)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> NoticeDetailOut:
    """Notice detail + attachments; marks it read for the caller (docs §6).

    Drafts/withdrawn notices are visible only to ``notices.publish`` holders;
    otherwise 404 (no existence leak).
    """
    return NoticesService(session).crud.get_detail(
        _society_id(tenant),
        notice_id,
        caller_user_id=auth.user_id,
        can_manage=_can_manage(auth),
    )


@router.patch(
    "/{notice_id}", response_model=NoticeDetailOut, dependencies=_PUBLISH
)
def edit_notice(
    notice_id: int,
    body: NoticeUpdateRequest,
    auth: AuthContext = Depends(require_permission(PERM_PUBLISH)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> NoticeDetailOut:
    """Edit title/body/pin/expiry (admin) (docs §6)."""
    return NoticesService(session).crud.edit(
        _society_id(tenant), notice_id, body, actor_user_id=auth.user_id
    )


# ============================ Lifecycle ======================================


@router.post(
    "/{notice_id}/publish", response_model=NoticeDetailOut, dependencies=_PUBLISH
)
def publish_notice(
    notice_id: int,
    auth: AuthContext = Depends(require_permission(PERM_PUBLISH)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> NoticeDetailOut:
    """Publish a draft → published; emits ``notice_posted`` (docs §6)."""
    return NoticesService(session).lifecycle.publish(
        _society_id(tenant), notice_id, actor_user_id=auth.user_id
    )


@router.post(
    "/{notice_id}/withdraw", response_model=NoticeDetailOut, dependencies=_PUBLISH
)
def withdraw_notice(
    notice_id: int,
    auth: AuthContext = Depends(require_permission(PERM_PUBLISH)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> NoticeDetailOut:
    """Soft-withdraw a notice (docs §6)."""
    return NoticesService(session).lifecycle.withdraw(
        _society_id(tenant), notice_id, actor_user_id=auth.user_id
    )


# ============================ Attachments ====================================


@router.post(
    "/{notice_id}/attachments",
    response_model=NoticeDetailOut,
    dependencies=_PUBLISH_VAULT,
)
async def add_attachment(
    notice_id: int,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_permission(PERM_PUBLISH)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> NoticeDetailOut:
    """Add an attachment to a notice (admin; multipart → Vault) (docs §6).

    Returns the updated notice detail (with the new attachment). Vault 413/415
    (quota / denied type) propagate.
    """
    return await NoticesService(session).attachments.add_attachment(
        _society_id(tenant), notice_id, file, actor_user_id=auth.user_id
    )


@router.delete(
    "/{notice_id}/attachments/{attachment_id}",
    status_code=204,
    dependencies=_PUBLISH_VAULT,
)
async def remove_attachment(
    notice_id: int,
    attachment_id: int,
    auth: AuthContext = Depends(require_permission(PERM_PUBLISH)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> None:
    """Remove an attachment (Vault soft-delete + drop row) (admin) (docs §6)."""
    await NoticesService(session).attachments.remove_attachment(
        _society_id(tenant), notice_id, attachment_id, actor_user_id=auth.user_id
    )


# ============================ Read receipts ==================================


@router.get(
    "/{notice_id}/receipts",
    response_model=NoticeReceiptsOut,
    dependencies=_READ_RECEIPTS,
)
def notice_receipts(
    notice_id: int,
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> NoticeReceiptsOut:
    """Read vs unread owners for a notice (admin) (docs §6)."""
    return NoticesService(session).receipts.receipts(
        _society_id(tenant), notice_id
    )

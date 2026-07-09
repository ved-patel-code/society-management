"""Notifications routes (docs/modules/notifications.md §6), prefix ``/notifications``.

Society + caller come from the JWT (``TenantContext.society_id`` /
``AuthContext.user_id``) — never a path id, so a caller can only ever act on their
OWN feed. Every route gates on ``require_module('notifications')`` + a permission
(``notifications.read`` for the feed/mark-read, ``notifications.configure`` for
config). There is NO create endpoint — notifications are created only by the
engine (event handlers + the reminder worker), never by a public POST (docs §6).

The router stays thin: resolve tenant + caller → call ``NotificationsService`` →
shape response.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
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
from app.modules.notifications.schemas import (
    ConfigOut,
    ConfigUpdateRequest,
    FeedOut,
    MarkReadResult,
    UnreadCountOut,
)
from app.modules.notifications.service import NotificationsService
from app.modules.notifications.spec import PERM_CONFIGURE, PERM_READ

router = APIRouter(prefix="/notifications", tags=["notifications"])

_MODULE = require_module("notifications")


def _gate(perm: str) -> list:
    """Both gates for a permission: module enabled + the permission held."""
    return [Depends(_MODULE), Depends(require_permission(perm))]


_READ = _gate(PERM_READ)
_CONFIGURE = _gate(PERM_CONFIGURE)


def _society_id(tenant: TenantContext) -> int:
    if tenant.society_id is None:
        raise ValidationError("No active society for this request.")
    return tenant.society_id


# ============================== Static routes ================================
# Declared BEFORE the dynamic ``/{notification_id}/read`` so "unread-count" /
# "read-all" / "config" are never swallowed as an id.


@router.get("/unread-count", response_model=UnreadCountOut, dependencies=_READ)
def unread_count(
    auth: AuthContext = Depends(require_permission(PERM_READ)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> UnreadCountOut:
    """The caller's unread badge count (docs §6)."""
    return NotificationsService(session).get_unread_count(
        _society_id(tenant), auth.user_id
    )


@router.post("/read-all", response_model=MarkReadResult, dependencies=_READ)
def read_all(
    auth: AuthContext = Depends(require_permission(PERM_READ)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> MarkReadResult:
    """Mark all the caller's unread notifications read (docs §6)."""
    return NotificationsService(session).mark_all_read(
        _society_id(tenant), auth.user_id
    )


@router.get("/config", response_model=ConfigOut, dependencies=_CONFIGURE)
def get_config(
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ConfigOut:
    """Read the society's reminder cadence + retention config (docs §6/§8).

    The ``_CONFIGURE`` dependency already enforces module + ``notifications.configure``;
    this read needs no ``AuthContext`` value (no actor to record)."""
    return NotificationsService(session).get_config(_society_id(tenant))


@router.put("/config", response_model=ConfigOut, dependencies=_CONFIGURE)
def update_config(
    body: ConfigUpdateRequest,
    auth: AuthContext = Depends(require_permission(PERM_CONFIGURE)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ConfigOut:
    """Partial-merge update the config; audits before/after (docs §5/§6/§8)."""
    return NotificationsService(session).update_config(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


# ============================== Feed + dynamic ===============================


@router.get("", response_model=FeedOut, dependencies=_READ)
def list_feed(
    page: PageParams = Depends(),
    auth: AuthContext = Depends(require_permission(PERM_READ)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> FeedOut:
    """The caller's unread feed (newest first, paginated) + unread count (§6)."""
    return NotificationsService(session).get_feed(
        _society_id(tenant), auth.user_id, page
    )


@router.post(
    "/{notification_id}/read",
    response_model=MarkReadResult,
    dependencies=_READ,
)
def mark_read(
    notification_id: int,
    auth: AuthContext = Depends(require_permission(PERM_READ)),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> MarkReadResult:
    """Mark one notification read — clears it (docs §6, own only → 404 if not)."""
    return NotificationsService(session).mark_read(
        _society_id(tenant), auth.user_id, notification_id
    )

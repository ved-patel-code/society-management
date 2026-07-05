"""Super-admin role/permission routes (docs/PF §5/§10). Filled by P2.

Endpoints: POST /admin/societies/{id}/roles (accepts portal),
PUT /admin/roles/{id}/permissions. Gate on ``require_super_admin``. Thin router —
it parses the request, calls :class:`RoleService`, and shapes the response; all
logic lives in the service and all queries in the repository (docs/03 §2).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import AuthContext, require_super_admin
from app.platform.roles.schemas import (
    CreateRoleRequest,
    RoleResponse,
    SetPermissionsRequest,
)
from app.platform.roles.service import RoleService

router = APIRouter(prefix="/admin", tags=["admin:roles"])


def _to_response(service: RoleService, role) -> RoleResponse:
    """Shape a Role ORM row + its permission keys into the API response."""
    return RoleResponse(
        id=role.id,
        society_id=role.society_id,
        key=role.key,
        name=role.name,
        scope=role.scope,
        portal=role.portal,
        is_system=role.is_system,
        permission_keys=sorted(service.role_permission_keys(role.id)),
    )


@router.post(
    "/societies/{society_id}/roles",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_society_role(
    society_id: int,
    body: CreateRoleRequest,
    auth: AuthContext = Depends(require_super_admin),
    session: Session = Depends(get_session),
) -> RoleResponse:
    """Create a society-scoped custom role (docs/PF §10). Audits ``role.created``."""
    service = RoleService(session)
    role = service.create_role(
        society_id=society_id,
        key=body.key,
        name=body.name,
        portal=body.portal,
        scope=body.scope,
        permission_keys=body.permission_keys,
        actor_user_id=auth.user_id,
    )
    return _to_response(service, role)


@router.put(
    "/roles/{role_id}/permissions",
    response_model=RoleResponse,
)
def set_role_permissions(
    role_id: int,
    body: SetPermissionsRequest,
    auth: AuthContext = Depends(require_super_admin),
    session: Session = Depends(get_session),
) -> RoleResponse:
    """Replace a role's permission set (docs/PF §10). Audits ``permission.set_changed``."""
    service = RoleService(session)
    role = service.set_role_permissions(
        role_id=role_id,
        permission_keys=body.permission_keys,
        actor_user_id=auth.user_id,
    )
    return _to_response(service, role)

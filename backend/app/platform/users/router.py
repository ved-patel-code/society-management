"""Super-admin user management routes (docs/PF §8/§10). Filled by P5.

Endpoints (all gate on ``require_super_admin`` — core/deps.py):
- POST /admin/societies/{society_id}/users  → create or link a user (docs/PF §8/§5.1)
- PATCH /admin/users/{user_id}              → deactivate (is_active=false, docs/PF §4)
- POST /admin/users/{user_id}/roles         → assign a role (docs/PF §8)

The router stays thin: parse the request, call ``UserProvisioningService``, reload
and shape the response. All business rules live in the service (docs/03 §2).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.common.errors import NotFoundError, ValidationError
from app.core.db import get_session
from app.core.deps import AuthContext, require_super_admin
from app.platform.models import User
from app.platform.users.provisioning import UserProvisioningService
from app.platform.users.schemas import (
    AssignRoleRequest,
    CreateUserRequest,
    UpdateUserRequest,
    UserOut,
)

router = APIRouter(prefix="/admin", tags=["admin:users"])


def _get_user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.", details={"user_id": user_id})
    return user


@router.post(
    "/societies/{society_id}/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
def create_user(
    society_id: int,
    body: CreateUserRequest,
    auth: AuthContext = Depends(require_super_admin),
    session: Session = Depends(get_session),
) -> UserOut:
    """Create a new account (default password + must_change) or link the role onto
    an existing email (dual-role — docs/PF §8/§5.1)."""
    user = UserProvisioningService(session).create_or_link_user(
        email=body.email,
        society_id=society_id,
        role_key=body.role_key,
        profile={"full_name": body.full_name, "phone": body.phone},
        actor_user_id=auth.user_id,
    )
    return UserOut.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UpdateUserRequest,
    auth: AuthContext = Depends(require_super_admin),
    session: Session = Depends(get_session),
) -> UserOut:
    """Deactivate a user (``is_active=false`` revokes their tokens — docs/PF §4).

    Reactivation is not part of v1's provisioning surface; ``is_active=true`` is
    rejected explicitly rather than silently ignored.
    """
    if body.is_active:
        raise ValidationError(
            "Reactivation is not supported via this endpoint.",
            details={"field": "is_active"},
        )
    service = UserProvisioningService(session)
    service.deactivate_user(user_id=user_id, actor_user_id=auth.user_id)
    return UserOut.model_validate(_get_user(session, user_id))


@router.post(
    "/users/{user_id}/roles",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
def assign_role(
    user_id: int,
    body: AssignRoleRequest,
    auth: AuthContext = Depends(require_super_admin),
    session: Session = Depends(get_session),
) -> UserOut:
    """Add a role to an existing user (respects one-society-per-user — docs/PF §8)."""
    service = UserProvisioningService(session)
    service.assign_role(
        user_id=user_id,
        society_id=body.society_id,
        role_key=body.role_key,
        actor_user_id=auth.user_id,
    )
    return UserOut.model_validate(_get_user(session, user_id))

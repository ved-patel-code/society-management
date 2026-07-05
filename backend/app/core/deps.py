"""Request-scoped dependencies: auth context, tenant context, and the two gates.

Every feature router wires into these (docs/PF §4/§5/§6/§7, docs/02 §3):

- :class:`AuthContext`   — the authenticated caller (from the JWT). Enforces the
  global must-change lockout: when ``password_state == "must_change"`` every
  endpoint except change-password is rejected.
- :class:`TenantContext` — resolves ``active_society_id`` for the request, with an
  explicit ``is_super_admin`` bypass for platform actors.
- :func:`require_permission` — the caller's FULL role set (union across roles in
  the active society) must hold the permission. Portal choice never narrows this.
- :func:`require_module`    — the active society must have the module enabled.

The effective-permission union is computed here from ``user_roles`` →
``role_permissions`` → ``permissions`` so the gate is self-contained; the roles
feature may expose richer helpers, but authorization always uses this union.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import (
    AuthenticationError,
    ModuleDisabledError,
    PermissionDeniedError,
)
from app.core.db import get_session
from app.core.security import decode_access_token
from app.platform.models import (
    Permission,
    RolePermission,
    SocietyModule,
    User,
    UserRole,
)

# auto_error=False so we raise our own typed AuthenticationError (consistent shape).
_bearer = HTTPBearer(auto_error=False)

# The one endpoint reachable while password_state == "must_change".
_MUST_CHANGE_ALLOWED_PATHS = frozenset({"/auth/change-password"})


@dataclass
class AuthContext:
    """The authenticated caller for this request."""

    user: User
    user_id: int
    active_society_id: int | None
    role_ids: list[int]
    password_state: str
    is_super_admin: bool = False
    permission_keys: set[str] = field(default_factory=set)

    def has_permission(self, key: str) -> bool:
        # Super-admin operates above societies; module permission checks don't apply
        # to platform ops (those routes gate on is_super_admin directly).
        return self.is_super_admin or key in self.permission_keys


@dataclass
class TenantContext:
    """Resolved tenant scope for the request (docs/PF §7)."""

    society_id: int | None
    is_super_admin: bool


def _effective_permission_keys(
    session: Session, user_id: int, society_id: int | None
) -> set[str]:
    """Union of permission keys across the user's roles in the active society."""
    if society_id is None:
        return set()
    rows = session.execute(
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id, UserRole.society_id == society_id)
    ).all()
    return {r[0] for r in rows}


def get_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> AuthContext:
    """Decode the bearer token, load the user, and build the auth context.

    Enforces the must-change lockout centrally (docs/PF §4).
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Not authenticated.")

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:  # expired / tampered / wrong alg
        raise AuthenticationError("Invalid or expired token.") from exc

    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        raise AuthenticationError("Invalid token.")

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid token.")

    active_society_id = payload.get("active_society_id")
    role_ids = payload.get("role_ids") or []
    password_state = payload.get("password_state", user.password_state)

    # Global must-change lockout: reject everything except change-password.
    if password_state == "must_change" and (
        request.url.path not in _MUST_CHANGE_ALLOWED_PATHS
    ):
        raise PermissionDeniedError(
            "Password change required before continuing.",
            details={"password_state": "must_change"},
        )

    permission_keys = (
        set()
        if user.is_platform_super_admin
        else _effective_permission_keys(session, user_id, active_society_id)
    )

    return AuthContext(
        user=user,
        user_id=user_id,
        active_society_id=active_society_id,
        role_ids=role_ids,
        password_state=password_state,
        is_super_admin=bool(user.is_platform_super_admin),
        permission_keys=permission_keys,
    )


def get_tenant_context(
    auth: AuthContext = Depends(get_auth_context),
) -> TenantContext:
    """Resolve the request's tenant scope from the auth context (docs/PF §7)."""
    return TenantContext(
        society_id=auth.active_society_id,
        is_super_admin=auth.is_super_admin,
    )


def require_super_admin(
    auth: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Gate for ``/admin/*`` platform routes."""
    if not auth.is_super_admin:
        raise PermissionDeniedError("Super-admin privileges required.")
    return auth


def require_permission(key: str):
    """Return a dependency asserting the caller's role set holds ``key`` (docs/PF §5)."""

    def _dep(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not auth.has_permission(key):
            raise PermissionDeniedError(
                "You do not have permission to perform this action.",
                details={"required_permission": key},
            )
        return auth

    return _dep


def require_module(key: str):
    """Return a dependency asserting the active society has module ``key`` enabled
    (docs/PF §6). Super-admin bypasses (platform ops are not society-scoped).
    """

    def _dep(
        auth: AuthContext = Depends(get_auth_context),
        session: Session = Depends(get_session),
    ) -> AuthContext:
        if auth.is_super_admin:
            return auth
        if auth.active_society_id is None:
            raise ModuleDisabledError(
                "No active society.", details={"module_key": key}
            )
        enabled = session.execute(
            select(SocietyModule.enabled).where(
                SocietyModule.society_id == auth.active_society_id,
                SocietyModule.module_key == key,
            )
        ).scalar_one_or_none()
        if not enabled:
            raise ModuleDisabledError(
                f"Module '{key}' is not enabled for this society.",
                details={"module_key": key},
            )
        return auth

    return _dep

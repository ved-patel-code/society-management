"""Request/response contracts for the super-admin user endpoints (docs/PF §10).

Pydantic owns shape + field validation; the service owns business rules (docs/03
§2/§6). The email is normalized (trim + lowercase) at the edge so the
case-insensitive login identity is consistent everywhere (users.email CITEXT).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.validators import normalize_email

_ROLE_KEY_PATTERN = r"^[a-z][a-z0-9_]*$"


class CreateUserRequest(BaseModel):
    """Body for ``POST /admin/societies/{society_id}/users`` (docs/PF §8/§10).

    Creates a society_admin by default, or links the role onto an existing email
    (dual-role — docs/PF §5.1). ``society_id`` comes from the path, not the body.
    """

    email: str = Field(min_length=1, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    role_key: str = Field(
        default="society_admin", min_length=1, max_length=64, pattern=_ROLE_KEY_PATTERN
    )

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return normalize_email(v)


class UpdateUserRequest(BaseModel):
    """Body for ``PATCH /admin/users/{user_id}`` — activation toggle (docs/PF §4).

    v1 exposes deactivation (``is_active=false``) which revokes the user's tokens.
    """

    is_active: bool


class AssignRoleRequest(BaseModel):
    """Body for ``POST /admin/users/{user_id}/roles`` (docs/PF §8)."""

    society_id: int = Field(gt=0)
    role_key: str = Field(
        min_length=1, max_length=64, pattern=_ROLE_KEY_PATTERN
    )


class UserOut(BaseModel):
    """User response shape. The password hash is NEVER exposed (docs/PF §14.6)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None
    phone: str | None
    password_state: str
    is_active: bool
    is_platform_super_admin: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

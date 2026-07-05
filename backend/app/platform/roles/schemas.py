"""Request/response contracts for the super-admin role endpoints (docs/PF §10).

Pydantic owns shape + field validation; the service owns business rules.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# roles.portal domain (docs/PF §3/§5.1). scope is fixed to "society" for
# super-admin-created custom roles (platform roles are seed-only).
_PORTAL_PATTERN = r"^(admin|resident|platform)$"
_KEY_PATTERN = r"^[a-z][a-z0-9_]*$"


class CreateRoleRequest(BaseModel):
    """Body for ``POST /admin/societies/{society_id}/roles``."""

    key: str = Field(min_length=1, max_length=64, pattern=_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    portal: str = Field(pattern=_PORTAL_PATTERN)
    # scope is society-only for custom roles; accept it for forward-compat but
    # constrain it so the intent is explicit (docs/PF §14.1).
    scope: str = Field(default="society", pattern=r"^society$")
    permission_keys: list[str] = Field(default_factory=list)


class SetPermissionsRequest(BaseModel):
    """Body for ``PUT /admin/roles/{role_id}/permissions``."""

    permission_keys: list[str] = Field(default_factory=list)


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int | None
    key: str
    name: str
    scope: str
    portal: str
    is_system: bool
    permission_keys: list[str]

"""Foundation bootstrap constants: the global role templates + foundation spec.

The Platform Foundation is not a toggleable module, but it registers a
``ModuleSpec`` so any permission keys it owns seed through the same path as
feature modules (docs/PF §5). Foundation platform ops are gated by the
``is_platform_super_admin`` flag rather than permission rows, so its permission
set is intentionally empty for now; the seed still creates the role templates.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.registry import MODULE_REGISTRY, ModuleSpec

# --- Global role templates (society_id NULL), copied per-society on creation ---
# portal is view-only (docs/PF §5.1); scope drives platform vs society.


@dataclass(frozen=True)
class RoleTemplate:
    key: str
    name: str
    scope: str  # platform | society
    portal: str  # admin | resident | platform


SUPER_ADMIN = RoleTemplate("super_admin", "Super Admin", "platform", "platform")
SOCIETY_ADMIN = RoleTemplate("society_admin", "Society Admin", "society", "admin")
RESIDENT = RoleTemplate("resident", "Resident", "society", "resident")

GLOBAL_ROLE_TEMPLATES: tuple[RoleTemplate, ...] = (
    SUPER_ADMIN,
    SOCIETY_ADMIN,
    RESIDENT,
)

# Roles copied into each new society (docs/PF §5/§14.1). super_admin stays global.
SOCIETY_DEFAULT_ROLE_KEYS: tuple[str, ...] = (SOCIETY_ADMIN.key, RESIDENT.key)


FOUNDATION_SPEC = ModuleSpec(
    key="platform",
    name="Platform Foundation",
    permissions=[],  # platform ops gate on is_platform_super_admin, not perm rows
    is_core=True,
)


def register_foundation() -> None:
    """Register the foundation spec once (idempotent within a process)."""
    if MODULE_REGISTRY.get(FOUNDATION_SPEC.key) is None:
        MODULE_REGISTRY.register(FOUNDATION_SPEC)

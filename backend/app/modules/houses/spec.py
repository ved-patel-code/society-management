"""House & Occupancy ``ModuleSpec`` + registration (docs/modules/house-occupancy.md §2/§8).

Registering the spec seeds the module's permissions through the same path as every
module (``MODULE_REGISTRY.all_permission_keys`` → ``cli.seed``), lets a society
enable it via ``PUT /admin/societies/{id}/modules`` (gated by ``depends_on:
onboarding``), and declares the default role→permission grants applied on enable
(society_admin gets all three; residents get none — the module is admin-facing).
"""
from __future__ import annotations

from app.core.registry import MODULE_REGISTRY, ModuleSpec, PermissionDef

MODULE_KEY = "houses"

PERM_READ = "houses.read"
PERM_UPDATE_STATUS = "houses.update_status"
PERM_MANAGE_OCCUPANCY = "houses.manage_occupancy"

HOUSES_SPEC = ModuleSpec(
    key=MODULE_KEY,
    name="House & Occupancy",
    permissions=[
        PermissionDef(PERM_READ, "Read houses, occupancy details, and history."),
        PermissionDef(
            PERM_UPDATE_STATUS,
            "Change a house's status and capture the target occupancy payload.",
        ),
        PermissionDef(
            PERM_MANAGE_OCCUPANCY, "Edit owner/tenant occupancy details."
        ),
    ],
    # Not core: a society opts in. Requires the onboarding registry to exist first.
    depends_on=["onboarding"],
    is_core=False,
    # Granted to the society's society_admin role when the module is enabled.
    default_role_permissions={
        "society_admin": [PERM_READ, PERM_UPDATE_STATUS, PERM_MANAGE_OCCUPANCY]
    },
)


def register_houses() -> None:
    """Register the houses spec once (idempotent within a process)."""
    if MODULE_REGISTRY.get(HOUSES_SPEC.key) is None:
        MODULE_REGISTRY.register(HOUSES_SPEC)

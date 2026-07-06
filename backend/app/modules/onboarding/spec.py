"""Onboarding ``ModuleSpec`` + registration (docs/02 §3, docs/modules/onboarding.md §2/§8).

Registering the spec makes the module's permissions seed through the same path as
every module (``MODULE_REGISTRY.all_permission_keys`` → ``cli.seed``), lets a
society enable it via ``PUT /admin/societies/{id}/modules``, and declares the
default role→permission grants applied on enable (society_admin gets both keys;
residents get none — onboarding is admin-only).
"""
from __future__ import annotations

from app.core.registry import MODULE_REGISTRY, ModuleSpec, PermissionDef

MODULE_KEY = "onboarding"

PERM_MANAGE = "onboarding.manage"
PERM_READ = "onboarding.read"

ONBOARDING_SPEC = ModuleSpec(
    key=MODULE_KEY,
    name="Onboarding",
    permissions=[
        PermissionDef(
            PERM_MANAGE,
            "Map and edit society structure, override house numbers, complete onboarding.",
        ),
        PermissionDef(PERM_READ, "Read the society's onboarding state and structure."),
    ],
    # Effectively core: a society can't operate unmapped (docs §8). It is still a
    # toggleable module row so require_module gates it like any other.
    is_core=True,
    default_config={"count_pad": 2, "ground_prefix": "G"},
    # Granted to the society's matching roles when the module is enabled (docs §2).
    default_role_permissions={"society_admin": [PERM_MANAGE, PERM_READ]},
)


def register_onboarding() -> None:
    """Register the onboarding spec once (idempotent within a process)."""
    if MODULE_REGISTRY.get(ONBOARDING_SPEC.key) is None:
        MODULE_REGISTRY.register(ONBOARDING_SPEC)

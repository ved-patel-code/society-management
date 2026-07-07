"""Vault ``ModuleSpec`` + registration (docs/modules/vault.md §2/§8).

Registering the spec seeds ``vault.read`` / ``vault.manage`` through the same path
as every module (``MODULE_REGISTRY.all_permission_keys`` → ``cli.seed``), lets a
society enable it via ``PUT /admin/societies/{id}/modules`` (gated by
``depends_on: onboarding`` — the vault labels house/proof folders with the
onboarding display code), and declares the default grants applied on enable
(society_admin gets both; residents get none — the vault is admin-only).

``default_config`` carries the file-type denylist + trash retention days; the
storage limit lives on ``societies.storage_limit_bytes`` (super-admin owns it).
"""
from __future__ import annotations

from app.core.registry import MODULE_REGISTRY, ModuleSpec, PermissionDef
from app.modules.vault.schemas import (
    DEFAULT_DENYLIST_EXTENSIONS,
    DEFAULT_TRASH_RETENTION_DAYS,
)

MODULE_KEY = "vault"

PERM_READ = "vault.read"
PERM_MANAGE = "vault.manage"

VAULT_SPEC = ModuleSpec(
    key=MODULE_KEY,
    name="Vault",
    permissions=[
        PermissionDef(
            PERM_READ,
            "Browse folders, preview/download documents, view usage and trash.",
        ),
        PermissionDef(
            PERM_MANAGE,
            "Create/rename/move/delete folders & files, upload, restore, empty "
            "trash.",
        ),
    ],
    # Not core: a society opts in. Needs the onboarding registry for house labels.
    depends_on=["onboarding"],
    is_core=False,
    default_config={
        "denylist_extensions": sorted(DEFAULT_DENYLIST_EXTENSIONS),
        "trash_retention_days": DEFAULT_TRASH_RETENTION_DAYS,
    },
    # Granted to the society's society_admin role when the module is enabled.
    default_role_permissions={"society_admin": [PERM_READ, PERM_MANAGE]},
)


def register_vault() -> None:
    """Register the vault spec once (idempotent within a process)."""
    if MODULE_REGISTRY.get(VAULT_SPEC.key) is None:
        MODULE_REGISTRY.register(VAULT_SPEC)

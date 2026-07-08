"""Complaints ``ModuleSpec`` + registration (docs/modules/complaints.md §2/§8).

Registering the spec seeds the six ``complaints.*`` permissions through the same
path as every module (``MODULE_REGISTRY.all_permission_keys`` -> ``cli.seed``),
lets a society enable it via ``PUT /admin/societies/{id}/modules`` (gated by
``depends_on: houses`` — complaints needs the house registry + owner occupancy),
and declares the default grants applied on enable:
- resident -> ``create``, ``read`` (raise + view own house's complaints).
- society_admin -> ``read``, ``read_all``, ``update_status``,
  ``manage_categories``, ``configure`` (see all, drive the workflow, manage
  categories + config).

``default_config`` carries ``auto_archive_days`` + ``max_report_images`` +
``max_proof_images`` (docs §8). Default categories are seeded LAZILY on first use
(``services/support.ensure_default_categories``) — no platform enable-flow edit
(documented deviation, matches finance).

Image uploads additionally require the ``vault`` module; that is enforced at the
ROUTE level (``require_module('vault')`` on the image routes), not via
``depends_on``, so complaints can be enabled and used for text-only complaints
even before vault — image routes simply 403 until vault is on.
"""
from __future__ import annotations

from app.core.registry import MODULE_REGISTRY, ModuleSpec, PermissionDef
from app.modules.complaints.schemas import (
    DEFAULT_AUTO_ARCHIVE_DAYS,
    DEFAULT_MAX_PROOF_IMAGES,
    DEFAULT_MAX_REPORT_IMAGES,
)

MODULE_KEY = "complaints"

PERM_CREATE = "complaints.create"
PERM_READ = "complaints.read"
PERM_READ_ALL = "complaints.read_all"
PERM_UPDATE_STATUS = "complaints.update_status"
PERM_MANAGE_CATEGORIES = "complaints.manage_categories"
PERM_CONFIGURE = "complaints.configure"

_ADMIN_PERMS = [
    PERM_READ,
    PERM_READ_ALL,
    PERM_UPDATE_STATUS,
    PERM_MANAGE_CATEGORIES,
    PERM_CONFIGURE,
]

COMPLAINTS_SPEC = ModuleSpec(
    key=MODULE_KEY,
    name="Complaints",
    permissions=[
        PermissionDef(
            PERM_CREATE,
            "Raise a complaint; edit/withdraw one's own while open; add/remove "
            "one's own report images while open. Held by residents.",
        ),
        PermissionDef(
            PERM_READ,
            "View complaints scoped to the caller's own house(s) (resident view).",
        ),
        PermissionDef(
            PERM_READ_ALL,
            "View ALL of the society's complaints + admin filters (admin view). "
            "Grant to staff roles that oversee complaints.",
        ),
        PermissionDef(
            PERM_UPDATE_STATUS,
            "Transition a complaint's status, attach the optional note, and add "
            "proof images when resolving (admin).",
        ),
        PermissionDef(
            PERM_MANAGE_CATEGORIES,
            "Create / rename / deactivate complaint categories (admin).",
        ),
        PermissionDef(
            PERM_CONFIGURE,
            "Set module config, e.g. auto_archive_days + image caps (admin).",
        ),
    ],
    # Not core: a society opts in. Needs the houses module (registry + occupancy).
    depends_on=["houses"],
    is_core=False,
    default_config={
        "auto_archive_days": DEFAULT_AUTO_ARCHIVE_DAYS,
        "max_report_images": DEFAULT_MAX_REPORT_IMAGES,
        "max_proof_images": DEFAULT_MAX_PROOF_IMAGES,
    },
    default_role_permissions={
        "society_admin": _ADMIN_PERMS,
        "resident": [PERM_CREATE, PERM_READ],
    },
)


def register_complaints() -> None:
    """Register the complaints spec once (idempotent within a process)."""
    if MODULE_REGISTRY.get(COMPLAINTS_SPEC.key) is None:
        MODULE_REGISTRY.register(COMPLAINTS_SPEC)

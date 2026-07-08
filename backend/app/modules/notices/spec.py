"""Notice Board ``ModuleSpec`` + registration (docs/modules/notice-board.md §2/§8).

Registering the spec seeds the three ``notices.*`` permissions through the same
path as every module (``MODULE_REGISTRY.all_permission_keys`` -> ``cli.seed``),
lets a society enable it via ``PUT /admin/societies/{id}/modules`` (gated by
``depends_on: houses`` — the read-receipt denominator + broadcast audience come
from Occupancy's current-owner set), and declares the default grants applied on
enable:
- resident -> ``read`` (read the active feed, open a notice, mark-all-read).
- society_admin -> ``read``, ``publish``, ``read_receipts`` (post + drive the
  lifecycle + manage attachments + see receipts and the archive).

``default_config`` is empty — Notice Board needs no per-society knobs in v1
(attachments are Vault-quota-bound; expiry/pin are per-notice) (docs §8).

Attachment uploads additionally require the ``vault`` module; that is enforced at
the ROUTE level (``require_module('vault')`` on the attachment routes), not via
``depends_on``, so notices can be enabled and used for text-only notices even
before vault — attachment routes simply 403 until vault is on (mirrors
complaints' image routes).
"""
from __future__ import annotations

from app.core.registry import MODULE_REGISTRY, ModuleSpec, PermissionDef

MODULE_KEY = "notices"

PERM_READ = "notices.read"
PERM_PUBLISH = "notices.publish"
PERM_READ_RECEIPTS = "notices.read_receipts"

_ADMIN_PERMS = [PERM_READ, PERM_PUBLISH, PERM_READ_RECEIPTS]

NOTICES_SPEC = ModuleSpec(
    key=MODULE_KEY,
    name="Notice Board",
    permissions=[
        PermissionDef(
            PERM_READ,
            "Read the active notice feed, open a notice (marks it read), and "
            "mark-all-read. Held by residents and admins.",
        ),
        PermissionDef(
            PERM_PUBLISH,
            "Create / draft / edit / publish / withdraw / pin / set-expiry a "
            "notice and manage its attachments (admin).",
        ),
        PermissionDef(
            PERM_READ_RECEIPTS,
            "View per-notice read-receipt lists and the admin archive (expired + "
            "withdrawn history) (admin).",
        ),
    ],
    # Not core: a society opts in. Needs the houses module (current-owner set for
    # receipts + the broadcast audience).
    depends_on=["houses"],
    is_core=False,
    default_config={},
    default_role_permissions={
        "society_admin": _ADMIN_PERMS,
        "resident": [PERM_READ],
    },
)


def register_notices() -> None:
    """Register the notices spec once (idempotent within a process)."""
    if MODULE_REGISTRY.get(NOTICES_SPEC.key) is None:
        MODULE_REGISTRY.register(NOTICES_SPEC)

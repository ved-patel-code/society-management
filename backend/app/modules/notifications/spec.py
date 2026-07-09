"""Notifications ``ModuleSpec`` + registration (docs/modules/notifications.md §2/§8).

Registering the spec seeds the two ``notifications.*`` permissions through the
same path as every module (``MODULE_REGISTRY.all_permission_keys`` -> ``cli.seed``),
lets a society enable it via ``PUT /admin/societies/{id}/modules`` (gated by
``depends_on: finance`` — the dues reminder rule consumes Finance's
``outstanding_dues`` + ``maintenance_due_day``), and declares the default grants
applied on enable:
- resident -> ``read`` (read own feed, unread count, mark-read / mark-all-read).
- society_admin -> ``read``, ``configure`` (also set the reminder cadence + retention).

``default_config`` carries the doc's cadence/retention defaults (docs §8):
``dues_advance_days=3``, ``dues_reminder_interval_days=5``, ``read_retention_days=30``.

**Soft dependencies:** the complaint/notice event handlers are no-ops if that
module isn't enabled for the society — Notifications ``depends_on`` only Finance
(the one HARD dependency, for the dues rule). Complaints/Notice-Board wiring is
purely via the event bus; enabling Notifications without them is valid (those
events simply never fire).
"""
from __future__ import annotations

from app.core.registry import MODULE_REGISTRY, ModuleSpec, PermissionDef
from app.modules.notifications.schemas import (
    DEFAULT_DUES_ADVANCE_DAYS,
    DEFAULT_DUES_REMINDER_INTERVAL_DAYS,
    DEFAULT_READ_RETENTION_DAYS,
)

MODULE_KEY = "notifications"

PERM_READ = "notifications.read"
PERM_CONFIGURE = "notifications.configure"

_ADMIN_PERMS = [PERM_READ, PERM_CONFIGURE]

NOTIFICATIONS_SPEC = ModuleSpec(
    key=MODULE_KEY,
    name="Notifications",
    permissions=[
        PermissionDef(
            PERM_READ,
            "Read your own notification feed + unread count, and mark "
            "notifications read (own only). Held by residents and admins.",
        ),
        PermissionDef(
            PERM_CONFIGURE,
            "Configure the reminder cadence (advance days, recurring interval) "
            "and read-retention for the society (admin).",
        ),
    ],
    # Not core: a society opts in. HARD-needs Finance (the dues reminder rule
    # consumes outstanding_dues + maintenance_due_day). Complaint/notice events
    # are SOFT — their handlers no-op when that module is off for the society.
    depends_on=["finance"],
    is_core=False,
    default_config={
        "dues_advance_days": DEFAULT_DUES_ADVANCE_DAYS,
        "dues_reminder_interval_days": DEFAULT_DUES_REMINDER_INTERVAL_DAYS,
        "read_retention_days": DEFAULT_READ_RETENTION_DAYS,
    },
    default_role_permissions={
        "society_admin": _ADMIN_PERMS,
        "resident": [PERM_READ],
    },
)


def register_notifications() -> None:
    """Register the notifications spec once (idempotent within a process)."""
    if MODULE_REGISTRY.get(NOTIFICATIONS_SPEC.key) is None:
        MODULE_REGISTRY.register(NOTIFICATIONS_SPEC)

"""Finance ``ModuleSpec`` + registration (docs/modules/finance.md §2/§8).

Registering the spec seeds the five ``finance.*`` permissions through the same
path as every module (``MODULE_REGISTRY.all_permission_keys`` → ``cli.seed``), lets
a society enable it via ``PUT /admin/societies/{id}/modules`` (gated by
``depends_on: houses`` — Finance needs the house status/registry to know who owes),
and declares the default grants applied on enable (society_admin gets all; resident
gets read-only — they may view their own house's dues, docs §2).

``default_config`` carries ``maintenance_due_day`` + ``prepaid_blocks`` (docs §8);
currency lives on ``societies``. System expense categories are seeded lazily on
first use (``services/support.ensure_default_categories``) — no platform edit.
"""
from __future__ import annotations

from app.core.registry import MODULE_REGISTRY, ModuleSpec, PermissionDef
from app.modules.finance.schemas import (
    DEFAULT_MAINTENANCE_DUE_DAY,
    DEFAULT_PREPAID_BLOCKS,
)

MODULE_KEY = "finance"

PERM_READ = "finance.read"
PERM_MANAGE_RATE = "finance.manage_rate"
PERM_RECORD_PAYMENT = "finance.record_payment"
PERM_MANAGE_EXPENSES = "finance.manage_expenses"
PERM_MANAGE_RESERVE = "finance.manage_reserve"

_ALL_ADMIN_PERMS = [
    PERM_READ,
    PERM_MANAGE_RATE,
    PERM_RECORD_PAYMENT,
    PERM_MANAGE_EXPENSES,
    PERM_MANAGE_RESERVE,
]

FINANCE_SPEC = ModuleSpec(
    key=MODULE_KEY,
    name="Finance",
    permissions=[
        PermissionDef(
            PERM_READ,
            "View dues, expenses, reserve ledger, and analytics.",
        ),
        PermissionDef(
            PERM_MANAGE_RATE,
            "Set the effective-dated maintenance rate and generate dues.",
        ),
        PermissionDef(
            PERM_RECORD_PAYMENT,
            "Record payments and prepaid blocks; void payments.",
        ),
        PermissionDef(
            PERM_MANAGE_EXPENSES,
            "Record/void expenses and add expense categories.",
        ),
        PermissionDef(
            PERM_MANAGE_RESERVE,
            "Post/reverse reserve ledger entries and reconcile to bank.",
        ),
    ],
    # Not core: a society opts in. Needs the houses module (who owes + registry).
    depends_on=["houses"],
    is_core=False,
    default_config={
        "maintenance_due_day": DEFAULT_MAINTENANCE_DUE_DAY,
        "prepaid_blocks": list(DEFAULT_PREPAID_BLOCKS),
    },
    # society_admin gets full finance control; residents get read-only (own dues).
    default_role_permissions={
        "society_admin": _ALL_ADMIN_PERMS,
        "resident": [PERM_READ],
    },
)


def register_finance() -> None:
    """Register the finance spec once (idempotent within a process)."""
    if MODULE_REGISTRY.get(FINANCE_SPEC.key) is None:
        MODULE_REGISTRY.register(FINANCE_SPEC)

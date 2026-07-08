"""Shared Finance service internals (docs/modules/finance.md §3/§4/§8).

Small, dependency-free helpers every concern reuses so logic lives in ONE place
(docs/03 §1): resolve the validated per-society finance config, seed the default
expense categories idempotently on first use, post a ledger entry (the single
choke-point every money movement goes through), and reach House & Occupancy via
its service interface (never its tables — docs/05).
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.finance.models import ExpenseCategory, LedgerEntry
from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    DEFAULT_EXPENSE_CATEGORIES,
    MONEY_QUANT,
    FinanceConfig,
)
from app.platform.models import Society, SocietyModule

MODULE_KEY = "finance"


def money(value: Decimal | int | str) -> Decimal:
    """Coerce to a 2 dp Decimal (ROUND_HALF_UP). The one rounding rule money uses."""
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def load_config(session: Session, society_id: int) -> FinanceConfig:
    """The validated finance config for a society (docs §8).

    Reads ``society_modules.config`` for the finance module and validates it
    through :class:`FinanceConfig` (falling back to defaults for missing keys).
    """
    module = session.execute(
        select(SocietyModule).where(
            SocietyModule.society_id == society_id,
            SocietyModule.module_key == MODULE_KEY,
        )
    ).scalar_one_or_none()
    raw = (module.config or {}) if module is not None else {}
    # Only pull the keys we own; ignore unrelated config.
    data = {
        k: raw[k]
        for k in ("maintenance_due_day", "prepaid_blocks")
        if k in raw
    }
    return FinanceConfig(**data)


def society_currency(session: Session, society_id: int) -> str:
    """The society's currency code (docs §8: currency lives on ``societies``)."""
    currency = session.execute(
        select(Society.currency).where(Society.id == society_id)
    ).scalar_one_or_none()
    return currency or "INR"


def ensure_default_categories(
    session: Session, society_id: int, repo: FinanceRepository
) -> None:
    """Idempotently seed the system expense categories for a society (docs §3).

    Called on first use of the expenses feature (mirrors Vault's lazy
    ``ensure_*_folder`` pattern) so no edit to the platform enable flow is needed.
    Grant-only: never removes society-added categories.
    """
    if repo.count_categories(society_id) > 0:
        return
    for name in DEFAULT_EXPENSE_CATEGORIES:
        # Guard against a concurrent seeder having inserted it already.
        if repo.category_by_name(society_id, name) is None:
            repo.add_category(
                ExpenseCategory(
                    society_id=society_id, name=name, is_system=True
                )
            )


def post_ledger_entry(
    repo: FinanceRepository,
    *,
    society_id: int,
    entry_type: str,
    direction: str,
    amount: Decimal,
    occurred_on: date,
    description: str | None,
    source_type: str | None,
    source_id: int | None,
    recorded_by: int | None,
    reverses_entry_id: int | None = None,
) -> LedgerEntry:
    """The single choke-point for a ledger write (docs §4).

    Every money movement posts exactly one entry here so the reserve balance
    (Σ inflow − Σ outflow) is always derivable and auditable. A reversal passes
    ``reverses_entry_id`` (a negating entry); the caller flips the original's
    ``is_reversed`` flag.
    """
    return repo.add_ledger_entry(
        LedgerEntry(
            society_id=society_id,
            entry_type=entry_type,
            direction=direction,
            amount=money(amount),
            occurred_on=occurred_on,
            description=description,
            source_type=source_type,
            source_id=source_id,
            recorded_by=recorded_by,
            reverses_entry_id=reverses_entry_id,
        )
    )

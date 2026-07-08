"""Finance public inter-module contract (docs/modules/finance.md §7, docs/05).

The ONLY surface other modules import. Notifications (dues reminders) consumes
``outstanding_dues`` + the society's ``maintenance_due_day``; Onboarding's future
delete-guard consumes ``has_dues``; a future gateway calls ``record_payment``; the
worker calls ``generate_due_cycle``. Consumers NEVER touch finance tables directly.

Each call takes the caller's request-scoped ``Session`` so a write joins the
caller's transaction. Thin delegators over :class:`FinanceService`; no logic here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.finance.schemas import (
    HouseDuesOut,
    PaymentOut,
    PaymentRecordRequest,
)
from app.modules.finance.service import FinanceService
from app.modules.finance.services.support import load_config


def outstanding_dues(
    session: Session, society_id: int, house_id: int
) -> HouseDuesOut:
    """A house's outstanding dues + total + history (docs §7).

    The data the Notifications ``maintenance_due`` reminder rule consolidates.
    """
    return FinanceService(session).outstanding_dues(society_id, house_id)


def outstanding_total(
    session: Session, society_id: int, house_id: int
) -> Decimal:
    """Σ of a house's outstanding dues (docs §7)."""
    return FinanceService(session).outstanding_total(society_id, house_id)


def has_dues(session: Session, society_id: int, house_id: int) -> bool:
    """Whether a house has any outstanding due (Onboarding delete-guard §7)."""
    return FinanceService(session).has_dues(society_id, house_id)


def maintenance_due_day(session: Session, society_id: int) -> int:
    """The society's configured maintenance due day (docs §8).

    Exposed so the Notifications reminder rule can align its cadence to the due
    day without reading finance config directly.
    """
    return load_config(session, society_id).maintenance_due_day


def record_payment(
    session: Session,
    society_id: int,
    house_id: int,
    req: PaymentRecordRequest,
    *,
    actor_user_id: int,
) -> PaymentOut:
    """Record a payment against a house's dues (docs §7 — gateway/future)."""
    return FinanceService(session).record_payment(
        society_id, house_id, req, actor_user_id=actor_user_id
    )


def generate_due_cycle(
    session: Session,
    society_id: int,
    *,
    as_of: date | None = None,
    actor_user_id: int | None = None,
) -> int:
    """Materialize a society's due cycle; returns the count created (docs §7/§9)."""
    return FinanceService(session).generate_due_cycle(
        society_id, as_of=as_of, actor_user_id=actor_user_id
    )

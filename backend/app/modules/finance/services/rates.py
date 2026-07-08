"""Rates concern (docs/modules/finance.md §4 — Rate).

Effective-dated society-wide rate: set a new row (never edit history), read the
current rate + history, resolve the rate for a given month, and project a
rate-change preview. WRITE (``set_rate``) is a frozen stub — Wave A implements it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.common.errors import ConflictError
from app.modules.finance.models import MaintenanceRate
from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    RateHistoryOut,
    RateOut,
    RatePreviewOut,
    RateSetRequest,
)
from app.modules.finance.services.support import money
from app.modules.houses.service import HouseService
from app.platform.audit.service import AuditService


class RatesService:
    def __init__(self, session: Session, repo: FinanceRepository) -> None:
        self._session = session
        self._repo = repo

    # --- reads (implemented in core) ---------------------------------------

    def get_rate(self, society_id: int) -> RateHistoryOut:
        """Current rate + full history, newest first (docs §6)."""
        history = self._repo.rate_history(society_id)
        current = history[0] if history else None
        return RateHistoryOut(
            current=RateOut.model_validate(current) if current else None,
            history=[RateOut.model_validate(r) for r in history],
        )

    def rate_amount_for_month(
        self, society_id: int, first_of_month: date
    ) -> Decimal | None:
        """The effective rate amount for a month (latest valid_from ≤ month)."""
        rate = self._repo.rate_for_month(society_id, first_of_month)
        return rate.amount if rate is not None else None

    def preview(self, society_id: int, proposed_amount: Decimal) -> RatePreviewOut:
        """Rate-change projection: proposed × dues-owing houses vs current (docs §4).

        Pure projection — nothing persisted. Dues-owing = House & Occupancy's
        ``houses_owing`` (status != empty), reached via its service interface.
        """
        owing = HouseService(self._session).houses_owing(society_id)
        count = len(owing)
        current = self._repo.current_rate(society_id)
        current_amount = money(current.amount) if current is not None else None
        projected = money(proposed_amount * count)
        current_collection = (
            money(current_amount * count) if current_amount is not None else None
        )
        delta = (
            money(projected - current_collection)
            if current_collection is not None
            else None
        )
        return RatePreviewOut(
            proposed_amount=proposed_amount,
            dues_owing_houses=count,
            projected_monthly_collection=projected,
            current_amount=current_amount,
            current_monthly_collection=current_collection,
            delta=delta,
        )

    # --- writes (FROZEN — Wave A implements) -------------------------------

    def set_rate(
        self, society_id: int, req: RateSetRequest, *, actor_user_id: int
    ) -> RateOut:
        """Set a new effective-dated rate (docs §4/§6).

        Wave A: reject a duplicate ``valid_from`` (uniqueness), insert a new
        ``maintenance_rates`` row (never edit history), audit ``finance.rate_set``
        (amount + valid_from), return it. ``valid_from`` is month-aligned by the
        schema. NEVER commits (the request session commits once at end — docs/03).
        """
        # Pre-check the UNIQUE(society_id, valid_from) for a clean 409 rather than
        # surfacing a raw DB integrity error (docs §4: setting a new rate is always
        # a new effective month; history is never edited).
        if self._repo.rate_at_valid_from(society_id, req.valid_from) is not None:
            raise ConflictError(
                "A rate already exists for this effective month.",
                details={"valid_from": str(req.valid_from)},
            )

        amount = money(req.amount)
        row = self._repo.add_rate(
            MaintenanceRate(
                society_id=society_id,
                amount=amount,
                valid_from=req.valid_from,
                created_by=actor_user_id,
            )
        )

        AuditService(self._session).record(
            action="finance.rate_set",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="maintenance_rate",
            entity_id=row.id,
            after={"amount": str(amount), "valid_from": str(req.valid_from)},
        )
        return RateOut.model_validate(row)

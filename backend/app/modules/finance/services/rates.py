"""Rates concern (docs/modules/finance.md §4 — Rate).

Effective-dated society-wide rate: set a new row (never edit history), read the
current rate + history, resolve the rate for a given month, and project a
rate-change preview. WRITE (``set_rate``) is a frozen stub — Wave A implements it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    RateHistoryOut,
    RateOut,
    RatePreviewOut,
    RateSetRequest,
)
from app.modules.houses.service import HouseService


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
        current_amount = current.amount if current is not None else None
        projected = proposed_amount * count
        current_collection = (
            current_amount * count if current_amount is not None else None
        )
        delta = (
            projected - current_collection
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
        (amount + valid_from), return it.
        """
        raise NotImplementedError("Wave A: set_rate")

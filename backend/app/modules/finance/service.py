"""Finance service facade (docs/modules/finance.md §4).

Thin ``FinanceService`` over the concern-split internals (``services/``). Routers
and the inter-module ``api`` talk to this one class; it constructs the shared
:class:`FinanceRepository` once per request session and exposes each concern
(``rates``, ``dues``, ``collection``, ``expenses``, ``reserve``, ``analytics``)
plus the few façade-level shortcuts the contract needs. The service NEVER commits
(``get_session`` commits once at request end — docs/03 §2); concerns flush where
an id is needed.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    HouseDuesOut,
    PaymentOut,
    PaymentRecordRequest,
)
from app.modules.finance.services.analytics import AnalyticsService
from app.modules.finance.services.collection import CollectionService
from app.modules.finance.services.dues import DuesService
from app.modules.finance.services.expenses import ExpensesService
from app.modules.finance.services.rates import RatesService
from app.modules.finance.services.reserve import ReserveService


class FinanceService:
    """Orchestration facade over the finance concerns (one per request session)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = FinanceRepository(session)
        self.rates = RatesService(session, self._repo)
        self.dues = DuesService(session, self._repo)
        self.collection = CollectionService(session, self._repo)
        self.expenses = ExpensesService(session, self._repo)
        self.reserve = ReserveService(session, self._repo)
        self.analytics = AnalyticsService(session, self._repo)

    # --- inter-module contract shortcuts (docs §7) -------------------------

    def outstanding_dues(self, society_id: int, house_id: int) -> HouseDuesOut:
        """Public contract: a house's outstanding dues + total (docs §7)."""
        return self.collection.get_house_dues(society_id, house_id)

    def outstanding_total(self, society_id: int, house_id: int) -> Decimal:
        """Public contract: Σ of a house's outstanding dues (docs §7)."""
        return self.collection.outstanding_total(society_id, house_id)

    def has_dues(self, society_id: int, house_id: int) -> bool:
        """Public contract: any outstanding due? (Onboarding delete-guard §7)."""
        return self.dues.has_dues(society_id, house_id)

    def record_payment(
        self,
        society_id: int,
        house_id: int,
        req: PaymentRecordRequest,
        *,
        actor_user_id: int,
    ) -> PaymentOut:
        """Public contract: record a payment (docs §7)."""
        return self.collection.record_payment(
            society_id, house_id, req, actor_user_id=actor_user_id
        )

    def generate_due_cycle(
        self,
        society_id: int,
        *,
        as_of: date | None = None,
        actor_user_id: int | None = None,
    ) -> int:
        """Public contract: generate a society's due cycle (docs §7/§9)."""
        return self.dues.generate_due_cycle(
            society_id, as_of=as_of, actor_user_id=actor_user_id
        )

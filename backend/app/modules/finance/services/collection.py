"""Collection & prepaid concern (docs/modules/finance.md §4/§6 — Collection).

The "enter house number → see dues" read, oldest-first whole-month payment
allocation (no partial-within-month), prepaid blocks (arrears-first, locked rate,
house-tied), and payment void (re-open dues + reversing ledger entry). All writes
are frozen stubs — Wave C implements them.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    HouseDueOut,
    HouseDuesOut,
    PaymentOut,
    PaymentRecordRequest,
    PaymentVoidRequest,
    PrepaidRecordRequest,
)
from app.modules.finance.services.support import money


class CollectionService:
    def __init__(self, session: Session, repo: FinanceRepository) -> None:
        self._session = session
        self._repo = repo

    # --- reads (implemented in core) ---------------------------------------

    def outstanding_total(self, society_id: int, house_id: int) -> Decimal:
        """Σ of a house's outstanding dues (contract ``outstanding_dues`` §7)."""
        return money(
            sum(
                (d.amount_due for d in self._repo.outstanding_dues(society_id, house_id)),
                Decimal("0"),
            )
        )

    def get_house_dues(self, society_id: int, house_id: int) -> HouseDuesOut:
        """Outstanding months + total + full history for a house (docs §4/§6)."""
        all_dues = self._repo.dues_for_house(society_id, house_id)
        outstanding = [d for d in all_dues if d.status == "outstanding"]
        total = money(sum((d.amount_due for d in outstanding), Decimal("0")))
        return HouseDuesOut(
            house_id=house_id,
            outstanding=[HouseDueOut.model_validate(d) for d in outstanding],
            outstanding_total=total,
            history=[HouseDueOut.model_validate(d) for d in all_dues],
        )

    # --- writes (FROZEN — Wave C implements) -------------------------------

    def record_payment(
        self,
        society_id: int,
        house_id: int,
        req: PaymentRecordRequest,
        *,
        actor_user_id: int,
    ) -> PaymentOut:
        """Settle N oldest outstanding months (or all) for a house (docs §4/§6).

        Wave C, one transaction with the dues row-locked
        (``outstanding_dues(lock=True)``):
        - Determine the target months (``req.months`` oldest, or all when
          ``pay_all``); reject if none outstanding or ``months`` exceeds the
          outstanding count.
        - Whole months only (no partial): payment amount = Σ of settled dues.
        - Insert the ``payments`` row (``provider=admin_manual``), one
          ``payment_allocations`` row per settled due, flip each due to ``paid``
          (+ ``paid_at``), post a ``collection`` inflow ledger entry, audit
          ``finance.payment_recorded`` (house, amount, allocations).
        """
        raise NotImplementedError("Wave C: record_payment")

    def record_prepaid(
        self,
        society_id: int,
        house_id: int,
        req: PrepaidRecordRequest,
        *,
        actor_user_id: int,
    ) -> PaymentOut:
        """Buy a prepaid block covering the next N months (docs §4/§6).

        Wave C: validate ``months_count`` ∈ the society's ``prepaid_blocks`` config;
        REQUIRE arrears cleared first (reject if any outstanding due); lock the
        current rate; materialize the next N months' ``house_dues`` as
        ``source=prepaid`` + ``locked_rate`` + paid (skipping any already
        materialized); create the payment + ``prepaid_blocks`` row + a
        ``collection`` inflow; audit ``finance.prepaid_recorded`` (house, months,
        locked rate). Tied to the house.
        """
        raise NotImplementedError("Wave C: record_prepaid")

    def void_payment(
        self,
        society_id: int,
        payment_id: int,
        req: PaymentVoidRequest,
        *,
        actor_user_id: int,
    ) -> PaymentOut:
        """Void a recorded payment (docs §4 corrections/transparency).

        Wave C: reject if already voided; flip ``status=voided`` (+ voided_by/at/
        reason); re-open each allocated due (``paid`` → ``outstanding``, clear
        ``paid_at``); post a REVERSING ledger entry negating the original
        ``collection`` (both stay visible, flag original ``is_reversed``); audit
        ``finance.payment_voided`` (+ reason). The original + allocations are NOT
        deleted.
        """
        raise NotImplementedError("Wave C: void_payment")

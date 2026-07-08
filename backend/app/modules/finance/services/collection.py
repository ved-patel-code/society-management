"""Collection & prepaid concern (docs/modules/finance.md §4/§6 — Collection).

The "enter house number → see dues" read, oldest-first whole-month payment
allocation (no partial-within-month), prepaid blocks (arrears-first, locked rate,
house-tied), and payment void (re-open dues + reversing ledger entry). All writes
are frozen stubs — Wave C implements them.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.common.time import utcnow
from app.modules.finance.models import (
    HouseDue,
    Payment,
    PaymentAllocation,
    PrepaidBlock,
)
from app.modules.finance.periods import (
    add_months,
    due_date_for,
    period_key,
    period_of,
)
from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    HouseDueOut,
    HouseDuesOut,
    PaymentAllocationOut,
    PaymentOut,
    PaymentRecordRequest,
    PaymentVoidRequest,
    PrepaidRecordRequest,
)
from app.modules.finance.services.dues import DuesService
from app.modules.finance.services.support import (
    load_config,
    money,
    post_ledger_entry,
)
from app.platform.audit.service import AuditService


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
        # Exactly one of months / pay_all selects the scope.
        if req.pay_all == (req.months is not None):
            raise ValidationError(
                "Provide exactly one of 'months' or 'pay_all'."
            )

        # Row-lock the outstanding dues oldest-first (no concurrent double-settle).
        outstanding = self._repo.outstanding_dues(
            society_id, house_id, lock=True
        )
        if not outstanding:
            raise ValidationError("This house has no outstanding dues.")

        if req.pay_all:
            settled = outstanding
        else:
            assert req.months is not None
            if req.months > len(outstanding):
                raise ValidationError(
                    "Requested months exceed the outstanding count.",
                    details={
                        "requested": req.months,
                        "outstanding": len(outstanding),
                    },
                )
            settled = outstanding[: req.months]

        amount = money(sum((d.amount_due for d in settled), Decimal("0")))
        paid_at = self._paid_at(req.paid_at)

        payment = self._repo.add_payment(
            Payment(
                society_id=society_id,
                house_id=house_id,
                amount=amount,
                method=req.method,
                reference=req.reference,
                provider="admin_manual",
                status="recorded",
                recorded_by=actor_user_id,
                paid_at=paid_at,
            )
        )

        allocations: list[dict] = []
        for due in settled:
            self._repo.add_allocation(
                PaymentAllocation(
                    society_id=society_id,
                    payment_id=payment.id,
                    house_due_id=due.id,
                    amount_applied=due.amount_due,
                )
            )
            due.status = "paid"
            due.paid_at = paid_at
            allocations.append(
                {
                    "house_due_id": due.id,
                    "period": period_key(due.period_year, due.period_month),
                }
            )
        self._session.flush()

        post_ledger_entry(
            self._repo,
            society_id=society_id,
            entry_type="collection",
            direction="inflow",
            amount=amount,
            occurred_on=paid_at.date(),
            description=f"Collection for house {house_id} ({len(settled)} month(s))",
            source_type="payment",
            source_id=payment.id,
            recorded_by=actor_user_id,
        )

        AuditService(self._session).record(
            action="finance.payment_recorded",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="payment",
            entity_id=payment.id,
            after={
                "house_id": house_id,
                "amount": str(amount),
                "allocations": allocations,
            },
        )
        return self._payment_out(payment, settled)

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
        config = load_config(self._session, society_id)
        n = req.months_count
        if n not in config.prepaid_blocks:
            raise ValidationError(
                "months_count must be an allowed prepaid block.",
                details={"allowed": config.prepaid_blocks},
            )

        # Materialize any not-yet-generated past/current dues FIRST, so the arrears
        # check runs against the TRUE owed set (from first_left_empty_on), not just
        # rows the worker happens to have created. Dues are generated lazily on the
        # due day; without this, a mid-cycle prepaid could slip past real arrears
        # and the block window could overlap a month about to be accrued (spec §4:
        # "arrears cleared first"). Idempotent — creates nothing if already current.
        DuesService(self._session, self._repo).generate_due_cycle(
            society_id, actor_user_id=actor_user_id
        )

        # Arrears must be cleared before a prepaid block (spec §4).
        if self._repo.has_outstanding(society_id, house_id):
            raise ConflictError("Clear arrears first.")

        # Lock the current rate at purchase time (spec §4: locked rate).
        rate = self._repo.current_rate(society_id)
        if rate is None:
            raise ValidationError(
                "No maintenance rate is set; cannot lock a prepaid rate."
            )
        rate_locked = money(rate.amount)

        # The window is the next N months of FUTURE coverage (spec §4: "the next
        # N months"). Anchor at the current period, but never before the month
        # after the latest already-materialized due — so the block starts at
        # max(current_period, latest+1). Anchoring only at latest+1 (as an earlier
        # cut did) would land in the past when the last due is long paid, billing
        # historical months and leaving the upcoming ones uncovered.
        current = period_of(utcnow().date())
        existing = self._repo.existing_periods(society_id, house_id)
        if existing:
            nxt = add_months(*max(existing), 1)
            first_y, first_m = max((nxt, current), key=lambda p: period_key(*p))
        else:
            first_y, first_m = current

        months = [add_months(first_y, first_m, i) for i in range(n)]
        amount = money(rate_locked * n)
        paid_at = self._paid_at(req.paid_at)
        due_day = config.maintenance_due_day

        payment = self._repo.add_payment(
            Payment(
                society_id=society_id,
                house_id=house_id,
                amount=amount,
                method=req.method,
                reference=req.reference,
                provider="admin_manual",
                status="recorded",
                recorded_by=actor_user_id,
                paid_at=paid_at,
            )
        )

        first, last = months[0], months[-1]
        self._repo.add_prepaid_block(
            PrepaidBlock(
                society_id=society_id,
                house_id=house_id,
                months_count=n,
                rate_locked=rate_locked,
                payment_id=payment.id,
                start_period=period_key(*first),
                end_period=period_key(*last),
            )
        )

        settled: list[HouseDue] = []
        for (y, m) in months:
            # Defensive: skip any month that somehow already has a due (shouldn't
            # happen after the arrears + uncovered-window logic).
            if (y, m) in existing:
                continue
            due = self._repo.add_due(
                HouseDue(
                    society_id=society_id,
                    house_id=house_id,
                    period_year=y,
                    period_month=m,
                    amount_due=rate_locked,
                    due_date=due_date_for(y, m, due_day),
                    status="paid",
                    source="prepaid",
                    locked_rate=rate_locked,
                    paid_at=paid_at,
                )
            )
            self._repo.add_allocation(
                PaymentAllocation(
                    society_id=society_id,
                    payment_id=payment.id,
                    house_due_id=due.id,
                    amount_applied=rate_locked,
                )
            )
            settled.append(due)
        self._session.flush()

        post_ledger_entry(
            self._repo,
            society_id=society_id,
            entry_type="collection",
            direction="inflow",
            amount=amount,
            occurred_on=paid_at.date(),
            description=f"Prepaid block ({n} months) for house {house_id}",
            source_type="payment",
            source_id=payment.id,
            recorded_by=actor_user_id,
        )

        AuditService(self._session).record(
            action="finance.prepaid_recorded",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="payment",
            entity_id=payment.id,
            after={
                "house_id": house_id,
                "months": n,
                "rate_locked": str(rate_locked),
                "start_period": period_key(*first),
                "end_period": period_key(*last),
            },
        )
        return self._payment_out(payment, settled)

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
        payment = self._repo.get_payment(society_id, payment_id)
        if payment is None:
            raise NotFoundError("Payment not found.")
        if payment.status == "voided":
            raise ConflictError("Payment is already voided.")

        now = utcnow()
        payment.status = "voided"
        payment.voided_by = actor_user_id
        payment.voided_at = now
        payment.void_reason = req.reason

        # Re-open every allocated due (batch-load; no N+1). A prepaid-sourced due
        # is re-opened too — voiding unwinds the block, so its months revert to
        # outstanding. Reset those to a normal accrued obligation (clear the
        # prepaid source + locked rate) so we never leave an ``outstanding`` due
        # still flagged ``source=prepaid`` — otherwise reports would treat a
        # revoked block as active coverage.
        allocations = self._repo.allocations_for_payment(payment_id)
        due_ids = [a.house_due_id for a in allocations]
        dues = self._repo.dues_by_ids(society_id, due_ids)
        reopened: list[HouseDue] = []
        for due_id in due_ids:
            due = dues.get(due_id)
            if due is None:
                continue
            due.status = "outstanding"
            due.paid_at = None
            if due.source == "prepaid":
                due.source = "accrued"
                due.locked_rate = None
            reopened.append(due)

        # Drop the prepaid block itself (its coverage no longer exists). The money
        # trail is preserved by the payment + the reversal ledger entry below.
        block = self._repo.prepaid_block_for_payment(society_id, payment_id)
        if block is not None:
            self._repo.delete_prepaid_block(block)

        # Post a reversing entry negating the original collection (both visible).
        original = self._repo.collection_entry_for_payment(society_id, payment_id)
        if original is not None:
            opposite = "outflow" if original.direction == "inflow" else "inflow"
            post_ledger_entry(
                self._repo,
                society_id=society_id,
                entry_type="reversal",
                direction=opposite,
                amount=original.amount,
                # Date the reversal to the ORIGINAL collection's period (not today)
                # so per-period analytics net it against the entry it undoes —
                # matching the expense-void path and ledger_monthly_totals' month
                # bucketing. A cross-month void otherwise overstates the original
                # month and dumps a negative into the void month.
                occurred_on=original.occurred_on,
                description=f"Reversal of voided payment {payment_id}",
                source_type="payment",
                source_id=payment_id,
                recorded_by=actor_user_id,
                reverses_entry_id=original.id,
            )
            original.is_reversed = True
        self._session.flush()

        AuditService(self._session).record(
            action="finance.payment_voided",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="payment",
            entity_id=payment.id,
            before={"status": "recorded"},
            after={
                "status": "voided",
                "reason": req.reason,
                "amount": str(payment.amount),
                "reopened_due_ids": [d.id for d in reopened],
            },
        )
        return self._payment_out(payment, reopened)

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _paid_at(paid_on: object | None) -> datetime:
        """A timezone-aware datetime for a payment: the request date at UTC
        midnight, or ``utcnow()`` when none is supplied."""
        if paid_on is None:
            return utcnow()
        return datetime.combine(paid_on, time.min, tzinfo=timezone.utc)

    def _payment_out(
        self, payment: Payment, dues: list[HouseDue]
    ) -> PaymentOut:
        """Shape a ``PaymentOut`` with allocations joined to their dues so each
        carries its period (year/month)."""
        due_by_id = {d.id: d for d in dues}
        allocations = self._repo.allocations_for_payment(payment.id)
        alloc_out: list[PaymentAllocationOut] = []
        for a in allocations:
            due = due_by_id.get(a.house_due_id)
            alloc_out.append(
                PaymentAllocationOut(
                    id=a.id,
                    house_due_id=a.house_due_id,
                    amount_applied=a.amount_applied,
                    period_year=due.period_year if due else None,
                    period_month=due.period_month if due else None,
                )
            )
        out = PaymentOut.model_validate(payment)
        out.allocations = alloc_out
        return out

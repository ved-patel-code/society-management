"""Finance queries (docs/03 §2) — pure DB access, ``society_id``-scoped.

No business rules here; the service decides, the repository fetches/writes rows.
Every query is tenant-scoped by ``society_id`` (cross-tenant isolation — docs/PF
§7), selects only needed columns, and avoids N+1 (batched ``IN`` / aggregates
pushed to the DB — docs/03 §4).

FROZEN interface: wave sub-agents implement service logic against these
signatures. They may ADD methods but must not change existing ones. Row locks
(``with_for_update``) guard the payment-allocation path against concurrent
double-settlement (docs/03 §4).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from app.modules.finance.models import (
    Expense,
    ExpenseCategory,
    HouseDue,
    LedgerEntry,
    MaintenanceRate,
    Payment,
    PaymentAllocation,
    PrepaidBlock,
)


class FinanceRepository:
    """Queries + row writes over the eight finance tables."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- maintenance_rates -------------------------------------------------

    def current_rate(self, society_id: int) -> MaintenanceRate | None:
        """The latest-effective rate as of today (highest ``valid_from``)."""
        return self._session.execute(
            select(MaintenanceRate)
            .where(MaintenanceRate.society_id == society_id)
            .order_by(MaintenanceRate.valid_from.desc())
            .limit(1)
        ).scalar_one_or_none()

    def rate_for_month(
        self, society_id: int, first_of_month: date
    ) -> MaintenanceRate | None:
        """Rate whose ``valid_from`` is the latest ``<= first_of_month`` (docs §4)."""
        return self._session.execute(
            select(MaintenanceRate)
            .where(
                MaintenanceRate.society_id == society_id,
                MaintenanceRate.valid_from <= first_of_month,
            )
            .order_by(MaintenanceRate.valid_from.desc())
            .limit(1)
        ).scalar_one_or_none()

    def rate_history(self, society_id: int) -> list[MaintenanceRate]:
        """All rates for the society, newest first (docs §6)."""
        return list(
            self._session.execute(
                select(MaintenanceRate)
                .where(MaintenanceRate.society_id == society_id)
                .order_by(MaintenanceRate.valid_from.desc())
            )
            .scalars()
            .all()
        )

    def rate_at_valid_from(
        self, society_id: int, valid_from: date
    ) -> MaintenanceRate | None:
        """Exact rate row for a ``valid_from`` (uniqueness pre-check)."""
        return self._session.execute(
            select(MaintenanceRate).where(
                MaintenanceRate.society_id == society_id,
                MaintenanceRate.valid_from == valid_from,
            )
        ).scalar_one_or_none()

    def add_rate(self, rate: MaintenanceRate) -> MaintenanceRate:
        self._session.add(rate)
        self._session.flush()
        return rate

    # --- house_dues --------------------------------------------------------

    def get_due(
        self, society_id: int, house_id: int, year: int, month: int
    ) -> HouseDue | None:
        return self._session.execute(
            select(HouseDue).where(
                HouseDue.society_id == society_id,
                HouseDue.house_id == house_id,
                HouseDue.period_year == year,
                HouseDue.period_month == month,
            )
        ).scalar_one_or_none()

    def existing_periods(
        self, society_id: int, house_id: int
    ) -> set[tuple[int, int]]:
        """The (year, month) periods a house already has dues for (idempotency)."""
        rows = self._session.execute(
            select(HouseDue.period_year, HouseDue.period_month).where(
                HouseDue.society_id == society_id,
                HouseDue.house_id == house_id,
            )
        ).all()
        return {(int(r[0]), int(r[1])) for r in rows}

    def outstanding_dues(
        self, society_id: int, house_id: int, *, lock: bool = False
    ) -> list[HouseDue]:
        """A house's outstanding dues, OLDEST-FIRST (docs §4 allocation order).

        ``lock=True`` takes a row lock (``FOR UPDATE``) so concurrent payments
        can't both settle the same month (docs/03 §4).
        """
        stmt: Select = (
            select(HouseDue)
            .where(
                HouseDue.society_id == society_id,
                HouseDue.house_id == house_id,
                HouseDue.status == "outstanding",
            )
            .order_by(HouseDue.period_year, HouseDue.period_month)
        )
        if lock:
            stmt = stmt.with_for_update()
        return list(self._session.execute(stmt).scalars().all())

    def dues_for_house(
        self, society_id: int, house_id: int
    ) -> list[HouseDue]:
        """All of a house's dues, oldest-first (outstanding + history)."""
        return list(
            self._session.execute(
                select(HouseDue)
                .where(
                    HouseDue.society_id == society_id,
                    HouseDue.house_id == house_id,
                )
                .order_by(HouseDue.period_year, HouseDue.period_month)
            )
            .scalars()
            .all()
        )

    def has_outstanding(self, society_id: int, house_id: int) -> bool:
        """Whether a house has any outstanding due (arrears / delete-guard)."""
        return (
            self._session.execute(
                select(HouseDue.id)
                .where(
                    HouseDue.society_id == society_id,
                    HouseDue.house_id == house_id,
                    HouseDue.status == "outstanding",
                )
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    def add_due(self, due: HouseDue) -> HouseDue:
        self._session.add(due)
        self._session.flush()
        return due

    # --- payments / allocations -------------------------------------------

    def get_payment(self, society_id: int, payment_id: int) -> Payment | None:
        return self._session.execute(
            select(Payment).where(
                Payment.id == payment_id,
                Payment.society_id == society_id,
            )
        ).scalar_one_or_none()

    def add_payment(self, payment: Payment) -> Payment:
        self._session.add(payment)
        self._session.flush()
        return payment

    def add_allocation(self, allocation: PaymentAllocation) -> PaymentAllocation:
        self._session.add(allocation)
        self._session.flush()
        return allocation

    def allocations_for_payment(
        self, payment_id: int
    ) -> list[PaymentAllocation]:
        return list(
            self._session.execute(
                select(PaymentAllocation)
                .where(PaymentAllocation.payment_id == payment_id)
                .order_by(PaymentAllocation.id)
            )
            .scalars()
            .all()
        )

    def dues_by_ids(
        self, society_id: int, due_ids: list[int]
    ) -> dict[int, HouseDue]:
        """Batch-load dues by id for a society (void re-open — no N+1)."""
        if not due_ids:
            return {}
        rows = (
            self._session.execute(
                select(HouseDue).where(
                    HouseDue.society_id == society_id,
                    HouseDue.id.in_(due_ids),
                )
            )
            .scalars()
            .all()
        )
        return {d.id: d for d in rows}

    # --- prepaid_blocks ----------------------------------------------------

    def add_prepaid_block(self, block: PrepaidBlock) -> PrepaidBlock:
        self._session.add(block)
        self._session.flush()
        return block

    # --- expense_categories ------------------------------------------------

    def list_categories(self, society_id: int) -> list[ExpenseCategory]:
        return list(
            self._session.execute(
                select(ExpenseCategory)
                .where(ExpenseCategory.society_id == society_id)
                .order_by(ExpenseCategory.name)
            )
            .scalars()
            .all()
        )

    def category_by_name(
        self, society_id: int, name: str
    ) -> ExpenseCategory | None:
        return self._session.execute(
            select(ExpenseCategory).where(
                ExpenseCategory.society_id == society_id,
                ExpenseCategory.name == name,
            )
        ).scalar_one_or_none()

    def get_category(
        self, society_id: int, category_id: int
    ) -> ExpenseCategory | None:
        return self._session.execute(
            select(ExpenseCategory).where(
                ExpenseCategory.id == category_id,
                ExpenseCategory.society_id == society_id,
            )
        ).scalar_one_or_none()

    def add_category(self, category: ExpenseCategory) -> ExpenseCategory:
        self._session.add(category)
        self._session.flush()
        return category

    def count_categories(self, society_id: int) -> int:
        return int(
            self._session.execute(
                select(func.count())
                .select_from(ExpenseCategory)
                .where(ExpenseCategory.society_id == society_id)
            ).scalar_one()
        )

    # --- expenses ----------------------------------------------------------

    def get_expense(self, society_id: int, expense_id: int) -> Expense | None:
        return self._session.execute(
            select(Expense).where(
                Expense.id == expense_id,
                Expense.society_id == society_id,
            )
        ).scalar_one_or_none()

    def list_expenses(
        self,
        society_id: int,
        *,
        offset: int,
        limit: int,
        include_voided: bool = True,
    ) -> tuple[list[Expense], int]:
        conditions = [Expense.society_id == society_id]
        if not include_voided:
            conditions.append(Expense.status == "recorded")
        total = self._session.execute(
            select(func.count()).select_from(Expense).where(*conditions)
        ).scalar_one()
        rows = (
            self._session.execute(
                select(Expense)
                .where(*conditions)
                .order_by(Expense.incurred_on.desc(), Expense.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), int(total)

    def add_expense(self, expense: Expense) -> Expense:
        self._session.add(expense)
        self._session.flush()
        return expense

    # --- ledger_entries ----------------------------------------------------

    def get_ledger_entry(
        self, society_id: int, entry_id: int
    ) -> LedgerEntry | None:
        return self._session.execute(
            select(LedgerEntry).where(
                LedgerEntry.id == entry_id,
                LedgerEntry.society_id == society_id,
            )
        ).scalar_one_or_none()

    def add_ledger_entry(self, entry: LedgerEntry) -> LedgerEntry:
        self._session.add(entry)
        self._session.flush()
        return entry

    def reserve_balance(self, society_id: int) -> Decimal:
        """Σ inflow − Σ outflow over ALL entries (computed reserve — docs §4).

        Pushed to the DB (one aggregate query, no row pull). Reversals are ordinary
        negating entries, so they're naturally included.
        """
        inflow = func.coalesce(
            func.sum(LedgerEntry.amount).filter(
                LedgerEntry.direction == "inflow"
            ),
            0,
        )
        outflow = func.coalesce(
            func.sum(LedgerEntry.amount).filter(
                LedgerEntry.direction == "outflow"
            ),
            0,
        )
        result = self._session.execute(
            select((inflow - outflow)).where(LedgerEntry.society_id == society_id)
        ).scalar_one()
        return Decimal(result)

    def list_ledger(
        self, society_id: int, *, offset: int, limit: int
    ) -> tuple[list[LedgerEntry], int]:
        """Full ledger history newest-first, incl. reversals (docs §4/§6)."""
        total = self._session.execute(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.society_id == society_id)
        ).scalar_one()
        rows = (
            self._session.execute(
                select(LedgerEntry)
                .where(LedgerEntry.society_id == society_id)
                .order_by(LedgerEntry.occurred_on.desc(), LedgerEntry.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), int(total)

    # --- analytics aggregates (pushed to the DB — docs/03 §4) --------------

    def collection_totals(
        self,
        society_id: int,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[Decimal, Decimal]:
        """(expected, collected) across dues, optionally a single period.

        expected = Σ amount_due; collected = Σ amount_due where status=paid.
        """
        conditions = [HouseDue.society_id == society_id]
        if year is not None:
            conditions.append(HouseDue.period_year == year)
        if month is not None:
            conditions.append(HouseDue.period_month == month)
        expected = func.coalesce(func.sum(HouseDue.amount_due), 0)
        collected = func.coalesce(
            func.sum(HouseDue.amount_due).filter(HouseDue.status == "paid"), 0
        )
        row = self._session.execute(
            select(expected, collected).where(and_(*conditions))
        ).one()
        return Decimal(row[0]), Decimal(row[1])

    def collection_by_house(
        self,
        society_id: int,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> list[tuple[int, Decimal, Decimal]]:
        """Per-house (house_id, expected, collected) (docs §4 collection summary)."""
        conditions = [HouseDue.society_id == society_id]
        if year is not None:
            conditions.append(HouseDue.period_year == year)
        if month is not None:
            conditions.append(HouseDue.period_month == month)
        expected = func.coalesce(func.sum(HouseDue.amount_due), 0)
        collected = func.coalesce(
            func.sum(HouseDue.amount_due).filter(HouseDue.status == "paid"), 0
        )
        rows = self._session.execute(
            select(HouseDue.house_id, expected, collected)
            .where(and_(*conditions))
            .group_by(HouseDue.house_id)
            .order_by(HouseDue.house_id)
        ).all()
        return [(int(r[0]), Decimal(r[1]), Decimal(r[2])) for r in rows]

    def arrears_by_house(
        self, society_id: int
    ) -> list[tuple[int, Decimal, int, int, int]]:
        """Per-house arrears: (house_id, outstanding_total, oldest_y, oldest_m,
        months_outstanding) for houses with any outstanding due (docs §4)."""
        rows = self._session.execute(
            select(
                HouseDue.house_id,
                func.coalesce(func.sum(HouseDue.amount_due), 0),
                func.min(HouseDue.period_year * 100 + HouseDue.period_month),
                func.count(),
            )
            .where(
                HouseDue.society_id == society_id,
                HouseDue.status == "outstanding",
            )
            .group_by(HouseDue.house_id)
            .order_by(HouseDue.house_id)
        ).all()
        out: list[tuple[int, Decimal, int, int, int]] = []
        for r in rows:
            oldest = int(r[2])
            out.append(
                (int(r[0]), Decimal(r[1]), oldest // 100, oldest % 100, int(r[3]))
            )
        return out

    def expense_by_category(
        self,
        society_id: int,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> list[tuple[int, str, Decimal]]:
        """(category_id, name, total) for RECORDED expenses (docs §4)."""
        conditions = [
            Expense.society_id == society_id,
            Expense.status == "recorded",
        ]
        if year is not None:
            conditions.append(func.extract("year", Expense.incurred_on) == year)
        if month is not None:
            conditions.append(func.extract("month", Expense.incurred_on) == month)
        rows = self._session.execute(
            select(
                ExpenseCategory.id,
                ExpenseCategory.name,
                func.coalesce(func.sum(Expense.amount), 0),
            )
            .join(Expense, Expense.category_id == ExpenseCategory.id)
            .where(and_(*conditions))
            .group_by(ExpenseCategory.id, ExpenseCategory.name)
            .order_by(ExpenseCategory.name)
        ).all()
        return [(int(r[0]), str(r[1]), Decimal(r[2])) for r in rows]

    def total_by_entry_type(
        self,
        society_id: int,
        entry_type: str,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> Decimal:
        """Σ amount for a ledger entry_type (income/collection/expense), optional
        period. Reversals are separate rows, so nets fall out of the ledger."""
        conditions = [
            LedgerEntry.society_id == society_id,
            LedgerEntry.entry_type == entry_type,
        ]
        if year is not None:
            conditions.append(func.extract("year", LedgerEntry.occurred_on) == year)
        if month is not None:
            conditions.append(
                func.extract("month", LedgerEntry.occurred_on) == month
            )
        result = self._session.execute(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                and_(*conditions)
            )
        ).scalar_one()
        return Decimal(result)

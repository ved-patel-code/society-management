"""Expenses & income concern (docs/modules/finance.md §4/§6 — Expenses).

Expense categories (seeded defaults + extendable), expense record/list, and
expense void (posts a reversal). Reads are implemented; writes are frozen stubs —
Wave D implements them.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError
from app.common.time import utcnow
from app.modules.finance.models import Expense, ExpenseCategory
from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    ExpenseCategoryCreateRequest,
    ExpenseCategoryOut,
    ExpenseCreateRequest,
    ExpenseOut,
    ExpenseVoidRequest,
)
from app.modules.finance.services.support import (
    ensure_default_categories,
    money,
    post_ledger_entry,
)
from app.platform.audit.service import AuditService


class ExpensesService:
    def __init__(self, session: Session, repo: FinanceRepository) -> None:
        self._session = session
        self._repo = repo

    # --- reads (implemented in core) ---------------------------------------

    def list_categories(self, society_id: int) -> list[ExpenseCategoryOut]:
        """All categories (seeds the system defaults on first access) (docs §3)."""
        ensure_default_categories(self._session, society_id, self._repo)
        return [
            ExpenseCategoryOut.model_validate(c)
            for c in self._repo.list_categories(society_id)
        ]

    def list_expenses(
        self, society_id: int, *, offset: int, limit: int
    ) -> tuple[list[ExpenseOut], int]:
        """Paginated expense list, newest-first, incl. voided (docs §4/§6)."""
        rows, total = self._repo.list_expenses(
            society_id, offset=offset, limit=limit
        )
        return [ExpenseOut.model_validate(e) for e in rows], total

    # --- writes (FROZEN — Wave D implements) -------------------------------

    def add_category(
        self,
        society_id: int,
        req: ExpenseCategoryCreateRequest,
        *,
        actor_user_id: int,
    ) -> ExpenseCategoryOut:
        """Add a society expense category (docs §4/§6).

        Wave D: ensure defaults seeded; reject a duplicate name (unique per
        society); insert (``is_system=false``); audit ``finance.category_added``.
        """
        # Seed the system defaults on first use (idempotent) so the new custom
        # category can't accidentally duplicate a not-yet-seeded system name.
        ensure_default_categories(self._session, society_id, self._repo)

        # Case-sensitive uniqueness per the UNIQUE index; pre-check for a clean 409.
        if self._repo.category_by_name(society_id, req.name) is not None:
            raise ConflictError(
                f"An expense category named '{req.name}' already exists."
            )

        category = self._repo.add_category(
            ExpenseCategory(
                society_id=society_id, name=req.name, is_system=False
            )
        )
        AuditService(self._session).record(
            action="finance.category_added",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="expense_category",
            entity_id=category.id,
            after={"name": category.name, "category_id": category.id},
        )
        return ExpenseCategoryOut.model_validate(category)

    def record_expense(
        self, society_id: int, req: ExpenseCreateRequest, *, actor_user_id: int
    ) -> ExpenseOut:
        """Record an expense (docs §4/§6).

        Wave D: validate the category belongs to the society; insert the
        ``expenses`` row (``status=recorded``); post an ``expense`` OUTFLOW ledger
        entry (``occurred_on = incurred_on``); audit ``finance.expense_recorded``.
        """
        # The category must exist and belong to THIS society (tenant isolation).
        category = self._repo.get_category(society_id, req.category_id)
        if category is None:
            raise NotFoundError(
                f"Expense category {req.category_id} was not found."
            )

        amount = money(req.amount)
        expense = self._repo.add_expense(
            Expense(
                society_id=society_id,
                category_id=req.category_id,
                amount=amount,
                description=req.description,
                incurred_on=req.incurred_on,
                recorded_by=actor_user_id,
                status="recorded",
            )
        )

        # One expense OUTFLOW ledger entry — the single money-movement choke-point.
        post_ledger_entry(
            self._repo,
            society_id=society_id,
            entry_type="expense",
            direction="outflow",
            amount=amount,
            occurred_on=req.incurred_on,
            description=req.description,
            source_type="expense",
            source_id=expense.id,
            recorded_by=actor_user_id,
        )

        AuditService(self._session).record(
            action="finance.expense_recorded",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="expense",
            entity_id=expense.id,
            after={
                "category_id": req.category_id,
                "amount": str(amount),
                "incurred_on": req.incurred_on.isoformat(),
            },
        )
        return ExpenseOut.model_validate(expense)

    def void_expense(
        self,
        society_id: int,
        expense_id: int,
        req: ExpenseVoidRequest,
        *,
        actor_user_id: int,
    ) -> ExpenseOut:
        """Void an expense (docs §4 corrections/transparency).

        Wave D: reject if already voided; flip ``status=voided`` (+ voided_by/at/
        reason); post a REVERSING ledger entry negating the original ``expense``
        (both visible, flag original ``is_reversed``); audit
        ``finance.expense_voided`` (+ reason).
        """
        expense = self._repo.get_expense(society_id, expense_id)
        if expense is None:
            raise NotFoundError(f"Expense {expense_id} was not found.")
        if expense.status == "voided":
            raise ConflictError("This expense is already voided.")

        expense.status = "voided"
        expense.voided_by = actor_user_id
        expense.voided_at = utcnow()
        expense.void_reason = req.reason
        self._session.flush()

        # Reverse the original expense outflow with a negating INFLOW entry; both
        # rows stay visible and the original is flagged ``is_reversed`` (docs §4).
        original = self._repo.expense_entry_for_expense(society_id, expense.id)
        if original is not None:
            post_ledger_entry(
                self._repo,
                society_id=society_id,
                entry_type="reversal",
                direction="inflow",
                amount=original.amount,
                occurred_on=original.occurred_on,
                description=f"Reversal of expense {expense.id}: {req.reason}",
                source_type="expense",
                source_id=expense.id,
                recorded_by=actor_user_id,
                reverses_entry_id=original.id,
            )
            original.is_reversed = True
            self._session.flush()

        AuditService(self._session).record(
            action="finance.expense_voided",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="expense",
            entity_id=expense.id,
            after={"status": "voided", "reason": req.reason},
        )
        return ExpenseOut.model_validate(expense)

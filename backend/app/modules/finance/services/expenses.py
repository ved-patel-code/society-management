"""Expenses & income concern (docs/modules/finance.md §4/§6 — Expenses).

Expense categories (seeded defaults + extendable), expense record/list, and
expense void (posts a reversal). Reads are implemented; writes are frozen stubs —
Wave D implements them.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    ExpenseCategoryCreateRequest,
    ExpenseCategoryOut,
    ExpenseCreateRequest,
    ExpenseOut,
    ExpenseVoidRequest,
)
from app.modules.finance.services.support import ensure_default_categories


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
        raise NotImplementedError("Wave D: add_category")

    def record_expense(
        self, society_id: int, req: ExpenseCreateRequest, *, actor_user_id: int
    ) -> ExpenseOut:
        """Record an expense (docs §4/§6).

        Wave D: validate the category belongs to the society; insert the
        ``expenses`` row (``status=recorded``); post an ``expense`` OUTFLOW ledger
        entry (``occurred_on = incurred_on``); audit ``finance.expense_recorded``.
        """
        raise NotImplementedError("Wave D: record_expense")

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
        raise NotImplementedError("Wave D: void_expense")

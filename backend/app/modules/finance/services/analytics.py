"""Analytics concern (docs/modules/finance.md §4/§6 — Analytics).

Read-time projections over the finance tables (aggregates pushed to the DB —
docs/03 §4); nothing persisted. Five views: collection summary, arrears, expenses
by category, income/net, and month-over-month trends. All are frozen stubs —
Wave F implements them.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    ArrearsOut,
    CollectionSummaryOut,
    ExpensesAnalyticsOut,
    IncomeAnalyticsOut,
    TrendsOut,
)


class AnalyticsService:
    def __init__(self, session: Session, repo: FinanceRepository) -> None:
        self._session = session
        self._repo = repo

    # --- reads (FROZEN — Wave F implements) --------------------------------

    def collection(
        self, society_id: int, *, year: int | None, month: int | None
    ) -> CollectionSummaryOut:
        """Expected vs collected vs outstanding, society + per house (docs §4).

        Wave F: use ``collection_totals`` + ``collection_by_house`` (optionally
        period-filtered); outstanding = expected − collected per line and overall.
        """
        raise NotImplementedError("Wave F: collection")

    def arrears(self, society_id: int) -> ArrearsOut:
        """Houses in arrears with their outstanding total + oldest period (docs §4).

        Wave F: use ``arrears_by_house``; total = Σ outstanding.
        """
        raise NotImplementedError("Wave F: arrears")

    def expenses(
        self, society_id: int, *, year: int | None, month: int | None
    ) -> ExpensesAnalyticsOut:
        """Expense-by-category + total for RECORDED expenses (docs §4).

        Wave F: use ``expense_by_category`` (optionally period-filtered).
        """
        raise NotImplementedError("Wave F: expenses")

    def income(
        self, society_id: int, *, year: int | None, month: int | None
    ) -> IncomeAnalyticsOut:
        """Income + collection − expense = net, from the ledger (docs §4).

        Wave F: use ``total_by_entry_type`` for income / collection / expense
        (optional period); net = income + collection − expense.
        """
        raise NotImplementedError("Wave F: income")

    def trends(self, society_id: int) -> TrendsOut:
        """Month-over-month collected / expense / net (docs §4).

        Wave F: aggregate the ledger by (year, month) of ``occurred_on`` for
        collection & expense; net per point = collected − expense; newest last.
        """
        raise NotImplementedError("Wave F: trends")

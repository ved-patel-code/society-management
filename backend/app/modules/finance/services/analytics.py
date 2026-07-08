"""Analytics concern (docs/modules/finance.md §4/§6 — Analytics).

Read-time projections over the finance tables (aggregates pushed to the DB —
docs/03 §4); nothing persisted. Five views: collection summary, arrears, expenses
by category, income/net, and month-over-month trends. Wave F implements them
against the repository's DB-side aggregates — no Python row loops for summing.

Reversals (docs §4 transparency): a void posts a separate negating ``reversal``
row, so a naive Σ over one ``entry_type`` is GROSS of reversals. Income/net and
trends therefore net reversals back against the type they undo (via
``reversal_totals_by_reversed_type`` / ``ledger_monthly_totals``) so a voided
payment or expense is reflected honestly — while the underlying ledger keeps both
rows visible.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.finance.repository import FinanceRepository
from app.modules.finance.schemas import (
    ArrearsLineOut,
    ArrearsOut,
    CollectionLineOut,
    CollectionSummaryOut,
    ExpenseCategoryBreakdownOut,
    ExpensesAnalyticsOut,
    IncomeAnalyticsOut,
    TrendPointOut,
    TrendsOut,
)
from app.modules.finance.services.support import money


class AnalyticsService:
    def __init__(self, session: Session, repo: FinanceRepository) -> None:
        self._session = session
        self._repo = repo

    # --- reads (Wave F) ----------------------------------------------------

    def collection(
        self, society_id: int, *, year: int | None, month: int | None
    ) -> CollectionSummaryOut:
        """Expected vs collected vs outstanding, society + per house (docs §4)."""
        expected, collected = self._repo.collection_totals(
            society_id, year=year, month=month
        )
        per_house = [
            CollectionLineOut(
                house_id=house_id,
                expected=money(exp),
                collected=money(col),
                outstanding=money(exp - col),
            )
            for house_id, exp, col in self._repo.collection_by_house(
                society_id, year=year, month=month
            )
        ]
        return CollectionSummaryOut(
            period_year=year,
            period_month=month,
            expected=money(expected),
            collected=money(collected),
            outstanding=money(expected - collected),
            per_house=per_house,
        )

    def arrears(self, society_id: int) -> ArrearsOut:
        """Houses in arrears with outstanding total + oldest period (docs §4)."""
        rows = self._repo.arrears_by_house(society_id)
        houses = [
            ArrearsLineOut(
                house_id=house_id,
                outstanding_total=money(outstanding_total),
                oldest_period_year=oldest_year,
                oldest_period_month=oldest_month,
                months_outstanding=months,
            )
            for (
                house_id,
                outstanding_total,
                oldest_year,
                oldest_month,
                months,
            ) in rows
        ]
        total = money(
            sum((h.outstanding_total for h in houses), Decimal("0"))
        )
        return ArrearsOut(total_outstanding=total, houses=houses)

    def expenses(
        self, society_id: int, *, year: int | None, month: int | None
    ) -> ExpensesAnalyticsOut:
        """Expense-by-category + total for RECORDED expenses only (docs §4).

        The repo already filters ``status='recorded'``, so voided expenses are
        excluded from both the breakdown and the total.
        """
        breakdown = [
            ExpenseCategoryBreakdownOut(
                category_id=category_id,
                category_name=name,
                total=money(total),
            )
            for category_id, name, total in self._repo.expense_by_category(
                society_id, year=year, month=month
            )
        ]
        total_expense = money(
            sum((b.total for b in breakdown), Decimal("0"))
        )
        return ExpensesAnalyticsOut(
            period_year=year,
            period_month=month,
            total_expense=total_expense,
            by_category=breakdown,
        )

    def income(
        self, society_id: int, *, year: int | None, month: int | None
    ) -> IncomeAnalyticsOut:
        """Income + collection − expense = net, NET of reversals (docs §4).

        Per-type ledger sums (``total_by_entry_type``) are gross: a void posts a
        separate ``reversal`` row that a single-type Σ never subtracts. So we pull
        the reversal totals attributed back to the type they undid and net them
        out — a voided payment lowers collection, a voided expense lowers expense —
        before computing ``net``. The reported per-type totals here are the NET
        figures (what actually stands after corrections), consistent with the
        computed reserve balance.
        """
        gross_income = self._repo.total_by_entry_type(
            society_id, "income", year=year, month=month
        )
        gross_collection = self._repo.total_by_entry_type(
            society_id, "collection", year=year, month=month
        )
        gross_expense = self._repo.total_by_entry_type(
            society_id, "expense", year=year, month=month
        )
        reversed_by_type = self._repo.reversal_totals_by_reversed_type(
            society_id, year=year, month=month
        )
        total_income = money(
            gross_income - reversed_by_type.get("income", Decimal("0"))
        )
        total_collection = money(
            gross_collection - reversed_by_type.get("collection", Decimal("0"))
        )
        total_expense = money(
            gross_expense - reversed_by_type.get("expense", Decimal("0"))
        )
        net = money(total_income + total_collection - total_expense)
        return IncomeAnalyticsOut(
            period_year=year,
            period_month=month,
            total_income=total_income,
            total_collection=total_collection,
            total_expense=total_expense,
            net=net,
        )

    def trends(self, society_id: int) -> TrendsOut:
        """Month-over-month collected / expense / net, oldest→newest (docs §4).

        One DB aggregate over the ledger's ``occurred_on``; collected/expense are
        already net of reversals (a voided month is reduced). ``net`` per point =
        collected − expense.
        """
        points = [
            TrendPointOut(
                period_year=year,
                period_month=month,
                collected=money(collected),
                expense=money(expense),
                net=money(collected - expense),
            )
            for year, month, collected, expense in self._repo.ledger_monthly_totals(
                society_id
            )
        ]
        return TrendsOut(points=points)

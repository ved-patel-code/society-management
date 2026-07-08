"""Dues generation concern (docs/modules/finance.md §4/§9 — Dues generation).

Materialize per-house monthly ``house_dues`` at the effective rate on the
society's due day: idempotent (skips existing / prepaid-covered months), backfills
missing past months from ``first_left_empty_on``. Callable standalone AND from the
worker. ``generate_due_cycle`` is a frozen stub — Wave B implements it.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.common.time import utcnow
from app.modules.finance.models import HouseDue
from app.modules.finance.periods import due_date_for, month_range, period_of
from app.modules.finance.repository import FinanceRepository
from app.modules.finance.services.rates import RatesService
from app.modules.finance.services.support import load_config, money
from app.modules.houses.service import HouseService


class DuesService:
    def __init__(self, session: Session, repo: FinanceRepository) -> None:
        self._session = session
        self._repo = repo
        self._rates = RatesService(session, repo)

    # --- reads (implemented in core) ---------------------------------------

    def has_dues(self, society_id: int, house_id: int) -> bool:
        """Whether a house has any outstanding due (delete-guard contract §7)."""
        return self._repo.has_outstanding(society_id, house_id)

    # --- writes (FROZEN — Wave B implements) -------------------------------

    def generate_due_cycle(
        self,
        society_id: int,
        *,
        as_of: date | None = None,
        actor_user_id: int | None = None,
    ) -> int:
        """Generate the current period's dues for all dues-owing houses (docs §4/§9).

        Wave B, in one transaction (idempotent, safe to re-run):
        - Resolve ``maintenance_due_day`` from :func:`load_config`; ``as_of``
          defaults to today (worker passes the run date).
        - For each House & Occupancy ``houses_owing`` house
          ``(house_id, first_left_empty_on)``: for every month from
          ``first_left_empty_on`` (or the current month if unset) up to the
          current period, create a ``house_dues`` row at the month's effective
          rate (``RatesService.rate_amount_for_month``) with
          ``due_date = due_date_for(period, due_day)`` — SKIPPING periods that
          already exist (``existing_periods``) or are prepaid-covered.
        - Return the count of dues rows created. No rate set → create nothing.

        Reached via the House service interface; never reads house tables directly.
        """
        due_day = load_config(self._session, society_id).maintenance_due_day
        as_of = as_of or utcnow().date()
        current_period = period_of(as_of)

        created = 0
        for house_id, first_left_empty_on in HouseService(
            self._session
        ).houses_owing(society_id):
            start = (
                period_of(first_left_empty_on)
                if first_left_empty_on is not None
                else current_period
            )
            # One existing-periods fetch per house (idempotency / no N+1); it also
            # covers prepaid-materialized months.
            existing = self._repo.existing_periods(society_id, house_id)
            for year, month in month_range(start, current_period):
                if (year, month) in existing:
                    continue
                rate = self._rates.rate_amount_for_month(
                    society_id, date(year, month, 1)
                )
                if rate is None:
                    # No rate set for/before this month — can't bill it. Skip.
                    continue
                self._repo.add_due(
                    HouseDue(
                        society_id=society_id,
                        house_id=house_id,
                        period_year=year,
                        period_month=month,
                        amount_due=money(rate),
                        due_date=due_date_for(year, month, due_day),
                        status="outstanding",
                        source="accrued",
                    )
                )
                created += 1

        return created

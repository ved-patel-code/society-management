"""Finance worker jobs (docs/modules/finance.md §9).

Runs in the ``worker`` service. A DAILY scan generates dues per society on its
``maintenance_due_day`` (idempotent, backfills). Owns its own ``SessionLocal`` +
commit (not a request session). Mirrors ``app/modules/vault/services/jobs.py``:
open one ``SessionLocal``, do the work with per-item failure isolation, and
close in a ``finally``.

Also callable on demand (docs §9): the same materialization is reachable via
``app.modules.finance.api.generate_due_cycle``; this job is just the scheduled
driver over every finance-enabled society.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.time import utcnow
from app.core.db import SessionLocal
from app.modules.finance.service import FinanceService
from app.modules.finance.services.support import MODULE_KEY, load_config
from app.platform.models import SocietyModule

logger = logging.getLogger("app.worker.finance")


def _enabled_finance_society_ids(session: Session) -> list[int]:
    """Society ids with the finance module enabled (docs §9)."""
    return list(
        session.execute(
            select(SocietyModule.society_id).where(
                SocietyModule.module_key == MODULE_KEY,
                SocietyModule.enabled.is_(True),
            )
        ).scalars()
    )


def _run_for_societies(
    session: Session, society_ids: list[int], as_of: date
) -> dict[str, int]:
    """Per-society dues generation for the given run date (docs §9).

    Failure isolation + commit choice: **commit after each successful society,
    rollback on failure** — mirroring how ``vault/services/jobs.py`` isolates
    per item. A commit-once-at-the-end strategy would let one poisoned society
    discard every prior society's work when it rolls back; committing per society
    means a mid-scan failure never taints an already-processed society, and a
    ``rollback()`` on error hands the next society a clean session. ``as_of`` is
    passed explicitly so tests (and backfills) drive the calendar deterministically.

    Only societies whose ``maintenance_due_day`` equals ``as_of.day`` are billed
    this run (idempotent — ``generate_due_cycle`` skips already-materialized
    periods, so a duplicate run creates 0). Returns a run summary.
    """
    processed = 0
    total_created = 0
    for society_id in society_ids:
        try:
            cfg = load_config(session, society_id)
            if as_of.day != cfg.maintenance_due_day:
                continue
            created = FinanceService(session).generate_due_cycle(
                society_id, as_of=as_of, actor_user_id=None
            )
            session.commit()
            processed += 1
            total_created += created
            logger.info(
                "finance dues: society=%s created=%d (as_of=%s)",
                society_id,
                created,
                as_of.isoformat(),
            )
        except Exception:
            # One society's failure must not abort the others. Roll back so the
            # poisoned unit of work can't taint the next society's session.
            session.rollback()
            logger.exception(
                "finance dues: society=%s FAILED (as_of=%s), skipping",
                society_id,
                as_of.isoformat(),
            )
            continue
    return {"societies_processed": processed, "dues_created": total_created}


def run_daily_dues_generation() -> dict[str, int]:
    """Daily scan: for each finance-enabled society, if today ==
    ``maintenance_due_day``, materialize the period's dues (docs §9).

    Owns its own session (mirror ``vault/services/jobs.py``): opens a
    ``SessionLocal``, gathers the enabled societies + today's UTC date, delegates
    the per-society loop to :func:`_run_for_societies` (which commits per society
    and isolates failures), then closes in a ``finally``. Idempotent and callable
    on demand. Returns a run summary.
    """
    session = SessionLocal()
    try:
        today = utcnow().date()
        society_ids = _enabled_finance_society_ids(session)
        result = _run_for_societies(session, society_ids, today)
        logger.info(
            "finance dues scan: %d societies processed, %d dues created",
            result["societies_processed"],
            result["dues_created"],
        )
        return result
    finally:
        session.close()

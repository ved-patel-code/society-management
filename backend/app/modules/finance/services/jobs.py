"""Finance worker jobs (docs/modules/finance.md §9).

Runs in the ``worker`` service. A DAILY scan generates dues per society on its
``maintenance_due_day`` (idempotent, backfills). Owns its own ``SessionLocal`` +
commit (not a request session). ``run_daily_dues_generation`` is a frozen stub —
Wave G implements it.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("app.worker.finance")


def run_daily_dues_generation() -> None:
    """Daily: for each society with finance ENABLED, if today ==
    ``maintenance_due_day`` (from its config), run ``generate_due_cycle`` (docs §9).

    Wave G, with its own session (mirror ``vault/services/jobs.py``):
    - Open a ``SessionLocal``; query ``society_modules`` for enabled finance rows.
    - For each, load config; if ``date.today().day == maintenance_due_day`` (in
      the society's tz — UTC date acceptable in v1), call
      ``FinanceService(session).generate_due_cycle(society_id, as_of=today,
      actor_user_id=None)``. Idempotent, so a duplicate run is safe.
    - Commit once; log a per-society summary; never let one society's failure abort
      the others (catch + log per society).
    """
    raise NotImplementedError("Wave G: run_daily_dues_generation")

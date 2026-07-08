"""Complaints worker jobs — WAVE F (docs/modules/complaints.md §9).

Runs in the ``worker`` service. A DAILY scan auto-archives each society's closed
complaints once ``closed_at`` is older than that society's ``auto_archive_days``
(config, default 15). Owns its own ``SessionLocal`` + commit-per-society (not a
request session), mirroring ``app/modules/finance/services/jobs.py``:
open one ``SessionLocal``, iterate enabled societies with per-society failure
isolation (commit on success, rollback+continue on error), close in a ``finally``.

Per society it selects ``status='closed' AND closed_at <= now - N`` (partial
index ``ix_complaints_status_closed_at``), sets ``status='archived'`` +
``archived_at`` and writes the ``(closed -> archived, changed_by=NULL)`` timeline
row via ``support.record_transition``, and audits ``complaint.archived``.
Idempotent — only ``status='closed'`` rows are ever touched, so a duplicate run
archives nothing new.

FROZEN STUBS: ``run_daily_auto_archive`` returns a summary dict so the worker
entrypoint import + green gate work; Wave F implements the real scan +
``_run_for_societies`` helper, editing only THIS file + its own test file.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

logger = logging.getLogger("app.worker.complaints")


def _enabled_complaints_society_ids(session: Session) -> list[int]:
    """Society ids with the complaints module enabled (§9)."""
    raise NotImplementedError("Wave F: _enabled_complaints_society_ids")


def _run_for_societies(
    session: Session, society_ids: list[int], as_of: date
) -> dict[str, int]:
    """Per-society auto-archive for the given run date (§9).

    Commit-per-society + rollback-on-failure (finance's isolation pattern).
    ``as_of`` is passed explicitly so tests drive the calendar deterministically.
    Returns a run summary ``{societies_processed, complaints_archived}``.
    """
    raise NotImplementedError("Wave F: _run_for_societies")


def run_daily_auto_archive() -> dict[str, int]:
    """Daily scan: archive closed complaints past their auto-archive window (§9).

    Owns its own ``SessionLocal``; delegates the per-society loop to
    :func:`_run_for_societies`; closes in a ``finally``. Idempotent.

    STUB: returns an empty summary until Wave F implements it (kept importable +
    callable so the worker entrypoint registration and the green gate pass).
    """
    return {"societies_processed": 0, "complaints_archived": 0}

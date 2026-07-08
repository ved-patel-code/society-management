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
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.time import utcnow
from app.core.db import SessionLocal
from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import STATUS_ARCHIVED
from app.modules.complaints.services import support
from app.modules.complaints.services.support import MODULE_KEY, load_config
from app.platform.audit.service import AuditService
from app.platform.models import SocietyModule

logger = logging.getLogger("app.worker.complaints")


def _enabled_complaints_society_ids(session: Session) -> list[int]:
    """Society ids with the complaints module enabled (§9)."""
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
    """Per-society auto-archive for the given run date (§9).

    Failure isolation + commit choice: **commit after each successful society,
    rollback on failure** — mirroring how ``finance/services/jobs.py`` isolates
    per society. A commit-once-at-the-end strategy would let one poisoned society
    discard every prior society's work when it rolls back; committing per society
    means a mid-scan failure never taints an already-processed society, and a
    ``rollback()`` on error hands the next society a clean session.

    For each society: load its ``auto_archive_days`` (N), compute the aware-UTC
    cutoff ``older_than = <as_of at UTC midnight> - N days`` (``as_of`` is a date
    so tests/backfills drive the calendar deterministically), fetch the
    ``status='closed'`` complaints whose ``closed_at <= older_than`` (partial
    index), and move each to ``archived`` via :func:`support.record_transition`
    (stamps ``archived_at`` + writes the ``(closed -> archived, changed_by=NULL)``
    timeline row). Each archive audits ``complaint.archived`` (actor = system
    worker). Idempotent — only ``status='closed'`` rows are ever selected, so a
    duplicate run archives nothing new. Returns a run summary.
    """
    # A single aware-UTC "now" for the whole scan, derived from the run date so
    # the run is deterministic. Timestamp comparisons (closed_at) want a datetime.
    now = datetime.combine(as_of, time.min, tzinfo=timezone.utc)
    processed = 0
    total_archived = 0
    for society_id in society_ids:
        try:
            cfg = load_config(session, society_id)
            older_than = now - timedelta(days=cfg.auto_archive_days)
            repo = ComplaintRepository(session)
            audit = AuditService(session)
            rows = repo.closed_to_archive(society_id, older_than=older_than)
            archived = 0
            for complaint in rows:
                support.record_transition(
                    repo,
                    complaint,
                    to_status=STATUS_ARCHIVED,
                    note=None,
                    changed_by=None,
                    at=now,
                )
                audit.record(
                    action="complaint.archived",
                    actor_user_id=None,
                    society_id=society_id,
                    entity_type="complaint",
                    entity_id=complaint.id,
                    after={"reference": complaint.reference},
                )
                archived += 1
            session.commit()
            processed += 1
            total_archived += archived
            logger.info(
                "complaints auto-archive: society=%s archived=%d (as_of=%s)",
                society_id,
                archived,
                as_of.isoformat(),
            )
        except Exception:
            # One society's failure must not abort the others. Roll back so the
            # poisoned unit of work can't taint the next society's session.
            session.rollback()
            logger.exception(
                "complaints auto-archive: society=%s FAILED (as_of=%s), skipping",
                society_id,
                as_of.isoformat(),
            )
            continue
    return {
        "societies_processed": processed,
        "complaints_archived": total_archived,
    }


def run_daily_auto_archive() -> dict[str, int]:
    """Daily scan: archive closed complaints past their auto-archive window (§9).

    Owns its own session (mirror ``finance/services/jobs.py``): opens a
    ``SessionLocal``, gathers the enabled societies + today's UTC date, delegates
    the per-society loop to :func:`_run_for_societies` (which commits per society
    and isolates failures), then closes in a ``finally``. Idempotent and callable
    on demand. Returns a run summary.
    """
    session = SessionLocal()
    try:
        today = utcnow().date()
        society_ids = _enabled_complaints_society_ids(session)
        result = _run_for_societies(session, society_ids, today)
        logger.info(
            "complaints auto-archive scan: %d societies processed, "
            "%d complaints archived",
            result["societies_processed"],
            result["complaints_archived"],
        )
        return result
    finally:
        session.close()

"""Notifications worker jobs (docs/modules/notifications.md §9).

Two DAILY scans, both running in the ``worker`` service with their own
``SessionLocal`` + per-society failure isolation (mirrors
``finance/services/jobs.py``):

1. **Dues reminder scan** — for each society with BOTH ``notifications`` and
   ``finance`` enabled, evaluate the dues reminder rule against each owing house
   and create consolidated ``maintenance_due`` notifications (idempotent via
   ``dedupe_key``). Consumes the Finance + House interfaces (never their tables).
2. **Read-purge** — for each society, delete notifications whose ``read_at`` is
   older than that society's ``read_retention_days``.

Both commit PER SOCIETY and roll back on a per-society error so one poisoned
society can never abort or taint the rest (plan §7 — containment). ``as_of`` /
``now`` are injected so tests drive the calendar deterministically.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.time import utcnow
from app.core.db import SessionLocal
from app.modules.houses.service import HouseService
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.services import dues_rule, support
from app.platform.models import SocietyModule

logger = logging.getLogger("app.worker.notifications")

_NOTIFICATIONS_KEY = "notifications"
_FINANCE_KEY = "finance"


def _enabled_society_ids(session: Session, module_key: str) -> set[int]:
    """Society ids with ``module_key`` enabled."""
    return set(
        session.execute(
            select(SocietyModule.society_id).where(
                SocietyModule.module_key == module_key,
                SocietyModule.enabled.is_(True),
            )
        ).scalars()
    )


# --- dues reminder scan -------------------------------------------------------


def _run_dues_for_societies(
    society_ids: list[int], as_of: date
) -> dict[str, int]:
    """Per-society dues-reminder evaluation for ``as_of`` (docs §9, §4.3).

    Fresh-session + commit + rollback-on-error PER SOCIETY (failure isolation).
    For each society: load its cadence config, list its owing houses
    (Finance/House interface), batch-resolve owners, and let the rule decide +
    build per house. Idempotent — a re-run inserts nothing new (dedupe). Returns
    a run summary.
    """
    processed = 0
    total_created = 0
    for society_id in society_ids:
        # Fresh session per society: on top of ORM-state isolation, this hands
        # each society a clean connection so even a connection-level failure in
        # one society can't taint the next (plan §7 — hard containment).
        society_session = SessionLocal()
        try:
            cfg = support.load_config(society_session, society_id)
            house_service = HouseService(society_session)
            # Candidate owing houses as (house_id, first_left_empty_on) tuples
            # (status != empty). The rule no-ops for any house that is actually
            # paid up (outstanding_dues empty).
            owing = house_service.houses_owing(society_id)
            house_ids = [house_id for house_id, _ in owing]
            # Batch-resolve owners for ALL owing houses in ONE query (no N+1).
            owners_by_house = house_service.owner_user_ids_by_house(
                society_id, house_ids
            )
            created_here = 0
            for house_id in house_ids:
                created_here += dues_rule.build_for_house(
                    society_session,
                    society_id=society_id,
                    house_id=house_id,
                    cfg=cfg,
                    today=as_of,
                    owners=owners_by_house.get(house_id, set()),
                )
            society_session.commit()
            processed += 1
            total_created += created_here
            if created_here:
                logger.info(
                    "dues reminders: society=%s created=%d (as_of=%s)",
                    society_id,
                    created_here,
                    as_of.isoformat(),
                )
        except Exception:
            society_session.rollback()
            logger.exception(
                "dues reminders: society=%s FAILED (as_of=%s), skipping",
                society_id,
                as_of.isoformat(),
            )
            continue
        finally:
            society_session.close()
    return {"societies_processed": processed, "reminders_created": total_created}


def run_daily_dues_reminders() -> dict[str, int]:
    """Daily scan: consolidated maintenance-due reminders per owing house (docs §9).

    Runs only for societies with BOTH notifications AND finance enabled (the dues
    rule needs Finance). Owns its own session; delegates the per-society loop to
    :func:`_run_dues_for_societies`. Idempotent. Returns a run summary.
    """
    session = SessionLocal()
    try:
        today = utcnow().date()
        eligible = _enabled_society_ids(session, _NOTIFICATIONS_KEY) & (
            _enabled_society_ids(session, _FINANCE_KEY)
        )
    finally:
        session.close()
    result = _run_dues_for_societies(sorted(eligible), today)
    logger.info(
        "dues reminder scan: %d societies, %d reminders created",
        result["societies_processed"],
        result["reminders_created"],
    )
    return result


# --- read-purge ---------------------------------------------------------------


def _run_purge_for_societies(
    society_ids: list[int], now: datetime
) -> dict[str, int]:
    """Per-society read-purge for ``now`` (docs §9).

    Each society's retention window comes from its own config; the cutoff is
    ``now - read_retention_days``. Fresh-session + commit + rollback-on-error per
    society. Returns a run summary.
    """
    processed = 0
    total_deleted = 0
    for society_id in society_ids:
        society_session = SessionLocal()
        try:
            cfg = support.load_config(society_session, society_id)
            cutoff = now - timedelta(days=cfg.read_retention_days)
            deleted = NotificationRepository(
                society_session
            ).delete_read_before_for_society(society_id, cutoff)
            society_session.commit()
            processed += 1
            total_deleted += deleted
            if deleted:
                logger.info(
                    "notif purge: society=%s deleted=%d (cutoff=%s)",
                    society_id,
                    deleted,
                    cutoff.isoformat(),
                )
        except Exception:
            society_session.rollback()
            logger.exception(
                "notif purge: society=%s FAILED, skipping", society_id
            )
            continue
        finally:
            society_session.close()
    return {"societies_processed": processed, "notifications_deleted": total_deleted}


def run_daily_read_purge() -> dict[str, int]:
    """Daily scan: delete read notifications older than each society's retention
    (docs §9). Owns its own session; per-society isolation. Returns a summary."""
    session = SessionLocal()
    try:
        now = utcnow()
        society_ids = _enabled_society_ids(session, _NOTIFICATIONS_KEY)
    finally:
        session.close()
    result = _run_purge_for_societies(sorted(society_ids), now)
    logger.info(
        "notif purge scan: %d societies, %d notifications deleted",
        result["societies_processed"],
        result["notifications_deleted"],
    )
    return result

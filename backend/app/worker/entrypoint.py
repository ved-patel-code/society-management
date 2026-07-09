"""Worker entrypoint (docs/PF §13, docs/02 §7).

Runs in the ``worker`` service (same image, different command). Foundation has a
single scheduled job: purge dead auth rows daily. Feature modules register their
own jobs here later (dues generation, notification reminders, trash purge, …).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from app.modules.complaints.services.jobs import run_daily_auto_archive
from app.modules.finance.services.jobs import run_daily_dues_generation
from app.modules.notifications.api import subscribe_handlers as subscribe_notifications
from app.modules.notifications.services.jobs import (
    run_daily_dues_reminders,
    run_daily_read_purge,
)
from app.modules.vault.services.jobs import purge_trash, reconcile_usage
from app.worker.jobs.cleanup import purge_expired_auth_rows

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.worker")


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")
    # Daily auth-row cleanup at 03:15 UTC.
    scheduler.add_job(
        purge_expired_auth_rows,
        trigger="cron",
        hour=3,
        minute=15,
        id="purge_expired_auth_rows",
        replace_existing=True,
    )
    # Vault trash auto-purge daily at 03:30 UTC (docs vault.md §9).
    scheduler.add_job(
        purge_trash,
        trigger="cron",
        hour=3,
        minute=30,
        id="vault_purge_trash",
        replace_existing=True,
    )
    # Vault usage reconcile nightly at 04:00 UTC (docs vault.md §9).
    scheduler.add_job(
        reconcile_usage,
        trigger="cron",
        hour=4,
        minute=0,
        id="vault_reconcile_usage",
        replace_existing=True,
    )
    # Finance daily dues-generation scan at 02:00 UTC (docs finance.md §9). Per
    # society, generates the period's dues when today == its maintenance_due_day
    # (idempotent, backfills). Callable on demand too via the API.
    scheduler.add_job(
        run_daily_dues_generation,
        trigger="cron",
        hour=2,
        minute=0,
        id="finance_daily_dues_generation",
        replace_existing=True,
    )
    # Complaints auto-archive scan at 01:30 UTC (docs complaints.md §9). Per
    # society, archives closed complaints older than auto_archive_days
    # (idempotent — only touches status='closed' rows).
    scheduler.add_job(
        run_daily_auto_archive,
        trigger="cron",
        hour=1,
        minute=30,
        id="complaints_auto_archive",
        replace_existing=True,
    )
    # Notifications dues-reminder scan at 06:00 UTC (docs notifications.md §9).
    # Per society (notifications + finance enabled), one consolidated
    # maintenance_due reminder per owing house on its cadence day (idempotent).
    scheduler.add_job(
        run_daily_dues_reminders,
        trigger="cron",
        hour=6,
        minute=0,
        id="notifications_dues_reminders",
        replace_existing=True,
    )
    # Notifications read-purge at 04:30 UTC (docs notifications.md §9). Per
    # society, deletes read notifications older than read_retention_days.
    scheduler.add_job(
        run_daily_read_purge,
        trigger="cron",
        hour=4,
        minute=30,
        id="notifications_read_purge",
        replace_existing=True,
    )
    return scheduler


def main() -> None:
    logger.info("Worker starting; scheduling foundation jobs.")
    # The worker process has its own event bus — register the Notifications
    # handlers so any event emitted during a job produces notifications too.
    subscribe_notifications()
    scheduler = build_scheduler()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover
        logger.info("Worker shutting down.")


if __name__ == "__main__":
    main()
